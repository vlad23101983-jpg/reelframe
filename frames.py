"""
Kinomotor — frames.py
Тариф "Кадры": человек сперва получает картинки, смотрит на них и только
потом отправляет их в видео.

ЭТАП 1 (этот файл): картинки. Второй шаг — отправка в Veo — появится
отдельно, когда будет понятно, что картинки устраивают.

Почему отдельный роутер, а не ветка в generate.py: между картинками и
видео человек уходит думать, и весь процесс перестаёт быть одной длинной
задачей в памяти. Это другая механика, и мешать её с работающим
конвейером обычных тарифов незачем.
"""

import os
import re
import json
import uuid
import shutil
import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

from db import get_db
from auth import get_current_user
from pages import templates

from app.script_generator import get_video_script, get_script_for_photos
from app.frame_images import generate_frame_image
from app.frame_photos import normalize_photo, describe_photos

router = APIRouter(tags=["frames"])

FRAMES_DIR = os.path.join("media", "frames")
UPLOADS_DIR = os.path.join("media", "uploads")
os.makedirs(FRAMES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Сколько кадров на ролик. Жёстко, без диапазона: на втором шаге каждый
# кадр станет отдельным оплаченным клипом Veo, поэтому число должно
# совпадать точно.
FRAMES_COUNT = {10: 4, 15: 5, 20: 6}

ALLOWED_DURATIONS = tuple(FRAMES_COUNT)

# ЦЕНЫ ЗА ЭТАП КАРТИНОК — предварительные, уточнить перед запуском.
# Берём только за картинки; видео будет оплачиваться отдельно, когда
# человек нажмёт "отправить в видео". Так неудачная раскадровка стоит
# человеку немного, а не всей стоимости ролика.
FRAME_PRICES = {
    "generate": {10: 30, 15: 40, 20: 50},   # рисует ИИ
    "upload":   {10: 10, 15: 15, 20: 20},   # свои фото: платим за разбор снимков
}

ALLOWED_SOURCES = tuple(FRAME_PRICES)

# Черновик живёт трое суток: человеку нужно время подумать, но вечно
# держать картинки на диске нельзя.
DRAFT_TTL_HOURS = 72

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp")

UPLOAD_ID_RE = re.compile(r"^upload_[0-9a-f]{8}$")

MAX_TOPIC_LENGTH = 500

# Столько же одновременных генераций, сколько у обычных роликов —
# чтобы новый тариф не съел сервер.
MAX_CONCURRENT_DRAFTS = 2
_draft_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DRAFTS)


# ---------------------------------------------------------------------------
# Страница
# ---------------------------------------------------------------------------

@router.get("/frames", response_class=HTMLResponse)
async def frames_page(request: Request):
    return templates.TemplateResponse("frames.html", {"request": request})


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

