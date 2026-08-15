"""
Озвучка (Text-to-Speech) и генерация фоновой музыки через ElevenLabs.
Синхронные функции — вызывайте через asyncio.to_thread(), как в вашем боте.

Перед использованием:
1. Зарегистрируйтесь на elevenlabs.io, получите API-ключ.
2. Положите его в .env как ELEVENLABS_API_KEY=...
3. Замените voice_id в config.py на реальные ID голосов из вашего аккаунта.
"""

import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
TTS_TIMESTAMPS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
MUSIC_URL = "https://api.elevenlabs.io/v1/music"


def generate_speech(text: str, output_path: str, voice_id: str, model_id: str = "eleven_v3") -> str:
    """Генерирует озвучку текста и сохраняет как mp3."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY не задан в .env")

    url = TTS_URL.format(voice_id=voice_id)
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    response = requests.post(url, headers=headers, json=payload, timeout=120)
    response.raise_for_status()

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path


def _chars_to_words(chars, starts, ends):
    """
    Склеивает посимвольную раскладку ElevenLabs в слова.
    Возвращает [{"word": "привет", "start": 0.12, "end": 0.54}, ...].

    Слово заканчивается на пробеле или переносе. Знаки препинания остаются
    приклеенными к слову — на экране они и должны быть вместе с ним.
    """
    words = []
    current, w_start, w_end = "", None, None

    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if current:
                words.append({"word": current, "start": w_start, "end": w_end})
                current, w_start, w_end = "", None, None
            continue
        if not current:
            w_start = s
        current += ch
        w_end = e

    if current:
        words.append({"word": current, "start": w_start, "end": w_end})

    return words


def generate_speech_with_timings(text: str, output_path: str, voice_id: str, model_id: str = "eleven_v3"):
    """
    То же, что generate_speech, но дополнительно возвращает тайминги слов —
    они нужны для караоке-субтитров.

    Возвращает (output_path, words), где words — список словарей
    {"word", "start", "end"} или пустой список, если тайминги получить
    не удалось. Пустой список НЕ является ошибкой: озвучка в этом случае
    всё равно записана, просто субтитры будут недоступны — ролик важнее.
    """
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY не задан в .env")

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    try:
        response = requests.post(
            TTS_TIMESTAMPS_URL.format(voice_id=voice_id),
            headers=headers, json=payload, timeout=120,
        )
        response.raise_for_status()
        data = response.json()

        audio_b64 = data.get("audio_base64")
        if not audio_b64:
            raise RuntimeError("в ответе нет audio_base64")

        with open(output_path, "wb") as f:
            f.write(base64.b64decode(audio_b64))

        alignment = data.get("alignment") or data.get("normalized_alignment") or {}
        chars = alignment.get("characters") or []
        starts = alignment.get("character_start_times_seconds") or []
        ends = alignment.get("character_end_times_seconds") or []

        if not (len(chars) == len(starts) == len(ends)) or not chars:
            print(f"[voice] Тайминги пришли неполными: символов={len(chars)}, "
                  f"начал={len(starts)}, концов={len(ends)}", flush=True)
            return output_path, []

        words = _chars_to_words(chars, starts, ends)
        print(f"[voice] Тайминги получены: слов={len(words)}, "
              f"длительность={ends[-1]:.2f} сек", flush=True)
        return output_path, words

    except Exception as e:
        # Падать нельзя: без субтитров ролик всё равно нужен пользователю.
        print(f"[voice] Тайминги недоступны ({e}) — озвучиваю обычным способом", flush=True)
        generate_speech(text, output_path, voice_id, model_id)
        return output_path, []


def generate_music(prompt: str, output_path: str, duration: int = 15) -> str:
    """Генерирует фоновую музыку по текстовому промпту."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY не задан в .env")

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "duration_seconds": min(max(duration, 10), 30),
    }

    response = requests.post(MUSIC_URL, headers=headers, json=payload, timeout=120)
    response.raise_for_status()

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path
