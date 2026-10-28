from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo("Asia/Almaty")


def local_time(raw: str | None) -> str:
    if not raw:
        return "—"
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ALMATY).strftime("%d.%m.%Y %H:%M")


def status_label(user, run=None) -> str:
    if user["paid_at"]:
        return "Оплатил"
    if run and run["booked_at"]:
        return "Записался"
    if user["phone"]:
        return "Оставил телефон"
    if run and run["completed_at"]:
        return "Получил результат"
    return "В процессе"
