"""Read-only Telegram connection check. Never prints the bot token."""

from __future__ import annotations

import asyncio

from aiogram import Bot

from .config import settings


async def main() -> None:
    config = settings()
    if not config.token:
        raise SystemExit("BOT_TOKEN is missing from .env")
    try:
        async with Bot(config.token) as bot:
            me = await bot.get_me()
            webhook = await bot.get_webhook_info()
    except Exception as exc:
        raise SystemExit(f"Telegram connection failed: {type(exc).__name__}") from None
    print(f"Bot: @{me.username} (id {me.id})")
    print(f"Webhook configured: {bool(webhook.url)}")
    print(f"Pending updates: {webhook.pending_update_count}")


if __name__ == "__main__":
    asyncio.run(main())
