import json
import os
import subprocess
import textwrap
from pydub import AudioSegment
from pydub.effects import compress_dynamic_range, normalize


def get_file_duration(file_path: str) -> float:
    """Получает точную длительность медиафайла в секундах."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def process_smart_audio(
    input_audio: str,
    cleaned_audio: str,
    target_duration: float,
    work_dir: str,
):
    """Вырезает паузы тишины и подгоняет скорость речи под target_duration."""
    silence_filter = (
        "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-35dB:"
        "stop_periods=-1:stop_duration=0.25:stop_threshold=-35dB"
    )

    temp_cleaned = os.path.join(work_dir, "temp_speech_no_silence.mp3")
    cmd_clean = [
        "ffmpeg",
        "-y",
        "-i",
        input_audio,
        "-af",
        silence_filter,
        temp_cleaned,
    ]
    subprocess.run(cmd_clean, capture_output=True, check=True)

    clean_dur = get_file_duration(temp_cleaned)
    print(
        f"🎙 Длина речи после чистки пауз: {clean_dur:.2f}s | Целевая длина: {target_duration}s"
    )

    speed_ratio = (
        (clean_dur / float(target_duration)) if clean_dur > 0 else 1.0
    )
    speed_ratio = max(0.85, min(speed_ratio, 1.70))

    cmd_tempo = [
        "ffmpeg",
        "-y",
        "-i",
        temp_cleaned,
        "-filter:a",
        f"atempo={speed_ratio:.2f}",
        cleaned_audio,
    ]
    subprocess.run(cmd_tempo, capture_output=True, check=True)


def studio_voice_processing(input_wav: str, output_wav: str):
    """Студийная компрессия и максимальная нормализация голоса через Pydub."""
    # 1. Загружаем голос
    audio = AudioSegment.from_file(input_wav)

    # 2. Компрессия: сжимаем громкие пики и вытягиваем тихие места
    compressed = compress_dynamic_range(
        audio, threshold=-20.0, ratio=4.0, attack=5.0, release=50.0
    )

    # 3. Нормализация до максимума (headroom 0.1dB защищает от клиппинга/хрипа)
    studio_audio = normalize(compressed, headroom=0.1)

    # 4. Экспорт
    studio_audio.export(output_wav, format="wav")


def assemble_final_video(
    video_files: list,
    audio_file: str,
    output_path: str,
    work_dir: str,
    bg_music_path: str = None,
    hook_text: str = "",
    target_duration: float = 10.0,
):
    temp_concat = os.path.join(work_dir, "temp_concat.mp4")
    processed_audio = os.path.join(work_dir, "temp_processed_voice.wav")
    studio_audio = os.path.join(work_dir, "temp_studio_voice.wav")

    # 1. Склейка кадров
    inputs = []
    for vp in video_files:
        inputs.extend(["-i", os.path.abspath(vp)])

    filter_v = "".join(
        [f"[{i}:v]setpts=PTS-STARTPTS[v{i}];" for i in range(len(video_files))]
    )
    filter_concat = (
        "".join([f"[v{i}]" for i in range(len(video_files))])
        + f"concat=n={len(video_files)}:v=1:a=0[outv]"
    )

    concat_cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        f"{filter_v}{filter_concat}",
        "-map",
        "[outv]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        temp_concat,
    ]
    subprocess.run(concat_cmd, check=True)

    # 2. Подготовка и студийная обработка речи
    process_smart_audio(
        audio_file,
        processed_audio,
        target_duration=target_duration,
        work_dir=work_dir,
    )
    studio_voice_processing(processed_audio, studio_audio)
    voice_dur = get_file_duration(studio_audio) or target_duration

    # 3. Финальное сведение аудио с чётким сайдчейном и защитой от хрипов
    voice_chain = (
        "[1:a]highpass=f=80,lowpass=f=12000[voice_clean];"
        "[voice_clean]asplit=2[v_for_mix][v_for_sidechain]"
    )

    audio_inputs = ["-i", temp_concat, "-i", studio_audio]

    if (
        bg_music_path
        and os.path.exists(bg_music_path)
        and os.path.getsize(bg_music_path) > 0
    ):
        audio_inputs.extend(["-stream_loop", "-1", "-i", bg_music_path])

        filter_complex_audio = (
            f"{voice_chain};"
            f"[2:a]volume=0.08[bg_music];"
            f"[bg_music][v_for_sidechain]sidechaincompress=threshold=0.01:ratio=8:attack=5:release=200[ducked_music];"
            f"[v_for_mix][ducked_music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mixed];"
            f"[mixed]loudnorm=I=-11.0:TP=-0.5:LRA=15[aout]"
        )
        audio_map = "[aout]"
    else:
        filter_complex_audio = (
            "[1:a]highpass=f=80,lowpass=f=12000,loudnorm=I=-12.0:TP=-1.5:LRA=11[aout]"
        )
        audio_map = "[aout]"

    # 4. Хук-заголовок
    vf_filters = []
    if hook_text:
        clean_text = (
            hook_text.replace("'", "")
            .replace('"', "")
            .replace(":", "\\:")
            .upper()
        )
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font_arg = f"fontfile={font_path}:" if os.path.exists(font_path) else ""

        wrapped_lines = textwrap.wrap(clean_text, width=14)
        formatted_hook = "\n".join(wrapped_lines)

        drawtext_cmd = (
            f"drawtext={font_arg}"
            f"text='{formatted_hook}':fontcolor=white:fontsize=60:x=(w-text_w)/2:y=240:"
            f"box=1:boxcolor=black@0.7:boxborderw=16:line_spacing=10:enable='between(t,0,3.5)'"
        )
        vf_filters.append(drawtext_cmd)

    vf_chain = ",".join(vf_filters) if vf_filters else "null"

    # 5. Финальный рендер
    final_cmd = [
        "ffmpeg",
        "-y",
        *audio_inputs,
        "-filter_complex",
        filter_complex_audio,
        "-vf",
        vf_chain,
        "-map",
        "0:v:0",
        "-map",
        audio_map,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        str(voice_dur),
        output_path,
    ]

    subprocess.run(final_cmd, check=True)
    print(f"✅ Сборка завершена со студийным звуком (-12 LUFS): {output_path}")