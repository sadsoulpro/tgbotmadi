from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    token: str
    admin_user: str
    admin_password: str
    admin_host: str
    admin_port: int
    booking_url: str
    contact_url: str
    booking_webhook_secret: str
    database_path: Path
    media_dir: Path


def settings() -> Settings:
    load_dotenv()
    def path(name: str, default: str) -> Path:
        value = Path(os.getenv(name, default))
        return value if value.is_absolute() else ROOT / value
    return Settings(
        token=os.getenv("BOT_TOKEN", ""),
        admin_user=os.getenv("ADMIN_USER", "owner"),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
        admin_host=os.getenv("ADMIN_HOST", "127.0.0.1"),
        admin_port=int(os.getenv("ADMIN_PORT", "8080")),
        booking_url=os.getenv("BOOKING_URL", ""),
        contact_url=os.getenv("CONTACT_URL", ""),
        booking_webhook_secret=os.getenv("BOOKING_WEBHOOK_SECRET", ""),
        database_path=path("DATABASE_PATH", "data/bot.sqlite3"),
        media_dir=path("MEDIA_DIR", "data/media"),
    )
