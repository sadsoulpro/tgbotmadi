from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, Update, User

from app.bot import create_router
from app.config import Settings
from app.db import Store


class FakeBot(Bot):
    def __init__(self):
        super().__init__("123456:TEST")
        self.calls = []

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        if method.__class__.__name__ == "AnswerCallbackQuery":
            return True
        return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                       chat=Chat(id=42, type="private"), text="sent",
                       from_user=User(id=self.id, is_bot=True, first_name="Bot"))


def test_supplied_checklist_start_sends_pdf_then_one_begin_button(tmp_path: Path):
    async def scenario():
        config = Settings("123456:TEST", "owner", "verylongtestpassword", "127.0.0.1", 8080,
                          "", "", "", tmp_path / "bot.sqlite3", tmp_path / "media")
        store = Store(config.database_path)
        bot = FakeBot()
        dispatcher = Dispatcher()
        dispatcher.include_router(create_router(store, config))
        person = User(id=42, is_bot=False, first_name="Иван")
        chat = Chat(id=42, type="private")
        update = Update(update_id=1, message=Message(message_id=1, date=datetime.now(timezone.utc),
                                                     chat=chat, text="/start checklist_udobniy", from_user=person))
        await dispatcher.feed_update(bot, update)
        assert [call.__class__.__name__ for call in bot.calls] == [
            "SendMessage", "SendDocument", "SendMessage", "SendMessage"]
        assert bot.calls[1].document.path.name == "checklist_udobniy.pdf"
        buttons = [call for call in bot.calls if isinstance(getattr(call, "reply_markup", None), InlineKeyboardMarkup)]
        assert len(buttons) == 1
        assert buttons[0].reply_markup.inline_keyboard[0][0].text == "Начать"
        assert store.user(42)["stage"] == "welcome"

    asyncio.run(scenario())


