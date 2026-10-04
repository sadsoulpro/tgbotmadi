from __future__ import annotations

import asyncio
import io
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.admin import create_admin
from app.bot import media_path
from app.config import Settings, valid_admin_password
from app.content import CHECKLIST_LEADS, SPHERES
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
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 302 and root.headers["location"] == "/admin"
    assert "Вход в админку" in client.get("/").text
    redirect = client.get("/admin", follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers["location"] == "/admin/login"
    login_html = client.get("/admin/login")
    login_token = re.search(r'name="csrf" value="([a-f0-9]+)"', login_html.text).group(1)
    assert client.post("/admin/login", data={"csrf": login_token, "username": "owner", "password": "wrong"}).status_code == 401
    assert client.post("/admin/login", data={"csrf": login_token, "username": "owner", "password": config.admin_password}).status_code == 200
    assert "Обзор" in client.get("/").text
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


def test_admin_password_length_and_login_throttle(product):
    assert not valid_admin_password("shortpass11")
    assert valid_admin_password("word-word-12")
    assert not valid_admin_password("replace_with_a_long_random_password")

    store, config = product
    client = TestClient(create_admin(store, config))
    login_token = re.search(r'name="csrf" value="([a-f0-9]+)"', client.get("/admin/login").text).group(1)
    credentials = {"csrf": login_token, "username": "owner", "password": "wrong"}
    for _ in range(5):
        assert client.post("/admin/login", data=credentials).status_code == 401
    blocked = client.post("/admin/login", data={**credentials, "password": config.admin_password})
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "600"


def test_admin_basic_auth_is_throttled(product):
    store, config = product
    client = TestClient(create_admin(store, config))
    for _ in range(5):
        assert client.get("/admin", auth=("owner", "wrong")).status_code == 401
    assert client.get("/admin", auth=("owner", config.admin_password)).status_code == 429


def test_admin_accepts_cyrillic_passphrase(product):
    store, config = product
    config = Settings(config.token, config.admin_user, "длинная-фраза", config.admin_host,
                      config.admin_port, config.booking_url, config.contact_url,
                      config.booking_webhook_secret, config.database_path, config.media_dir)
    assert valid_admin_password(config.admin_password)
    client = TestClient(create_admin(store, config))
    login_token = re.search(r'name="csrf" value="([a-f0-9]+)"', client.get("/admin/login").text).group(1)
    response = client.post("/admin/login", data={"csrf": login_token, "username": "owner",
                                                  "password": config.admin_password}, follow_redirects=False)
    assert response.status_code == 303


def test_lead_magnet_setup_and_tag_metrics(product):
    store, config = product
    client = TestClient(create_admin(store, config))
    auth = ("owner", config.admin_password)
    page = client.get("/admin/lead-magnets", auth=auth)
    assert page.status_code == 200
    assert "Лид-магниты настроены" in page.text
    assert "Материалов пока нет" not in page.text
    token = re.search(r'name="csrf" value="([a-f0-9]+)"', page.text).group(1)
    data = {"csrf": token, "tag": "guide_otec", "greeting": "{name}, держи гайд.",
            "bridge": "Теперь пройдём диагностику.", "file_kind": "document", "enabled": "on"}
    assert client.post("/admin/lead-magnets", auth=auth, data=data).status_code == 200
    assert store.lead_magnet("guide_otec")["greeting"] == "{name}, держи гайд."
    assert client.post("/admin/lead-magnets", auth=auth,
                       data={**data, "tag": "bad tag"}, follow_redirects=False).status_code == 400
    upload = client.post("/admin/lead-magnets/guide_otec/file", auth=auth, data={"csrf": token},
                         files={"file": ("guide.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")})
    assert upload.status_code == 200
    lead = store.lead_magnet("guide_otec")
    assert lead["file_path"].startswith("uploads/")
    assert (config.media_dir / lead["file_path"].removeprefix("uploads/")).is_file()
    store.upsert_user(42, "nick", "Имя", "guide_otec")
    run = store.start_run(42)
    store.complete(42, 5, "B2")
    dashboard = client.get("/admin", auth=auth).text
    assert "Лид-магниты и теги" in dashboard and "guide_otec" in dashboard
    assert "Отправлено" in dashboard and "Перешёл" in dashboard
    assert "guide_otec" in client.get("/admin/export/lead_magnets.csv", auth=auth).text
    assert store.one("SELECT source FROM runs WHERE id=?", (run,))["source"] == "guide_otec"


def test_supplied_checklists_are_seeded_and_remain_editable(product):
    store, config = product
    for tag, title in CHECKLIST_LEADS:
        lead = store.lead_magnet(tag)
        assert lead is not None
        assert title in lead["greeting"]
        assert media_path(lead["file_path"], config).is_file()
    tag = "checklist_gnev"
    store.execute("UPDATE lead_magnets SET greeting=? WHERE tag=?", ("Текст владельца, {name}", tag))
    reopened = Store(config.database_path)
    assert reopened.lead_magnet(tag)["greeting"] == "Текст владельца, {name}"


def test_existing_database_migrates_event_names_and_source(tmp_path: Path):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE users (telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            source TEXT, first_seen TEXT, last_seen TEXT, current_part INTEGER,
            current_run INTEGER, stage TEXT, phone TEXT, phone_asked INTEGER,
            reminder_count INTEGER, last_activity TEXT);
        CREATE TABLE runs (id INTEGER PRIMARY KEY, telegram_id INTEGER, started_at TEXT,
            completed_at TEXT, average REAL, variant TEXT, result_seen_at TEXT, booked_at TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, telegram_id INTEGER, run_id INTEGER,
            part INTEGER, kind TEXT, detail TEXT, created_at TEXT);
        INSERT INTO users VALUES (42,NULL,'Иван','inerciya','2026-01-01','2026-01-01',1,1,'part',NULL,0,0,'2026-01-01');
        INSERT INTO runs VALUES (1,42,'2026-01-01',NULL,NULL,NULL,NULL,NULL);
        INSERT INTO events VALUES (1,42,1,1,'audio_opened',NULL,'2026-01-01');
        INSERT INTO events VALUES (2,42,1,1,'part_next',NULL,'2026-01-01');
        INSERT INTO events VALUES (3,42,1,NULL,'booking_clicked',NULL,'2026-01-01');
    """)
    old.commit()
    old.close()
    store = Store(path)
    assert store.one("SELECT source FROM runs WHERE id=1")["source"] == "inerciya"
    assert [r["kind"] for r in store.all("SELECT kind FROM events ORDER BY id")] == [
        "step_sent", "step_advanced", "booking_requested"]


def test_booking_confirmation_requires_external_verification(tmp_path: Path):
    config = Settings("123:TEST", "owner", "verylongtestpassword", "127.0.0.1", 8080,
                      "", "", "example-webhook-secret", tmp_path / "book.sqlite3", tmp_path / "media")
    store = Store(config.database_path)
    store.upsert_user(42, "nick", "Имя", "inerciya")
    run = store.start_run(42)
    store.event(42, "booking_requested")
    assert store.one("SELECT booked_at FROM runs WHERE id=?", (run,))["booked_at"] is None
    client = TestClient(create_admin(store, config))
    payload = {"telegram_id": 42}
    assert client.post("/booking-webhook", json=payload).status_code == 401
    assert client.post("/booking-webhook", json=payload,
                       headers={"X-Booking-Secret": "example-webhook-secret"}).status_code == 200
    assert store.one("SELECT booked_at FROM runs WHERE id=?", (run,))["booked_at"] is not None
    assert store.one("SELECT detail FROM events WHERE kind='booked'")["detail"] == "booking_webhook"


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
