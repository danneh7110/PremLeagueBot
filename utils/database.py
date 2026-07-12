import json
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

TEAM_POINTS = {"WIN": 3, "DRAW": 1, "LOSS": 0}
PREDICTION_POINTS = 1


@dataclass(frozen=True)
class TableRow:
    team: str
    manager: str
    points: int
    prediction_points: int = 0


@dataclass(frozen=True)
class ScoreOutcome:
    team_points_awarded: dict[int, int]        # user_id -> points from managing a team
    prediction_points_awarded: dict[int, int]  # user_id -> points from correct predictions


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def initialise(self) -> None:
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS managers (
                    user_id INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    team TEXT NOT NULL UNIQUE,
                    points INTEGER NOT NULL DEFAULT 0,
                    prediction_points INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    match_id INTEGER PRIMARY KEY,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    kickoff_utc TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'SCHEDULED',
                    result TEXT,
                    home_score INTEGER,
                    away_score INTEGER,
                    poll_channel_id INTEGER,
                    poll_message_id INTEGER,
                    scored INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS predictions (
                    match_id INTEGER NOT NULL REFERENCES matches(match_id),
                    user_id INTEGER NOT NULL,
                    choice TEXT NOT NULL,
                    correct INTEGER,
                    PRIMARY KEY (match_id, user_id)
                )
                """
            )
            await connection.commit()

    # ---------------------------------------------------------- managers --

    async def get_user_team(self, user_id: int) -> str | None:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                "SELECT team FROM managers WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def get_manager_of_team(self, team: str) -> int | None:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                "SELECT user_id FROM managers WHERE team = ?", (team,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def available_teams_from_json(self, teams_json: str) -> list[str]:
        teams = json.loads(teams_json)
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute("SELECT team FROM managers")
            claimed = {row[0] for row in await cursor.fetchall()}
        return [team for team in teams if team not in claimed]

    async def assign_team(self, user_id: int, display_name: str, team: str) -> None:
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute(
                "INSERT INTO managers (user_id, display_name, team) VALUES (?, ?, ?)",
                (user_id, display_name, team),
            )
            await connection.commit()

    async def league_table(self) -> list[TableRow]:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                "SELECT team, display_name, points, prediction_points FROM managers "
                "ORDER BY (points + prediction_points) DESC, team COLLATE NOCASE"
            )
            rows = await cursor.fetchall()
        return [
            TableRow(team=row[0], manager=row[1], points=row[2], prediction_points=row[3])
            for row in rows
        ]

    # ----------------------------------------------------------- matches --

    async def upsert_match(
        self, match_id: int, home_team: str, away_team: str,
        kickoff_utc: str, status: str, result: str | None,
        home_score: int | None = None, away_score: int | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute(
                """INSERT INTO matches
                       (match_id, home_team, away_team, kickoff_utc, status, result, home_score, away_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(match_id) DO UPDATE SET
                       status = excluded.status, result = excluded.result,
                       home_score = excluded.home_score, away_score = excluded.away_score""",
                (match_id, home_team, away_team, kickoff_utc, status, result, home_score, away_score),
            )
            await connection.commit()

    async def get_match(self, match_id: int) -> aiosqlite.Row | None:
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM matches WHERE match_id = ?", (match_id,)
            )
            return await cursor.fetchone()

    async def set_poll_message(self, match_id: int, channel_id: int, message_id: int) -> None:
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute(
                "UPDATE matches SET poll_channel_id = ?, poll_message_id = ? WHERE match_id = ?",
                (channel_id, message_id, match_id),
            )
            await connection.commit()

    async def matches_needing_poll(self) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM matches WHERE status = 'SCHEDULED' AND poll_message_id IS NULL"
            )
            return await cursor.fetchall()

    async def matches_to_score(self) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT * FROM matches WHERE status = 'FINISHED' AND scored = 0"
            )
            return await cursor.fetchall()

    # -------------------------------------------------------- predictions --

    async def add_prediction(self, match_id: int, user_id: int, choice: str) -> None:
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute(
                """INSERT INTO predictions (match_id, user_id, choice) VALUES (?, ?, ?)
                   ON CONFLICT(match_id, user_id) DO UPDATE SET choice = excluded.choice""",
                (match_id, user_id, choice),
            )
            await connection.commit()

    async def get_team_form(self, team: str, limit: int = 5) -> list[dict]:
        """Last `limit` finished matches for a team, oldest first (standard
        football 'form guide' order). Each entry: {'outcome': 'W'/'D'/'L',
        'opponent': str, 'home_score': int|None, 'away_score': int|None,
        'was_home': bool}."""
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """SELECT * FROM matches
                   WHERE status = 'FINISHED' AND (home_team = ? OR away_team = ?)
                   ORDER BY kickoff_utc DESC LIMIT ?""",
                (team, team, limit),
            )
            rows = await cursor.fetchall()

        form = []
        for row in reversed(rows):  # oldest first
            was_home = row["home_team"] == team
            if row["result"] == "DRAW":
                outcome = "D"
            elif (row["result"] == "HOME") == was_home:
                outcome = "W"
            else:
                outcome = "L"
            form.append({
                "outcome": outcome,
                "opponent": row["away_team"] if was_home else row["home_team"],
                "home_score": row["home_score"],
                "away_score": row["away_score"],
                "was_home": was_home,
            })
        return form

    async def get_user_streak(self, user_id: int) -> tuple[int, int]:
        """Returns (current_streak, longest_streak) of consecutive correct
        predictions, ordered by match kickoff time. Only counts predictions
        that have been scored (i.e. the match has finished)."""
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                """SELECT p.correct FROM predictions p
                   JOIN matches m ON m.match_id = p.match_id
                   WHERE p.user_id = ? AND p.correct IS NOT NULL
                   ORDER BY m.kickoff_utc ASC""",
                (user_id,),
            )
            rows = await cursor.fetchall()

        results = [row[0] for row in rows]
        longest = current_run = 0
        for correct in results:
            current_run = current_run + 1 if correct else 0
            longest = max(longest, current_run)

        # current_streak = run at the very end of the sequence (0 if last pick was wrong)
        current_streak = 0
        for correct in reversed(results):
            if not correct:
                break
            current_streak += 1

        return current_streak, longest

    async def get_streak_leaderboard(self, limit: int = 10) -> list[tuple[int, str, int, int]]:
        """Returns [(user_id, display_name, current_streak, longest_streak), ...]
        for every manager, sorted by current streak descending. Cheap enough
        to just loop since friend-group scale (dozens of users, not thousands)."""
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute("SELECT user_id, display_name FROM managers")
            managers = await cursor.fetchall()

        results = []
        for user_id, display_name in managers:
            current, longest = await self.get_user_streak(user_id)
            results.append((user_id, display_name, current, longest))
        results.sort(key=lambda r: (r[2], r[3]), reverse=True)
        return results[:limit]

    # -------------------------------------------------------------- scoring --

    async def score_match(self, match_id: int) -> ScoreOutcome:
        """Award team points to the two managers involved and prediction
        points to everyone who called it correctly. No-op if already scored
        or not finished. Call matches_to_score() first to find candidates."""
        match = await self.get_match(match_id)
        if match is None or match["status"] != "FINISHED" or match["scored"]:
            return ScoreOutcome({}, {})

        result = match["result"]  # 'HOME' | 'AWAY' | 'DRAW'
        home_outcome = {"HOME": "WIN", "AWAY": "LOSS", "DRAW": "DRAW"}[result]
        away_outcome = {"HOME": "LOSS", "AWAY": "WIN", "DRAW": "DRAW"}[result]

        team_points_awarded: dict[int, int] = {}
        prediction_points_awarded: dict[int, int] = {}

        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row

            for team, outcome in ((match["home_team"], home_outcome),
                                   (match["away_team"], away_outcome)):
                cursor = await connection.execute(
                    "SELECT user_id FROM managers WHERE team = ?", (team,)
                )
                row = await cursor.fetchone()
                if row is None:
                    continue  # nobody has claimed this team, nothing to award
                pts = TEAM_POINTS[outcome]
                await connection.execute(
                    "UPDATE managers SET points = points + ? WHERE user_id = ?",
                    (pts, row["user_id"]),
                )
                team_points_awarded[row["user_id"]] = pts

            cursor = await connection.execute(
                "SELECT user_id, choice FROM predictions WHERE match_id = ?", (match_id,)
            )
            predictions = await cursor.fetchall()
            for pred in predictions:
                correct = int(pred["choice"] == result)
                await connection.execute(
                    "UPDATE predictions SET correct = ? WHERE match_id = ? AND user_id = ?",
                    (correct, match_id, pred["user_id"]),
                )
                if correct:
                    await connection.execute(
                        "UPDATE managers SET prediction_points = prediction_points + ? "
                        "WHERE user_id = ?",
                        (PREDICTION_POINTS, pred["user_id"]),
                    )
                    prediction_points_awarded[pred["user_id"]] = PREDICTION_POINTS

            await connection.execute(
                "UPDATE matches SET scored = 1 WHERE match_id = ?", (match_id,)
            )
            await connection.commit()

        return ScoreOutcome(team_points_awarded, prediction_points_awarded)