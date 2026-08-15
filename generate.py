"""
Kinomotor — generate.py
Всё, что связано с созданием видео: цены, очередь задач, сама генерация.
"""

import os
import uuid
import glob
import random
import shutil
import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from db import get_db
from auth import get_current_user

from app.config import VOICE_PRESETS
from app.script_generator import get_video_script
from app.image_generator import generate_imagen_clip
from app.zimage_generator import generate_zimage_clip, warm_up as warm_up_zimage
from app.photo_processor import prepare_user_photo
from app.veo_generator import generate_veo_clips, get_scenes_count
from app.assembler import assemble_final_video
from app.voice import generate_speech

router = APIRouter(prefix="/api", tags=["generate"])

os.makedirs("media", exist_ok=True)
os.makedirs("work", exist_ok=True)
os.makedirs(os.path.join("media", "music"), exist_ok=True)

PRICES = {
    "ai":     {10: 80,  15: 120, 20: 160},
    "upload": {10: 40,  15: 50,  20: 60},
    "veo":    {10: 180, 15: 240, 20: 280},
    "zimage": {10: 50,  15: 70,  20: 100},  # Kinom 1.7 — собственная модель на RunPod
}

PHOTO_RANGE = {10: (3, 5), 15: (4, 7), 20: (5, 8)}

MAX_CONCURRENT_GENERATIONS = 2
generation_semaphore = asyncio.Semaphore(MAX_CONCURRENT_GENERATIONS)

TASKS = {}


class GenerateRequest(BaseModel):
    source: str = "ai"
    duration: str = "15"
    voice: str = "v_artem"
    music: str = "m_energetic"
    hook: bool = True
    topic: str = ""
    upload_id: str = ""
    language: str = "ru"


