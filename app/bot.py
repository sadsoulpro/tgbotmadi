from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand, CallbackQuery, FSInputFile, InlineKeyboardButton,
    InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from PIL import Image, ImageDraw, ImageFont

from .config import ROOT, Settings
from .content import SPHERES, SPHERE_LABELS
from .db import Store, now
from .result import classify

log = logging.getLogger(__name__)


def inline(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        for row in rows
    ])


def media_path(raw: str | None, config: Settings) -> Path | None:
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    if raw.startswith("uploads/"):
        return config.media_dir / raw.removeprefix("uploads/")
    return ROOT / raw


def thumbnail(config: Settings) -> Path:
    path = config.media_dir / "audio_thumbnail.jpg"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (320, 320), "#122637")
    draw = ImageDraw.Draw(image)
    draw.ellipse((45, 45, 275, 275), outline="#d9b77c", width=4)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 29) if font_path.exists() else ImageFont.load_default()
    draw.text((160, 142), "Точка опоры", fill="#f8f5ed", font=font, anchor="mm")
    image.save(path, "JPEG", quality=82, optimize=True)
    return path


def sphere_label(store: Store, key: str) -> str:
    emoji = store.option("emoji_" + key)
    return (emoji + " " if emoji else "") + SPHERE_LABELS[key]


