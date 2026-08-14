"""
Обработка фотографий, загруженных пользователем.
Приводит любое изображение к 1080x1920 и накладывает движение камеры.
"""

import os
import asyncio
import subprocess

TARGET_W = 1080
TARGET_H = 1920
CROP_LIMIT_RATIO = 0.85


def get_image_ratio(image_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        image_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        w, h = res.stdout.strip().split("x")
        return float(w) / float(h)
    except Exception:
        return TARGET_W / TARGET_H


def build_fit_filter(ratio: float) -> str:
    if ratio <= CROP_LIMIT_RATIO:
        return (
            f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},setsar=1"
        )
    return (
        f"split[bg][fg];"
        f"[bg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},boxblur=40:2[blurred];"
        f"[fg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[scaled];"
        f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


async def run_ffmpeg(cmd: list, label: str) -> str:
    """Запускает ffmpeg и возвращает stderr (для диагностики)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    text = err.decode(errors="ignore") if err else ""
    if proc.returncode != 0:
        print(f"[{label}] ffmpeg завершился с кодом {proc.returncode}", flush=True)
        print(f"[{label}] {text[-800:]}", flush=True)
    return text


async def prepare_user_photo(
    photo_path: str,
    output_clip_path: str,
    duration: float = 3.75,
    motion_style_index: int = 0
) -> str:
    ratio = await asyncio.to_thread(get_image_ratio, photo_path)
    fit_filter = build_fit_filter(ratio)

    print(f"Обработка фото ({ratio:.2f}): {os.path.basename(photo_path)}", flush=True)

    normalized_png = output_clip_path.replace(".mp4", "_fit.png")

    await run_ffmpeg([
        "ffmpeg", "-y",
        "-i", photo_path,
        "-filter_complex", fit_filter,
        "-frames:v", "1",
        normalized_png
    ], "приведение к 9:16")

    if not os.path.exists(normalized_png) or os.path.getsize(normalized_png) == 0:
        raise RuntimeError(
            f"Не удалось обработать фото {os.path.basename(photo_path)} — "
            f"проверьте формат файла"
        )

    total_frames = int(duration * 25)
    target_total_zoom = 0.12
    zoom_increment = target_total_zoom / total_frames if total_frames > 0 else 0.0015
    zoom_expr = f"min(zoom+{zoom_increment:.6f},1.15)"

    motion_styles = [
        {"x": "iw/2-(iw/zoom/2)", "y": "ih/2-(ih/zoom/2)"},
        {"x": f"(iw*0.12)+(iw*0.40)*(on/{total_frames})-(iw/zoom/2)", "y": "ih/2-(ih/zoom/2)"},
        {"x": f"(iw*0.52)-(iw*0.40)*(on/{total_frames})-(iw/zoom/2)", "y": "ih/2-(ih/zoom/2)"},
        {"x": "iw/2-(iw/zoom/2)", "y": f"(ih*0.12)+(ih*0.30)*(on/{total_frames})-(ih/zoom/2)"},
    ]
    style = motion_styles[motion_style_index % len(motion_styles)]

    await run_ffmpeg([
        "ffmpeg", "-y",
        "-loop", "1", "-i", normalized_png,
        "-vf", (
            f"zoompan=z='{zoom_expr}':x='{style['x']}':y='{style['y']}':"
            f"d={total_frames}:s={TARGET_W}x{TARGET_H}:fps=25,setpts=PTS-STARTPTS"
        ),
        "-c:v", "libx264", "-preset", "ultrafast",
        "-t", str(duration), "-pix_fmt", "yuv420p",
        output_clip_path
    ], "анимация кадра")

    if os.path.exists(normalized_png):
        try:
            os.remove(normalized_png)
        except Exception:
            pass

    if not os.path.exists(output_clip_path) or os.path.getsize(output_clip_path) == 0:
        raise RuntimeError(
            f"Не удалось создать клип из фото {os.path.basename(photo_path)}"
        )

    return output_clip_path