@router.post("/generate")
async def api_generate(payload: GenerateRequest, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required", "message": "Войдите, чтобы создать видео"}, status_code=401)

    price = PRICES.get(payload.source, PRICES["ai"]).get(int(payload.duration), 0)
    price_kop = price * 100

    if user["balance_kop"] < price_kop:
        return JSONResponse({"error": "insufficient_balance", "message": "Недостаточно средств на балансе"}, status_code=402)
    db = get_db()
    try:
        db.execute("UPDATE users SET balance_kop = balance_kop - ? WHERE id = ?", (price_kop, user["id"]))
        cur = db.execute(
            "INSERT INTO generations (user_id, topic, source, duration, price_kop, status) VALUES (?, ?, ?, ?, ?, 'pending')",
            (user["id"], payload.topic, payload.source, int(payload.duration), price_kop),
        )
        generation_id = cur.lastrowid
        db.commit()
    finally:
        db.close()
    task_id = f"task_{uuid.uuid4().hex[:10]}"
    TASKS[task_id] = {"status": "queued", "step": 0, "video_url": None, "error": None}
    asyncio.create_task(run_generation(task_id, payload, generation_id))
    return {"task_id": task_id, "price": price}


@router.get("/status/{task_id}")
async def api_status(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse({"error": "task not found"}, status_code=404)
    return task


@router.post("/upload")
async def api_upload(request: Request, files: list[UploadFile] = File(...)):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required", "message": "Войдите, чтобы загрузить фото"}, status_code=401)

    upload_id = f"upload_{uuid.uuid4().hex[:8]}"
    upload_dir = os.path.join("media", "uploads", upload_id)
    os.makedirs(upload_dir, exist_ok=True)

    saved = 0
    for index, f in enumerate(files):
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        dest = os.path.join(upload_dir, f"{index:02d}{ext}")
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved += 1

    if saved == 0:
        return JSONResponse({"error": "нет подходящих изображений"}, status_code=400)

    return {"upload_id": upload_id, "count": saved}


_music_rotation_index = {}  # style -> индекс последнего использованного трека


def pick_music_track(music_key: str):
    """
    Берёт следующий трек ПО КРУГУ из библиотеки media/music (не случайно —
    так треки не повторяются подряд). После последнего трека снова начинает с первого.
    Файлы называются по стилю: calm_1.mp3, energetic_1.mp3, business_1.mp3.
    Возвращает None, если музыка не нужна или треков для стиля нет.
    """
    if music_key == "m_none":
        return None

    style = music_key.replace("m_", "")
    candidates = sorted(glob.glob(os.path.join("media", "music", f"{style}_*.mp3")))

    if not candidates:
        print(f"Нет треков для стиля '{style}' — ролик будет без музыки", flush=True)
        return None

    next_index = (_music_rotation_index.get(style, -1) + 1) % len(candidates)
    _music_rotation_index[style] = next_index
    track = candidates[next_index]
    print(f"Музыка: {os.path.basename(track)}", flush=True)
    return track


async def run_generation(task_id: str, payload: GenerateRequest, generation_id: int):
    duration = int(payload.duration)
    work_dir = os.path.join("work", task_id)
    os.makedirs(work_dir, exist_ok=True)

    if generation_semaphore.locked():
        TASKS[task_id].update(status="queued", step=0)

    async with generation_semaphore:
        try:
            TASKS[task_id].update(status="running", step=1)
            is_veo = payload.source == "veo"

            if payload.source == "zimage":
                asyncio.create_task(warm_up_zimage())

            if is_veo:
                scenes = get_scenes_count(duration)
                video_type = "veo"
            else:
                scenes = {10: 4, 15: 5, 20: 6}[duration]
                video_type = "images"

            script = await asyncio.to_thread(
                get_video_script, payload.topic, duration, scenes, video_type, payload.language
            )

            voice_text = script["voice_text"]
            hook_text = script.get("hook_text", "")
            keywords = script.get("keywords", [])

            audio_file_path = os.path.join(work_dir, "voice.mp3")

            music_file_path = pick_music_track(payload.music)

            TASKS[task_id].update(step=2)
            lang_voices = VOICE_PRESETS.get(payload.language, VOICE_PRESETS["ru"])
            voice_preset = lang_voices.get(payload.voice, list(lang_voices.values())[0])
            await asyncio.to_thread(
                generate_speech, voice_text, audio_file_path,
                voice_preset["voice_id"], "eleven_v3"
            )

            TASKS[task_id].update(step=3)

            if payload.source == "veo":
                video_files = await generate_veo_clips(
                    keywords=keywords,
                    duration=duration,
                    work_dir=work_dir
                )
                generation_tasks = []
            elif payload.source == "upload":
                upload_dir = os.path.join("media", "uploads", payload.upload_id)
                if not os.path.isdir(upload_dir):
                    raise RuntimeError("Загруженные фото не найдены")

                photo_files = sorted([
                    os.path.join(upload_dir, f)
                    for f in os.listdir(upload_dir)
                    if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                ])
                if not photo_files:
                    raise RuntimeError("Загруженные фото не найдены")

                shots_count = len(photo_files)
                clip_duration = float(duration) / float(shots_count)
                video_files = [os.path.join(work_dir, f"vid_{i}.mp4") for i in range(shots_count)]

                generation_tasks = [
                    prepare_user_photo(
                        photo_path=photo,
                        output_clip_path=out_p,
                        duration=clip_duration,
                        motion_style_index=i
                    )
                    for i, (photo, out_p) in enumerate(zip(photo_files, video_files))
                ]
            elif payload.source == "zimage":
                shots_count = len(keywords)
                clip_duration = float(duration) / float(shots_count)
                video_files = [os.path.join(work_dir, f"vid_{i}.mp4") for i in range(shots_count)]

                generation_tasks = [
                    generate_zimage_clip(
                        prompt=kw,
                        output_clip_path=out_p,
                        duration=clip_duration,
                        motion_style_index=i
                    )
                    for i, (kw, out_p) in enumerate(zip(keywords, video_files))
                ]
            else:
                shots_count = len(keywords)
                clip_duration = float(duration) / float(shots_count)
                video_files = [os.path.join(work_dir, f"vid_{i}.mp4") for i in range(shots_count)]

                generation_tasks = [
                    generate_imagen_clip(
                        prompt=kw,
                        output_clip_path=out_p,
                        duration=clip_duration,
                        motion_style_index=i
                    )
                    for i, (kw, out_p) in enumerate(zip(keywords, video_files))
                ]

            await asyncio.gather(*generation_tasks)

            TASKS[task_id].update(step=4)
            final_video_path = os.path.join(work_dir, "final.mp4")
            await asyncio.to_thread(
                assemble_final_video,
                video_files=video_files,
                audio_file=audio_file_path,
                output_path=final_video_path,
                work_dir=work_dir,
                bg_music_path=music_file_path,
                hook_text=hook_text if payload.hook else "",
                target_duration=float(duration),
            )
            public_name = f"{task_id}.mp4"
            public_path = os.path.join("media", public_name)
            shutil.move(final_video_path, public_path)
            TASKS[task_id].update(
                status="done",
                step=5,
                video_url=f"/media/{public_name}",
                social_description=script.get("social_description", ""),
                hashtags=script.get("hashtags", []),
            )
            db = get_db()
            try:
                expires_at = (datetime.utcnow() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
                db.execute(
                    "UPDATE generations SET status = 'done', video_path = ?, expires_at = ? WHERE id = ?",
                    (f"/media/{public_name}", expires_at, generation_id),
                )
                db.commit()
            finally:
                db.close()
        except Exception as e:
            print(f"Ошибка генерации [{task_id}]: {e}")
            TASKS[task_id].update(status="error", error=str(e))
            db = get_db()
            try:
                row = db.execute("SELECT user_id, price_kop FROM generations WHERE id = ?", (generation_id,)).fetchone()
                db.execute("UPDATE generations SET status = 'error', error_message = ? WHERE id = ?", (str(e), generation_id))
                if row:
                    db.execute("UPDATE users SET balance_kop = balance_kop + ? WHERE id = ?", (row["price_kop"], row["user_id"]))
                db.commit()
            finally:
                db.close()

        finally:
            if os.path.exists(work_dir):
                try:
                    shutil.rmtree(work_dir)
                except Exception:
                    pass

            if payload.source == "upload" and payload.upload_id:
                up_dir = os.path.join("media", "uploads", payload.upload_id)
                if os.path.isdir(up_dir):
                    try:
                        shutil.rmtree(up_dir)
                    except Exception:
                        pass
