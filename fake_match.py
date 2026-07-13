"""
Standalone helper for testing polls, scoring, form, and streaks without
waiting for real fixtures. Run from the project root (same folder as bot.py).
Talks to database.db directly — run these between bot restarts.

Fake matches use ids in the 900000000+ range so they'll never collide with
real football-data.org ids.

--- Testing a single poll/scoring flow (as before) ---

    python3 fake_match.py add 1 "Arsenal" "Chelsea"
    python3 bot.py                              # poll posts, go vote, Ctrl+C
    python3 fake_match.py finish 1 HOME 3 1
    python3 bot.py                              # scores it, posts result

--- Building form history for a team (no need to vote on these) ---

    python3 fake_match.py add 1 "Arsenal" "Chelsea" --minutes -50000
    python3 fake_match.py finish 1 HOME 2 0
    python3 fake_match.py add 2 "Liverpool" "Arsenal" --minutes -40000
    python3 fake_match.py finish 2 AWAY 0 1
    python3 fake_match.py add 3 "Arsenal" "Everton" --minutes -30000
    python3 fake_match.py finish 3 DRAW 1 1
    python3 fake_match.py add 4 "Arsenal" "Fulham" --minutes -20000
    python3 fake_match.py finish 4 HOME 3 1
    python3 fake_match.py add 5 "Brentford" "Arsenal" --minutes -10000
    python3 fake_match.py finish 5 AWAY 0 2
    python3 bot.py         # scores all 5 in one pass, then Ctrl+C
    # In Discord: /form Arsenal

    Use negative --minutes so kickoff is in the past — matches_needing_poll
    only looks at SCHEDULED matches within the next 24h, so past kickoffs
    just won't get a poll posted, which is fine since you're marking them
    finished immediately anyway.

--- Building a prediction streak for yourself ---

    Get your Discord user id first (Developer Mode on, right-click your
    name, Copy User ID). Then for each fake match, inject your pick BEFORE
    finishing it:

    python3 fake_match.py add 6 "Arsenal" "Chelsea" --minutes -5000
    python3 fake_match.py predict 6 123456789012345678 HOME
    python3 fake_match.py finish 6 HOME 2 1
    python3 fake_match.py add 7 "Liverpool" "Man City" --minutes -4000
    python3 fake_match.py predict 7 123456789012345678 DRAW
    python3 fake_match.py finish 7 DRAW 1 1
    python3 bot.py         # scores both, updates streak
    # In Discord: /streak

--- Utility ---

    python3 fake_match.py list           # show all fake matches + status
    python3 fake_match.py remove 3       # remove one fake match by id
    python3 fake_match.py remove-all     # remove every fake match
    python3 fake_match.py reset-points   # zero out everyone's points
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).parent / "database.db"
FAKE_ID_BASE = 900000000
FAKE_ID_MAX = 900001000  # anything below this (and >= BASE) is "ours"


def resolve_id(short_id: str) -> int:
    return FAKE_ID_BASE + int(short_id)


async def add(short_id: str, home: str, away: str, minutes: int, matchday: int | None) -> None:
    match_id = resolve_id(short_id)
    kickoff = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO matches (match_id, home_team, away_team, kickoff_utc, status, result, matchday)
               VALUES (?, ?, ?, ?, 'SCHEDULED', NULL, ?)
               ON CONFLICT(match_id) DO UPDATE SET
                   home_team=excluded.home_team, away_team=excluded.away_team,
                   kickoff_utc=excluded.kickoff_utc, status='SCHEDULED', result=NULL,
                   home_score=NULL, away_score=NULL, matchday=excluded.matchday,
                   poll_message_id=NULL, poll_channel_id=NULL, scored=0""",
            (match_id, home, away, kickoff, matchday),
        )
        await conn.commit()
    md_note = f", matchday {matchday}" if matchday is not None else ""
    print(f"Added fake match #{short_id} ({match_id}): {home} vs {away}, kickoff {kickoff}{md_note}.")


async def predict(short_id: str, user_id: str, choice: str) -> None:
    match_id = resolve_id(short_id)
    choice = choice.upper()
    if choice not in {"HOME", "AWAY", "DRAW"}:
        print("Choice must be HOME, AWAY, or DRAW.")
        return
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("SELECT match_id FROM matches WHERE match_id = ?", (match_id,))
        if await cursor.fetchone() is None:
            print(f"No fake match #{short_id} found — run 'add' first.")
            return
        await conn.execute(
            """INSERT INTO predictions (match_id, user_id, choice) VALUES (?, ?, ?)
               ON CONFLICT(match_id, user_id) DO UPDATE SET choice = excluded.choice""",
            (match_id, int(user_id), choice),
        )
        await conn.commit()
    print(f"Injected prediction for user {user_id} on match #{short_id}: {choice}.")


