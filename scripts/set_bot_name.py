"""Set the existing Telegram bot's display name without changing its username."""

from __future__ import annotations

import argparse
import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.config import settings


async def main(expected_username: str) -> None:
    token = settings().token
    if not token:
        raise SystemExit("BOT_TOKEN is missing")
    try:
        async with Bot(token) as bot:
            identity = await bot.get_me()
            if (identity.username or "").casefold() != expected_username.lstrip("@").casefold():
                raise SystemExit("BOT_TOKEN belongs to a different bot; name was not changed")
            for language in (None, "ru"):
                current = await bot.get_my_name(language_code=language)
                if current.name != "Точка опоры":
                    await bot.set_my_name(name="Точка опоры", language_code=language)
            result = await bot.get_my_name(language_code="ru")
            print(f"@{identity.username}: {result.name}")
    except TelegramAPIError:
        raise SystemExit("Telegram API rejected the profile update; check the bot token and try again") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-username", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.expect_username))
