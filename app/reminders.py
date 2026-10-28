from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .db import Store, now

log = logging.getLogger(__name__)
ALMATY = ZoneInfo("Asia/Almaty")


def reminder_group(user) -> str:
    part = user["current_part"]
    return "welcome" if part <= 1 else "middle" if part <= 4 else "late"


def eligible_users(store: Store, *, group: str = "all", source: str | None = None, next_series: bool = False):
    if next_series:
        return store.all("""SELECT u.* FROM users u WHERE u.wants_next_series=1 AND u.next_series_notified_at IS NULL
            AND u.is_test=0 AND u.bot_blocked=0""")
    sql = """SELECT u.* FROM users u WHERE u.is_test=0 AND u.reminder_opt_out=0
        AND u.bot_blocked=0 AND u.paid_at IS NULL AND u.reminder_count<2
        AND NOT EXISTS(SELECT 1 FROM outreach o WHERE o.telegram_id=u.telegram_id
            AND o.status IN ('queued','sending') AND o.kind='manual')
        AND NOT EXISTS(SELECT 1 FROM runs r WHERE r.telegram_id=u.telegram_id
            AND (r.completed_at IS NOT NULL OR r.booked_at IS NOT NULL))"""
    params = []
    if source is not None:
        sql += " AND u.source IS ?"
        params.append(source)
    users = store.all(sql, tuple(params))
    return [user for user in users if group == "all" or reminder_group(user) == group]


def reminder_text(store: Store, user, number: int) -> str:
    texts = store.texts()
    return texts["reminder_second"] if number == 2 else texts["reminder_first_" + reminder_group(user)]


def reminder_keyboard(store: Store) -> InlineKeyboardMarkup:
    texts = store.texts()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts["button_continue"], callback_data="resume")],
        [InlineKeyboardButton(text=texts["button_why_stopped"], callback_data="reminder:survey")],
        [InlineKeyboardButton(text=texts["button_no_more_reminders"], callback_data="reminder:optout")],
    ])


async def deliver(bot: Bot, store: Store, outreach_id: int, user_id: int, text: str,
                  *, next_series: bool = False) -> bool:
    store.execute("UPDATE outreach SET status='sending' WHERE id=? AND status='queued'", (outreach_id,))
    keyboard = (InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=store.texts()["button_continue"], callback_data="resume")]
    ]) if next_series else reminder_keyboard(store))
    try:
        await bot.send_message(user_id, text, reply_markup=keyboard)
    except TelegramForbiddenError:
        store.execute("UPDATE users SET bot_blocked=1 WHERE telegram_id=?", (user_id,))
        store.execute("UPDATE outreach SET status='blocked',error='bot_blocked' WHERE id=?", (outreach_id,))
        return False
    except Exception as exc:
        log.warning("Telegram rejected outreach %s: %s", outreach_id, type(exc).__name__)
        store.execute("UPDATE outreach SET status='failed',error=? WHERE id=?", (type(exc).__name__, outreach_id))
        return False
    store.execute("UPDATE outreach SET status='sent',sent_at=? WHERE id=?", (now(), outreach_id))
    return True


