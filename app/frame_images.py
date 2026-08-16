"""
Kinomotor — app/frame_images.py
Картинки для тарифа "Кадры".

Отличие от image_generator.py и zimage_generator.py: там картинка сразу
превращается в клип с движением камеры и удаляется. Здесь картинка —
самостоятельный результат: человек будет на неё смотреть и решать,
годится она или нет. Поэтому нужен отдельный модуль, отдающий просто файл.

Существующие тарифы этот модуль не трогает.
"""

import os
import asyncio

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

from app.zimage_generator import _generate_image_bytes_sync as _kinom_image_bytes

load_dotenv()

# Чем рисовать кадры. "gemini" — проверенная модель из тарифа "Картинки от ИИ".
# "kinom" — своя модель на RunPod, дешевле, но первый запрос ждёт холодного
# старта до трёх минут. Меняется одной строкой.
IMAGE_ENGINE = "gemini"

GEMINI_IMAGE_MODEL = "gemini-3.1-flash-lite-image"

_gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def _gemini_image_bytes(prompt: str) -> bytes:
    """Рисует кадр 9:16 через Gemini Image. Бросает исключение, если не вышло."""
    response = _gemini_client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=genai_types.ImageConfig(aspect_ratio="9:16"),
        ),
    )

    if response and response.candidates:
        candidate = response.candidates[0]
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if getattr(part, "inline_data", None) is not None:
                    return part.inline_data.data

    raise RuntimeError("Gemini не вернул изображение в ответе")


async def generate_frame_image(prompt: str, output_path: str) -> str:
    """
    Рисует один кадр и кладёт его по указанному пути.

    В отличие от обычной генерации, здесь НЕТ подмены неудачного кадра
    цветной заглушкой. В обычном ролике заглушку почти не видно — она
    мелькает две секунды среди других кадров. Здесь человек смотрит
    именно на картинки, и серый прямоугольник вместо кадра, за который
    он заплатил, — это брак, а не спасение ситуации. Лучше честная
    ошибка и возврат денег.
    """
    if IMAGE_ENGINE == "kinom":
        image_bytes = await asyncio.to_thread(_kinom_image_bytes, prompt, 720, 1280)
    else:
        image_bytes = await asyncio.to_thread(_gemini_image_bytes, prompt)

    if not image_bytes:
        raise RuntimeError("Модель вернула пустое изображение")

    with open(output_path, "wb") as f:
        f.write(image_bytes)

    if os.path.getsize(output_path) == 0:
        raise RuntimeError("Файл кадра получился пустым")

    return output_path
