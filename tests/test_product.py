from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.admin import create_admin
from app.config import Settings
from app.content import SPHERES
from app.db import Store
from app.reminders import send_due_reminders
from app.result import classify


@pytest.fixture
def product(tmp_path: Path):
    config = Settings("123:TEST", "owner", "verylongtestpassword", "127.0.0.1", 8080, "", "", "",
                      tmp_path / "test.sqlite3", tmp_path / "media")
    store = Store(config.database_path)
    return store, config


def test_result_priority_and_personalization(product):
    store, _ = product
    rules, texts = store.rules(), store.texts()
    safety = classify(dict(zip(SPHERES, [0, 2, 2, 9])), rules, texts)
    assert safety.variant == "C" and safety.safe
    assert classify(dict(zip(SPHERES, [1, 6, 7, 7])), rules, texts).variant == "A1"
    tied = classify(dict(zip(SPHERES, [2, 3, 8, 8])), rules, texts)
    assert tied.variant == "A1" and texts["two_low"] in tied.paragraph
    assert "ментальной (8)" in tied.paragraph
    assert classify(dict(zip(SPHERES, [4, 4, 5, 5])), rules, texts).variant == "B2"
    assert classify(dict(zip(SPHERES, [9, 9, 10, 10])), rules, texts).variant == "B3"


def test_progress_and_prior_run_are_preserved(product):
    store, _ = product
    assert store.upsert_user(42, "nick", "Имя", "inerciya")
    first = store.start_run(42)
    store.set_stage(42, "score", 4)
    store.score(first, "spiritual", 6)
    assert not store.upsert_user(42, "nick", "Имя", "nadryv")
    assert store.user(42)["source"] == "inerciya"
    assert store.user(42)["current_part"] == 4
    assert store.scores(first) == {"spiritual": 6}
    second = store.start_run(42)
    assert second != first and store.scores(first) == {"spiritual": 6}
    assert store.scores(second) == {}


def test_admin_edits_and_export(product):
    store, config = product
    client = TestClient(create_admin(store, config))
    redirect = client.get("/admin", follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers["location"] == "/admin/login"
    login_html = client.get("/admin/login")
    login_token = re.search(r'name="csrf" value="([a-f0-9]+)"', login_html.text).group(1)
    assert client.post("/admin/login", data={"csrf": login_token, "username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/admin/login", data={"csrf": login_token, "username": "owner", "password": config.admin_password}).status_code == 200
    assert client.get("/admin").status_code == 200
    auth = ("owner", config.admin_password)
    page = client.get("/admin/texts", auth=auth)
    token = re.search(r'name="csrf" value="([a-f0-9]+)"', page.text).group(1)
    assert client.post("/admin/texts/A1", auth=auth, data={"csrf": token, "value": "Новый текст"}).status_code == 200
    assert store.texts()["A1"] == "Новый текст"
    assert client.post("/admin/texts/A1", auth=auth, data={"csrf": token, "value": "{unknown}"}).status_code == 400
    assert client.post("/admin/rules", auth=auth, data={"csrf": token, **{k: str(v) for k, v in store.rules().items()}}).status_code == 200
    store.upsert_user(42, "nick", "Имя", "inerciya")
    csv_response = client.get("/admin/export/users.csv", auth=auth)
    assert csv_response.status_code == 200 and "inerciya" in csv_response.text
    assert client.get("/admin/backup.sqlite3", auth=auth).content.startswith(b"SQLite format 3")
    image_bytes = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(image_bytes, "PNG")
    upload = client.post("/admin/parts/1/media", auth=auth, data={"csrf": token}, files={
        "audio": ("sample.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00", "audio/mpeg"),
        "image": ("card.png", image_bytes.getvalue(), "image/png"),
    })
    assert upload.status_code == 200
    part = store.part(1)
    assert part["audio_path"].startswith("uploads/") and part["image_path"].startswith("uploads/")


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, user_id, text, **kwargs):
        self.messages.append((user_id, text))


def test_two_reminders_only(product):
    store, _ = product
    store.upsert_user(42, "nick", "Имя", "inerciya")
    store.start_run(42)
    store.set_stage(42, "score", 4)
    past = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(timespec="seconds")
    store.execute("UPDATE users SET last_activity=? WHERE telegram_id=42", (past,))
    bot = FakeBot()
    assert asyncio.run(send_due_reminders(bot, store)) == 1
    assert asyncio.run(send_due_reminders(bot, store)) == 0
    store.execute("UPDATE users SET last_reminder_at=? WHERE telegram_id=42", (past,))
    assert asyncio.run(send_due_reminders(bot, store)) == 1
    assert asyncio.run(send_due_reminders(bot, store)) == 0
    assert len(bot.messages) == 2
    store.event(42, "menu_continue")
    store.execute("UPDATE users SET last_activity=? WHERE telegram_id=42", (past,))
    assert asyncio.run(send_due_reminders(bot, store)) == 0


def test_welcome_reminder(product):
    store, _ = product
    store.upsert_user(99, "nick", "Имя", None)
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    store.execute("UPDATE users SET last_activity=? WHERE telegram_id=99", (past,))
    bot = FakeBot()
    assert asyncio.run(send_due_reminders(bot, store)) == 1
    assert "семь аудио" in bot.messages[0][1]
