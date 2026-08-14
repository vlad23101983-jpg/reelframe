"""
Озвучка (Text-to-Speech) и генерация фоновой музыки через ElevenLabs.
Синхронные функции — вызывайте через asyncio.to_thread(), как в вашем боте.

Перед использованием:
1. Зарегистрируйтесь на elevenlabs.io, получите API-ключ.
2. Положите его в .env как ELEVENLABS_API_KEY=...
3. Замените voice_id в config.py на реальные ID голосов из вашего аккаунта.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
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
