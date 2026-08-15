"""
Kinomotor — scripts/check_voice_ids.py
Проверяет каждый voice_id из app/config.py напрямую через ElevenLabs API —
показывает, какие голоса реально существуют в вашем аккаунте, а какие нет.
Удобно, чтобы не искать вручную в библиотеке голосов.

Запуск (на сервере, где уже настроен .env с ELEVENLABS_API_KEY):
    cd ~/reelframe
    python3 scripts/check_voice_ids.py
"""

import os
import sys
import requests
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import VOICE_PRESETS

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_URL = "https://api.elevenlabs.io/v1/voices/{voice_id}"


def check_voice(voice_id: str) -> tuple:
    headers = {"xi-api-key": ELEVENLABS_API_KEY}
    try:
        response = requests.get(VOICE_URL.format(voice_id=voice_id), headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json()
            return True, data.get("name", "?")
        else:
            return False, f"HTTP {response.status_code}"
    except Exception as e:
        return False, str(e)


def main():
    if not ELEVENLABS_API_KEY:
        print("ELEVENLABS_API_KEY не задан в .env — проверка невозможна.")
        return

    print("Проверяю голоса через ElevenLabs API...\n")

    for language, voices in VOICE_PRESETS.items():
        print(f"=== {language.upper()} ===")
        for key, preset in voices.items():
            ok, detail = check_voice(preset["voice_id"])
            status = "✅ РАБОТАЕТ" if ok else "❌ НЕ НАЙДЕН"
            print(f"  {status}  {preset['title']:<20} ({key})  {preset['voice_id']}  [{detail}]")
        print()

    print("Готово. Голоса с пометкой ❌ нужно заменить — возьмите новый voice_id из ElevenLabs Voice Library.")


if __name__ == "__main__":
    main()
