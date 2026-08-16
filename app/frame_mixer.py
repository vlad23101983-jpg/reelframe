"""
Kinomotor — app/frame_mixer.py
Ролик как набор частей, а не как готовый файл.

Обычная сборка (app/assembler.py) смешивает картинку, голос и музыку в один
mp4 и удаляет промежуточные файлы. Назад уже не разобрать: чтобы сделать
музыку тише, пришлось бы генерировать ролик заново со всеми затратами.

Здесь сборка разделена надвое:

    build_parts() — делает дорожки и складывает их рядом:
        video.mp4  — картинка без звука, с хуком и субтитрами
        voice.m4a  — обработанная озвучка
        veo.m4a    — родной звук клипов Veo, если он есть
        music      — берётся из общей библиотеки, копия не нужна
        parts.json — что получилось и какой длины

    mix() — сводит их в готовый ролик с заданными громкостями.

Второй шаг переклеивает только звук, видеодорожка копируется как есть
(-c:v copy). Поэтому пересборка занимает секунды и ничего не стоит:
кадры не перерисовываются, озвучка не перезаписывается.

Из этого же разделения дальше вырастает всё остальное: перерисовать один
кадр, сменить голос, сделать вторую языковую версию — это замена одной
части, а не пересоздание ролика.
"""

import os
import json
import shutil
import textwrap
import subprocess

from app.assembler import (
    get_file_duration,
    process_smart_audio,
    studio_voice_processing,
)
from app.subtitles import rescale_word_timings, build_ass, ass_filter

PARTS_FILE = "parts.json"

# Громкости по умолчанию, в процентах. Музыка негромкая специально:
# 0.11 по амплитуде — то, что стоит в проверенной сборке, и на слух это
# уже достаточно, чтобы голос оставался главным.
DEFAULT_MIX = {"voice": 100, "music": 35, "veo": 0}

# Во что превращается 100% на ползунке.
VOICE_FULL = 1.0
MUSIC_FULL = 0.31   # 35% от этого дают привычные 0.11
VEO_FULL = 0.60     # родной звук Veo — приправа, а не основа


def _has_audio(path: str) -> bool:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return "audio" in res.stdout


def _clamp(percent, full: float) -> float:
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(value, 200.0)) / 100.0 * full


def load_mix(raw) -> dict:
    """Разбирает сохранённые громкости, подставляя значения по умолчанию."""
    mix = dict(DEFAULT_MIX)
    if not raw:
        return mix
    try:
        saved = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return mix
    for key in mix:
        if key in saved:
            try:
                mix[key] = max(0, min(int(saved[key]), 200))
            except (TypeError, ValueError):
                pass
    return mix


def build_parts(video_files: list, audio_file: str, parts_dir: str, work_dir: str,
                music_path: str = None, hook_text: str = "",
                target_duration: float = 15.0, word_timings: list = None) -> dict:
    """
    Готовит дорожки ролика и складывает их в parts_dir.
    Возвращает описание — оно же пишется в parts.json.
    """
    os.makedirs(parts_dir, exist_ok=True)

    concat_path = os.path.join(work_dir, "concat.mp4")
    inputs = []
    for vp in video_files:
        inputs.extend(["-i", os.path.abspath(vp)])

    n = len(video_files)
    filter_v = "".join(f"[{i}:v]setpts=PTS-STARTPTS[v{i}];" for i in range(n))
    filter_v += "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[outv]"

    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", filter_v, "-map", "[outv]",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", concat_path],
        check=True, capture_output=True,
    )

    # --- Озвучка ---------------------------------------------------------
    processed = os.path.join(work_dir, "voice_processed.wav")
    studio = os.path.join(work_dir, "voice_studio.wav")
    transform = process_smart_audio(audio_file, processed, target_duration, work_dir)
    studio_voice_processing(processed, studio)
    voice_duration = get_file_duration(studio) or target_duration

    voice_out = os.path.join(parts_dir, "voice.m4a")
    subprocess.run(
        ["ffmpeg", "-y", "-i", studio, "-c:a", "aac", "-b:a", "192k", voice_out],
        check=True, capture_output=True,
    )

    # --- Родной звук клипов ---------------------------------------------
    # Veo отдаёт клипы со звуком, и мы за него уже заплатили. Обычная сборка
    # его выбрасывает; здесь сохраняем — шаги, ветер, шум города пригодятся
    # тем, кто захочет подмешать немного атмосферы.
    veo_out = None
    if all(_has_audio(vp) for vp in video_files):
        try:
            a_inputs = []
            for vp in video_files:
                a_inputs.extend(["-i", os.path.abspath(vp)])
            filter_a = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[outa]"
            candidate = os.path.join(parts_dir, "veo.m4a")
            subprocess.run(
                ["ffmpeg", "-y", *a_inputs, "-filter_complex", filter_a,
                 "-map", "[outa]", "-c:a", "aac", "-b:a", "160k", candidate],
                check=True, capture_output=True,
            )
            veo_out = candidate
        except Exception as e:
            print(f"[дорожки] Родной звук клипов сохранить не удалось: {e}", flush=True)

    # --- Картинка: хук и субтитры ---------------------------------------
    vf = []
    if hook_text:
        clean = hook_text.replace("'", "").replace('"', "").replace(":", "\\:").upper()
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font_arg = f"fontfile={font_path}:" if os.path.exists(font_path) else ""
        wrapped = "\n".join(textwrap.wrap(clean, width=14))
        vf.append(
            f"drawtext={font_arg}text='{wrapped}':fontcolor=white:fontsize=60:"
            f"x=(w-text_w)/2:y=240:box=1:boxcolor=black@0.7:boxborderw=16:"
            f"line_spacing=10:enable='between(t,0,3.5)'"
        )

    if word_timings:
        try:
            rescaled = rescale_word_timings(word_timings, transform)
            if rescaled:
                ass_path = os.path.join(work_dir, "subtitles.ass")
                if build_ass(rescaled, ass_path):
                    flt = ass_filter(ass_path)
                    if flt:
                        vf.append(flt)
        except Exception as e:
            print(f"[дорожки] Субтитры не наложены ({e}) — рендерю без них", flush=True)

    video_out = os.path.join(parts_dir, "video.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-i", concat_path, "-vf", ",".join(vf) if vf else "null",
         "-an", "-t", str(voice_duration),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", video_out],
        check=True, capture_output=True,
    )

    manifest = {
        "duration": round(voice_duration, 3),
        "video": "video.mp4",
        "voice": "voice.m4a",
        "veo": os.path.basename(veo_out) if veo_out else None,
        # Музыка живёт в общей библиотеке и никуда не девается — копия не нужна.
        "music": music_path if music_path and os.path.exists(music_path) else None,
    }
    with open(os.path.join(parts_dir, PARTS_FILE), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)

    print(f"[дорожки] Готовы: {voice_duration:.2f} сек, "
          f"звук Veo — {'есть' if veo_out else 'нет'}", flush=True)
    return manifest


