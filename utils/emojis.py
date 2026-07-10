import discord

TEAM_EMOJIS = {
    "Arsenal": "pl_arsenal",
    "Aston Villa": "pl_aston_villa",
    "Bournemouth": "pl_bournemouth",
    "Brentford": "pl_brentford",
    "Brighton": "pl_brighton",
    "Chelsea": "pl_chelsea",
    "Crystal Palace": "pl_crystal_palace",
    "Everton": "pl_everton",
    "Fulham": "pl_fulham",
    "Hull City": "pl_hull_city",
    "Ipswich Town": "pl_ipswich",
    "Leeds United": "pl_leeds",
    "Liverpool": "pl_liverpool",
    "Manchester City": "pl_manchester_city",
    "Manchester United": "pl_manchester_united",
    "Newcastle United": "pl_newcastle",
    "Nottingham Forest": "pl_nottingham_forest",
    "Sunderland": "pl_sunderland",
    "Tottenham Hotspur": "pl_tottenham",
    "Coventry City": "pl_coventry_city",
}


def get_team_emoji(bot: discord.Client, team: str) -> str:
    """Return the emoji string for a team."""

    emoji_name = TEAM_EMOJIS.get(team)

    if not emoji_name:
        return ""

    for guild in bot.guilds:
        emoji = discord.utils.get(guild.emojis, name=emoji_name)
        if emoji:
            return str(emoji)

    return ""


def team_name(bot: discord.Client, team: str) -> str:
    """Return '<emoji> Team Name'."""

    emoji = get_team_emoji(bot, team)

    if emoji:
        return f"{emoji} {team}"

    return team