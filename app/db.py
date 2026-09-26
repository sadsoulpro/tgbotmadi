from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .content import DEFAULT_RULES, PARTS, TEXTS


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT NOT NULL,
            source TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            current_part INTEGER NOT NULL DEFAULT 0, current_run INTEGER,
            stage TEXT NOT NULL DEFAULT 'welcome', phone TEXT, phone_asked INTEGER NOT NULL DEFAULT 0,
            reminder_count INTEGER NOT NULL DEFAULT 0, last_activity TEXT NOT NULL,
            last_reminder_at TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL REFERENCES users(telegram_id),
            started_at TEXT NOT NULL, completed_at TEXT, average REAL, variant TEXT,
            result_seen_at TEXT, booked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS scores (
            run_id INTEGER NOT NULL REFERENCES runs(id), sphere TEXT NOT NULL,
            value INTEGER NOT NULL CHECK(value BETWEEN 0 AND 10), rated_at TEXT NOT NULL,
            PRIMARY KEY(run_id, sphere)
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
            run_id INTEGER, part INTEGER, kind TEXT NOT NULL, detail TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS events_kind_user ON events(kind, telegram_id);
        CREATE TABLE IF NOT EXISTS parts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, position INTEGER NOT NULL UNIQUE,
            title TEXT NOT NULL, intro TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'audio',
            audio_path TEXT, image_path TEXT, image_position TEXT NOT NULL DEFAULT 'after',
            sphere TEXT, prompt TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS answers (
            run_id INTEGER NOT NULL REFERENCES runs(id), part INTEGER NOT NULL,
            answer TEXT NOT NULL, answered_at TEXT NOT NULL, PRIMARY KEY(run_id, part)
        );
        CREATE TABLE IF NOT EXISTS texts (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rules (key TEXT PRIMARY KEY, value REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS options (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if "last_reminder_at" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(users)")}:
            self.conn.execute("ALTER TABLE users ADD COLUMN last_reminder_at TEXT")
        if not self.conn.execute("SELECT 1 FROM parts LIMIT 1").fetchone():
            for pos, (title, audio, image, sphere) in enumerate(PARTS, 1):
                self.conn.execute("INSERT INTO parts(position,title,intro,kind,audio_path,image_path,sphere) VALUES(?,?,?,?,?,?,?)",
                                  (pos, title, f"Часть {pos}. {title}.", "audio", str(Path(audio)), image, sphere))
        for key, value in TEXTS.items():
            self.conn.execute("INSERT OR IGNORE INTO texts VALUES(?,?)", (key, value))
        for key, value in DEFAULT_RULES.items():
            self.conn.execute("INSERT OR IGNORE INTO rules VALUES(?,?)", (key, value))
        for key, value in {"booking_url": "", "contact_url": "", "emoji_spiritual": "", "emoji_emotional": "", "emoji_mental": "", "emoji_physical": ""}.items():
            self.conn.execute("INSERT OR IGNORE INTO options VALUES(?,?)", (key, value))
        self.conn.commit()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.conn.execute(sql, params)
        self.conn.commit()

    def event(self, user_id: int, kind: str, part: int | None = None, detail: str | None = None, *, activity: bool = True) -> None:
        user = self.user(user_id)
        stamp = now()
        self.conn.execute("INSERT INTO events(telegram_id,run_id,part,kind,detail,created_at) VALUES(?,?,?,?,?,?)",
                          (user_id, user["current_run"] if user else None, part, kind, detail, stamp))
        if activity:
            self.conn.execute("UPDATE users SET last_seen=?,last_activity=? WHERE telegram_id=?", (stamp, stamp, user_id))
        self.conn.commit()

    def user(self, user_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM users WHERE telegram_id=?", (user_id,))

    def upsert_user(self, user_id: int, username: str | None, first_name: str, source: str | None) -> bool:
        fresh = self.user(user_id) is None
        stamp = now()
        if fresh:
            self.conn.execute("INSERT INTO users(telegram_id,username,first_name,source,first_seen,last_seen,last_activity) VALUES(?,?,?,?,?,?,?)",
                              (user_id, username, first_name, source, stamp, stamp, stamp))
        else:
            self.conn.execute("UPDATE users SET username=?,first_name=?,last_seen=?,last_activity=? WHERE telegram_id=?",
                              (username, first_name, stamp, stamp, user_id))
        self.conn.commit()
        self.event(user_id, "start", detail=source)
        return fresh

    def start_run(self, user_id: int) -> int:
        stamp = now()
        cursor = self.conn.execute("INSERT INTO runs(telegram_id,started_at) VALUES(?,?)", (user_id, stamp))
        run_id = cursor.lastrowid
        self.conn.execute("UPDATE users SET current_run=?,current_part=1,stage='part',phone_asked=0 WHERE telegram_id=?", (run_id, user_id))
        self.conn.commit()
        self.event(user_id, "run_started")
        return run_id

    def set_stage(self, user_id: int, stage: str, part: int | None = None) -> None:
        if part is None:
            self.execute("UPDATE users SET stage=? WHERE telegram_id=?", (stage, user_id))
        else:
            self.execute("UPDATE users SET stage=?,current_part=? WHERE telegram_id=?", (stage, part, user_id))

    def score(self, run_id: int, sphere: str, value: int) -> None:
        self.execute("INSERT INTO scores(run_id,sphere,value,rated_at) VALUES(?,?,?,?) ON CONFLICT(run_id,sphere) DO UPDATE SET value=excluded.value,rated_at=excluded.rated_at",
                     (run_id, sphere, value, now()))

    def scores(self, run_id: int) -> dict[str, int]:
        return {r["sphere"]: r["value"] for r in self.all("SELECT sphere,value FROM scores WHERE run_id=?", (run_id,))}

    def parts(self) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM parts WHERE enabled=1 ORDER BY position")

    def part(self, position: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM parts WHERE enabled=1 AND position=?", (position,))

    def texts(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.all("SELECT * FROM texts")}

    def rules(self) -> dict[str, float]:
        return {r["key"]: r["value"] for r in self.all("SELECT * FROM rules")}

    def option(self, key: str) -> str:
        row = self.one("SELECT value FROM options WHERE key=?", (key,))
        return row["value"] if row else ""

    def set_option(self, key: str, value: str) -> None:
        self.execute("INSERT INTO options VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def complete(self, user_id: int, average: float, variant: str) -> None:
        user = self.user(user_id)
        stamp = now()
        self.execute("UPDATE runs SET completed_at=COALESCE(completed_at,?),result_seen_at=COALESCE(result_seen_at,?),average=?,variant=? WHERE id=?",
                     (stamp, stamp, average, variant, user["current_run"]))
        self.set_stage(user_id, "result")
        self.event(user_id, "result", user["current_part"], variant)

    def export_rows(self, table: str) -> list[dict]:
        if table not in {"users", "runs", "scores", "events", "answers"}:
            raise ValueError(table)
        return [dict(row) for row in self.all(f"SELECT * FROM {table}")]

    def backup(self, destination: Path) -> None:
        copy = sqlite3.connect(destination)
        try:
            self.conn.backup(copy)
        finally:
            copy.close()