def read_parts(parts_dir: str):
    path = os.path.join(parts_dir, PARTS_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def mix(parts_dir: str, output_path: str, volumes: dict) -> str:
    """
    Сводит дорожки в готовый ролик.

    Видео копируется без перекодирования, поэтому вся работа — это только
    звук: несколько секунд независимо от длины ролика.
    """
    manifest = read_parts(parts_dir)
    if not manifest:
        raise RuntimeError("Дорожки ролика не найдены")

    vol = load_mix(volumes)

    video_path = os.path.join(parts_dir, manifest["video"])
    voice_path = os.path.join(parts_dir, manifest["voice"])
    for p in (video_path, voice_path):
        if not os.path.exists(p):
            raise RuntimeError("Дорожки ролика больше не хранятся на сервере")

    inputs = ["-i", video_path, "-i", voice_path]
    parts = []
    mix_labels = []

    voice_gain = _clamp(vol["voice"], VOICE_FULL)
    music_gain = _clamp(vol["music"], MUSIC_FULL)
    veo_gain = _clamp(vol["veo"], VEO_FULL)

    # Голос чистим всегда одинаково и раздваиваем: одна копия идёт в микс,
    # вторая управляет сайдчейном, который приглушает музыку под речь.
    parts.append(
        f"[1:a]highpass=f=80,lowpass=f=12000,volume={voice_gain:.3f}[voice_clean];"
        f"[voice_clean]asplit=2[v_mix][v_side]"
    )
    mix_labels.append("[v_mix]")

    next_index = 2

    music_path = manifest.get("music")
    if music_path and os.path.exists(music_path) and music_gain > 0:
        inputs.extend(["-stream_loop", "-1", "-i", music_path])
        parts.append(
            f"[{next_index}:a]volume={music_gain:.3f}[bg];"
            f"[bg][v_side]sidechaincompress=threshold=0.01:ratio=5:attack=5:"
            f"release=200:makeup=2[bg_ducked]"
        )
        mix_labels.append("[bg_ducked]")
        next_index += 1
    else:
        # Сайдчейну нужен потребитель, иначе ffmpeg ругается на висящий выход.
        parts.append("[v_side]anullsink")

    veo_name = manifest.get("veo")
    if veo_name and veo_gain > 0:
        veo_path = os.path.join(parts_dir, veo_name)
        if os.path.exists(veo_path):
            inputs.extend(["-i", veo_path])
            parts.append(f"[{next_index}:a]volume={veo_gain:.3f}[veo]")
            mix_labels.append("[veo]")
            next_index += 1

    if len(mix_labels) > 1:
        parts.append(
            "".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=first:"
            f"dropout_transition=0:normalize=0[mixed];"
            f"[mixed]loudnorm=I=-11.0:TP=-0.5:LRA=15[aout]"
        )
    else:
        parts.append(f"{mix_labels[0]}loudnorm=I=-12.0:TP=-1.5:LRA=11[aout]")

    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(parts),
         "-map", "0:v:0", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", output_path],
        check=True, capture_output=True,
    )

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("Сведение не дало файла")

    return output_path
