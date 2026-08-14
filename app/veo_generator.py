"""
Генерация живых AI-видео через Veo с ротацией API-ключей.
"""

import os
import time
import asyncio
import itertools
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

load_dotenv()

raw_keys = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY")
if not raw_keys:
    raise ValueError("API-ключи не найдены в .env файле!")

API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
key_iterator = itertools.cycle(API_KEYS)


def get_next_client():
    """Возвращает клиент со следующим по очереди ключом."""
    key = next(key_iterator)
    return genai.Client(api_key=key)


VEO_CLIP_PLAN = {
    10: [4, 6],
    15: [4, 4, 4, 4],
    20: [4, 4, 4, 4, 4],
}

MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 60


def _is_rate_limit_error(exc: Exception) -> bool:
    error_text = str(exc)
    return "RESOURCE_EXHAUSTED" in error_text or "429" in error_text


def _sync_generate_single_clip(prompt: str, duration_seconds: int, output_path: str) -> str:
    print(f"[Veo] Генерация клипа ({duration_seconds} сек): {prompt[:50]}...", flush=True)

    operation = None
    client = None
    delay = INITIAL_RETRY_DELAY

    for attempt in range(MAX_RETRIES):
        client = get_next_client()
        try:
            operation = client.models.generate_videos(
                model="veo-3.1-lite-generate-preview",
                prompt=prompt,
                config=genai_types.GenerateVideosConfig(
                    aspect_ratio="9:16",
                    duration_seconds=str(duration_seconds),
                    resolution="720p",
                ),
            )
            break
        except Exception as e:
            if _is_rate_limit_error(e) and attempt < MAX_RETRIES - 1:
                print(f"[Veo] Лимит 429. Меняю ключ, жду {delay} сек...", flush=True)
                time.sleep(delay)
            else:
                raise

    if operation is None:
        raise RuntimeError("Не удалось отправить запрос на генерацию Veo")

    start_time = time.time()

    while not operation.done:
        elapsed = int(time.time() - start_time)
        if elapsed > 360:
            raise TimeoutError(f"Veo не ответил за {elapsed} сек")

        time.sleep(12)

        try:
            operation = client.operations.get(operation)
        except Exception as e:
            if _is_rate_limit_error(e):
                print("[Veo] Лимит при опросе статуса. Пауза 30 сек...", flush=True)
                time.sleep(30)
            else:
                raise

    total_time = int(time.time() - start_time)
    print(f"[Veo] Клип готов за {total_time} сек", flush=True)

    generated_video = operation.response.generated_videos[0]
    client.files.download(file=generated_video.video)
    generated_video.video.save(output_path)

    return output_path


async def generate_veo_clips(keywords: list, duration: int, work_dir: str) -> list:
    clip_durations = VEO_CLIP_PLAN.get(duration)
    if not clip_durations:
        raise ValueError(f"Нет схемы разбивки для длительности {duration} сек")

    if len(keywords) != len(clip_durations):
        raise ValueError(
            f"Ожидалось {len(clip_durations)} промптов для {duration} сек, "
            f"получено {len(keywords)}"
        )

    if len(API_KEYS) > 1:
        print(f"[Veo] Параллельная генерация на {len(API_KEYS)} ключах", flush=True)
        tasks = []
        for i, (kw, clip_dur) in enumerate(zip(keywords, clip_durations)):
            output_path = os.path.join(work_dir, f"veo_clip_{i}.mp4")
            tasks.append(
                asyncio.to_thread(_sync_generate_single_clip, kw, clip_dur, output_path)
            )
        clip_paths = await asyncio.gather(*tasks)
        return list(clip_paths)

    clip_paths = []
    for i, (kw, clip_dur) in enumerate(zip(keywords, clip_durations)):
        output_path = os.path.join(work_dir, f"veo_clip_{i}.mp4")
        if i > 0:
            await asyncio.sleep(3)
        path = await asyncio.to_thread(_sync_generate_single_clip, kw, clip_dur, output_path)
        clip_paths.append(path)
    return clip_paths


def get_scenes_count(duration: int) -> int:
    plan = VEO_CLIP_PLAN.get(duration)
    if not plan:
        raise ValueError(f"Нет схемы разбивки для длительности {duration} сек")
    return len(plan)