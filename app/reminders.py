from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .db import Store

log = logging.getLogger(__name__)


async def send_due_reminders(bot: Bot, store: Store) -> int:
    sent = 0
    current = datetime.now(timezone.utc)
    total = max(7, len(store.parts()))
    users = store.all("SELECT * FROM users WHERE reminder_count<2 AND stage!='finished'")
    for user in users:
        if user["stage"] in ("result_preview", "result", "edit_score") and total <= 7:
            continue
        activity = datetime.fromisoformat(user["last_activity"])
        threshold = timedelta(hours=24) if user["reminder_count"] == 0 else timedelta(days=3)
        if current - activity < threshold:
            continue
        if user["reminder_count"] == 1 and user["last_reminder_at"]:
            if current - datetime.fromisoformat(user["last_reminder_at"]) < timedelta(days=2):
                continue
        part = min(max(user["current_part"], 1), total)
        left = max(0, 7 - part) if user["stage"] not in ("result_preview", "result", "edit_score") else 0
        texts = store.texts()
        if user["stage"] == "welcome":
            text = texts["reminder_welcome"]
        elif user["stage"] in ("result_preview", "result", "edit_score"):
            text = texts["reminder_after_result"]
        else:
            text = texts["reminder"].format(part=part, total=total, left=left)
        try:
            await bot.send_message(user["telegram_id"], text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts["button_continue"], callback_data="resume")]
            ]))
        except Exception:
            log.exception("Reminder delivery failed for %s", user["telegram_id"])
            store.execute("UPDATE users SET reminder_count=2 WHERE telegram_id=?", (user["telegram_id"],))
            continue
        store.execute("UPDATE users SET reminder_count=reminder_count+1,last_reminder_at=? WHERE telegram_id=?",
                      (current.isoformat(timespec="seconds"), user["telegram_id"]))
        store.event(user["telegram_id"], "reminder_sent", part, str(user["reminder_count"] + 1), activity=False)
        sent += 1
    return sent


async def reminder_loop(bot: Bot, store: Store) -> None:
    while True:
        await send_due_reminders(bot, store)
        await asyncio.sleep(300)
