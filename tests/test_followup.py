from __future__ import annotations

import asyncio
import io
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from fastapi.testclient import TestClient
from PIL import Image

from app.admin import create_admin
from app.analytics import metrics, period_bounds
from app.config import Settings
from app.db import Store
from app.reminders import process_outreach_queue, send_due_reminders


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, user_id, text, **kwargs):
        self.messages.append((user_id, text, kwargs))


def setup(tmp_path: Path):
    config = Settings("123:TEST", "owner", "verylongtestpassword", "127.0.0.1", 8080,
                      "", "", "", tmp_path / "db.sqlite3", tmp_path / "media")
    return Store(config.database_path), config


def test_incoming_action_is_the_only_activity_and_admin_can_filter(tmp_path):
    store, config = setup(tmp_path)
    store.upsert_user(42, "Nick", "Иван", "source_a")
    old = "2026-10-01T08:00:00+00:00"
    store.execute("UPDATE users SET first_seen=?,last_activity=? WHERE telegram_id=42", (old, old))
    store.event(42, "step_sent", 1)
    assert store.user(42)["last_activity"] == old
    store.record_action(42, "NewNick")
    assert store.user(42)["last_activity"] != old
    assert store.user(42)["username"] == "NewNick"

    client = TestClient(create_admin(store, config))
    auth = ("owner", config.admin_password)
    page = client.get("/admin/users", auth=auth, params={"sort": "recent"}).text
    assert "Первый запуск" in page and "Последнее действие" in page
    assert "01.10.2026 13:00" in page
    assert "NewNick" in page
    assert "<td>42</td>" not in client.get("/admin/users", auth=auth, params={"inactive_days": "1"}).text
    csv = client.get("/admin/export/users.csv", auth=auth).text
    assert "first_seen_almaty" in csv and "last_activity_almaty" in csv and "status" in csv

    csrf = re.search(r'name="csrf" value="([a-f0-9]+)"', page).group(1)
    response = client.post("/admin/users/42/flags", auth=auth, data={"csrf": csrf, "is_test": "on"},
                           follow_redirects=False)
    assert response.status_code == 303 and store.user(42)["is_test"] == 1


def test_cohort_period_and_test_account_exclusion(tmp_path):
    store, config = setup(tmp_path)
    store.upsert_user(1, "one", "Первый", "a")
    store.upsert_user(2, "two", "Тест", "a")
    store.upsert_user(3, "three", "Второй", "b")
    store.execute("UPDATE users SET first_seen=? WHERE telegram_id=1", ("2026-10-03T20:30:00+00:00",))
    store.execute("UPDATE users SET first_seen=?,is_test=1 WHERE telegram_id=2", ("2026-10-03T21:00:00+00:00",))
    store.execute("UPDATE users SET first_seen=? WHERE telegram_id=3", ("2026-09-25T12:00:00+00:00",))
    store.event(1, "step_sent", 1)
    store.event(1, "step_sent", 2)
    store.event(2, "step_sent", 1)
    start, end = period_bounds("custom", "2026-10-04", "2026-10-04", today=date(2026, 10, 28))
    totals, sources, months, _ = metrics(store, start, end)
    assert totals["starts"] == 1 and totals["part2"] == 1
    assert sources["a"]["starts"] == 1
    assert months["2026-10"]["starts"] == 1
    assert months["2026-09"]["starts"] == 1
    client = TestClient(create_admin(store, config))
    dashboard = client.get("/admin", auth=("owner", config.admin_password),
                           params={"period": "custom", "from": "2026-10-04", "to": "2026-10-04"})
    assert dashboard.status_code == 200
    assert "Свои даты" in dashboard.text and "Действий в периоде" in dashboard.text
    assert "По месяцам" in dashboard.text and "По источникам" in dashboard.text


def test_reminder_schedule_quiet_hours_exclusions_and_limit(tmp_path):
    store, _ = setup(tmp_path)
    store.set_option("auto_reminders_enabled", "1")
    for user_id in range(1, 7):
        store.upsert_user(user_id, None, f"Имя {user_id}", "source")
        store.start_run(user_id)
    store.execute("UPDATE users SET is_test=1 WHERE telegram_id=2")
    store.execute("UPDATE users SET reminder_opt_out=1 WHERE telegram_id=3")
    store.execute("UPDATE users SET bot_blocked=1 WHERE telegram_id=4")
    store.execute("UPDATE users SET paid_at=? WHERE telegram_id=5", ("2026-10-01T00:00:00+00:00",))
    store.complete(6, 5, "B2")
    current = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)  # 15:00 Almaty
    old = (current - timedelta(hours=25)).isoformat()
    store.execute("UPDATE users SET last_activity=?", (old,))
    bot = FakeBot()
    assert asyncio.run(send_due_reminders(bot, store, current=current.replace(hour=2))) == 0  # 07:00
    assert asyncio.run(send_due_reminders(bot, store, current=current)) == 1
    assert bot.messages[0][0] == 1 and store.user(1)["reminder_count"] == 1
    assert asyncio.run(send_due_reminders(bot, store, current=current + timedelta(hours=24))) == 0
    store.execute("UPDATE users SET last_reminder_at=? WHERE telegram_id=1",
                  ((current - timedelta(hours=72)).isoformat(),))
    assert asyncio.run(send_due_reminders(bot, store, current=current)) == 1
    assert asyncio.run(send_due_reminders(bot, store, current=current)) == 0
    assert store.user(1)["reminder_count"] == 2
    assert len(bot.messages) == 2


