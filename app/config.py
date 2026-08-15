"""
Пресеты голосов и музыки для Kinomotor.
Ключи должны совпадать с теми, что отправляет фронтенд (create.html).

VOICE_PRESETS разбит по языку ("ru" / "en") — какой набор голосов показывать
и использовать, зависит от выбранного языка озвучки. Это нужно, потому что
голос с русским акцентом звучит с этим же акцентом и на английском —
для нормальной английской озвучки нужны отдельные, англоязычные голоса.

ВАЖНО про en-голоса ниже: voice_id — это стандартные готовые голоса,
доступные в любом аккаунте ElevenLabs "из коробки". Замените на свои,
если в библиотеке голосов найдёте варианты, которые нравятся больше —
просто скопируйте нужный voice_id из ElevenLabs (Voice Library).
"""

VOICE_PRESETS = {
    "ru": {
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
    },
    "en": {
        "v_adam": {
            "title": "Adam",
            "description": "Deliberate, confident engineering professor — e-learning, training, narration",
            "voice_id": "QIhD5ivPGEoYZQDocuHI",
        },
        "v_rachel": {
            "title": "Rachel",
            "description": "Warm female — stories, blogs, philosophy",
            "voice_id": "21m00Tcm4TlvDq8ikWAM",
        },
        "v_antoni": {
            "title": "Antoni",
            "description": "Well-rounded male — universal for social media",
            "voice_id": "ErXwobaYiN019PkySvjV",
        },
        "v_bella": {
            "title": "Bella",
            "description": "Soft female — calm, narration, gentle content",
            "voice_id": "EXAVITQu4vr4xnSDxMaL",
        },
        "v_jon": {
            "title": "Jon",
            "description": "Calm, reassuring, deeply human — wellness, coaching, support",
            "voice_id": "enzbGixeo55iqn1QxbbC",
        },
        "v_jessica": {
            "title": "Jessica",
            "description": "Playful, bright, warm, youthful — conversational content",
            "voice_id": "r1KmysJdVYZjJCm4mL3b",
        },
        "v_jon_catalyst": {
            "title": "Jon (Catalyst)",
            "description": "Sharp, confident, witty — documentaries, explainers, podcasts",
            "voice_id": "dSByRdUbTGloB7TFA1qD",
        },
        "v_laura": {
            "title": "Laura",
            "description": "Wise, mature, captivating — news, media, top-story narration",
            "voice_id": "GZ4PpFJV8ikEGUtBrjK7",
        },
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