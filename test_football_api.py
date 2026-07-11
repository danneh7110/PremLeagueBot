"""
Quick standalone test — run this locally (not part of the bot) to confirm:
  1. Your FOOTBALL_DATA_API_KEY works
  2. What team name strings the API is currently returning for PL
  3. Whether the 2026-27 season/promoted teams are live yet

Usage:
    python test_football_api.py

Requires: pip install aiohttp python-dotenv
"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from utils.football_api import fetch_matches, normalise_team_name, FootballAPIError  # noqa: E402


async def main() -> None:
    if not os.getenv("FOOTBALL_DATA_API_KEY"):
        print("FOOTBALL_DATA_API_KEY not found — check your .env file.")
        return

    try:
        matches = await fetch_matches()
    except FootballAPIError as exc:
        print(f"API call failed: {exc}")
        return

    if not matches:
        print("API call succeeded but returned 0 matches — the 2026-27 "
              "fixture list may not be published yet.")
        return

    print(f"Got {len(matches)} matches. Sample of team names returned:\n")

    seen = set()
    for m in matches:
        for side in ("homeTeam", "awayTeam"):
            raw_name = m[side]["name"]
            if raw_name in seen:
                continue
            seen.add(raw_name)
            print(f"  API name: {raw_name!r:35} -> normalised: {normalise_team_name(raw_name)!r}")

    print(f"\n{len(seen)} unique team names seen out of {len(matches)} matches fetched.")
    print("Compare the 'normalised' column above against your data/teams.json entries.")


if __name__ == "__main__":
    asyncio.run(main())