import json
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


@dataclass(frozen=True)
class TableRow:
    team: str
    manager: str
    points: int


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
                    points INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await connection.commit()

    async def get_user_team(self, user_id: int) -> str | None:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                "SELECT team FROM managers WHERE user_id = ?", (user_id,)
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
                "SELECT team, display_name, points FROM managers "
                "ORDER BY points DESC, team COLLATE NOCASE"
            )
            rows = await cursor.fetchall()
        return [TableRow(team=row[0], manager=row[1], points=row[2]) for row in rows]

