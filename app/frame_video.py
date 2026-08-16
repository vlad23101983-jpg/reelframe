"""
Kinomotor — app/frame_video.py
Оживление утверждённых кадров через Veo (image-to-video).

Отличие от veo_generator.py: там Veo придумывает картинку сам по тексту.
Здесь картинка уже есть и человек её утвердил — Veo получает её первым
кадром и только добавляет движение. Поэтому и промпт другой: он описывает
не сюжет, а то, что должно ожить.

Ключи и обработку лимитов берём из veo_generator — они общие.
"""

import os
import time
import asyncio
import subprocess

from google.genai import types as genai_types

from app.veo_generator import get_next_client, _is_rate_limit_error, API_KEYS

# Какой моделью оживлять кадры.
#   veo-3.1-lite-generate-preview  — самая дешёвая, ~$0.05 за секунду 720p
#   veo-3.1-fast-generate-preview  — вдвое дороже
#   gemini-omni-flash-preview      — Google советует её по умолчанию: лучше
#                                    держит связность и постоянство персонажа
#                                    между кадрами, но примерно вдвое дороже
# Меняется одной строкой, если кадры в ролике будут разъезжаться между собой.
VIDEO_MODEL = "veo-3.1-lite-generate-preview"

# Короче четырёх секунд Veo не умеет. Заказываем минимум, лишнее отрезаем.
VEO_MIN_CLIP_SECONDS = 4

MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 60
POLL_INTERVAL_SECONDS = 12
MAX_WAIT_SECONDS = 360


def _sync_animate_frame(image_path: str, motion_prompt: str, output_path: str) -> str:
    """Отправляет кадр в Veo и сохраняет получившийся клип."""
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
    start_frame = genai_types.Image(image_bytes=image_bytes, mime_type=mime)

    print(f"[кадры→видео] Оживляю {os.path.basename(image_path)}: {motion_prompt[:60]}...", flush=True)

    operation = None
    client = None
    delay = INITIAL_RETRY_DELAY

    for attempt in range(MAX_RETRIES):
        client = get_next_client()
        try:
            operation = client.models.generate_videos(
                model=VIDEO_MODEL,
                prompt=motion_prompt,
                image=start_frame,
                config=genai_types.GenerateVideosConfig(
                    aspect_ratio="9:16",
                    duration_seconds=str(VEO_MIN_CLIP_SECONDS),
                    resolution="720p",
                ),
            )
            break
        except Exception as e:
            if _is_rate_limit_error(e) and attempt < MAX_RETRIES - 1:
                print(f"[кадры→видео] Лимит 429. Меняю ключ, жду {delay} сек...", flush=True)
                time.sleep(delay)
            else:
                raise

    if operation is None:
        raise RuntimeError("Не удалось отправить кадр в Veo")

    start_time = time.time()
    while not operation.done:
        if time.time() - start_time > MAX_WAIT_SECONDS:
            raise TimeoutError(f"Veo не ответил за {int(time.time() - start_time)} сек")
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            operation = client.operations.get(operation)
        except Exception as e:
            if _is_rate_limit_error(e):
                print("[кадры→видео] Лимит при опросе статуса. Пауза 30 сек...", flush=True)
                time.sleep(30)
            else:
                raise

    generated = operation.response.generated_videos[0]
    client.files.download(file=generated.video)
    generated.video.save(output_path)

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Veo вернул пустой клип")

    print(f"[кадры→видео] Готов клип за {int(time.time() - start_time)} сек", flush=True)
    return output_path


def _trim(src: str, dst: str, seconds: float) -> str:
    """
    Отрезает клип до нужной длины, оставляя начало.

    Начало, а не середину или конец: картинка — это нулевой кадр, и чем
    дальше от него, тем сильнее Veo уводит изображение от утверждённого.
    Отрезая хвост, мы оставляем то, что ближе всего к одобренному кадру.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-t", f"{seconds:.3f}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-an", dst],
        capture_output=True, check=True,
    )
    if not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise RuntimeError("Не удалось обрезать клип")
    return dst


async def animate_frames(image_paths: list, motion_prompts: list,
                         work_dir: str, clip_seconds: float) -> list:
    """
    Оживляет все кадры и подгоняет клипы под нужную длину.
    Возвращает список путей к готовым клипам в исходном порядке.
    """
    async def one(index: int, image_path: str, prompt: str):
        raw = os.path.join(work_dir, f"veo_raw_{index}.mp4")
        await asyncio.to_thread(_sync_animate_frame, image_path, prompt, raw)
        trimmed = os.path.join(work_dir, f"clip_{index}.mp4")
        await asyncio.to_thread(_trim, raw, trimmed, clip_seconds)
        try:
            os.remove(raw)
        except Exception:
            pass
        return trimmed

    jobs = [one(i, img, pr) for i, (img, pr) in enumerate(zip(image_paths, motion_prompts))]

    # На нескольких ключах Veo можно генерировать параллельно, на одном —
    # по очереди, иначе первый же клип упрётся в лимит и утянет остальные.
    if len(API_KEYS) > 1:
        return list(await asyncio.gather(*jobs))

    results = []
    for job in jobs:
        results.append(await job)
    return results