def test_broadcast_requires_preview_and_sends_once(tmp_path):
    store, config = setup(tmp_path)
    store.upsert_user(42, "Nick", "Иван", "source_a")
    store.start_run(42)
    store.set_stage(42, "score", 3)
    client = TestClient(create_admin(store, config))
    auth = ("owner", config.admin_password)
    page = client.get("/admin/broadcast", auth=auth)
    csrf = re.search(r'name="csrf" value="([a-f0-9]+)"', page.text).group(1)
    assert store.option("auto_reminders_enabled") == "0"
    assert client.post("/admin/broadcast/automation", auth=auth,
                       data={"csrf": csrf, "action": "enable"}).status_code == 400
    assert client.post("/admin/broadcast/automation", auth=auth,
                       data={"csrf": csrf, "action": "enable", "confirm_auto": "on"},
                       follow_redirects=False).status_code == 303
    assert store.option("auto_reminders_enabled") == "1"
    preview = client.post("/admin/broadcast/preview", auth=auth,
                          data={"csrf": csrf, "group": "middle", "source": "source_a"})
    assert preview.status_code == 200 and "Получателей: <b>1</b>" in preview.text
    campaign_id = re.search(r'name="campaign_id" value="(\d+)"', preview.text).group(1)
    payload = {"csrf": csrf, "campaign_id": campaign_id, "expected_count": "1"}
    assert client.post("/admin/broadcast/send", auth=auth, data=payload, follow_redirects=False).status_code == 303
    assert client.post("/admin/broadcast/send", auth=auth, data=payload, follow_redirects=False).status_code == 409
    assert store.one("SELECT COUNT(*) AS n FROM outreach")["n"] == 1
    bot = FakeBot()
    current = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
    assert asyncio.run(process_outreach_queue(bot, store, current=current)) == 1
    assert store.user(42)["reminder_count"] == 1
    store.record_action(42, "Nick")
    assert store.one("SELECT response_at FROM outreach")["response_at"] is not None


def test_review_media_upload_and_consent(tmp_path):
    store, config = setup(tmp_path)
    client = TestClient(create_admin(store, config))
    auth = ("owner", config.admin_password)
    page = client.get("/admin/reviews", auth=auth)
    csrf = re.search(r'name="csrf" value="([a-f0-9]+)"', page.text).group(1)
    image = io.BytesIO()
    Image.new("RGB", (10, 10), "blue").save(image, "PNG")
    upload = client.post("/admin/reviews", auth=auth,
                         data={"csrf": csrf, "consent_confirmed": "on"},
                         files={"video": ("review.mp4", b"\0\0\0\x10ftypisom", "video/mp4"),
                                "image1": ("review.png", image.getvalue(), "image/png")},
                         follow_redirects=False)
    assert upload.status_code == 303
    media = store.one("SELECT * FROM review_media WHERE id=1")
    assert media["consent_confirmed"] == 1
    assert (config.media_dir / media["video_path"].removeprefix("uploads/")).is_file()
    assert (config.media_dir / media["image1_path"].removeprefix("uploads/")).is_file()


def test_blocked_bot_is_marked_and_not_retried(tmp_path):
    store, _ = setup(tmp_path)
    store.set_option("auto_reminders_enabled", "1")
    store.upsert_user(42, None, "Иван", None)
    current = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
    store.execute("UPDATE users SET last_activity=? WHERE telegram_id=42",
                  ((current - timedelta(days=2)).isoformat(),))

    class BlockedBot:
        calls = 0

        async def send_message(self, user_id, text, **kwargs):
            self.calls += 1
            raise TelegramForbiddenError(SendMessage(chat_id=user_id, text=text), "bot was blocked")

    bot = BlockedBot()
    assert asyncio.run(send_due_reminders(bot, store, current=current, pace_seconds=0)) == 0
    assert store.user(42)["bot_blocked"] == 1
    assert store.one("SELECT status FROM outreach")["status"] == "blocked"
    assert asyncio.run(send_due_reminders(bot, store, current=current, pace_seconds=0)) == 0
    assert bot.calls == 1


def test_next_series_notification_only_to_subscribers(tmp_path):
    store, config = setup(tmp_path)
    store.upsert_user(41, None, "Подписан", None)
    store.upsert_user(42, None, "Не подписан", None)
    store.execute("UPDATE users SET wants_next_series=1 WHERE telegram_id=41")
    client = TestClient(create_admin(store, config))
    auth = ("owner", config.admin_password)
    csrf = re.search(r'name="csrf" value="([a-f0-9]+)"', client.get("/admin/broadcast", auth=auth).text).group(1)
    data = {"csrf": csrf, "group": "next_series"}
    assert client.post("/admin/broadcast/preview", auth=auth, data=data).status_code == 400
    store.execute("INSERT INTO parts(position,title,intro,kind,enabled) VALUES(8,'Продолжение','Продолжение','button',1)")
    preview = client.post("/admin/broadcast/preview", auth=auth, data=data)
    assert "Получателей: <b>1</b>" in preview.text
    campaign_id = re.search(r'name="campaign_id" value="(\d+)"', preview.text).group(1)
    assert client.post("/admin/broadcast/send", auth=auth,
                       data={"csrf": csrf, "campaign_id": campaign_id, "expected_count": "1"},
                       follow_redirects=False).status_code == 303
    bot = FakeBot()
    current = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
    assert asyncio.run(process_outreach_queue(bot, store, current=current)) == 1
    assert [message[0] for message in bot.messages] == [41]
    assert store.user(41)["next_series_notified_at"]
    assert asyncio.run(process_outreach_queue(bot, store, current=current)) == 0

