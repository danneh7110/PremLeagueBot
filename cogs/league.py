import logging
import random
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import football_api
from utils.database import Database

DATA_DIR = Path(__file__).parents[1] / "data"
DB_PATH = Path(__file__).parents[1] / "database.db"

logger = logging.getLogger(__name__)

SYNC_INTERVAL_MINUTES = 10
POLL_WINDOW_HOURS = 24
ANNOUNCE_CHANNEL_ID = 1525121343316820039  # TODO: replace with your channel id


class PredictionView(discord.ui.View):
    """Buttons on a prediction poll message, labelled with the actual teams
    playing. timeout=None + custom_id so it survives restarts once
    re-registered in League.cog_load()."""

    def __init__(self, match_id: int, database: Database, home_team: str, away_team: str):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.database = database
        self.home_button.custom_id = f"predict:{match_id}:HOME"
        self.draw_button.custom_id = f"predict:{match_id}:DRAW"
        self.away_button.custom_id = f"predict:{match_id}:AWAY"
        # Truncate defensively - Discord button labels cap at 80 chars, team
        # names are short but this keeps it safe if a long name ever appears.
        self.home_button.label = f"{home_team} Win"[:80]
        self.away_button.label = f"{away_team} Win"[:80]

    async def _handle_vote(self, interaction: discord.Interaction, choice: str, label: str):
        assert interaction.user is not None
        team = await self.database.get_user_team(interaction.user.id)
        if team is None:
            await interaction.response.send_message(
                "You need to `/join` a team before you can predict matches.",
                ephemeral=True,
            )
            return

        match = await self.database.get_match(self.match_id)
        if match is None or match["status"] != "SCHEDULED":
            await interaction.response.send_message(
                "Voting is closed for this match.", ephemeral=True
            )
            return

        await self.database.add_prediction(self.match_id, interaction.user.id, choice)
        await interaction.response.send_message(
            f"✅ Locked in: **{label}**. You can change this up until kickoff.",
            ephemeral=True,
        )

    @discord.ui.button(label="Home Win", style=discord.ButtonStyle.primary, emoji="🏠")
    async def home_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, "HOME", button.label)

    @discord.ui.button(label="Draw", style=discord.ButtonStyle.secondary, emoji="🤝")
    async def draw_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, "DRAW", button.label)

    @discord.ui.button(label="Away Win", style=discord.ButtonStyle.primary, emoji="✈️")
    async def away_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, "AWAY", button.label)


