"""
SQLite data layer for PremLeagueBot.

Handles:
- The 20 Premier League teams
- Random team assignment for each Discord user
- Match fixtures/results pulled from the football API
- Per-user predictions and scoring

Drop this in as utils/db.py. Call init_db() once at bot startup
(e.g. in setup_hook, before loading cogs).
"""

from __future__ import annotations

import random
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "league.db"

# Points awarded for a user's assigned team's match result
TEAM_POINTS = {"WIN": 3, "DRAW": 1, "LOSS": 0}
# Points awarded for a correct prediction
PREDICTION_POINTS = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,   -- short code, e.g. "ARS"
    name TEXT NOT NULL          -- display name, e.g. "Arsenal"
);

CREATE TABLE IF NOT EXISTS user_teams (
    user_id INTEGER PRIMARY KEY,   -- Discord user id
    team_id TEXT NOT NULL REFERENCES teams(team_id)
);

CREATE TABLE IF NOT EXISTS matches (
    match_id INTEGER PRIMARY KEY,       -- id from the football API
    home_team_id TEXT NOT NULL REFERENCES teams(team_id),
    away_team_id TEXT NOT NULL REFERENCES teams(team_id),
    kickoff_utc TEXT NOT NULL,          -- ISO 8601 string
    status TEXT NOT NULL DEFAULT 'SCHEDULED',  -- SCHEDULED | FINISHED
    result TEXT,                        -- 'HOME' | 'AWAY' | 'DRAW' once finished
    poll_message_id INTEGER,            -- Discord message id of the prediction poll
    poll_channel_id INTEGER,
    scored INTEGER NOT NULL DEFAULT 0   -- 1 once team points + predictions are applied
);

CREATE TABLE IF NOT EXISTS predictions (
    match_id INTEGER NOT NULL REFERENCES matches(match_id),
    user_id INTEGER NOT NULL,
    choice TEXT NOT NULL,        -- 'HOME' | 'AWAY' | 'DRAW'
    correct INTEGER,             -- NULL until scored, then 0/1
    PRIMARY KEY (match_id, user_id)
);

CREATE TABLE IF NOT EXISTS scores (
    user_id INTEGER PRIMARY KEY,
    team_points INTEGER NOT NULL DEFAULT 0,
    prediction_points INTEGER NOT NULL DEFAULT 0
);
"""

# The 20 current Premier League teams. Update each season as needed,
# or better: populate this from the football API on first run instead
# of hardcoding it.
DEFAULT_TEAMS = [
    ("ARS", "Arsenal"), ("AVL", "Aston Villa"), ("BOU", "Bournemouth"),
    ("BRE", "Brentford"), ("BHA", "Brighton"), ("BUR", "Burnley"),
    ("CHE", "Chelsea"), ("CRY", "Crystal Palace"), ("EVE", "Everton"),
    ("FUL", "Fulham"), ("LEE", "Leeds United"), ("LIV", "Liverpool"),
    ("MCI", "Manchester City"), ("MUN", "Manchester United"),
    ("NEW", "Newcastle United"), ("NFO", "Nottingham Forest"),
    ("SUN", "Sunderland"), ("TOT", "Tottenham Hotspur"),
    ("WHU", "West Ham United"), ("WOL", "Wolverhampton Wanderers"),
]


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO teams (team_id, name) VALUES (?, ?)",
            DEFAULT_TEAMS,
        )


# ---------------------------------------------------------------- teams --

def assign_random_team(user_id: int) -> str:
    """Assign a user a random team if they don't already have one.
    Returns the team_id they end up with (existing or new)."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT team_id FROM user_teams WHERE user_id = ?", (user_id,)
        ).fetchone()
        if existing:
            return existing["team_id"]

        team_ids = [row["team_id"] for row in conn.execute("SELECT team_id FROM teams")]
        team_id = random.choice(team_ids)
        conn.execute(
            "INSERT INTO user_teams (user_id, team_id) VALUES (?, ?)",
            (user_id, team_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO scores (user_id) VALUES (?)", (user_id,)
        )
        return team_id


def get_user_team(user_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            """SELECT ut.team_id, t.name FROM user_teams ut
               JOIN teams t ON t.team_id = ut.team_id
               WHERE ut.user_id = ?""",
            (user_id,),
        ).fetchone()


def users_supporting(team_id: str) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM user_teams WHERE team_id = ?", (team_id,)
        ).fetchall()
        return [r["user_id"] for r in rows]