async def send_due_reminders(bot: Bot, store: Store, *, current: datetime | None = None,
                             limit: int = 20, pace_seconds: float = 1.0) -> int:
    if store.option("auto_reminders_enabled") != "1":
        return 0
    current = current or datetime.now(timezone.utc)
    local_hour = current.astimezone(ALMATY).hour
    if local_hour >= 22 or local_hour < 9:
        return 0
    sent = 0
    for user in eligible_users(store):
        if sent >= limit:
            break
        activity = datetime.fromisoformat(user["last_activity"])
        if activity.tzinfo is None:
            activity = activity.replace(tzinfo=timezone.utc)
        if current - activity < timedelta(hours=24):
            continue
        count = user["reminder_count"]
        if count == 1:
            if not user["last_reminder_at"]:
                continue
            previous = datetime.fromisoformat(user["last_reminder_at"])
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            if current - previous < timedelta(hours=72):
                continue
        recent_failure = store.one("""SELECT queued_at FROM outreach WHERE telegram_id=? AND kind='automatic'
            AND status='failed' ORDER BY id DESC LIMIT 1""", (user["telegram_id"],))
        if recent_failure and current - datetime.fromisoformat(recent_failure["queued_at"]) < timedelta(hours=1):
            continue
        cursor = store.conn.execute("""INSERT INTO outreach
            (telegram_id,kind,part,source,queued_at) VALUES(?,'automatic',?,?,?)""",
            (user["telegram_id"], user["current_part"], user["source"], now()))
        store.conn.commit()
        if await deliver(bot, store, cursor.lastrowid, user["telegram_id"], reminder_text(store, user, count + 1)):
            store.execute("UPDATE users SET reminder_count=reminder_count+1,last_reminder_at=? WHERE telegram_id=?",
                          (now(), user["telegram_id"]))
            store.event(user["telegram_id"], "reminder_sent", user["current_part"], str(count + 1))
            sent += 1
            if pace_seconds:
                await asyncio.sleep(pace_seconds)
    return sent


async def process_outreach_queue(bot: Bot, store: Store, *, limit: int = 20,
                                 current: datetime | None = None) -> int:
    current = current or datetime.now(timezone.utc)
    if current.astimezone(ALMATY).hour >= 22 or current.astimezone(ALMATY).hour < 9:
        return 0
    sent = 0
    rows = store.all("""SELECT o.*,c.body FROM outreach o JOIN campaigns c ON c.id=o.campaign_id
        WHERE o.status='queued' ORDER BY o.id LIMIT ?""", (limit,))
    for row in rows:
        user = store.user(row["telegram_id"])
        if not user or user["bot_blocked"] or user["is_test"] or (row["kind"] != "next_series" and user["paid_at"]):
            store.execute("UPDATE outreach SET status='skipped' WHERE id=?", (row["id"],))
            continue
        if row["kind"] == "manual" and (user["reminder_opt_out"] or user["reminder_count"] >= 2 or
             (user["last_reminder_at"] and current - datetime.fromisoformat(user["last_reminder_at"]) < timedelta(hours=24)) or store.one(
            "SELECT 1 FROM runs WHERE telegram_id=? AND (completed_at IS NOT NULL OR booked_at IS NOT NULL)",
            (row["telegram_id"],))):
            store.execute("UPDATE outreach SET status='skipped' WHERE id=?", (row["id"],))
            continue
        if row["kind"] == "next_series" and (not user["wants_next_series"] or user["next_series_notified_at"]):
            store.execute("UPDATE outreach SET status='skipped' WHERE id=?", (row["id"],))
            continue
        if await deliver(bot, store, row["id"], row["telegram_id"], row["body"],
                         next_series=row["kind"] == "next_series"):
            if row["kind"] == "next_series":
                store.execute("UPDATE users SET next_series_notified_at=? WHERE telegram_id=?",
                              (now(), row["telegram_id"]))
            else:
                store.execute("UPDATE users SET reminder_count=reminder_count+1,last_reminder_at=? WHERE telegram_id=?",
                              (now(), row["telegram_id"]))
            sent += 1
        await asyncio.sleep(1)  # one message per second
    for campaign in store.all("SELECT id FROM campaigns WHERE status='queued'"):
        pending = store.one("SELECT 1 FROM outreach WHERE campaign_id=? AND status IN ('queued','sending') LIMIT 1",
                            (campaign["id"],))
        if not pending:
            store.execute("UPDATE campaigns SET status='done' WHERE id=?", (campaign["id"],))
    return sent


async def reminder_loop(bot: Bot, store: Store) -> None:
    tick = 0
    while True:
        if tick % 30 == 0:
            await send_due_reminders(bot, store)
        await process_outreach_queue(bot, store)
        tick += 1
        await asyncio.sleep(10)