def test_complete_diagnostic_and_restart(tmp_path: Path):
    async def scenario():
        config = Settings("123456:TEST", "owner", "verylongtestpassword", "127.0.0.1", 8080,
                          "https://example.org/book", "", "", tmp_path / "bot.sqlite3", tmp_path / "media")
        store = Store(config.database_path)
        config.media_dir.mkdir(parents=True)
        (config.media_dir / "guide.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        store.execute("INSERT INTO lead_magnets(tag,greeting,bridge,file_path) VALUES(?,?,?,?)",
                      ("inerciya", "{name}, держи материал.", "Теперь перейдём к диагностике.", "uploads/guide.pdf"))
        store.execute("INSERT INTO lead_magnets(tag,greeting,bridge,file_path) VALUES(?,?,?,?)",
                      ("nadryv", "{name}, новый материал.", "Продолжай с сохранённого шага.", "uploads/guide.pdf"))
        bot = FakeBot()
        dispatcher = Dispatcher()
        dispatcher.include_router(create_router(store, config))
        person = User(id=42, is_bot=False, first_name="Иван")
        chat = Chat(id=42, type="private")
        ordinal = 0

        async def message(text):
            nonlocal ordinal
            ordinal += 1
            update = Update(update_id=ordinal, message=Message(message_id=ordinal, date=datetime.now(timezone.utc),
                                                               chat=chat, text=text, from_user=person))
            await dispatcher.feed_update(bot, update)

        async def click(data):
            nonlocal ordinal
            ordinal += 1
            source = Message(message_id=ordinal, date=datetime.now(timezone.utc), chat=chat,
                             text="button", from_user=User(id=bot.id, is_bot=True, first_name="Bot"))
            update = Update(update_id=ordinal, callback_query=CallbackQuery(id=str(ordinal), from_user=person,
                            chat_instance="test", message=source, data=data))
            await dispatcher.feed_update(bot, update)

        await message("/start inerciya")
        assert store.user(42)["source"] == "inerciya"
        assert [call.__class__.__name__ for call in bot.calls[:4]] == [
            "SendMessage", "SendDocument", "SendMessage", "SendMessage"]
        assert sum(1 for call in bot.calls[:4] if isinstance(getattr(call, "reply_markup", None), InlineKeyboardMarkup)) == 1
        await click("begin")
        first = store.user(42)["current_run"]
        assert store.one("SELECT source FROM runs WHERE id=?", (first,))["source"] == "inerciya"
        for part in range(1, 8):
            assert store.user(42)["current_part"] == part
            if part >= 4:
                await click(f"score:{first}:{part}:6")
            if part < 7:
                await click(f"next:{first}:{part}")
        assert store.user(42)["stage"] == "result"
        assert store.one("SELECT COUNT(*) AS n FROM events WHERE kind='step_sent'")["n"] == 7
        assert store.one("SELECT COUNT(*) AS n FROM events WHERE kind='step_advanced'")["n"] == 6
        assert any("Следующие части серии пока готовятся" in (getattr(call, "text", "") or "") for call in bot.calls)
        assert not any(
            isinstance(getattr(call, "reply_markup", None), InlineKeyboardMarkup)
            and any(button.text == "Слушать дальше" for row in call.reply_markup.inline_keyboard for button in row)
            for call in bot.calls
        )
        assert store.one("SELECT variant FROM runs WHERE id=?", (first,))["variant"] == "B2"
        assert any(
            isinstance(getattr(call, "reply_markup", None), InlineKeyboardMarkup)
            and any(button.text == "Хочу личный разбор" for row in call.reply_markup.inline_keyboard for button in row)
            for call in bot.calls
        )
        assert any(isinstance(getattr(call, "reply_markup", None), ReplyKeyboardMarkup) for call in bot.calls)
        await click(f"booking:result:{first}")
        assert store.one("SELECT COUNT(*) AS n FROM events WHERE kind='booking_requested'")["n"] == 1
        assert store.one("SELECT booked_at FROM runs WHERE id=?", (first,))["booked_at"] is None
        await click(f"edit:list:{first}")
        await click(f"edit:{first}:4")
        await click(f"score:{first}:4:1")
        assert store.scores(first)["spiritual"] == 1
        assert store.one("SELECT variant FROM runs WHERE id=?", (first,))["variant"] == "A1"
        await message("/start nadryv")
        assert store.user(42)["source"] == "inerciya"
        assert bot.calls[-3].__class__.__name__ == "SendDocument"
        assert store.user(42)["stage"] == "result"
        await click("begin")
        assert store.user(42)["current_run"] == first
        assert store.one("SELECT source FROM runs WHERE id=?", (first,))["source"] == "inerciya"
        store.execute("INSERT INTO parts(position,title,intro,kind,prompt,enabled) VALUES(8,'Роли','Роли','text_answer','Назови роли',1)")
        await click(f"continue:result:{first}")
        assert store.user(42)["stage"] == "text_answer"
        await message("Позже")
        assert store.one("SELECT answer FROM answers WHERE run_id=? AND part=8", (first,))["answer"] == "Позже"
        await message("/restart")
        assert store.user(42)["current_run"] != first
        second = store.user(42)["current_run"]
        assert store.one("SELECT source FROM runs WHERE id=?", (second,))["source"] == "nadryv"
        new_calls = len(bot.calls)
        assert store.scores(first)["spiritual"] == 1
        await click(f"next:{first}:1")
        assert store.user(42)["current_part"] == 1
        for part in range(1, 8):
            if part >= 4:
                await click(f"score:{second}:{part}:{0 if part == 4 else 1}")
            if part < 7:
                await click(f"next:{second}:{part}")
        assert store.one("SELECT variant FROM runs WHERE id=?", (second,))["variant"] == "C"
        assert any("Дальше можно слушать серию" in (getattr(call, "text", "") or "") for call in bot.calls[new_calls:])
        assert not any(
            isinstance(getattr(call, "reply_markup", None), InlineKeyboardMarkup)
            and any(button.text == "Хочу личный разбор" for row in call.reply_markup.inline_keyboard for button in row)
            for call in bot.calls[new_calls:]
        )
        assert any(call.__class__.__name__ == "SendAudio" for call in bot.calls)
        await bot.session.close()

    asyncio.run(scenario())