# -------------------------------------------------------------- matches --

def upsert_match(match_id: int, home_team_id: str, away_team_id: str,
                  kickoff_utc: str, status: str, result: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO matches (match_id, home_team_id, away_team_id, kickoff_utc, status, result)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(match_id) DO UPDATE SET
                   status=excluded.status, result=excluded.result""",
            (match_id, home_team_id, away_team_id, kickoff_utc, status, result),
        )


def get_match(match_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()


def set_poll_message(match_id: int, channel_id: int, message_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE matches SET poll_channel_id = ?, poll_message_id = ? WHERE match_id = ?",
            (channel_id, message_id, match_id),
        )


def matches_needing_poll() -> list[sqlite3.Row]:
    """Scheduled matches that haven't had a prediction poll posted yet."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM matches WHERE status = 'SCHEDULED' AND poll_message_id IS NULL"
        ).fetchall()


def matches_to_score() -> list[sqlite3.Row]:
    """Finished matches whose points haven't been applied yet."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM matches WHERE status = 'FINISHED' AND scored = 0"
        ).fetchall()


# ---------------------------------------------------------- predictions --

def add_prediction(match_id: int, user_id: int, choice: str) -> None:
    """choice must be 'HOME', 'AWAY', or 'DRAW'. Overwrites any earlier vote
    for this match by this user (so they can change their mind pre-kickoff)."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO predictions (match_id, user_id, choice) VALUES (?, ?, ?)
               ON CONFLICT(match_id, user_id) DO UPDATE SET choice = excluded.choice""",
            (match_id, user_id, choice),
        )


def get_user_prediction(match_id: int, user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT choice FROM predictions WHERE match_id = ? AND user_id = ?",
            (match_id, user_id),
        ).fetchone()
        return row["choice"] if row else None


# -------------------------------------------------------------- scoring --

@dataclass
class ScoreResult:
    team_points_awarded: dict[int, int]       # user_id -> points from team result
    prediction_points_awarded: dict[int, int]  # user_id -> points from correct predictions


def score_match(match_id: int) -> ScoreResult:
    """Apply team points to everyone supporting either side, and prediction
    points to everyone who called it correctly. Marks the match as scored.
    Safe to call once per finished match (checked by matches_to_score())."""
    match = get_match(match_id)
    if match is None or match["status"] != "FINISHED" or match["scored"]:
        return ScoreResult({}, {})

    result = match["result"]  # 'HOME' | 'AWAY' | 'DRAW'
    home_outcome = {"HOME": "WIN", "AWAY": "LOSS", "DRAW": "DRAW"}[result]
    away_outcome = {"HOME": "LOSS", "AWAY": "WIN", "DRAW": "DRAW"}[result]

    team_points_awarded: dict[int, int] = {}
    prediction_points_awarded: dict[int, int] = {}

    with get_conn() as conn:
        for team_id, outcome in ((match["home_team_id"], home_outcome),
                                  (match["away_team_id"], away_outcome)):
            pts = TEAM_POINTS[outcome]
            for user_id in users_supporting(team_id):
                conn.execute(
                    """INSERT INTO scores (user_id, team_points) VALUES (?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET team_points = team_points + ?""",
                    (user_id, pts, pts),
                )
                team_points_awarded[user_id] = pts

        preds = conn.execute(
            "SELECT user_id, choice FROM predictions WHERE match_id = ?", (match_id,)
        ).fetchall()
        for pred in preds:
            correct = int(pred["choice"] == result)
            conn.execute(
                "UPDATE predictions SET correct = ? WHERE match_id = ? AND user_id = ?",
                (correct, match_id, pred["user_id"]),
            )
            if correct:
                conn.execute(
                    """INSERT INTO scores (user_id, prediction_points) VALUES (?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET prediction_points = prediction_points + ?""",
                    (pred["user_id"], PREDICTION_POINTS, PREDICTION_POINTS),
                )
                prediction_points_awarded[pred["user_id"]] = PREDICTION_POINTS

        conn.execute("UPDATE matches SET scored = 1 WHERE match_id = ?", (match_id,))

    return ScoreResult(team_points_awarded, prediction_points_awarded)


def get_leaderboard(limit: int = 20) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT s.user_id, s.team_points, s.prediction_points,
                      (s.team_points + s.prediction_points) AS total
               FROM scores s
               ORDER BY total DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
