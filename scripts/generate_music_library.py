"""
Kinomotor — scripts/generate_music_library.py
Разовый скрипт: генерирует по 5 вариаций фоновой музыки на каждый стиль
через ElevenLabs Music API и сохраняет в media/music/ с именами, которые
уже ждёт round-robin ротация в generate.py (energetic_1.mp3 ... energetic_5.mp3
и так же для business и calm).

Запуск (один раз, на сервере, где уже настроен .env с ELEVENLABS_API_KEY):
    cd ~/reelframe
    python3 scripts/generate_music_library.py

Если какой-то файл уже существует — он пропускается (можно безопасно
перезапускать, если генерация прервалась на середине).
"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.voice import generate_music

TRACKS = {
    "energetic": [
        "Upbeat dynamic electronic beat, driving motivation, modern energetic vibe, 120 bpm",
        "Energetic electronic track with punchy percussion and driving synth bass, motivational, 122 bpm",
        "Uplifting electronic beat with bright synth lead melody, fast-paced, energizing, 118 bpm",
        "Dynamic electronic music with strong groove and rising energy, modern, motivational, 124 bpm",
        "High-energy electronic beat with pulsing bassline and crisp hi-hats, driving momentum, 120 bpm",
    ],
    "business": [
        "Minimalist corporate background music, calm deep bass synth, professional atmosphere, 110 bpm",
        "Clean corporate background track, subtle piano and soft synth pads, professional mood, 108 bpm",
        "Modern minimalist business music, light percussion and warm bass, focused atmosphere, 112 bpm",
        "Understated corporate soundtrack, ambient synth textures, confident professional feel, 110 bpm",
        "Sleek corporate background music, gentle rhythmic pulse, calm and trustworthy tone, 106 bpm",
    ],
    "calm": [
        "Chill ambient acoustic melody, relaxing soft pad, inspiring atmosphere",
        "Peaceful ambient soundscape, gentle acoustic guitar, soothing and reflective mood",
        "Calm ambient background music, warm soft piano, tranquil and inspiring feel",
        "Serene ambient track, light airy pads, quiet contemplative atmosphere",
        "Soft ambient melody, subtle strings and gentle textures, relaxing and hopeful tone",
    ],
}

DURATION_SECONDS = 20
OUTPUT_DIR = os.path.join("media", "music")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    total = sum(len(v) for v in TRACKS.values())
    done = 0

    for style, prompts in TRACKS.items():
        for i, prompt in enumerate(prompts, start=1):
            output_path = os.path.join(OUTPUT_DIR, f"{style}_{i}.mp3")
            done += 1

            if os.path.exists(output_path):
                print(f"[{done}/{total}] {style}_{i}.mp3 уже существует, пропускаю", flush=True)
                continue

            print(f"[{done}/{total}] Генерирую {style}_{i}.mp3 ...", flush=True)
            try:
                generate_music(prompt, output_path, duration=DURATION_SECONDS)
                print(f"[{done}/{total}] Готово: {output_path}", flush=True)
            except Exception as e:
                print(f"[{done}/{total}] ОШИБКА при генерации {style}_{i}.mp3: {e}", flush=True)

    print("\nГотово! Все треки в media/music/ — round-robin ротация в generate.py подхватит их автоматически.", flush=True)


if __name__ == "__main__":
    main()