async def finish(short_id: str, result: str, home_score: int | None = None, away_score: int | None = None) -> None:
    match_id = resolve_id(short_id)
    result = result.upper()
    if result not in {"HOME", "AWAY", "DRAW"}:
        print("Result must be HOME, AWAY, or DRAW.")
        return
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute("SELECT match_id FROM matches WHERE match_id = ?", (match_id,))
        if await cursor.fetchone() is None:
            print(f"No fake match #{short_id} found — run 'add' first.")
            return
        await conn.execute(
            """UPDATE matches SET status = 'FINISHED', result = ?,
                   home_score = ?, away_score = ? WHERE match_id = ?""",
            (result, home_score, away_score, match_id),
        )
        await conn.commit()
    score_note = f" ({home_score}-{away_score})" if home_score is not None else ""
    print(f"Marked fake match #{short_id} FINISHED, result={result}{score_note}.")
    print("Restart your bot to trigger scoring for this (and any other pending) fake matches.")


async def remove(short_id: str) -> None:
    match_id = resolve_id(short_id)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("DELETE FROM predictions WHERE match_id = ?", (match_id,))
        await conn.execute("DELETE FROM matches WHERE match_id = ?", (match_id,))
        await conn.commit()
    print(f"Removed fake match #{short_id} and its predictions.")


async def remove_all() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "DELETE FROM predictions WHERE match_id >= ? AND match_id < ?", (FAKE_ID_BASE, FAKE_ID_MAX)
        )
        await conn.execute(
            "DELETE FROM matches WHERE match_id >= ? AND match_id < ?", (FAKE_ID_BASE, FAKE_ID_MAX)
        )
        await conn.commit()
    print("Removed all fake matches and their predictions.")


async def list_fake() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM matches WHERE match_id >= ? AND match_id < ? ORDER BY match_id",
            (FAKE_ID_BASE, FAKE_ID_MAX),
        )
        rows = await cursor.fetchall()
    if not rows:
        print("No fake matches exist right now.")
        return
    for row in rows:
        short_id = row["match_id"] - FAKE_ID_BASE
        score = f"{row['home_score']}-{row['away_score']}" if row["home_score"] is not None else "?"
        print(
            f"#{short_id}: {row['home_team']} vs {row['away_team']} | "
            f"{row['status']} | result={row['result']} ({score}) | scored={bool(row['scored'])}"
        )


async def reset_points() -> None:
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE managers SET points = 0, prediction_points = 0")
        await conn.commit()
    print("All manager points reset to 0.")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    cmd, rest = args[0], args[1:]

    if cmd == "add":
        minutes = 2
        matchday = None
        if "--minutes" in rest:
            i = rest.index("--minutes")
            minutes = int(rest[i + 1])
            rest = rest[:i] + rest[i + 2:]
        if "--matchday" in rest:
            i = rest.index("--matchday")
            matchday = int(rest[i + 1])
            rest = rest[:i] + rest[i + 2:]
        if len(rest) != 3:
            print('Usage: fake_match.py add <id> "<home>" "<away>" [--minutes N] [--matchday N]')
            return
        asyncio.run(add(rest[0], rest[1], rest[2], minutes, matchday))
    elif cmd == "predict":
        if len(rest) != 3:
            print("Usage: fake_match.py predict <id> <user_id> HOME|AWAY|DRAW")
            return
        asyncio.run(predict(*rest))
    elif cmd == "finish":
        if len(rest) not in (2, 4):
            print("Usage: fake_match.py finish <id> HOME|AWAY|DRAW [home_score away_score]")
            return
        if len(rest) == 4:
            asyncio.run(finish(rest[0], rest[1], int(rest[2]), int(rest[3])))
        else:
            asyncio.run(finish(rest[0], rest[1]))
    elif cmd == "remove":
        if len(rest) != 1:
            print("Usage: fake_match.py remove <id>")
            return
        asyncio.run(remove(rest[0]))
    elif cmd == "remove-all":
        asyncio.run(remove_all())
    elif cmd == "list":
        asyncio.run(list_fake())
    elif cmd == "reset-points":
        asyncio.run(reset_points())
    else:
        print(__doc__)


if __name__ == "__main__":
    main()