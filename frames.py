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

from app.script_generator import get_video_script, get_script_for_photos, get_motion_prompts
from app.frame_images import generate_frame_image
from app.frame_photos import normalize_photo, describe_photos
from app.frame_video import animate_frames
from app.config import VOICE_PRESETS
from app.voice import generate_speech_with_timings
from app.frame_mixer import build_parts, mix, read_parts, load_mix, DEFAULT_MIX
from generate import pick_music_track

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

# ЦЕНА ВТОРОГО ШАГА — тоже предварительная.
# Себестоимость Veo Lite: 4 ₽ за секунду заказанного видео. Клипы Veo
# короче 4 секунд не бывают, поэтому заказываем 4 сек на кадр и лишнее
# отрезаем — то есть 16 / 20 / 24 секунды на ролик, около 64 / 80 / 96 ₽.
VIDEO_PRICES = {10: 170, 15: 220, 20: 260}

WORK_DIR = "work"
os.makedirs(WORK_DIR, exist_ok=True)

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
# Второй шаг: кадры → видео
# ---------------------------------------------------------------------------

class RenderBody(BaseModel):
    voice: str = "v_artem"
    music: str = "m_energetic"
    hook: bool = True
    subtitles: bool = True


@router.post("/api/frames/{draft_id}/render")
async def frames_render(draft_id: int, body: RenderBody, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required", "message": "Войдите, чтобы собрать видео"}, status_code=401)

    draft, images = _load_draft(draft_id, user["id"])
    if not draft:
        return JSONResponse({"error": "not_found"}, status_code=404)

    if draft["status"] != "ready":
        return JSONResponse(
            {"error": "not_ready", "message": "Кадры этого черновика недоступны"},
            status_code=400,
        )

    if not images:
        return JSONResponse({"error": "no_images", "message": "У черновика нет кадров"}, status_code=400)

    # Файлы кадров должны быть на диске: автоочистка могла их уже убрать,
    # а строки в базе оставить.
    missing = [i for i in images if not os.path.exists(i["image_path"].lstrip("/"))]
    if missing:
        return JSONResponse(
            {"error": "frames_gone", "message": "Кадры больше не хранятся на сервере — сделайте новые"},
            status_code=400,
        )

    duration = draft["duration"]
    price_kop = VIDEO_PRICES[duration] * 100

    db = get_db()
    try:
        # UNIQUE на draft_id не даст оплатить один и тот же черновик дважды,
        # даже если кнопку нажали в двух вкладках одновременно.
        existing = db.execute(
            "SELECT id, status FROM frame_videos WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        if existing:
            return JSONResponse(
                {"error": "already_started", "message": "Видео по этим кадрам уже собирается"},
                status_code=409,
            )

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

        cur = db.execute(
            "INSERT INTO generations (user_id, topic, source, duration, price_kop, status) "
            "VALUES (?, ?, 'frames', ?, ?, 'pending')",
            (user["id"], draft["topic"], duration, price_kop),
        )
        generation_id = cur.lastrowid

        try:
            db.execute(
                "INSERT INTO frame_videos (draft_id, user_id, generation_id, price_kop, status) "
                "VALUES (?, ?, ?, ?, 'pending')",
                (draft_id, user["id"], generation_id, price_kop),
            )
        except Exception:
            # Кто-то успел раньше — деньги не списываем.
            db.rollback()
            return JSONResponse(
                {"error": "already_started", "message": "Видео по этим кадрам уже собирается"},
                status_code=409,
            )

        db.commit()
    finally:
        db.close()

    asyncio.create_task(_render_video(draft_id, generation_id, draft, images, body))
    return {"ok": True, "price": price_kop // 100}


def _fail_render(draft_id: int, generation_id: int, message: str) -> None:
    """Помечает рендер ошибкой и возвращает деньги ровно один раз."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT user_id, price_kop, status FROM frame_videos WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if not row or row["status"] == "error":
            return

        cur = db.execute(
            "UPDATE frame_videos SET status = 'error', error_message = ? "
            "WHERE draft_id = ? AND status != 'error'",
            (message, draft_id),
        )
        if cur.rowcount == 1:
            db.execute(
                "UPDATE generations SET status = 'error', error_message = ? WHERE id = ?",
                (message, generation_id),
            )
            db.execute(
                "UPDATE users SET balance_kop = balance_kop + ? WHERE id = ?",
                (row["price_kop"], row["user_id"]),
            )
        db.commit()
    finally:
        db.close()


def _set_step(draft_id: int, step: int) -> None:
    db = get_db()
    try:
        db.execute("UPDATE frame_videos SET step = ? WHERE draft_id = ?", (step, draft_id))
        db.commit()
    finally:
        db.close()


async def _render_video(draft_id: int, generation_id: int, draft: dict,
                        images: list, opts: RenderBody):
    work_dir = os.path.join(WORK_DIR, f"frames_{draft_id}")
    os.makedirs(work_dir, exist_ok=True)

    async with _draft_semaphore:
        try:
            script = json.loads(draft.get("script_json") or "{}")
            voice_text = script.get("voice_text") or ""
            if not voice_text:
                raise RuntimeError("У черновика нет текста для озвучки")

            duration = float(draft["duration"])
            language = draft["language"]
            image_paths = [i["image_path"].lstrip("/") for i in images]
            descriptions = [i["prompt"] for i in images]

            # 1. Промпты движения
            _set_step(draft_id, 1)
            motion_prompts = await asyncio.to_thread(get_motion_prompts, descriptions, language)

            # 2. Озвучка — параллельно с видео, она заметно быстрее
            _set_step(draft_id, 2)
            audio_path = os.path.join(work_dir, "voice.mp3")
            lang_voices = VOICE_PRESETS.get(language, VOICE_PRESETS["ru"])
            preset = lang_voices.get(opts.voice, list(lang_voices.values())[0])

            voice_job = asyncio.to_thread(
                generate_speech_with_timings, voice_text, audio_path,
                preset["voice_id"], "eleven_v3",
            )

            # 3. Оживление кадров
            _set_step(draft_id, 3)
            clip_seconds = duration / len(image_paths)
            clips_job = animate_frames(image_paths, motion_prompts, work_dir, clip_seconds)

            (_, word_timings), clips = await asyncio.gather(voice_job, clips_job)

            # 4. Дорожки и сведение
            _set_step(draft_id, 4)
            music_path = pick_music_track(opts.music)
            parts_dir = os.path.join(_draft_dir(draft_id), "parts")
            final_path = os.path.join(work_dir, "final.mp4")

            await asyncio.to_thread(
                build_parts,
                video_files=clips,
                audio_file=audio_path,
                parts_dir=parts_dir,
                work_dir=work_dir,
                music_path=music_path,
                hook_text=script.get("hook_text", "") if opts.hook else "",
                target_duration=duration,
                word_timings=word_timings if opts.subtitles else None,
            )

            mix_volumes = dict(DEFAULT_MIX)
            await asyncio.to_thread(mix, parts_dir, final_path, mix_volumes)

            public_name = f"frames_{draft_id}_{generation_id}.mp4"
            public_path = os.path.join("media", public_name)
            shutil.move(final_path, public_path)

            expires_at = (datetime.utcnow() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            db = get_db()
            try:
                db.execute(
                    "UPDATE generations SET status = 'done', video_path = ?, expires_at = ?, "
                    "social_description = ?, hashtags = ? WHERE id = ?",
                    (f"/media/{public_name}", expires_at,
                     script.get("social_description", ""),
                     json.dumps(script.get("hashtags", []), ensure_ascii=False),
                     generation_id),
                )
                db.execute(
                    "UPDATE frame_videos SET status = 'done', step = 5, mix_json = ? "
                    "WHERE draft_id = ?",
                    (json.dumps(mix_volumes), draft_id),
                )
                db.commit()
            finally:
                db.close()

            print(f"[кадры→видео] Черновик {draft_id}: ролик готов", flush=True)

        except Exception as e:
            print(f"[кадры→видео] Черновик {draft_id} не собрался: {e}", flush=True)
            _fail_render(draft_id, generation_id, str(e))

        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


class MixBody(BaseModel):
    voice: int = 100
    music: int = 35
    veo: int = 0


@router.post("/api/frames/{draft_id}/mix")
async def frames_mix(draft_id: int, body: MixBody, request: Request):
    """
    Пересобирает готовый ролик с новыми громкостями.

    Денег не берём: кадры уже нарисованы, озвучка записана, за них
    заплачено. Здесь только заново сводится звук, видеодорожка копируется
    как есть — это несколько секунд работы ffmpeg.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    db = get_db()
    try:
        row = db.execute(
            "SELECT fv.generation_id, fv.status, g.video_path "
            "FROM frame_videos fv LEFT JOIN generations g ON g.id = fv.generation_id "
            "WHERE fv.draft_id = ? AND fv.user_id = ?",
            (draft_id, user["id"]),
        ).fetchone()
    finally:
        db.close()

    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if row["status"] != "done":
        return JSONResponse(
            {"error": "not_ready", "message": "Ролик ещё не готов"}, status_code=400
        )

    parts_dir = os.path.join(_draft_dir(draft_id), "parts")
    if not read_parts(parts_dir):
        return JSONResponse(
            {"error": "parts_gone", "message": "Дорожки ролика больше не хранятся на сервере"},
            status_code=400,
        )

    volumes = load_mix({"voice": body.voice, "music": body.music, "veo": body.veo})

    # Пишем в новый файл, а не поверх старого: браузер держит прежний
    # открытым, и перезапись на лету даёт битое видео в плеере.
    public_name = f"frames_{draft_id}_{row['generation_id']}_{int(datetime.utcnow().timestamp())}.mp4"
    public_path = os.path.join("media", public_name)

    try:
        await asyncio.to_thread(mix, parts_dir, public_path, volumes)
    except Exception as e:
        print(f"[дорожки] Пересборка черновика {draft_id} не удалась: {e}", flush=True)
        return JSONResponse(
            {"error": "mix_failed", "message": "Не удалось пересобрать ролик"}, status_code=500
        )

    old_path = (row["video_path"] or "").lstrip("/")

    db = get_db()
    try:
        db.execute(
            "UPDATE generations SET video_path = ? WHERE id = ?",
            (f"/media/{public_name}", row["generation_id"]),
        )
        db.execute(
            "UPDATE frame_videos SET mix_json = ? WHERE draft_id = ?",
            (json.dumps(volumes), draft_id),
        )
        db.commit()
    finally:
        db.close()

    if old_path and os.path.exists(old_path) and old_path != public_path:
        try:
            os.remove(old_path)
        except Exception:
            pass

    return {"ok": True, "video_url": f"/media/{public_name}", "mix": volumes}


@router.get("/api/frames/voices")
async def frames_voices():
    """Голоса для выпадающего списка на странице — те же, что в обычном создании."""
    return {
        lang: [{"key": k, "title": v["title"], "description": v["description"]}
               for k, v in voices.items()]
        for lang, voices in VOICE_PRESETS.items()
    }


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

    db = get_db()
    try:
        video = db.execute(
            "SELECT fv.status, fv.step, fv.error_message, fv.mix_json, g.video_path, "
            "       g.social_description, g.hashtags "
            "FROM frame_videos fv LEFT JOIN generations g ON g.id = fv.generation_id "
            "WHERE fv.draft_id = ?",
            (draft_id,),
        ).fetchone()
    finally:
        db.close()

    video_block = None
    if video:
        hashtags = []
        if video["hashtags"]:
            try:
                hashtags = json.loads(video["hashtags"])
            except Exception:
                hashtags = []

        # Ссылки на отдельные дорожки — по ним браузер проигрывает ролик
        # с живой регулировкой громкости, не дёргая сервер.
        tracks = None
        parts_dir = os.path.join(_draft_dir(draft_id), "parts")
        manifest = read_parts(parts_dir)
        if manifest:
            base = f"/media/frames/{draft_id}/parts"
            music = manifest.get("music")
            tracks = {
                "video": f"{base}/{manifest['video']}",
                "voice": f"{base}/{manifest['voice']}",
                "veo": f"{base}/{manifest['veo']}" if manifest.get("veo") else None,
                "music": f"/{music}" if music else None,
                "duration": manifest.get("duration"),
            }

        video_block = {
            "status": video["status"],
            "step": video["step"],
            "error": video["error_message"],
            "video_url": video["video_path"],
            "social_description": video["social_description"] or "",
            "hashtags": hashtags,
            "mix": load_mix(video["mix_json"]),
            "tracks": tracks,
        }

    return {
        "id": draft["id"],
        "status": draft["status"],
        "source": draft["source"],
        "duration": draft["duration"],
        "language": draft["language"],
        "topic": draft["topic"],
        "frames_count": draft["frames_count"],
        "error": draft["error_message"],
        "hook_text": script.get("hook_text", ""),
        "voice_text": script.get("voice_text", ""),
        "video_price": VIDEO_PRICES[draft["duration"]],
        "video": video_block,
        "images": images,
    }


# ---------------------------------------------------------------------------
# Восстановление после перезапуска
# ---------------------------------------------------------------------------

STALE_DRAFT_MINUTES = 40

# Veo на шесть кадров работает заметно дольше, чем генерация картинок,
# поэтому порог отдельный и с запасом.
STALE_RENDER_MINUTES = 90


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

    # То же самое для второго шага: сборка ролика тоже живёт в asyncio-задаче
    # и тоже исчезает вместе с процессом.
    db = get_db()
    try:
        stuck = db.execute(
            f"SELECT draft_id, generation_id FROM frame_videos WHERE status = 'pending' "
            f"AND created_at <= datetime('now', '-{STALE_RENDER_MINUTES} minutes')"
        ).fetchall()
    finally:
        db.close()

    for row in stuck:
        _fail_render(row["draft_id"], row["generation_id"],
                     "Сборка прервалась при перезапуске сервиса — деньги возвращены")

    if stuck:
        print(f"[кадры→видео] Возвращено денег за прерванные сборки: {len(stuck)}", flush=True)
