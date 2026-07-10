import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).parent
load_dotenv(PROJECT_DIR / ".env")


class PremierLeagueBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.load_extension("cogs.league")
        await self.tree.sync()


intents = discord.Intents.default()
bot = PremierLeagueBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    assert bot.user is not None
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


def main() -> None:
    token = os.getenv("TOKEN")
    if not token or token == "YOUR_DISCORD_BOT_TOKEN":
        raise RuntimeError(
            "Set TOKEN in .env before starting the bot. Copy .env.example to .env first."
        )
    bot.run(token)


if __name__ == "__main__":
    main()

