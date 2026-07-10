import random
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from utils.database import Database


DATA_DIR = Path(__file__).parents[1] / "data"
DB_PATH = Path(__file__).parents[1] / "database.db"


class League(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = Database(DB_PATH)

    async def cog_load(self) -> None:
        await self.database.initialise()

    def team_label(self, team: str) -> str:
        """Return a team name prefixed with its available custom emoji."""
        emoji_name = "pl_" + re.sub(r"[^a-z0-9]+", "_", team.lower()).strip("_")
        emoji = discord.utils.get(self.bot.emojis, name=emoji_name)
        return f"{emoji} {team}" if emoji else f"⚽ {team}"

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
            lines.append(
                f"{position}. **{self.team_label(row.team)}** — "
                f"{row.points} pts ({row.manager})"
            )
        await interaction.response.send_message("\n".join(lines))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(League(bot))
