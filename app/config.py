"""
Пресеты голосов и музыки для Kinomotor.
Ключи должны совпадать с теми, что отправляет фронтенд (create.html).
"""

VOICE_PRESETS = {
    "v_artem": {
        "title": "Артём",
        "description": "Уверенный мужской — бизнес, обзоры, мотивация",
        "voice_id": "rQOBu7YxCDxGiFdTm28w",
    },
    "v_maria": {
        "title": "Мария",
        "description": "Тёплый женский — истории, блоги, философия",
        "voice_id": "t6lBrEl93uCiLR1Lgm8v",
    },
    "v_nikolay": {
        "title": "Николай",
        "description": "Мужской средних лет — универсальный для соцсетей",
        "voice_id": "3EuKHIEZbSzrHGNmdYsx",
    },
    "v_mikhail": {
        "title": "Михаил",
        "description": "Спокойный без пафоса — история, наука",
        "voice_id": "a2cWmGM4AUuNsHwInFjF",
    },
    "v_viktoria": {
        "title": "Виктория",
        "description": "Харизматичный женский — реклама, сторителлинг",
        "voice_id": "FZGeNF7bE3syeQOynDKC",
    },
    "v_pavel": {
        "title": "Павел",
        "description": "Молодой мужской — живой разговорный стиль",
        "voice_id": "O9f5Hqzk8FPymrA0cAZq",
    },
    "v_ekaterina": {
        "title": "Екатерина",
        "description": "Мягкий женский — сказки, медитации, дети",
        "voice_id": "GN4wbsbejSnGSa1AzjH5",
    },
}

MUSIC_PRESETS = {
    "m_energetic": {
        "title": "Энергичная",
        "prompt": "Upbeat dynamic electronic beat, driving motivation, modern upbeat vibe, 120 bpm",
    },
    "m_business": {
        "title": "Бизнес / фон",
        "prompt": "Minimalist corporate background music, calm deep bass synth, professional atmosphere, 110 bpm",
    },
    "m_calm": {
        "title": "Спокойная",
        "prompt": "Chill ambient acoustic melody, relaxing soft pad, inspiring atmosphere",
    },
    "m_none": {
        "title": "Без музыки",
        "prompt": None,
    },
}