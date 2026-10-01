from __future__ import annotations

import asyncio
import logging

import uvicorn
from aiogram import Bot

from .admin import create_admin
from .bot import run_bot
from .config import MIN_ADMIN_PASSWORD_LENGTH, settings, valid_admin_password
from .db import Store
from .reminders import reminder_loop


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = settings()
    if not config.token or config.token.endswith("replace_with_real_token"):
        raise SystemExit("Укажите BOT_TOKEN в .env")
    if not valid_admin_password(config.admin_password):
        raise SystemExit(f"Укажите уникальный ADMIN_PASSWORD длиной от {MIN_ADMIN_PASSWORD_LENGTH} символов")
    if config.admin_host not in ("127.0.0.1", "localhost", "::1"):
        logging.warning("Admin is exposed beyond localhost. Use HTTPS behind a reverse proxy.")
    store = Store(config.database_path)
    app = create_admin(store, config)
    server = uvicorn.Server(uvicorn.Config(
        app, host=config.admin_host, port=config.admin_port, log_level="info",
        proxy_headers=True, forwarded_allow_ips="*",
    ))
    async with Bot(config.token) as bot:
        tasks = [asyncio.create_task(server.serve()), asyncio.create_task(run_bot(bot, store, config)),
                 asyncio.create_task(reminder_loop(bot, store))]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()


if __name__ == "__main__":
    asyncio.run(main())
