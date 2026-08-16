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


def _script_limits(duration: int):
    """
    Сколько кадров и какой длины текст диктора для данной длительности.

    ВАЖНО: лимиты символов подобраны под естественную скорость речи —
    не менять. Вынесены сюда, чтобы у тарифа "Кадры" и у обычной
    генерации они не разъехались.
    """
    if duration <= 10:
        return 4, 180, 210
    if duration <= 15:
        return 5, 260, 290
    return 6, 345, 385


def get_video_script(topic: str, duration: int, scenes_override: int = None, video_type: str = "images", language: str = "ru") -> dict:
    """
    Возвращает JSON: hook_text, voice_text, keywords, social_description, hashtags.

    ВАЖНО: лимиты символов voice_text подобраны под естественную скорость речи —
    не менять.

    language: "ru" (по умолчанию) или "en" — язык хука, текста диктора, описания
    и хештегов. Промпты для картинок/видео (keywords) всегда на английском,
    независимо от языка озвучки — так лучше работают модели генерации изображений.
    """
    shots_count, min_chars, max_chars = _script_limits(duration)

    if scenes_override is not None:
        shots_count = scenes_override

    lang_name = {"ru": "РУССКОМ", "en": "АНГЛИЙСКОМ"}.get(language, "РУССКОМ")

    text_rule = (
        "Не добавляй читаемый текст, надписи, субтитры, логотипы или водяные знаки "
        "в изображения и видео, если текст прямо не требуется сюжетом пользователя.\n"
    )

    if video_type == "veo":
        keywords_instruction = (
            f"3. 'keywords': массив из {shots_count} конкретных визуальных prompts НА АНГЛИЙСКОМ "
            f"для генерации видео через Veo. Каждый prompt — примерно 25-45 слов. "
            f"Самостоятельно выбирай персонажей, предметы, действия, локации, ракурсы, "
            f"движения камеры, освещение и визуальные эффекты в зависимости от сюжета. "
            f"Не ограничивай сюжет искусственными правилами. Главное — чтобы все сцены были интересными, "
            f"разнообразными, логичными и последовательно раскрывали тему пользователя.\n"
        )
    else:
        keywords_instruction = (
            f"3. 'keywords': массив из {shots_count} конкретных визуальных prompts НА АНГЛИЙСКОМ "
            f"для генерации изображений. Каждый prompt — примерно 20-35 слов. "
            f"Самостоятельно выбирай персонажей, предметы, действия, локации, композицию, ракурсы, "
            f"освещение и визуальный стиль в зависимости от сюжета. "
            f"Не ограничивай сюжет искусственными правилами. Главное — чтобы все сцены были интересными, "
            f"разнообразными, логичными и последовательно раскрывали тему пользователя.\n"
        )

    system_instruction = (
        f"Ты профессиональный сценарист коротких вирусных роликов для Reels/Shorts.\n"
        f"Создай сценарий короткого видео по теме пользователя. Самостоятельно придумай интересную и "
        f"логичную последовательность из {shots_count} сцен, которая лучше всего раскрывает тему.\n"
        f"Пользователь может написать абсолютно любую тему. Не меняй смысл его запроса и не подменяй его другой темой.\n"
        f"Для каждой сцены создай конкретный визуальный prompt. Разрешены любые персонажи, предметы, действия, "
        f"локации, смены планов, ракурсы, движения камеры и визуальные эффекты — выбирай их сам в зависимости от сюжета.\n"
        f"Все {shots_count} сцен должны быть содержательными, логичными, разнообразными и последовательно раскрывать тему. "
        f"Не создавай пустые или случайные кадры, которые не относятся к рассказу.\n"
        f"{text_rule}"
        f"1. 'hook_text': цепляющий короткий заголовок-хук из 2-4 слов НА {lang_name} ЗАГЛАВНЫМИ БУКВАМИ.\n"
        f"2. 'voice_text': текст НА {lang_name} языке объемом {min_chars}-{max_chars} символов для диктора. Без эмодзи. "
        f"Текст должен естественно рассказывать историю и соответствовать последовательности сцен.\n"
        f"{keywords_instruction}"
        f"4. 'social_description': текст НА {lang_name} языке объёмом 500-700 символов — готовое SEO-описание "
        f"для публикации этого видео в Instagram Reels и YouTube Shorts. Должно ясно объяснять, о чём видео, "
        f"цеплять внимание в первых 1-2 предложениях, содержать релевантные слова по теме для алгоритмов "
        f"соцсетей. Без хештегов внутри этого текста.\n"
        f"5. 'hashtags': массив ровно из 5 хештегов НА {lang_name} ЯЗЫКЕ по теме видео, каждый начинается с # "
        f"(например: '#бизнесидеи', '#мотивация' для русского, или '#business', '#motivation' для английского), "
        f"без пробелов внутри тега.\n"
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


def get_script_for_photos(photo_descriptions: list, duration: int, topic: str = "", language: str = "ru") -> dict:
    """
    Сценарий для тарифа "Кадры", когда кадры — это фотографии пользователя.

    Отличие от get_video_script: там модель сама придумывает, что будет в
    кадре, и выдаёт keywords. Здесь кадры уже есть и поменять их нельзя —
    задача обратная: написать текст, который ложится ИМЕННО на эти снимки,
    в их порядке. Поэтому keywords не запрашиваются вовсе.

    photo_descriptions — что Gemini увидел на снимках (app/frame_photos.py),
    по одному описанию на кадр, в порядке показа.
    topic — необязательное пожелание человека, о чём должен быть ролик.
    """
    _, min_chars, max_chars = _script_limits(duration)
    lang_name = {"ru": "РУССКОМ", "en": "АНГЛИЙСКОМ"}.get(language, "РУССКОМ")

    frames_list = "\n".join(
        f"Кадр {i + 1}: {d}" for i, d in enumerate(photo_descriptions)
    )
    topic_line = (
        f"Пожелание автора о содержании ролика: {topic}\n"
        if topic and topic.strip() else
        "Автор не указал тему — опирайся только на сами кадры.\n"
    )

    system_instruction = (
        f"Ты профессиональный сценарист коротких вирусных роликов для Reels/Shorts.\n"
        f"Кадры ролика уже сняты и поменять их нельзя — это фотографии автора. "
        f"Ниже они перечислены по порядку. Напиши текст, который ложится именно на "
        f"эти кадры и раскрывает их как единую историю.\n"
        f"Не описывай кадры вслух и не пересказывай, что на них видно — зритель и так "
        f"это видит. Текст должен добавлять смысл, а не дублировать картинку.\n"
        f"Не выдумывай событий, которых на кадрах нет, и не называй имён людей.\n\n"
        f"{topic_line}\n"
        f"{frames_list}\n\n"
        f"Верни JSON с полями:\n"
        f"1. 'hook_text': цепляющий короткий заголовок-хук из 2-4 слов НА {lang_name} "
        f"ЗАГЛАВНЫМИ БУКВАМИ.\n"
        f"2. 'voice_text': текст НА {lang_name} языке объемом {min_chars}-{max_chars} символов "
        f"для диктора. Без эмодзи. Должен естественно разворачиваться в том же порядке, "
        f"что и кадры.\n"
        f"3. 'social_description': текст НА {lang_name} языке объёмом 500-700 символов — "
        f"готовое SEO-описание для публикации в Instagram Reels и YouTube Shorts. "
        f"Цепляет в первых 1-2 предложениях. Без хештегов внутри текста.\n"
        f"4. 'hashtags': массив ровно из 5 хештегов НА {lang_name} ЯЗЫКЕ, каждый "
        f"начинается с #, без пробелов внутри тега.\n"
        f"Формат ответа: СТРОГО чистый JSON без markdown."
    )

    interaction = ai_client.interactions.create(
        model="gemini-3.6-flash",
        input=system_instruction
    )

    raw_text = interaction.output_text.strip()
    match = re.search(r'\{.*\}', raw_text, re.DOTALL)
    if match:
        raw_text = match.group(0)

    return json.loads(raw_text)


def get_motion_prompts(frame_descriptions: list, language: str = "ru") -> list:
    """
    Превращает описания кадров в короткие англоязычные промпты движения
    для image-to-video.

    Кадр уже утверждён человеком, менять его нельзя — значит промпт должен
    описывать не сюжет, а исключительно то, что в этом кадре шевелится:
    движение камеры, живые детали, свет. Всё, что звучит как новая сцена,
    заставит Veo перерисовать картинку, и человек получит не то, что одобрил.

    Промпты всегда на английском, независимо от языка озвучки — модели
    генерации так работают заметно лучше.

    При неудаче возвращает безопасные заглушки: без промптов движения
    ролик всё равно должен собраться.
    """
    fallback = [
        "Subtle slow camera push-in. Natural ambient motion only. "
        "Keep the composition and subject exactly as in the source image. "
        "Quiet ambient sound of the scene. "
        "No speech, no dialogue, no singing, no laughter, no giggling, no sighs, "
        "no gasps, no shouting, no human vocal sounds of any kind, lips closed, "
        "people silent."
    ] * len(frame_descriptions)

    if not frame_descriptions:
        return []

    frames_list = "\n".join(
        f"Frame {i + 1}: {d}" for i, d in enumerate(frame_descriptions)
    )

    prompt = (
        f"Ниже описаны {len(frame_descriptions)} кадров короткого вертикального ролика. "
        f"Каждый кадр — уже готовое изображение, которое НЕЛЬЗЯ менять: оно пойдёт "
        f"первым кадром в модель image-to-video.\n\n"
        f"{frames_list}\n\n"
        f"Для каждого кадра напиши промпт движения НА АНГЛИЙСКОМ, 15-30 слов. "
        f"Описывай ТОЛЬКО движение: движение камеры, оживающие детали, свет, "
        f"частицы, ткань, волосы, воду. Композиция, персонажи и обстановка должны "
        f"остаться ровно теми же, что на исходном изображении.\n"
        f"Не вводи новых объектов, не меняй план, не описывай смену сцены. "
        f"Движение должно быть спокойным — это 3-4 секунды экранного времени.\n"
        f"ЗВУК. В каждом промпте отдельным предложением опиши звуки, которые "
        f"рождает сам кадр: шаги, клавиши, ветер, посуда, шум улицы, музыка "
        f"места. Это заметно оживляет ролик.\n"
        f"РЕЧЬ И ЛЮБЫЕ ЗВУКИ ЧЕЛОВЕКА ЗАПРЕЩЕНЫ. В кадре никто не говорит, "
        f"не поёт, не смеётся, не вздыхает, не охает, не кричит и не шепчет. "
        f"Ни реплик, ни диалогов, ни закадрового голоса, ни смеха, ни возгласов. "
        f"Губы сомкнуты, люди молчат. Прямо напиши это в конце каждого промпта: "
        f"'No speech, no dialogue, no singing, no laughter, no giggling, no sighs, "
        f"no gasps, no shouting, no human vocal sounds of any kind, lips closed, "
        f"people silent.'\n"
        f"Поверх ролика идёт собственная озвучка диктора, и любой звук человека "
        f"из кадра накладывается на неё и портит результат.\n"
        f"Ответ: СТРОГО JSON-массив из {len(frame_descriptions)} строк, без markdown."
    )

    try:
        interaction = ai_client.interactions.create(
            model="gemini-3.6-flash",
            input=prompt
        )
        raw_text = interaction.output_text.strip()
        match = re.search(r'\[.*\]', raw_text, re.DOTALL)
        if match:
            raw_text = match.group(0)

        prompts = json.loads(raw_text)
        if not isinstance(prompts, list) or not prompts:
            return fallback

        prompts = [str(p) for p in prompts][:len(frame_descriptions)]
        if len(prompts) < len(frame_descriptions):
            prompts += fallback[len(prompts):]
        return prompts

    except Exception as e:
        print(f"[кадры→видео] Промпты движения не получены ({e}) — беру запасные", flush=True)
        return fallback


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