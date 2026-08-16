"""
Kinomotor — app/frame_photos.py
Подготовка фотографий пользователя для тарифа "Кадры".

Две задачи:
    1. Привести снимок к 1080x1920 — тем же способом, что и в обычном
       тарифе с фото (photo_processor), чтобы результат выглядел одинаково.
    2. Рассказать, что на снимке изображено.

Второе нужно потому, что про свои фото мы не знаем ничего. А знать надо
для двух вещей: о чём будет говорить диктор и что сказать Veo про движение
в кадре. Спрашивать это у человека — лишний труд и лишний повод бросить
всё на полпути, поэтому смотрим сами через Gemini.
"""

import os
import asyncio
import mimetypes

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

from app.photo_processor import get_image_ratio, build_fit_filter, run_ffmpeg

load_dotenv()

# Модель для чтения картинок. Та же, что пишет сценарии.
VISION_MODEL = "gemini-3.6-flash"

_gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

TARGET_W = 1080
TARGET_H = 1920


async def normalize_photo(photo_path: str, output_path: str) -> str:
    """
    Приводит фото к 1080x1920 PNG.

    Вертикальные и близкие к вертикали кадрируются, горизонтальные
    вписываются на размытый фон — правило то же, что в photo_processor,
    и порог там же (CROP_LIMIT_RATIO).
    """
    ratio = await asyncio.to_thread(get_image_ratio, photo_path)
    fit_filter = build_fit_filter(ratio)

    await run_ffmpeg([
        "ffmpeg", "-y",
        "-i", photo_path,
        "-filter_complex", fit_filter,
        "-frames:v", "1",
        output_path,
    ], "кадр из фото")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(
            f"Не удалось обработать фото {os.path.basename(photo_path)} — "
            f"проверьте формат файла"
        )

    return output_path


def _describe_sync(image_paths: list, language: str = "ru") -> list:
    """
    Показывает Gemini все снимки разом и просит описать каждый.

    Разом, а не по одному, специально: увидев всю подборку, модель понимает
    общий сюжет и описывает кадры согласованно, как части одной истории.
    По одному снимку она такой связи не видит.
    """
    lang_name = "русском" if language == "ru" else "английском"

    parts = []
    for path in image_paths:
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            parts.append(genai_types.Part.from_bytes(data=f.read(), mime_type=mime))

    parts.append(genai_types.Part.from_text(text=(
        f"Перед тобой {len(image_paths)} фотографий в том порядке, в каком они "
        f"пойдут в коротком вертикальном ролике.\n"
        f"Опиши КАЖДУЮ отдельной строкой на {lang_name} языке: что на ней "
        f"изображено, где это происходит, какое настроение. Одно-два предложения "
        f"на снимок, без вступлений и без нумерации.\n"
        f"Ровно {len(image_paths)} строк, каждая с новой строки, ничего больше.\n"
        f"Не называй имён людей и не пытайся угадать, кто именно изображён."
    )))

    response = _gemini_client.models.generate_content(
        model=VISION_MODEL,
        contents=parts,
    )

    raw = (getattr(response, "text", "") or "").strip()
    lines = [ln.strip(" -•\t") for ln in raw.split("\n") if ln.strip()]

    # Модель может ответить не тем количеством строк. Ролик из-за этого
    # ронять нельзя: недостающие описания заполняем нейтральной заглушкой,
    # лишние отбрасываем.
    if len(lines) < len(image_paths):
        lines += ["фотография из подборки пользователя"] * (len(image_paths) - len(lines))

    return lines[:len(image_paths)]


async def describe_photos(image_paths: list, language: str = "ru") -> list:
    """
    Возвращает список описаний — по одному на снимок, в том же порядке.
    При неудаче возвращает нейтральные заглушки: без описаний ролик
    получится хуже, но собраться он должен.
    """
    if not image_paths:
        return []

    try:
        return await asyncio.to_thread(_describe_sync, image_paths, language)
    except Exception as e:
        print(f"[кадры] Не удалось описать фото ({e}) — беру заглушки", flush=True)
        return ["фотография из подборки пользователя"] * len(image_paths)