def create_router(store: Store, config: Settings) -> Router:
    router = Router()

    async def show_part(message: Message, user_id: int, part_no: int) -> None:
        part = store.part(part_no)
        if not part:
            store.set_stage(user_id, "finished")
            await message.answer(store.texts()["finished"], reply_markup=ReplyKeyboardRemove())
            return
        total = len(store.parts())
        run_id = store.user(user_id)["current_run"]
        store.set_stage(user_id, "part", part_no)
        texts = store.texts()
        if part_no == 4:
            await message.answer(texts["before_diagnosis"])
        intro = part["intro"] or part["title"]
        await message.answer(texts["part_header"].format(part=part_no, total=total, intro=intro), reply_markup=ReplyKeyboardRemove())
        path = media_path(part["image_path"], config)
        async def send_image() -> None:
            if path and path.is_file():
                await message.answer_photo(FSInputFile(path))
        if part["image_position"] == "before" or part["kind"] == "image":
            await send_image()
        audio = media_path(part["audio_path"], config)
        if part["kind"] == "audio":
            if audio and audio.is_file():
                await message.answer_audio(
                    FSInputFile(audio), title=part["title"],
                    thumbnail=FSInputFile(thumbnail(config)),
                )
            else:
                log.error("Missing audio for part %s: %s", part_no, audio)
                await message.answer(texts["audio_missing"])
                return
        if part["image_position"] != "before" and part["kind"] != "image":
            await send_image()
        store.event(user_id, "step_sent", part_no)
        if part["sphere"] or part["kind"] == "score":
            store.set_stage(user_id, "score", part_no)
            await show_score_prompt(message, user_id, part_no)
        elif part["kind"] == "text_answer":
            store.set_stage(user_id, "text_answer", part_no)
            await message.answer(part["prompt"] or texts["text_answer_prompt"])
        else:
            await message.answer(texts["ready"], reply_markup=inline([(texts["button_next"], f"next:{run_id}:{part_no}")]))
        if part_no >= 8 and part_no == max(8, total - 1):
            user = store.user(user_id)
            if user and not user["phone"] and user["phone_asked"] == 1:
                store.execute("UPDATE users SET phone_asked=2 WHERE telegram_id=?", (user_id,))
                await message.answer(texts["phone_prompt"], reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text=texts["button_share_phone"], request_contact=True)], [KeyboardButton(text=texts["button_later"])]],
                    resize_keyboard=True, one_time_keyboard=True,
                ))
                store.event(user_id, "phone_prompt_repeat")

    async def show_score_prompt(message: Message, user_id: int, part_no: int, edit: bool = False) -> None:
        part = store.part(part_no)
        if not part or not (part["sphere"] or part["kind"] == "score"):
            return
        label = sphere_label(store, part["sphere"]).lower() if part["sphere"] else part["title"]
        text = part["prompt"] or store.texts()["score_prompt"].format(sphere=label)
        run_id = store.user(user_id)["current_run"]
        rows = [[(str(n), f"score:{run_id}:{part_no}:{n}") for n in range(0, 6)],
                [(str(n), f"score:{run_id}:{part_no}:{n}") for n in range(6, 11)]]
        await message.answer(text, reply_markup=inline(*rows))
        store.event(user_id, "score_prompt", part_no, "edit" if edit else None)

    async def show_result(message: Message, user_id: int, *, update: bool = False) -> None:
        user = store.user(user_id)
        run_id = user["current_run"]
        scores = store.scores(user["current_run"])
        if any(key not in scores for key in SPHERES):
            await message.answer(store.texts()["incomplete_scores"])
            return
        texts = store.texts()
        result = classify(scores, store.rules(), texts)
        if update:
            store.execute("UPDATE runs SET average=?,variant=? WHERE id=?", (result.average, result.variant, user["current_run"]))
        else:
            store.complete(user_id, result.average, result.variant)
        lines = "\n".join(f"{sphere_label(store, key)} — {scores[key]}" for key in SPHERES)
        disclaimer = texts["disclaimer"]
        await message.answer(texts["result_intro"].format(scores=lines, paragraph=result.paragraph) + "\n\n" + disclaimer,
                             reply_markup=inline([(texts["button_edit_score"], f"edit:list:{run_id}")]))
        second = result.state if result.safe else texts["result_state"].format(paragraph=result.state)
        await message.answer(second + "\n\n" + disclaimer)
        next_ready = store.part(8) is not None
        buttons = [(texts["button_listen_further"], f"continue:result:{run_id}")] if next_ready else []
        booking_url = store.option("booking_url") or config.booking_url
        if not result.safe and booking_url:
            buttons.append((texts["button_personal_review"], f"booking:result:{run_id}"))
        choice_text = texts["result_waiting"] if not next_ready else texts["result_choice"] if len(buttons) == 2 else texts["result_choice_single"]
        await message.answer(choice_text + "\n\n" + disclaimer,
                             reply_markup=inline(*[[button] for button in buttons]) if buttons else None)
        if not update and user["phone_asked"] == 0 and not user["phone"]:
            store.execute("UPDATE users SET phone_asked=1 WHERE telegram_id=?", (user_id,))
            await message.answer(texts["phone_prompt"], reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text=texts["button_share_phone"], request_contact=True)], [KeyboardButton(text=texts["button_later"])]],
                resize_keyboard=True, one_time_keyboard=True,
            ))
            store.event(user_id, "phone_prompt")

    async def resume(message: Message, user_id: int) -> None:
        user = store.user(user_id)
        if not user or user["stage"] == "welcome":
            texts = store.texts()
            await message.answer(texts["begin_prompt"], reply_markup=inline([(texts["button_begin"], "begin")]))
        elif user["stage"] in ("result", "edit_score"):
            await show_result(message, user_id, update=True)
        elif user["stage"] == "finished":
            await message.answer(store.texts()["finished"])
        elif user["stage"] == "score":
            await show_score_prompt(message, user_id, user["current_part"])
        elif user["stage"] in ("score_saved", "text_saved"):
            texts = store.texts()
            await message.answer(texts["ready"], reply_markup=inline([(texts["button_next"], f"next:{user['current_run']}:{user['current_part']}")]))
        elif user["stage"] == "text_answer":
            part = store.part(user["current_part"])
            await message.answer((part["prompt"] if part else "") or store.texts()["text_answer_prompt"])
        else:
            await show_part(message, user_id, user["current_part"])

    async def show_welcome(message: Message, user_id: int) -> None:
        texts = store.texts()
        name = message.from_user.first_name if message.from_user else store.user(user_id)["first_name"]
        await message.answer(texts["welcome"].format(name=name or "Друг"),
                             reply_markup=inline([(texts["button_begin"], "begin")]))

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if not message.from_user:
            return
        user_id = message.from_user.id
        source = (message.text or "").partition(" ")[2].strip()
        source = source[:64] if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source) else None
        store.upsert_user(user_id, message.from_user.username, message.from_user.first_name or "друг", source)
        lead = store.lead_magnet(source) if source else None
        if lead:
            await message.answer(lead["greeting"].format(name=message.from_user.first_name or "Друг"))
            file = media_path(lead["file_path"], config) if lead["file_path"] else lead["file_url"]
            if file:
                try:
                    if isinstance(file, Path):
                        if not file.is_file():
                            raise FileNotFoundError(file)
                        file = FSInputFile(file)
                    if lead["file_kind"] == "image":
                        await message.answer_photo(file)
                    else:
                        await message.answer_document(file)
                    store.event(user_id, "lead_file_sent", detail=source)
                except (TelegramAPIError, OSError):
                    log.exception("Could not send lead magnet for tag %s", source)
                    await message.answer(store.texts()["lead_file_error"])
            await message.answer(lead["bridge"])
        await show_welcome(message, user_id)

    @router.message(Command("continue"))
    async def continue_command(message: Message) -> None:
        if message.from_user:
            store.event(message.from_user.id, "menu_continue")
            await resume(message, message.from_user.id)

    @router.message(Command("restart"))
    async def restart(message: Message) -> None:
        if not message.from_user:
            return
        user = store.user(message.from_user.id)
        if not user:
            await message.answer(store.texts()["start_required"])
            return
        store.event(message.from_user.id, "restart_requested")
        store.start_run(message.from_user.id)
        await show_part(message, message.from_user.id, 1)

    @router.message(Command("scores"))
    async def my_scores(message: Message) -> None:
        if not message.from_user:
            return
        user = store.user(message.from_user.id)
        if user:
            store.event(message.from_user.id, "menu_scores")
        if not user or not user["current_run"]:
            await message.answer(store.texts()["no_scores"])
            return
        scores = store.scores(user["current_run"])
        if not scores:
            await message.answer(store.texts()["no_scores"])
            return
        lines = "\n".join(f"{sphere_label(store, key)} — {scores[key]}" for key in SPHERES if key in scores)
        await message.answer(lines + "\n\n" + store.texts()["disclaimer"])

    @router.message(Command("booking"))
    async def booking_menu(message: Message) -> None:
        if not message.from_user:
            return
        user = store.user(message.from_user.id)
        if user and user["current_run"]:
            row = store.one("SELECT variant FROM runs WHERE id=?", (user["current_run"],))
            if row and row["variant"] == "C":
                await message.answer(store.texts()["safe_booking_response"])
                return
        url = store.option("booking_url") or config.booking_url
        if url:
            store.event(message.from_user.id, "booking_requested")
            texts = store.texts()
            await message.answer(texts["booking_intro"], reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts["button_open_booking"], url=url)]
            ]))
        else:
            await message.answer(store.texts()["booking_missing"])

    @router.message(Command("contact"))
    async def contact_menu(message: Message) -> None:
        if message.from_user and store.user(message.from_user.id):
            store.event(message.from_user.id, "menu_contact")
        url = store.option("contact_url") or config.contact_url
        if url:
            texts = store.texts()
            await message.answer(texts["contact_intro"], reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts["button_open_contact"], url=url)]
            ]))
        else:
            await message.answer(store.texts()["contact_missing"])

    @router.callback_query(F.data == "begin")
    async def begin(query: CallbackQuery) -> None:
        await query.answer()
        if not query.message or not query.from_user:
            return
        user = store.user(query.from_user.id)
        if not user:
            return
        store.event(query.from_user.id, "button_begin")
        if user["current_run"]:
            await resume(query.message, query.from_user.id)
        else:
            store.start_run(query.from_user.id)
            await show_part(query.message, query.from_user.id, 1)

    @router.callback_query(F.data == "resume")
    async def resume_click(query: CallbackQuery) -> None:
        await query.answer()
        if query.message:
            store.event(query.from_user.id, "button_resume")
            await resume(query.message, query.from_user.id)

    @router.callback_query(F.data.startswith("next:"))
    async def next_part(query: CallbackQuery) -> None:
        await query.answer()
        if not query.message:
            return
        user_id = query.from_user.id
        user = store.user(user_id)
        if not user:
            return
        try:
            _, raw_run, raw_part = query.data.split(":")
            run_id, from_part = int(raw_run), int(raw_part)
        except (ValueError, IndexError):
            return
        if run_id != user["current_run"] or from_part != user["current_part"] or user["stage"] not in ("part", "score_saved", "text_saved"):
            await query.message.answer(store.texts()["stale_button"])
            return
        part = store.part(from_part)
        if part and part["sphere"] and part["sphere"] not in store.scores(user["current_run"]):
            await show_score_prompt(query.message, user_id, from_part)
            return
        if part and part["kind"] == "score" and from_part > 7 and not store.one(
            "SELECT 1 FROM answers WHERE run_id=? AND part=?", (user["current_run"], from_part)
        ):
            await show_score_prompt(query.message, user_id, from_part)
            return
        store.event(user_id, "step_advanced", from_part)
        await show_part(query.message, user_id, from_part + 1)

    @router.callback_query(F.data.startswith("score:"))
    async def score_click(query: CallbackQuery) -> None:
        await query.answer()
        if not query.message:
            return
        user_id = query.from_user.id
        user = store.user(user_id)
        if not user or not user["current_run"]:
            return
        try:
            _, raw_run, raw_part, raw_value = query.data.split(":")
            run_id, part_no, value = int(raw_run), int(raw_part), int(raw_value)
        except (ValueError, TypeError):
            return
        if run_id != user["current_run"]:
            return
        part = store.part(part_no)
        if not part or not (part["sphere"] or part["kind"] == "score") or not 0 <= value <= 10:
            return
        editing = user["stage"] == "edit_score" and user["current_part"] == part_no
        if not editing and (user["stage"] != "score" or user["current_part"] != part_no):
            await query.message.answer(store.texts()["score_already_set"])
            return
        if part_no <= 7 and part["sphere"]:
            store.score(user["current_run"], part["sphere"], value)
        else:
            store.execute("INSERT INTO answers(run_id,part,answer,answered_at) VALUES(?,?,?,?) ON CONFLICT(run_id,part) DO UPDATE SET answer=excluded.answer,answered_at=excluded.answered_at",
                          (user["current_run"], part_no, str(value), now()))
        store.event(user_id, "score_set", part_no, str(value))
        await query.message.answer(store.texts()["score_saved"].format(value=value))
        if editing:
            store.set_stage(user_id, "result", 7)
            await show_result(query.message, user_id, update=True)
            return
        if part_no == 4:
            await query.message.answer(store.texts()["first_score"])
        if part_no == 7:
            await show_result(query.message, user_id)
        else:
            store.set_stage(user_id, "score_saved", part_no)
            texts = store.texts()
            await query.message.answer(texts["ready"], reply_markup=inline([(texts["button_next"], f"next:{run_id}:{part_no}")]))

    @router.callback_query(F.data.startswith("edit:list:"))
    async def edit_list(query: CallbackQuery) -> None:
        await query.answer()
        if not query.message:
            return
        user = store.user(query.from_user.id)
        if not user or user["stage"] not in ("result", "edit_score"):
            return
        if query.data != f"edit:list:{user['current_run']}":
            return
        store.event(query.from_user.id, "edit_list")
        rows = [[(sphere_label(store, key), f"edit:{user['current_run']}:{i}")] for i, key in enumerate(SPHERES, 4)]
        await query.message.answer(store.texts()["edit_which"], reply_markup=inline(*rows))

    @router.callback_query(F.data.startswith("edit:"))
    async def edit_one(query: CallbackQuery) -> None:
        await query.answer()
        if not query.message:
            return
        user = store.user(query.from_user.id)
        if not user or user["stage"] not in ("result", "edit_score"):
            return
        try:
            _, raw_run, raw_part = query.data.split(":")
            run_id, part_no = int(raw_run), int(raw_part)
        except (ValueError, IndexError):
            return
        if run_id != user["current_run"] or part_no not in range(4, 8):
            return
        store.event(query.from_user.id, "edit_score", part_no)
        store.set_stage(query.from_user.id, "edit_score", part_no)
        await show_score_prompt(query.message, query.from_user.id, part_no, edit=True)

    @router.callback_query(F.data.startswith("continue:result:"))
    async def after_result(query: CallbackQuery) -> None:
        await query.answer()
        if not query.message:
            return
        user_id = query.from_user.id
        user = store.user(user_id)
        if not user or user["stage"] not in ("result", "edit_score"):
            return
        if query.data != f"continue:result:{user['current_run']}":
            return
        if not store.part(8):
            await query.message.answer(store.texts()["result_waiting"])
            return
        store.event(user_id, "continue_after_result")
        store.event(user_id, "step_advanced", 7)
        await show_part(query.message, user_id, 8)

    @router.callback_query(F.data.startswith("booking:result:"))
    async def result_booking(query: CallbackQuery) -> None:
        await query.answer()
        if not query.message:
            return
        user = store.user(query.from_user.id)
        if not user or not user["current_run"]:
            return
        if query.data != f"booking:result:{user['current_run']}":
            return
        run = store.one("SELECT variant FROM runs WHERE id=?", (user["current_run"],))
        if run and run["variant"] == "C":
            return
        url = store.option("booking_url") or config.booking_url
        if url:
            store.event(query.from_user.id, "booking_requested")
            texts = store.texts()
            await query.message.answer(texts["booking_intro"], reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts["button_open_booking"], url=url)]
            ]))

    @router.message(F.contact)
    async def phone(message: Message) -> None:
        if not message.from_user or not message.contact:
            return
        if message.contact.user_id != message.from_user.id:
            await message.answer(store.texts()["contact_own_only"])
            return
        store.execute("UPDATE users SET phone=? WHERE telegram_id=?", (message.contact.phone_number, message.from_user.id))
        store.event(message.from_user.id, "phone_saved")
        await message.answer(store.texts()["phone_saved"], reply_markup=ReplyKeyboardRemove())

    async def phone_later(message: Message) -> None:
        if message.from_user:
            store.event(message.from_user.id, "phone_later")
        await message.answer(store.texts()["phone_later"], reply_markup=ReplyKeyboardRemove())

    @router.message(F.text)
    async def free_text(message: Message) -> None:
        if not message.from_user:
            return
        user = store.user(message.from_user.id)
        if user and user["stage"] == "text_answer" and user["current_run"]:
            answer = (message.text or "").strip()[:4000]
            if answer:
                store.execute("INSERT INTO answers(run_id,part,answer,answered_at) VALUES(?,?,?,?) ON CONFLICT(run_id,part) DO UPDATE SET answer=excluded.answer,answered_at=excluded.answered_at",
                              (user["current_run"], user["current_part"], answer, now()))
                store.event(message.from_user.id, "text_answer", user["current_part"])
                store.set_stage(message.from_user.id, "text_saved")
                texts = store.texts()
                await message.answer(texts["answer_saved"], reply_markup=inline([(texts["button_next"], f"next:{user['current_run']}:{user['current_part']}")]))
                return
        texts = store.texts()
        if message.text == texts["button_later"]:
            await phone_later(message)
            return
        if message.text == texts["menu_continue"]:
            await continue_command(message)
            return
        if message.text == texts["menu_scores"]:
            await my_scores(message)
            return
        if message.text == texts["menu_booking"]:
            await booking_menu(message)
            return
        if message.text == texts["menu_contact"]:
            await contact_menu(message)
            return
        await message.answer(store.texts()["fallback"])

    return router


async def configure_commands(bot: Bot, store: Store) -> None:
    texts = store.texts()
    await bot.set_my_commands([
        BotCommand(command="continue", description=texts["menu_continue"]),
        BotCommand(command="scores", description=texts["menu_scores"]),
        BotCommand(command="booking", description=texts["menu_booking"]),
        BotCommand(command="contact", description=texts["menu_contact"]),
        BotCommand(command="restart", description=texts["menu_restart"]),
    ])


async def run_bot(bot: Bot, store: Store, config: Settings) -> None:
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(store, config))
    await configure_commands(bot, store)
    await dispatcher.start_polling(bot, handle_signals=False)
