from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import Store

ALMATY = ZoneInfo("Asia/Almaty")
METRICS = (
    ("starts", "Запуски"), ("part1", "Часть 1"), ("part2", "Часть 2"),
    ("part4", "Часть 4"), ("part7", "Часть 7"), ("result", "Результат"),
    ("phone", "Телефон"), ("booking", "Ссылка на запись"), ("booked", "Записался"),
    ("video_click", "Нажал видео"), ("video", "Видео отправлено"),
    ("booking_after_video", "Запрос ссылки после видео"),
)


def period_bounds(period: str, start_raw: str = "", end_raw: str = "", *, today: date | None = None):
    today = today or datetime.now(ALMATY).date()
    if period == "all":
        return None, None
    if period == "week":
        first, last = today - timedelta(days=today.weekday()), today
    elif period == "month":
        first, last = today.replace(day=1), today
    elif period == "custom":
        first, last = date.fromisoformat(start_raw), date.fromisoformat(end_raw)
        if last < first:
            raise ValueError("Конечная дата раньше начальной")
    else:
        raise ValueError("Неизвестный период")
    start = datetime.combine(first, time.min, ALMATY).astimezone(timezone.utc)
    end = datetime.combine(last + timedelta(days=1), time.min, ALMATY).astimezone(timezone.utc)
    return start, end


def in_period(raw: str | None, start: datetime | None, end: datetime | None) -> bool:
    if not raw:
        return False
    stamp = datetime.fromisoformat(raw)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (start is None or stamp >= start) and (end is None or stamp < end)


def metrics(store: Store, start: datetime | None = None, end: datetime | None = None):
    users = store.all("SELECT * FROM users WHERE is_test=0")
    events = defaultdict(list)
    for row in store.all("SELECT telegram_id,kind,part,created_at FROM events"):
        events[row["telegram_id"]].append(row)
    runs = defaultdict(list)
    for row in store.all("SELECT telegram_id,completed_at,booked_at FROM runs"):
        runs[row["telegram_id"]].append(row)
    totals = {key: 0 for key, _ in METRICS}
    sources = defaultdict(lambda: {key: 0 for key, _ in METRICS})
    months = defaultdict(lambda: {key: 0 for key, _ in METRICS})
    for user in users:
        user_events = events[user["telegram_id"]]
        user_runs = runs[user["telegram_id"]]
        kinds = {row["kind"] for row in user_events}
        steps = {row["part"] for row in user_events if row["kind"] == "step_sent"}
        flags = {
            "starts": True,
            "part1": 1 in steps, "part2": 2 in steps,
            "part4": 4 in steps, "part7": 7 in steps,
            "result": any(row["completed_at"] for row in user_runs),
            "phone": bool(user["phone"]), "booking": "booking_requested" in kinds,
            "booked": any(row["booked_at"] for row in user_runs),
            "video_click": "review_video_clicked" in kinds,
            "video": "review_video_opened" in kinds,
            "booking_after_video": "booking_after_video" in kinds,
        }
        first_seen = datetime.fromisoformat(user["first_seen"])
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        month = first_seen.astimezone(ALMATY).strftime("%Y-%m")
        for key, active in flags.items():
            if active:
                months[month][key] += 1
                if in_period(user["first_seen"], start, end):
                    totals[key] += 1
                    sources[user["source"] or "(без метки)"][key] += 1
    actions = sum(1 for user in users for row in events[user["telegram_id"]]
                  if row["kind"] == "user_action" and in_period(row["created_at"], start, end))
    return totals, sources, months, actions