class League(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = Database(DB_PATH)

    async def cog_load(self) -> None:
        await self.database.initialise()
        # Re-attach persistent views for any match that already has an open poll,
        # so the buttons keep working after a bot restart.
        for match in await self.database.matches_needing_poll():
            self.bot.add_view(
                PredictionView(match["match_id"], self.database, match["home_team"], match["away_team"])
            )
        self.sync_fixtures.start()

    async def cog_unload(self) -> None:
        self.sync_fixtures.cancel()

    def team_label(self, team: str) -> str:
        """Return a team name prefixed with its available custom emoji."""
        emoji_name = "pl_" + re.sub(r"[^a-z0-9]+", "_", team.lower()).strip("_")
        emoji = discord.utils.get(self.bot.emojis, name=emoji_name)
        return f"{emoji} {team}" if emoji else f"⚽ {team}"

    # ------------------------------------------------------------ commands --

    @app_commands.command(name="ping", description="Check whether the bot is online.")
    async def ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Pong! The Premier League bot is online.")

    @app_commands.command(name="join", description="Claim a random available Premier League team.")
    async def join(self, interaction: discord.Interaction) -> None:
        assert interaction.user is not None
        existing_team = await self.database.get_user_team(interaction.user.id)
        if existing_team:
            await interaction.response.send_message(
                f"You already manage **{self.team_label(existing_team)}**. "
                "Use `/myteam` to check it.",
                ephemeral=True,
            )
            return

        teams = (DATA_DIR / "teams.json").read_text(encoding="utf-8")
        available_teams = await self.database.available_teams_from_json(teams)
        if not available_teams:
            await interaction.response.send_message(
                "Every team has been claimed. Ask an admin to reset the league.", ephemeral=True
            )
            return

        team = random.choice(available_teams)
        await self.database.assign_team(interaction.user.id, interaction.user.display_name, team)
        await interaction.response.send_message(
            f"Welcome to the league, {interaction.user.mention}! You now manage "
            f"**{self.team_label(team)}**."
        )

    @app_commands.command(name="myteam", description="Show the team you manage.")
    async def myteam(self, interaction: discord.Interaction) -> None:
        assert interaction.user is not None
        team = await self.database.get_user_team(interaction.user.id)
        if team:
            await interaction.response.send_message(
                f"You manage **{self.team_label(team)}**.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "You have not joined the league yet. Use `/join` to claim a team.", ephemeral=True
            )

    @app_commands.command(name="table", description="Show the current league table.")
    async def table(self, interaction: discord.Interaction) -> None:
        rows = await self.database.league_table()
        if not rows:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🏆 League Table",
                    description="No managers yet — use `/join` to get started!",
                    color=discord.Color.blurple(),
                )
            )
            return

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for position, row in enumerate(rows, start=1):
            total = row.points + row.prediction_points
            rank = medals.get(position, f"`#{position}`")
            lines.append(
                f"{rank}  **{self.team_label(row.team)}** — {row.manager}\n"
                f"　　**{total} pts**  ·  {row.points} team + {row.prediction_points} predictions"
            )

        embed = discord.Embed(
            title="🏆 League Table",
            description="\n\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Win = 3pts · Draw = 1pt · Loss = 0pts · Correct prediction = +1pt")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="form", description="Show a team's recent form (last 5 matches).")
    @app_commands.describe(team="Leave blank to check your own team")
    async def form(self, interaction: discord.Interaction, team: str | None = None) -> None:
        assert interaction.user is not None
        if team is None:
            team = await self.database.get_user_team(interaction.user.id)
            if team is None:
                await interaction.response.send_message(
                    "You haven't joined a team — use `/join`, or pass a `team` to check someone else's.",
                    ephemeral=True,
                )
                return

        results = await self.database.get_team_form(team, limit=5)
        if not results:
            await interaction.response.send_message(
                f"No finished matches for **{team}** yet.", ephemeral=True
            )
            return

        squares = {"W": "🟩", "D": "🟨", "L": "🟥"}
        guide = "".join(squares[r["outcome"]] for r in results)

        lines = []
        for r in results:
            vs = "vs" if r["was_home"] else "@"
            score = f"{r['home_score']}-{r['away_score']}" if r["home_score"] is not None else "?"
            lines.append(f"{squares[r['outcome']]} {vs} {r['opponent']} ({score})")

        embed = discord.Embed(
            title=f"📈 {self.team_label(team)} — Recent Form",
            description=f"{guide}\n\n" + "\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Oldest → newest, left to right")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="streak", description="Show your (or someone's) prediction streak.")
    @app_commands.describe(member="Leave blank to check your own streak")
    async def streak(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        assert interaction.user is not None
        target = member or interaction.user
        current, longest = await self.database.get_user_streak(target.id)

        flame = " 🔥" * min(current, 5) if current >= 3 else ""
        embed = discord.Embed(
            title=f"🔥 {target.display_name}'s Prediction Streak",
            description=(
                f"**Current streak:** {current} correct in a row{flame}\n"
                f"**Best ever:** {longest} correct in a row"
            ),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="streaks", description="Show the top prediction streaks in the league.")
    async def streaks(self, interaction: discord.Interaction) -> None:
        rows = await self.database.get_streak_leaderboard(limit=10)
        if not rows:
            await interaction.response.send_message("No scored predictions yet.")
            return

        lines = []
        for user_id, display_name, current, longest in rows:
            flame = " 🔥" * min(current, 5) if current >= 3 else ""
            lines.append(f"**{display_name}** — {current} current{flame} (best: {longest})")

        embed = discord.Embed(
            title="🔥 Prediction Streaks",
            description="\n".join(lines),
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------------------- tasks --

    @tasks.loop(minutes=SYNC_INTERVAL_MINUTES)
    async def sync_fixtures(self):
        try:
            await self._pull_latest_matches()
            await self._post_new_polls()
            await self._score_finished_matches()
        except football_api.FootballAPIError as exc:
            logger.warning("Football API error during sync: %s", exc)
        except Exception:
            logger.exception("Unexpected error in sync_fixtures")

    @sync_fixtures.before_loop
    async def before_sync_fixtures(self):
        await self.bot.wait_until_ready()

    async def _pull_latest_matches(self):
        raw_matches = await football_api.fetch_matches()
        for raw in raw_matches:
            parsed = football_api.parse_match(raw)
            await self.database.upsert_match(**parsed)

    async def _post_new_polls(self):
        channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        if channel is None:
            logger.warning("ANNOUNCE_CHANNEL_ID not set/found — skipping poll posting.")
            return

        for match in await self.database.matches_needing_poll():
            if not football_api.kickoff_is_within(match["kickoff_utc"], POLL_WINDOW_HOURS):
                continue

            home_raw, away_raw = match["home_team"], match["away_team"]
            home = self.team_label(home_raw)
            away = self.team_label(away_raw)

            embed = discord.Embed(
                title=f"⚽ {home}  vs  {away}",
                description=(
                    f"🕒 Kickoff: <t:{_to_unix(match['kickoff_utc'])}:F> "
                    f"(<t:{_to_unix(match['kickoff_utc'])}:R>)\n\n"
                    "**Who's going to win?** Vote below — you can change your "
                    "pick right up until kickoff."
                ),
                color=discord.Color.green(),
            )
            embed.set_footer(text="Correct prediction = +1 pt")

            view = PredictionView(match["match_id"], self.database, home_raw, away_raw)
            message = await channel.send(embed=embed, view=view)
            await self.database.set_poll_message(match["match_id"], channel.id, message.id)

    async def _score_finished_matches(self):
        channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        for match in await self.database.matches_to_score():
            outcome = await self.database.score_match(match["match_id"])
            if channel is None:
                continue

            home_raw, away_raw = match["home_team"], match["away_team"]
            home = self.team_label(home_raw)
            away = self.team_label(away_raw)
            result = match["result"]
            home_score, away_score = match["home_score"], match["away_score"]

            if home_score is not None and away_score is not None:
                scoreline = f"{home}  **{home_score} - {away_score}**  {away}"
            else:
                scoreline = f"{home}  vs  {away}"

            outcome_text = {
                "HOME": f"{home} win", "AWAY": f"{away} win", "DRAW": "It's a draw",
            }[result]
            color = discord.Color.blue() if result == "DRAW" else discord.Color.dark_green()

            embed = discord.Embed(
                title="📣 Full Time",
                description=f"## {scoreline}\n\n**{outcome_text}!**",
                color=color,
            )

            manager_pts = ", ".join(
                f"<@{uid}> ({pts:+d})" for uid, pts in outcome.team_points_awarded.items()
            ) or "No one manages either team."
            embed.add_field(name="Team points awarded", value=manager_pts, inline=False)

            correct_mentions = [f"<@{uid}>" for uid in outcome.prediction_points_awarded]
            if correct_mentions:
                content = f"{' '.join(correct_mentions)} predicted correctly! 🎉 (+1 pt each)"
            else:
                content = "Nobody predicted this one correctly. 😬"

            await channel.send(content=content, embed=embed)


def _to_unix(iso_utc: str) -> int:
    from datetime import datetime
    return int(datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).timestamp())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(League(bot))