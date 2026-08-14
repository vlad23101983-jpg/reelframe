"""
Генерация сценария ролика через Gemini: хук, текст для диктора,
промпты для кадров, SEO-описание и хештеги.
"""

import os
import re
import json
from dotenv import load_dotenv
from google import genai

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)


def get_video_script(topic: str, duration: int, scenes_override: int = None, video_type: str = "images") -> dict:
    """
    Возвращает JSON: hook_text, voice_text, keywords, social_description, hashtags.

    ВАЖНО: лимиты символов voice_text подобраны под естественную скорость речи —
    не менять.
    """
    if duration <= 10:
        shots_count = 4
        min_chars, max_chars = 180, 210
    elif duration <= 15:
        shots_count = 5
        min_chars, max_chars = 260, 290
    else:
        shots_count = 6
        min_chars, max_chars = 345, 385

    if scenes_override is not None:
        shots_count = scenes_override

    if video_type == "veo":
        keywords_instruction = (
            f"3. 'keywords': массив из {shots_count} промптов НА АНГЛИЙСКОМ для генерации ЖИВОГО ВИДЕО через Veo. "
            f"КАЖДЫЙ промпт должен описывать МОНТАЖ из 3-4 быстрых смен РАКУРСА КАМЕРЫ внутри ОДНОГО "
            f"видео-клипа, но все ракурсы показывают ОДНО И ТО ЖЕ непрерывное действие/сцену — например, "
            f"общий план → крупный план → вид сбоку → вид спереди одного и того же момента, как "
            f"профессиональный монтаж одной сцены с нескольких камер. НЕ меняй сюжет, локацию или действие "
            f"внутри одного клипа — только ракурс камеры. Пример: 'A person running on a beach at sunset: "
            f"wide shot showing full stride, quick cut to close-up of feet hitting sand, quick cut to side "
            f"profile view, quick cut to front view, same continuous run action throughout.' "
            f"Каждый промпт — 25-35 слов. "
            f"Если пользователь в теме ролика ПРЯМО указал визуальный стиль (например: 'мультфильм', 'аниме', "
            f"'как в кино', 'фотореалистично', 'рисованная графика', '3D-анимация') — используй именно этот "
            f"стиль во всех промптах. Если стиль не указан явно — используй фотореализм по умолчанию "
            f"(photorealistic, cinematic).\n"
        )
    else:
        keywords_instruction = (
            f"3. 'keywords': массив из {shots_count} детальных ярких промптов НА АНГЛИЙСКОМ (10-15 слов) "
            f"для генерации фотореалистичных кадров. Опиши облик объекта, освещение и "
            f"кинематографичную композицию. "
            f"Если пользователь в теме ролика ПРЯМО указал визуальный стиль (например: 'мультфильм', 'аниме', "
            f"'рисованная графика', '3D-анимация') — используй именно этот стиль. Если стиль не указан явно — "
            f"используй фотореализм по умолчанию.\n"
        )

    system_instruction = (
        f"Ты сценарист коротких вирусно-динамичных роликов для Reels/Shorts.\n"
        f"Создай JSON со следующей структурой:\n"
        f"1. 'hook_text': цепляющий короткий заголовок-хук из 2-4 слов НА РУССКОМ ЗАГЛАВНЫМИ БУКВАМИ.\n"
        f"2. 'voice_text': текст НА РУССКОМ языке объемом {min_chars}-{max_chars} символов для диктора. Без эмодзи.\n"
        f"{keywords_instruction}"
        f"4. 'social_description': текст НА РУССКОМ языке объёмом 500-700 символов — готовое SEO-описание "
        f"для публикации этого видео в Instagram Reels и YouTube Shorts. Должно ясно объяснять, о чём видео, "
        f"цеплять внимание в первых 1-2 предложениях, содержать релевантные слова по теме для алгоритмов "
        f"соцсетей. Без хештегов внутри этого текста.\n"
        f"5. 'hashtags': массив ровно из 5 хештегов НА РУССКОМ ЯЗЫКЕ по теме видео, каждый начинается с # "
        f"(например: '#бизнесидеи', '#мотивация'), без пробелов внутри тега.\n"
        f"ВАЖНО: НИКОГДА не используй имена реальных людей, знаменитостей, политиков, публичных персон — "
        f"генератор блокирует такие промпты. Вместо имени описывай обобщённый образ: 'a tech entrepreneur', "
        f"'a business leader', 'a young athlete' и т.д., без узнаваемых черт конкретного человека.\n"
        f"Формат ответа: СТРОГО чистый JSON без markdown."
    )

    prompt_with_instruction = f"{system_instruction}\n\nТема ролика: {topic}"

    interaction = ai_client.interactions.create(
        model="gemini-3.6-flash",
        input=prompt_with_instruction
    )

    raw_text = interaction.output_text.strip()
    match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    if match:
        raw_text = match.group(0)

    return json.loads(raw_text)


def translate_music_prompt(user_text: str) -> str:
    """Переводит запрос о стиле музыки в короткий англоязычный промпт."""
    prompt = (
        f"Преобразуй запрос пользователя о стиле музыки в короткий (до 15 слов) англоязычный промпт "
        f"для генерации фоновой музыки.\nЗапрос: {user_text}\n"
        f"Ответ СТРОГО только на английском языке."
    )
    interaction = ai_client.interactions.create(
        model="gemini-3.6-flash",
        input=prompt
    )
    return interaction.output_text.strip()