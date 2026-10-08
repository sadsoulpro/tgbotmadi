from __future__ import annotations

import csv
import binascii
import hmac
import html
import io
import re
import string
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import UploadFile

from .config import Settings
from .db import Store, now


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def secure_equals(value: str, expected: str) -> bool:
    return hmac.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


def create_admin(store: Store, config: Settings) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    csrf_token = hmac.new(config.admin_password.encode(), b"admin-csrf-v1", "sha256").hexdigest()
    session_seconds = 12 * 60 * 60
    login_window_seconds = 10 * 60
    login_attempt_limit = 5
    failed_logins: dict[str, list[float]] = {}

    def login_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def check_login_limit(request: Request) -> None:
        key = login_key(request)
        current = time.monotonic()
        recent = [stamp for stamp in failed_logins.get(key, []) if current - stamp < login_window_seconds]
        if recent:
            failed_logins[key] = recent
        else:
            failed_logins.pop(key, None)
        if len(recent) >= login_attempt_limit:
            raise HTTPException(429, "Слишком много попыток входа. Повтори через 10 минут.",
                                headers={"Retry-After": str(login_window_seconds)})

    def failed_login(request: Request) -> None:
        failed_logins.setdefault(login_key(request), []).append(time.monotonic())
        if len(failed_logins) > 1024:
            current = time.monotonic()
            for key in list(failed_logins):
                if not any(current - stamp < login_window_seconds for stamp in failed_logins[key]):
                    del failed_logins[key]

    def successful_login(request: Request) -> None:
        failed_logins.pop(login_key(request), None)

    def signed_session(stamp: int) -> str:
        body = f"{config.admin_user}:{stamp}"
        signature = hmac.new(config.admin_password.encode(), body.encode(), "sha256").hexdigest()
        return f"{stamp}.{signature}"

    def valid_session(cookie: str | None) -> bool:
        if not cookie:
            return False
        try:
            raw_stamp, _ = cookie.split(".", 1)
            stamp = int(raw_stamp)
        except (ValueError, TypeError):
            return False
        current = int(time.time())
        return current - session_seconds <= stamp <= current + 60 and hmac.compare_digest(cookie, signed_session(stamp))

    def auth(request: Request) -> None:
        if valid_session(request.cookies.get("admin_session")):
            return
        header = request.headers.get("authorization", "")
        if not header.startswith("Basic "):
            raise HTTPException(303, headers={"Location": "/admin/login"})
        check_login_limit(request)
        try:
            import base64
            user, password = base64.b64decode(header[6:], validate=True).decode().split(":", 1)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            failed_login(request)
            raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="Tochka Opory"'})
        if not secure_equals(user, config.admin_user) or not secure_equals(password, config.admin_password):
            failed_login(request)
            raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="Tochka Opory"'})
        successful_login(request)

    async def form(request: Request):
        auth(request)
        data = await request.form()
        if not hmac.compare_digest(str(data.get("csrf", "")), csrf_token):
            raise HTTPException(403, "Invalid form token")
        return data

    def hidden() -> str:
        return f'<input type="hidden" name="csrf" value="{csrf_token}">'

    def page(title: str, body: str) -> HTMLResponse:
        nav = ' '.join(f'<a href="{url}">{name}</a>' for name, url in [
            ("Обзор", "/admin"), ("Части", "/admin/parts"),
            ("Лид-магниты", "/admin/lead-magnets"), ("Тексты", "/admin/texts"),
            ("Правила", "/admin/rules"), ("Настройки", "/admin/options"),
            ("Пользователи", "/admin/users"), ("Выгрузка", "/admin/export"), ("Выйти", "/admin/logout"),
        ])
        css = """<style>body{font:16px system-ui;margin:0;background:#f4f6f7;color:#162331}header{background:#173347;color:white;padding:18px 4vw}header a{color:white;margin-right:20px}main{max-width:1100px;margin:28px auto;padding:0 20px}article{background:white;padding:20px;margin:14px 0;border-radius:10px;box-shadow:0 1px 5px #ccd}input,textarea,select{display:block;width:100%;box-sizing:border-box;padding:9px;margin:5px 0 14px;font:inherit}input[type=checkbox]{width:auto;display:inline}button{background:#173347;color:white;border:0;padding:10px 16px;border-radius:5px;cursor:pointer}table{width:100%;border-collapse:collapse;background:white}td,th{text-align:left;padding:9px;border-bottom:1px solid #ddd}small{color:#526}a{color:#125477}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}.metric{background:#fff;padding:18px;border-radius:8px}.metric b{display:block;font-size:26px}</style>"""
        return HTMLResponse(f'<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{esc(title)} — Точка опоры</title>{css}<header><h1>Точка опоры</h1><nav>{nav}</nav></header><main><h2>{esc(title)}</h2>{body}</main></html>')

    def login_page(error: str = "") -> HTMLResponse:
        warning = f'<p style="color:#a31d1d">{esc(error)}</p>' if error else ""
        body = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Вход — Точка опоры</title>
        <style>body{{font:16px system-ui;background:#f4f6f7;color:#162331;display:grid;min-height:100vh;place-items:center;margin:0}}main{{background:white;padding:32px;width:min(92vw,380px);border-radius:12px;box-shadow:0 2px 12px #ccd}}input{{display:block;width:100%;box-sizing:border-box;padding:11px;margin:7px 0 18px;font:inherit}}button{{background:#173347;color:white;border:0;padding:12px 18px;border-radius:5px;cursor:pointer;width:100%}}</style>
        <main><h1>Точка опоры</h1><p>Вход в админку</p>{warning}<form method="post" action="/admin/login">{hidden()}<label>Логин<input name="username" autocomplete="username" required></label><label>Пароль<input type="password" name="password" autocomplete="current-password" required></label><button>Войти</button></form></main></html>'''
        return HTMLResponse(body, status_code=401 if error else 200)

    @app.get("/")
    async def homepage():
        return RedirectResponse("/admin", status_code=302)

    @app.get("/admin/login")
    async def login(request: Request):
        if valid_session(request.cookies.get("admin_session")):
            return RedirectResponse("/admin", status_code=303)
        return login_page()

    @app.post("/admin/login")
    async def login_submit(request: Request):
        data = await request.form()
        if not hmac.compare_digest(str(data.get("csrf", "")), csrf_token):
            raise HTTPException(403)
        check_login_limit(request)
        username = str(data.get("username", ""))
        password = str(data.get("password", ""))
        if not secure_equals(username, config.admin_user) or not secure_equals(password, config.admin_password):
            failed_login(request)
            return login_page("Неверный логин или пароль.")
        successful_login(request)
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie("admin_session", signed_session(int(time.time())), max_age=session_seconds,
                            httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/admin")
        return response

    @app.get("/admin/logout")
    async def logout(request: Request):
        response = RedirectResponse("/admin/login", status_code=303)
        response.delete_cookie("admin_session", path="/admin")
        return response

    @app.get("/admin")
    async def dashboard(request: Request):
        auth(request)
        total = store.one("SELECT COUNT(*) AS n FROM users")["n"]
        utc_now = datetime.now(timezone.utc)
        periods = [("За день", 1), ("За неделю", 7), ("За месяц", 30)]
        cards = f'<div class="metric">Всего пользователей<b>{total}</b></div>'
        for label, days in periods:
            since = (utc_now - timedelta(days=days)).isoformat(timespec="seconds")
            n = store.one("SELECT COUNT(*) AS n FROM users WHERE first_seen>=?", (since,))["n"]
            cards += f'<div class="metric">{label}<b>{n}</b></div>'
        parts = store.parts()
        funnel = [("Запустил бота", total)]
        for part in parts:
            n = store.one("SELECT COUNT(DISTINCT telegram_id) AS n FROM events WHERE kind='step_sent' AND part=?", (part["position"],))["n"]
            funnel.append((f"Отправлено на шаге {part['position']} — {part['title']}", n))
            if part["position"] < parts[-1]["position"]:
                advanced = store.one("SELECT COUNT(DISTINCT telegram_id) AS n FROM events WHERE kind='step_advanced' AND part=?", (part["position"],))["n"]
                funnel.append((f"Перешёл на шаг {part['position'] + 1}", advanced))
                funnel.append((f"Не перешёл после шага {part['position']}", max(0, n - advanced)))
        for label, sql in [
            ("Получил результат", "SELECT COUNT(DISTINCT telegram_id) AS n FROM events WHERE kind='result'"),
            ("Оставил телефон", "SELECT COUNT(*) AS n FROM users WHERE phone IS NOT NULL"),
            ("Запросил ссылку записи", "SELECT COUNT(DISTINCT telegram_id) AS n FROM events WHERE kind='booking_requested'"),
            ("Запись подтверждена", "SELECT COUNT(DISTINCT telegram_id) AS n FROM runs WHERE booked_at IS NOT NULL"),
        ]:
            funnel.append((label, store.one(sql)["n"]))
        rows = "".join(f'<tr><td>{esc(label)}</td><td>{count}</td></tr>' for label, count in funnel)
        source_rows = store.all("""SELECT COALESCE(u.source,'(без метки)') AS source,
            COUNT(*) AS total,
            SUM(CASE WHEN EXISTS(SELECT 1 FROM events e WHERE e.telegram_id=u.telegram_id AND e.kind='result') THEN 1 ELSE 0 END) AS results,
            SUM(CASE WHEN u.phone IS NOT NULL THEN 1 ELSE 0 END) AS phones,
            SUM(CASE WHEN EXISTS(SELECT 1 FROM events e WHERE e.telegram_id=u.telegram_id AND e.kind='booking_requested') THEN 1 ELSE 0 END) AS booking_clicks
            FROM users u GROUP BY u.source ORDER BY total DESC""")
        source = "".join(f'<tr><td>{esc(r["source"])}</td><td>{r["total"]}</td><td>{r["results"]}</td><td>{r["phones"]}</td><td>{r["booking_clicks"]}</td></tr>' for r in source_rows)
        source_part_rows = []
        for src in source_rows:
            key = None if src["source"] == "(без метки)" else src["source"]
            cells = []
            for part in parts:
                count = store.one("""SELECT COUNT(DISTINCT e.telegram_id) AS n FROM events e JOIN users u ON u.telegram_id=e.telegram_id
                    WHERE e.kind='step_sent' AND e.part=? AND u.source IS ?""", (part["position"], key))["n"]
                cells.append(f"<td>{count}</td>")
            source_part_rows.append(f'<tr><td>{esc(src["source"])}</td><td>{src["total"]}</td>{"".join(cells)}<td>{src["results"]}</td></tr>')
        variants = store.all("SELECT variant,COUNT(*) AS n FROM runs WHERE variant IS NOT NULL GROUP BY variant ORDER BY variant")
        variant_html = "".join(f'<tr><td>{esc(r["variant"])}</td><td>{r["n"]}</td></tr>' for r in variants)
        score_rows = store.all("SELECT sphere,value,COUNT(*) AS n FROM scores GROUP BY sphere,value ORDER BY sphere,value")
        scores_html = "".join(f'<tr><td>{esc(r["sphere"])}</td><td>{r["value"]}</td><td>{r["n"]}</td></tr>' for r in score_rows)
        unanswered = store.one("""SELECT COUNT(*) AS n FROM (
            SELECT DISTINCT e.run_id,e.part FROM events e
            JOIN parts p ON p.position=e.part AND p.sphere IS NOT NULL
            LEFT JOIN scores s ON s.run_id=e.run_id AND s.sphere=p.sphere
            WHERE e.kind='step_sent' AND s.run_id IS NULL
        )""")["n"]
        asked = store.one("""SELECT COUNT(*) AS n FROM (SELECT DISTINCT e.run_id,e.part FROM events e JOIN parts p ON p.position=e.part AND p.sphere IS NOT NULL WHERE e.kind='step_sent')""")["n"]
        percent = round(100 * unanswered / asked, 1) if asked else 0
        body = f'<div class="grid">{cards}<div class="metric">Не ответили после аудио<b>{unanswered}/{asked} ({percent}%)</b></div></div>'
        body += '<article><h3>Воронка</h3><small>«Отправлено» — бот отправил материал шага. «Перешёл» — человек нажал переход дальше. Разница показывает отвал на шаге, но не причину. Telegram не сообщает факт прослушивания.</small><table><tr><th>Шаг</th><th>Люди</th></tr>' + rows + '</table></article>'
        body += '<article><h3>Источники первого входа</h3><table><tr><th>Метка</th><th>Всего</th><th>Результат</th><th>Телефон</th><th>Запросили ссылку записи</th></tr>' + source + '</table></article>'
        body += '<article><h3>Воронка по источникам</h3><table><tr><th>Метка</th><th>Старт</th>' + ''.join(f'<th>Часть {p["position"]}</th>' for p in parts) + '<th>Результат</th></tr>' + ''.join(source_part_rows) + '</table></article>'
        tags = store.all("""SELECT tag FROM lead_magnets UNION SELECT detail AS tag FROM events
            WHERE kind='start' AND detail IS NOT NULL AND detail!='' ORDER BY tag""")
        tag_rows = ""
        for row in tags:
            tag = row["tag"]
            visits = store.one("SELECT COUNT(DISTINCT telegram_id) AS n FROM events WHERE kind='start' AND detail=?", (tag,))["n"]
            started = store.one("SELECT COUNT(DISTINCT telegram_id) AS n FROM runs WHERE source=?", (tag,))["n"]
            results = store.one("SELECT COUNT(DISTINCT telegram_id) AS n FROM runs WHERE source=? AND completed_at IS NOT NULL", (tag,))["n"]
            bookings = store.one("SELECT COUNT(DISTINCT telegram_id) AS n FROM runs WHERE source=? AND booked_at IS NOT NULL", (tag,))["n"]
            tag_rows += f"<tr><td>{esc(tag)}</td><td>{visits}</td><td>{started}</td><td>{results}</td><td>{bookings}</td></tr>"
        body += '<article><h3>Лид-магниты и теги</h3><small>«Пришли» — уникальные люди по каждому тегу. Диагностика и результат относятся к тегу последнего входа перед началом прохождения. Повторный вход после начала прохождения не переносит его в другой тег.</small><table><tr><th>Тег</th><th>Пришли</th><th>Начали</th><th>Результат</th><th>Записались</th></tr>' + tag_rows + '</table></article>'
        body += '<article><h3>Варианты</h3><table>' + variant_html + '</table></article>'
        body += '<article><h3>Распределение оценок</h3><table><tr><th>Сфера</th><th>Оценка</th><th>Количество</th></tr>' + scores_html + '</table></article>'
        return page("Обзор", body)

    @app.get("/admin/parts")
    async def parts(request: Request):
        auth(request)
        body = "<p>Части 1–7 составляют диагностику. Новые части добавляются после результата.</p>"
        for part in store.all("SELECT * FROM parts ORDER BY position"):
            pid = part["id"]
            body += f'''<article><h3>Часть {part['position']}: {esc(part['title'])}</h3><form method="post" action="/admin/parts/{pid}">{hidden()}
            <label>Название<input name="title" value="{esc(part['title'])}" required></label>
            <label>Подводка<textarea name="intro" rows="3">{esc(part['intro'])}</textarea></label>
            <label>Тип<select name="kind">{''.join(f'<option value="{k}" {"selected" if part["kind"]==k else ""}>{k}</option>' for k in ('audio','image','score','text_answer','button'))}</select></label>
            <label>Сфера для оценки<select name="sphere">{''.join(f'<option value="{k}" {"selected" if (part["sphere"] or "")==k else ""}>{k or "Без оценки"}</option>' for k in ('','spiritual','emotional','mental','physical'))}</select></label>
            <label>Вопрос для текстового ответа<textarea name="prompt" rows="2">{esc(part['prompt'])}</textarea></label>
            <label>Картинка<select name="image_position"><option value="after" {'selected' if part['image_position']=='after' else ''}>После аудио</option><option value="before" {'selected' if part['image_position']=='before' else ''}>Перед аудио</option></select></label>
            <label><input type="checkbox" name="enabled" {'checked' if part['enabled'] else ''}> Доступна</label><button>Сохранить часть</button></form>
            <small>Аудио: {esc(part['audio_path']) or 'нет'}; картинка: {esc(part['image_path']) or 'нет'}</small>
            <form method="post" action="/admin/parts/{pid}/media" enctype="multipart/form-data">{hidden()}<label>Заменить аудио MP3<input type="file" name="audio" accept="audio/mpeg,.mp3"></label><label>Заменить картинку PNG/JPEG<input type="file" name="image" accept="image/png,image/jpeg"></label><button>Загрузить файлы</button></form></article>'''
        body += f'''<article><h3>Добавить часть в конец</h3><form method="post" action="/admin/parts/new">{hidden()}<label>Название<input name="title" required></label><label>Тип<select name="kind"><option value="audio">Аудио</option><option value="image">Картинка</option><option value="score">Оценка</option><option value="text_answer">Текстовый ответ</option><option value="button">Кнопка</option></select></label><button>Добавить</button></form></article>'''
        return page("Части серии", body)

    @app.post("/admin/parts/{pid}")
    async def save_part(pid: int, request: Request):
        data = await form(request)
        part = store.one("SELECT * FROM parts WHERE id=?", (pid,))
        if not part:
            raise HTTPException(404)
        kind = str(data.get("kind", "audio"))
        if kind not in {"audio", "image", "score", "text_answer", "button"}:
            raise HTTPException(400)
        sphere = str(data.get("sphere", "")) or None
        if sphere not in {None, "spiritual", "emotional", "mental", "physical"}:
            raise HTTPException(400)
        if part["position"] <= 7:
            sphere = part["sphere"]
            kind = "audio"
        enabled = int("enabled" in data)
        if part["position"] <= 7:
            enabled = 1
        if enabled and kind == "audio" and not part["audio_path"]:
            raise HTTPException(400, "Сначала загрузите аудио")
        if enabled and kind == "image" and not part["image_path"]:
            raise HTTPException(400, "Сначала загрузите картинку")
        if enabled and part["position"] > 7 and store.one(
            "SELECT 1 FROM parts WHERE position<? AND enabled=0 LIMIT 1", (part["position"],)
        ):
            raise HTTPException(400, "Сначала включите предыдущие части")
        if not enabled and store.one(
            "SELECT 1 FROM parts WHERE position>? AND enabled=1 LIMIT 1", (part["position"],)
        ):
            raise HTTPException(400, "Сначала выключите последующие части")
        title = str(data.get("title", "")).strip()[:150]
        if not title:
            raise HTTPException(400)
        store.execute("UPDATE parts SET title=?,intro=?,kind=?,prompt=?,image_position=?,sphere=?,enabled=? WHERE id=?",
                      (title, str(data.get("intro", ""))[:3000], kind, str(data.get("prompt", ""))[:1000],
                       "before" if data.get("image_position") == "before" else "after", sphere, enabled, pid))
        return RedirectResponse("/admin/parts", status_code=303)

    @app.post("/admin/parts/new")
    async def new_part(request: Request):
        data = await form(request)
        title = str(data.get("title", "")).strip()[:150]
        kind = str(data.get("kind", "audio"))
        if not title or kind not in {"audio", "image", "score", "text_answer", "button"}:
            raise HTTPException(400)
        next_pos = store.one("SELECT COALESCE(MAX(position),0)+1 AS n FROM parts")["n"]
        store.execute("INSERT INTO parts(position,title,intro,kind,enabled) VALUES(?,?,?,?,0)",
                      (next_pos, title, title, kind))
        return RedirectResponse("/admin/parts", status_code=303)

    @app.post("/admin/parts/{pid}/media")
    async def upload_media(pid: int, request: Request):
        data = await form(request)
        if not store.one("SELECT 1 FROM parts WHERE id=?", (pid,)):
            raise HTTPException(404)
        config.media_dir.mkdir(parents=True, exist_ok=True)
        for field, ext, limit in [("audio", ".mp3", 50 * 1024 * 1024), ("image", None, 10 * 1024 * 1024)]:
            upload = data.get(field)
            if not isinstance(upload, UploadFile) or not upload.filename:
                continue
            content = await upload.read(limit + 1)
            if len(content) > limit:
                raise HTTPException(413, "File too large")
            if field == "audio":
                if Path(upload.filename).suffix.lower() != ext or not content.startswith(b"ID3") and content[:2] not in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
                    raise HTTPException(400, "Expected MP3")
                suffix = ".mp3"
            else:
                from PIL import Image
                try:
                    image = Image.open(io.BytesIO(content))
                    image.verify()
                    suffix = ".png" if image.format == "PNG" else ".jpg" if image.format == "JPEG" else ""
                except Exception:
                    suffix = ""
                if not suffix:
                    raise HTTPException(400, "Expected PNG or JPEG")
            name = uuid.uuid4().hex + suffix
            destination = config.media_dir / name
            destination.write_bytes(content)
            store.execute(f"UPDATE parts SET {'audio_path' if field=='audio' else 'image_path'}=? WHERE id=?", ("uploads/" + name, pid))
        return RedirectResponse("/admin/parts", status_code=303)

    def save_lead(tag: str, data) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tag):
            raise HTTPException(400, "Тег: латиница, цифры, _ или -, до 64 символов")
        greeting = str(data.get("greeting", "")).strip()[:3000]
        bridge = str(data.get("bridge", "")).strip()[:3000]
        if not greeting or not bridge:
            raise HTTPException(400, "Укажите приветствие и фразу-переход")
        try:
            fields = [field for _, field, _, _ in string.Formatter().parse(greeting) if field]
            if any(field != "name" for field in fields):
                raise ValueError
            greeting.format(name="Иван")
        except (ValueError, KeyError, IndexError):
            raise HTTPException(400, "В приветствии допустима только переменная {name}")
        url = str(data.get("file_url", "")).strip()[:2048]
        if url and not re.fullmatch(r"https://[^\s]+", url):
            raise HTTPException(400, "Ссылка на файл должна начинаться с https://")
        kind = str(data.get("file_kind", "document"))
        if kind not in {"document", "image"}:
            raise HTTPException(400, "Неизвестный тип файла")
        old = store.one("SELECT file_path,file_url FROM lead_magnets WHERE tag=?", (tag,))
        file_path = None if "remove_file" in data or url else old["file_path"] if old else None
        if "remove_file" in data:
            url = ""
        elif not url and old:
            url = old["file_url"] or ""
        store.execute("""INSERT INTO lead_magnets(tag,greeting,bridge,file_path,file_url,file_kind,enabled)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(tag) DO UPDATE SET greeting=excluded.greeting,
            bridge=excluded.bridge,file_path=excluded.file_path,file_url=excluded.file_url,
            file_kind=excluded.file_kind,enabled=excluded.enabled""",
            (tag, greeting, bridge, file_path, url or None, kind, int("enabled" in data)))

    @app.get("/admin/lead-magnets")
    async def lead_magnets(request: Request):
        auth(request)
        leads = store.all("SELECT * FROM lead_magnets ORDER BY tag")
        body = ('<p>Лид-магниты настроены. Для каждого тега можно изменить приветствие, файл и фразу-переход. '
                'После материала бот покажет общее приветствие с одной кнопкой.</p>' if leads else
                '<p>Материалов пока нет: бот показывает обычное приветствие. Добавьте тег, приветствие '
                'и фразу-переход; файл необязателен.</p>')
        for lead in leads:
            tag = esc(lead["tag"])
            kind = lead["file_kind"]
            body += f'''<article><h3>{tag}</h3><form method="post" action="/admin/lead-magnets/{tag}">{hidden()}
                <label>Приветствие (можно {{name}})<textarea name="greeting" rows="3" required>{esc(lead['greeting'])}</textarea></label>
                <label>Фраза-переход к диагностике<textarea name="bridge" rows="3" required>{esc(lead['bridge'])}</textarea></label>
                <label>Публичная HTTPS-ссылка на файл (если есть)<input name="file_url" value="{esc(lead['file_url'])}"></label>
                <label>Тип файла по ссылке<select name="file_kind"><option value="document" {'selected' if kind=='document' else ''}>PDF / документ</option><option value="image" {'selected' if kind=='image' else ''}>Картинка</option></select></label>
                <p>Загруженный файл: {esc(lead['file_path']) or 'нет'}</p>
                <label><input type="checkbox" name="remove_file"> Убрать файл или ссылку</label>
                <label><input type="checkbox" name="enabled" {'checked' if lead['enabled'] else ''}> Включён</label><button>Сохранить</button></form>
                <form method="post" action="/admin/lead-magnets/{tag}/file" enctype="multipart/form-data">{hidden()}
                <label>Загрузить PDF, PNG или JPEG<input type="file" name="file" accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg" required></label>
                <button>Загрузить файл</button></form></article>'''
        body += f'''<article><h3>Новый тег</h3><form method="post" action="/admin/lead-magnets">{hidden()}
            <label>Тег для ссылки ?start=...<input name="tag" pattern="[A-Za-z0-9_-]{{1,64}}" maxlength="64" required></label>
            <label>Приветствие (можно {{name}})<textarea name="greeting" rows="3" required></textarea></label>
            <label>Фраза-переход к диагностике<textarea name="bridge" rows="3" required></textarea></label>
            <label>Публичная HTTPS-ссылка на файл (необязательно)<input name="file_url"></label>
            <label>Тип файла по ссылке<select name="file_kind"><option value="document">PDF / документ</option><option value="image">Картинка</option></select></label>
            <label><input type="checkbox" name="enabled" checked> Включён</label><button>Добавить</button></form></article>'''
        return page("Лид-магниты", body)

    @app.post("/admin/lead-magnets")
    async def add_lead_magnet(request: Request):
        data = await form(request)
        tag = str(data.get("tag", "")).strip()
        if store.one("SELECT 1 FROM lead_magnets WHERE tag=?", (tag,)):
            raise HTTPException(409, "Такой тег уже существует")
        save_lead(tag, data)
        return RedirectResponse("/admin/lead-magnets", status_code=303)

    @app.post("/admin/lead-magnets/{tag}")
    async def edit_lead_magnet(tag: str, request: Request):
        data = await form(request)
        if not store.one("SELECT 1 FROM lead_magnets WHERE tag=?", (tag,)):
            raise HTTPException(404)
        save_lead(tag, data)
        return RedirectResponse("/admin/lead-magnets", status_code=303)

    @app.post("/admin/lead-magnets/{tag}/file")
    async def upload_lead_magnet_file(tag: str, request: Request):
        data = await form(request)
        if not store.one("SELECT 1 FROM lead_magnets WHERE tag=?", (tag,)):
            raise HTTPException(404)
        upload = data.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise HTTPException(400, "Выберите файл")
        content = await upload.read(20 * 1024 * 1024 + 1)
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(413, "Файл больше 20 МБ")
        extension = Path(upload.filename).suffix.lower()
        if extension == ".pdf" and content.startswith(b"%PDF-"):
            kind = "document"
        elif extension in {".png", ".jpg", ".jpeg"}:
            from PIL import Image
            try:
                image = Image.open(io.BytesIO(content))
                image.verify()
                if image.format not in {"PNG", "JPEG"}:
                    raise ValueError
            except Exception:
                raise HTTPException(400, "Ожидается PDF, PNG или JPEG")
            extension = ".png" if image.format == "PNG" else ".jpg"
            kind = "image"
        else:
            raise HTTPException(400, "Ожидается PDF, PNG или JPEG")
        config.media_dir.mkdir(parents=True, exist_ok=True)
        path = config.media_dir / (uuid.uuid4().hex + extension)
        path.write_bytes(content)
        store.execute("UPDATE lead_magnets SET file_path=?,file_url=NULL,file_kind=? WHERE tag=?",
                      ("uploads/" + path.name, kind, tag))
        return RedirectResponse("/admin/lead-magnets", status_code=303)

    @app.get("/admin/texts")
    async def texts(request: Request):
        auth(request)
        body = "<p>Сохраняйте переменные в фигурных скобках: {name}, {scores}, {paragraph}, {sphere}, {value}, {part}, {total}, {left}, {url}.</p>"
        for row in store.all("SELECT * FROM texts ORDER BY key"):
            key = quote(row["key"])
            body += f'<article><form method="post" action="/admin/texts/{key}">{hidden()}<label><b>{esc(row["key"])}</b><textarea name="value" rows="5">{esc(row["value"])}</textarea></label><button>Сохранить</button></form></article>'
        return page("Тексты", body)

    @app.post("/admin/texts/{key}")
    async def save_text(key: str, request: Request):
        data = await form(request)
        if not store.one("SELECT 1 FROM texts WHERE key=?", (key,)):
            raise HTTPException(404)
        value = str(data.get("value", ""))[:10000]
        if not value.strip():
            raise HTTPException(400, "Текст не может быть пустым")
        if key.startswith("button_") and len(value) > 64:
            raise HTTPException(400, "Подпись кнопки слишком длинная")
        if key.startswith("menu_") and len(value) > 256:
            raise HTTPException(400, "Описание меню слишком длинное")
        required = {
            "welcome": {"name"}, "part_header": {"part", "total", "intro"},
            "score_prompt": {"sphere"}, "score_saved": {"value"},
            "result_intro": {"scores", "paragraph"}, "result_state": {"paragraph"},
            "booking_offer": {"url"},
            "compensation": {"sphere", "value"},
            "reminder": {"part", "total", "left"},
        }.get(key, set())
        present = {field for _, field, _, _ in string.Formatter().parse(value) if field}
        if not required.issubset(present):
            raise HTTPException(400, "Не удаляйте обязательные переменные шаблона")
        # Validate formatting variables against a harmless sample before saving.
        samples = {name: "пример" for name in ("name", "scores", "paragraph", "sphere", "value", "part", "total", "left", "url")}
        try:
            value.format(**samples)
        except (KeyError, ValueError, IndexError):
            raise HTTPException(400, "Некорректная переменная в тексте")
        store.execute("UPDATE texts SET value=? WHERE key=?", (value, key))
        return RedirectResponse("/admin/texts", status_code=303)

    @app.get("/admin/rules")
    async def rules(request: Request):
        auth(request)
        body = f'<article><form method="post" action="/admin/rules">{hidden()}'
        for row in store.all("SELECT * FROM rules ORDER BY key"):
            body += f'<label>{esc(row["key"])}<input type="number" min="0" max="10" step="0.25" name="{esc(row["key"])}" value="{row["value"]}" required></label>'
        body += '<button>Сохранить пороги</button></form></article>'
        return page("Пороговые значения", body)

    @app.post("/admin/rules")
    async def save_rules(request: Request):
        data = await form(request)
        values = {}
        for row in store.all("SELECT key FROM rules"):
            try:
                value = float(data[row["key"]])
            except (KeyError, ValueError, TypeError):
                raise HTTPException(400, "Неверный порог")
            if not 0 <= value <= 10:
                raise HTTPException(400, "Порог должен быть от 0 до 10")
            values[row["key"]] = value
        if values["low_max"] >= values["mid_max"]:
            raise HTTPException(400, "low_max должен быть меньше mid_max")
        for key, value in values.items():
            store.conn.execute("UPDATE rules SET value=? WHERE key=?", (value, key))
        store.conn.commit()
        return RedirectResponse("/admin/rules", status_code=303)

    @app.get("/admin/options")
    async def options(request: Request):
        auth(request)
        body = f'<article><form method="post" action="/admin/options">{hidden()}'
        for row in store.all("SELECT * FROM options ORDER BY key"):
            body += f'<label>{esc(row["key"])}<input name="{esc(row["key"])}" value="{esc(row["value"])}"></label>'
        body += '<button>Сохранить</button></form></article>'
        return page("Настройки", body)

    @app.post("/admin/options")
    async def save_options(request: Request):
        data = await form(request)
        for row in store.all("SELECT key FROM options"):
            key = row["key"]
            value = str(data.get(key, "")).strip()[:300]
            if key.endswith("_url") and value and not re.fullmatch(r"https://[^\s]+", value):
                raise HTTPException(400, "Ссылка должна начинаться с https://")
            store.set_option(key, value)
        return RedirectResponse("/admin/options", status_code=303)

    @app.get("/admin/users")
    async def users(request: Request):
        auth(request)
        query = request.query_params.get("q", "").strip()[:64]
        username_filter = request.query_params.get("has_username", "")
        if username_filter not in ("", "yes", "no"):
            raise HTTPException(400)
        conditions = []
        params = []
        if query:
            needle = query.removeprefix("@").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{needle}%"
            if query.startswith("@"):
                conditions.append("u.username LIKE ? ESCAPE '\\'")
                params.append(pattern)
            else:
                conditions.append("(u.username LIKE ? ESCAPE '\\' OR u.first_name LIKE ? ESCAPE '\\' OR CAST(u.telegram_id AS TEXT) LIKE ? ESCAPE '\\')")
                params.extend((pattern, pattern, pattern))
        if username_filter == "yes":
            conditions.append("u.username IS NOT NULL AND u.username != ''")
        elif username_filter == "no":
            conditions.append("(u.username IS NULL OR u.username = '')")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = store.all("""SELECT u.*,r.variant,r.completed_at,r.booked_at FROM users u
            LEFT JOIN runs r ON r.id=u.current_run""" + where +
                         " ORDER BY u.first_seen DESC LIMIT 500", tuple(params))
        options = ''.join(f'<option value="{value}"{" selected" if username_filter == value else ""}>{label}</option>'
                          for value, label in (("", "Все"), ("yes", "Есть username"), ("no", "Нет username")))
        body = (f'<form method="get" action="/admin/users"><label>Поиск по имени, ID или username '
                f'<input name="q" value="{esc(query)}" placeholder="@username"></label>'
                f'<label>Username <select name="has_username">{options}</select></label>'
                '<button>Найти</button> <a href="/admin/users">Сбросить</a></form>'
                '<p>Последние 500 пользователей по выбранному фильтру. Подтверждайте запись вручную только после проверки в системе бронирования, если она не присылает webhook.</p>'
                '<table><tr><th>ID</th><th>Имя</th><th>Username</th><th>Источник</th><th>Шаг</th><th>Результат</th><th>Телефон</th><th>Запись</th></tr>')
        for r in rows:
            booking = esc(r["booked_at"]) if r["booked_at"] else f'<form method="post" action="/admin/booked/{r["telegram_id"]}">{hidden()}<button>Подтвердить запись</button></form>'
            username = (r["username"] or "").removeprefix("@")
            username_cell = (f'<a href="https://t.me/{quote(username, safe="")}" target="_blank" '
                             f'rel="noopener noreferrer">@{esc(username)}</a>') if username else "—"
            body += f'<tr><td>{r["telegram_id"]}</td><td>{esc(r["first_name"])}</td><td>{username_cell}</td><td>{esc(r["source"])}</td><td>{esc(r["current_part"])} {esc(r["stage"])}</td><td>{esc(r["variant"])}</td><td>{esc(r["phone"])}</td><td>{booking}</td></tr>'
        return page("Пользователи", body + '</table>')

    @app.post("/admin/booked/{user_id}")
    async def mark_booked(user_id: int, request: Request):
        await form(request)
        user = store.user(user_id)
        if not user or not user["current_run"]:
            raise HTTPException(404)
        store.execute("UPDATE runs SET booked_at=COALESCE(booked_at,?) WHERE id=?", (now(), user["current_run"]))
        store.event(user_id, "booked", detail="verified_manually", activity=False)
        return RedirectResponse("/admin/users", status_code=303)

    @app.post("/booking-webhook")
    async def booking_webhook(request: Request):
        secret = config.booking_webhook_secret
        if not secret or not hmac.compare_digest(request.headers.get("x-booking-secret", ""), secret):
            raise HTTPException(401)
        payload = await request.json()
        try:
            user_id = int(payload["telegram_id"])
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400)
        user = store.user(user_id)
        if not user or not user["current_run"]:
            raise HTTPException(404)
        store.execute("UPDATE runs SET booked_at=COALESCE(booked_at,?) WHERE id=?", (now(), user["current_run"]))
        store.event(user_id, "booked", detail="booking_webhook", activity=False)
        return {"ok": True}

    @app.get("/admin/export")
    async def exports(request: Request):
        auth(request)
        links = ''.join(f'<li><a href="/admin/export/{table}.csv">{table}.csv</a></li>' for table in ("users", "runs", "scores", "events", "answers", "lead_magnets"))
        return page("Выгрузка", f'<article><ul>{links}<li><a href="/admin/backup.sqlite3">Полная резервная копия SQLite</a></li></ul></article>')

    @app.get("/admin/export/{table}.csv")
    async def export_csv(table: str, request: Request):
        auth(request)
        try:
            rows = store.export_rows(table)
        except ValueError:
            raise HTTPException(404)
        stream = io.StringIO()
        if rows:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return Response("\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'})

    @app.get("/admin/backup.sqlite3")
    async def backup(request: Request):
        auth(request)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "backup.sqlite3"
            store.backup(path)
            contents = path.read_bytes()
        return Response(contents, media_type="application/vnd.sqlite3",
                        headers={"Content-Disposition": 'attachment; filename="backup.sqlite3"'})

    return app
