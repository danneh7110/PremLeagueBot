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
    """Buttons on a prediction poll message. timeout=None + custom_id so it
    survives restarts once re-registered in League.cog_load()."""

    def __init__(self, match_id: int, database: Database):
        super().__init__(timeout=None)
        self.match_id = match_id
        self.database = database
        self.home_button.custom_id = f"predict:{match_id}:HOME"
        self.draw_button.custom_id = f"predict:{match_id}:DRAW"
        self.away_button.custom_id = f"predict:{match_id}:AWAY"

    async def _handle_vote(self, interaction: discord.Interaction, choice: str):
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
            f"Locked in: **{choice.title()}**. You can change this up until kickoff.",
            ephemeral=True,
        )

    @discord.ui.button(label="Home Win", style=discord.ButtonStyle.primary)
    async def home_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._handle_vote(interaction, "HOME")

    @discord.ui.button(label="Draw", style=discord.ButtonStyle.secondary)
    async def draw_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._handle_vote(interaction, "DRAW")

    @discord.ui.button(label="Away Win", style=discord.ButtonStyle.primary)
    async def away_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._handle_vote(interaction, "AWAY")


class League(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = Database(DB_PATH)

    async def cog_load(self) -> None:
        await self.database.initialise()
        # Re-attach persistent views for any match that already has an open poll,
        # so the buttons keep working after a bot restart.
        for match in await self.database.matches_needing_poll():
            self.bot.add_view(PredictionView(match["match_id"], self.database))
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
            await interaction.response.send_message("The league has no managers yet.")
            return

        lines = ["**Premier League Bot table**"]
        for position, row in enumerate(rows, start=1):
            total = row.points + row.prediction_points
            lines.append(
                f"{position}. **{self.team_label(row.team)}** — "
                f"{total} pts ({row.points} team + {row.prediction_points} predictions) "
                f"({row.manager})"
            )
        await interaction.response.send_message("\n".join(lines))

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

            home = self.team_label(match["home_team"])
            away = self.team_label(match["away_team"])
            embed = discord.Embed(
                title=f"{home} vs {away}",
                description=f"Kickoff: <t:{_to_unix(match['kickoff_utc'])}:F>\nVote below!",
            )
            view = PredictionView(match["match_id"], self.database)
            message = await channel.send(embed=embed, view=view)
            await self.database.set_poll_message(match["match_id"], channel.id, message.id)

    async def _score_finished_matches(self):
        channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        for match in await self.database.matches_to_score():
            outcome = await self.database.score_match(match["match_id"])
            if channel is None:
                continue
            home = self.team_label(match["home_team"])
            away = self.team_label(match["away_team"])
            outcome_text = {
                "HOME": f"{home} win", "AWAY": f"{away} win", "DRAW": "Draw",
            }[match["result"]]
            correct_voters = len(outcome.prediction_points_awarded)
            await channel.send(
                f"📣 **Full time: {home} vs {away} — {outcome_text}**\n"
                f"{correct_voters} member(s) predicted it correctly (+1 pt each)."
            )


def _to_unix(iso_utc: str) -> int:
    from datetime import datetime
    return int(datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).timestamp())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(League(bot))