def _refund(draft_id: int, message: str) -> None:
    """
    Помечает черновик ошибкой и возвращает деньги.

    Возврат делается ровно один раз: условие status != 'error' в UPDATE
    гарантирует, что двойного зачисления не будет, даже если сюда зайдут
    дважды (например, задача упала и её же добила проверка при старте).
    """
    db = get_db()
    try:
        row = db.execute(
            "SELECT user_id, price_kop, status FROM frame_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        if not row or row["status"] == "error":
            return

        cur = db.execute(
            "UPDATE frame_drafts SET status = 'error', error_message = ? "
            "WHERE id = ? AND status != 'error'",
            (message, draft_id),
        )
        if cur.rowcount == 1 and row["price_kop"]:
            db.execute(
                "UPDATE users SET balance_kop = balance_kop + ? WHERE id = ?",
                (row["price_kop"], row["user_id"]),
            )
        db.commit()
    finally:
        db.close()


def _draft_dir(draft_id: int) -> str:
    return os.path.join(FRAMES_DIR, str(draft_id))


def _load_draft(draft_id: int, user_id: int):
    """Черновик вместе с кадрами — только свой, чужой не отдаём."""
    db = get_db()
    try:
        draft = db.execute(
            "SELECT * FROM frame_drafts WHERE id = ? AND user_id = ?",
            (draft_id, user_id),
        ).fetchone()
        if not draft:
            return None, []
        images = db.execute(
            "SELECT position, image_path, prompt FROM frame_images "
            "WHERE draft_id = ? ORDER BY position ASC",
            (draft_id,),
        ).fetchall()
        return dict(draft), [dict(i) for i in images]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Загрузка фотографий
# ---------------------------------------------------------------------------

@router.post("/api/frames/upload")
async def frames_upload(request: Request, files: list[UploadFile] = File(...)):
    """
    Принимает фотографии. Количество проверяется позже, при создании
    черновика: здесь ещё неизвестно, какую длительность выберет человек.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required", "message": "Войдите, чтобы загрузить фото"}, status_code=401)

    if len(files) > max(FRAMES_COUNT.values()):
        return JSONResponse(
            {"error": "too_many", "message": f"Не больше {max(FRAMES_COUNT.values())} фото"},
            status_code=400,
        )

    upload_id = f"upload_{uuid.uuid4().hex[:8]}"
    upload_dir = os.path.join(UPLOADS_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)

    saved = 0
    for index, f in enumerate(files):
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ALLOWED_PHOTO_EXT:
            continue

        dest = os.path.join(upload_dir, f"{index:02d}{ext}")
        written = 0
        with open(dest, "wb") as out:
            # Читаем кусками и обрываем на пределе: иначе одним запросом
            # можно залить на сервер сколько угодно.
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    break
                out.write(chunk)

        if written > MAX_UPLOAD_BYTES:
            os.remove(dest)
            shutil.rmtree(upload_dir, ignore_errors=True)
            return JSONResponse(
                {"error": "too_large", "message": "Каждое фото — не больше 15 МБ"},
                status_code=400,
            )
        saved += 1

    if saved == 0:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return JSONResponse(
            {"error": "no_images", "message": "Подходящих изображений не нашлось"},
            status_code=400,
        )

    return {"upload_id": upload_id, "count": saved}


# ---------------------------------------------------------------------------
# Создание черновика
# ---------------------------------------------------------------------------

class CreateDraftBody(BaseModel):
    source: str = "generate"
    duration: int = 15
    topic: str = ""
    language: str = "ru"
    upload_id: str = ""


@router.post("/api/frames/create")
async def frames_create(body: CreateDraftBody, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required", "message": "Войдите, чтобы создать кадры"}, status_code=401)

    if body.source not in ALLOWED_SOURCES:
        return JSONResponse({"error": "invalid_source", "message": "Неизвестный источник кадров"}, status_code=400)

    if body.duration not in ALLOWED_DURATIONS:
        return JSONResponse({"error": "invalid_duration", "message": "Длительность может быть 10, 15 или 20 секунд"}, status_code=400)

    frames_count = FRAMES_COUNT[body.duration]
    topic = (body.topic or "").strip()[:MAX_TOPIC_LENGTH]
    language = body.language if body.language in ("ru", "en") else "ru"

    photo_files = []
    if body.source == "upload":
        if not UPLOAD_ID_RE.match(body.upload_id or ""):
            return JSONResponse({"error": "invalid_upload_id", "message": "Загруженные фото не найдены"}, status_code=400)

        upload_dir = os.path.join(UPLOADS_DIR, body.upload_id)
        if not os.path.isdir(upload_dir):
            return JSONResponse({"error": "invalid_upload_id", "message": "Загруженные фото не найдены"}, status_code=400)

        photo_files = sorted(
            os.path.join(upload_dir, f)
            for f in os.listdir(upload_dir)
            if f.lower().endswith(ALLOWED_PHOTO_EXT)
        )
        if len(photo_files) != frames_count:
            return JSONResponse(
                {
                    "error": "wrong_photo_count",
                    "message": f"Для {body.duration} секунд нужно ровно {frames_count} фото, "
                               f"а выбрано {len(photo_files)}",
                },
                status_code=400,
            )
    elif not topic:
        return JSONResponse({"error": "no_topic", "message": "Напишите, какие кадры нужны"}, status_code=400)

    price_kop = FRAME_PRICES[body.source][body.duration] * 100

    db = get_db()
    try:
        # Списываем одним запросом с проверкой остатка. Если сделать
        # "прочитать баланс, потом вычесть", два одновременных запроса
        # успеют пройти проверку оба и уведут баланс в минус.
        cur = db.execute(
            "UPDATE users SET balance_kop = balance_kop - ? WHERE id = ? AND balance_kop >= ?",
            (price_kop, user["id"], price_kop),
        )
        if cur.rowcount != 1:
            db.rollback()
            return JSONResponse(
                {"error": "insufficient_balance", "message": "Недостаточно средств на балансе"},
                status_code=402,
            )

        expires_at = (datetime.utcnow() + timedelta(hours=DRAFT_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        cur = db.execute(
            "INSERT INTO frame_drafts "
            "(user_id, topic, source, duration, language, frames_count, price_kop, status, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (user["id"], topic, body.source, body.duration, language, frames_count, price_kop, expires_at),
        )
        draft_id = cur.lastrowid
        db.commit()
    finally:
        db.close()

    asyncio.create_task(_build_draft(draft_id, body.source, topic, body.duration,
                                     language, frames_count, photo_files, body.upload_id))

    return {"draft_id": draft_id, "price": price_kop // 100, "frames_count": frames_count}


# ---------------------------------------------------------------------------
# Фоновая сборка кадров
# ---------------------------------------------------------------------------

async def _build_draft(draft_id: int, source: str, topic: str, duration: int,
                       language: str, frames_count: int, photo_files: list, upload_id: str):
    draft_dir = _draft_dir(draft_id)
    os.makedirs(draft_dir, exist_ok=True)

    async with _draft_semaphore:
        try:
            if source == "upload":
                images, script = await _build_from_photos(
                    draft_dir, photo_files, duration, topic, language
                )
            else:
                images, script = await _build_from_prompts(
                    draft_dir, topic, duration, frames_count, language
                )

            db = get_db()
            try:
                for position, (path, prompt) in enumerate(images):
                    db.execute(
                        "INSERT INTO frame_images (draft_id, position, image_path, prompt) "
                        "VALUES (?, ?, ?, ?)",
                        (draft_id, position, path, prompt),
                    )
                db.execute(
                    "UPDATE frame_drafts SET status = 'ready', script_json = ? WHERE id = ?",
                    (json.dumps(script, ensure_ascii=False), draft_id),
                )
                db.commit()
            finally:
                db.close()

            print(f"[кадры] Черновик {draft_id} готов: кадров {len(images)}", flush=True)

        except Exception as e:
            print(f"[кадры] Черновик {draft_id} не собрался: {e}", flush=True)
            shutil.rmtree(draft_dir, ignore_errors=True)
            _refund(draft_id, str(e))

        finally:
            # Исходники больше не нужны: нормализованные кадры уже лежат
            # в папке черновика. Чужие фотографии на диске не держим.
            if source == "upload" and UPLOAD_ID_RE.match(upload_id or ""):
                shutil.rmtree(os.path.join(UPLOADS_DIR, upload_id), ignore_errors=True)


async def _build_from_prompts(draft_dir: str, topic: str, duration: int,
                              frames_count: int, language: str):
    """Кадры рисует ИИ: сперва сценарий, из него промпты сцен, по ним картинки."""
    script = await asyncio.to_thread(
        get_video_script, topic, duration, frames_count, "images", language
    )

    prompts = script.get("keywords") or []
    if len(prompts) < frames_count:
        raise RuntimeError(
            f"Сценарист вернул {len(prompts)} описаний кадров вместо {frames_count}"
        )
    prompts = prompts[:frames_count]

    async def one(position: int, prompt: str):
        path = os.path.join(draft_dir, f"{position:02d}.png")
        await generate_frame_image(prompt, path)
        return f"/{path}", prompt

    images = await asyncio.gather(*[one(i, p) for i, p in enumerate(prompts)])
    return list(images), script


async def _build_from_photos(draft_dir: str, photo_files: list, duration: int,
                             topic: str, language: str):
    """Кадры — фото пользователя: приводим к формату, смотрим их, пишем текст."""
    async def one(position: int, photo: str):
        path = os.path.join(draft_dir, f"{position:02d}.png")
        await normalize_photo(photo, path)
        return path

    paths = await asyncio.gather(*[one(i, p) for i, p in enumerate(photo_files)])

    descriptions = await describe_photos(list(paths), language)

    script = await asyncio.to_thread(
        get_script_for_photos, descriptions, duration, topic, language
    )

    images = [(f"/{p}", d) for p, d in zip(paths, descriptions)]
    return images, script


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------

@router.get("/api/frames/list")
async def frames_list(request: Request):
    """Последние черновики — чтобы человек нашёл свои кадры, вернувшись позже."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, topic, source, duration, status, created_at "
            "FROM frame_drafts WHERE user_id = ? ORDER BY id DESC LIMIT 10",
            (user["id"],),
        ).fetchall()
    finally:
        db.close()

    return {"drafts": [dict(r) for r in rows]}


