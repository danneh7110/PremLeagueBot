"""
Thin wrapper around the football-data.org API (v4) for Premier League data.

Get a free API key at https://www.football-data.org/client/register
and put it in .env as FOOTBALL_DATA_API_KEY. Free tier is rate-limited
(10 requests/min at time of writing).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

import aiohttp

BASE_URL = "https://api.football-data.org/v4"
COMPETITION = "PL"

# football-data.org returns names like "Arsenal FC", "Wolverhampton Wanderers FC".
# Your data/teams.json presumably stores the plain names ("Arsenal",
# "Wolverhampton Wanderers"). This strips the common suffixes to line them up.
# If any of your teams.json entries don't match after stripping, add an
# explicit override in MANUAL_OVERRIDES below (api_name -> your_teams_json_name).
_SUFFIXES = (" FC", " AFC", " CF")

MANUAL_OVERRIDES: dict[str, str] = {
    # "Brighton & Hove Albion FC": "Brighton",
    "AFC Bournemouth": "Bournemouth",
}


class FootballAPIError(RuntimeError):
    pass


def normalise_team_name(api_name: str) -> str:
    if api_name in MANUAL_OVERRIDES:
        return MANUAL_OVERRIDES[api_name]
    name = api_name
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip()


def _headers() -> dict[str, str]:
    api_key = os.getenv("FOOTBALL_DATA_API_KEY")
    if not api_key:
        raise FootballAPIError(
            "Set FOOTBALL_DATA_API_KEY in .env before fetching fixtures."
        )
    return {"X-Auth-Token": api_key}


async def fetch_matches(status: str | None = None) -> list[dict[str, Any]]:
    """Fetch PL matches. status: 'SCHEDULED', 'FINISHED', or None for all."""
    url = f"{BASE_URL}/competitions/{COMPETITION}/matches"
    params = {"status": status} if status else {}

    async with aiohttp.ClientSession(headers=_headers()) as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise FootballAPIError(f"football-data.org returned {resp.status}: {text}")
            data = await resp.json()

    return data.get("matches", [])


def parse_match(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw API match into the kwargs Database.upsert_match expects."""
    home_team = normalise_team_name(raw["homeTeam"]["name"])
    away_team = normalise_team_name(raw["awayTeam"]["name"])

    status = raw["status"]
    result = None
    if status == "FINISHED":
        winner = raw["score"]["winner"]  # 'HOME_TEAM' | 'AWAY_TEAM' | 'DRAW'
        result = {"HOME_TEAM": "HOME", "AWAY_TEAM": "AWAY", "DRAW": "DRAW"}.get(winner)

    normalised_status = "FINISHED" if status == "FINISHED" else "SCHEDULED"

    return {
        "match_id": raw["id"],
        "home_team": home_team,
        "away_team": away_team,
        "kickoff_utc": raw["utcDate"],
        "status": normalised_status,
        "result": result,
    }


def kickoff_is_within(kickoff_utc: str, hours: float) -> bool:
    kickoff = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    delta = kickoff - datetime.now(timezone.utc)
    return 0 <= delta.total_seconds() <= hours * 3600