@router.get("/api/frames/{draft_id}")
async def frames_get(draft_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    draft, images = _load_draft(draft_id, user["id"])
    if not draft:
        return JSONResponse({"error": "not_found"}, status_code=404)

    script = {}
    if draft.get("script_json"):
        try:
            script = json.loads(draft["script_json"])
        except Exception:
            script = {}

    return {
        "id": draft["id"],
        "status": draft["status"],
        "source": draft["source"],
        "duration": draft["duration"],
        "topic": draft["topic"],
        "frames_count": draft["frames_count"],
        "error": draft["error_message"],
        "hook_text": script.get("hook_text", ""),
        "voice_text": script.get("voice_text", ""),
        "images": images,
    }


# ---------------------------------------------------------------------------
# Восстановление после перезапуска
# ---------------------------------------------------------------------------

STALE_DRAFT_MINUTES = 40


def recover_stuck_drafts() -> None:
    """
    Вызывается при старте приложения.

    Сборка кадров живёт в asyncio-задаче. Если сервис перезапустили на
    середине, задача исчезает, а черновик навсегда остаётся в 'pending' —
    человек заплатил и смотрит на вечное "готовим кадры". Возвращаем деньги
    и честно помечаем ошибкой.
    """
    db = get_db()
    try:
        rows = db.execute(
            f"SELECT id FROM frame_drafts WHERE status = 'pending' "
            f"AND created_at <= datetime('now', '-{STALE_DRAFT_MINUTES} minutes')"
        ).fetchall()
    finally:
        db.close()

    for row in rows:
        _refund(row["id"], "Генерация прервалась при перезапуске сервиса — деньги возвращены")

    if rows:
        print(f"[кадры] Возвращено денег за прерванные черновики: {len(rows)}", flush=True)
