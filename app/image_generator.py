"""
Генерация статичных картинок (через Gemini Image) с последующей zoom/pan-анимацией
через ffmpeg.
"""

import os
import asyncio
import traceback
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)


async def generate_imagen_clip(prompt: str, output_clip_path: str, duration: float = 3.75, motion_style_index: int = 0) -> str:
    """
    1. Генерирует картинку 9:16 через Gemini Image.
    2. Анимирует кадр (ZoomPan с вариацией движения камеры).
    """
    print(f"🎨 Генерация кадра: {prompt[:40]}...")
    img_path = output_clip_path.replace(".mp4", ".png")
    image_saved = False

    try:
        response = await asyncio.to_thread(
            ai_client.models.generate_content,
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=genai_types.ImageConfig(aspect_ratio="9:16")
            )
        )

        image_bytes = None
        if response and response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if getattr(part, "inline_data", None) is not None:
                        image_bytes = part.inline_data.data
                        break

        if image_bytes:
            with open(img_path, "wb") as f:
                f.write(image_bytes)
            image_saved = True
            print(f"✅ Картинка сохранена: {img_path}")
        else:
            raise RuntimeError("Gemini не вернул изображение в ответе")

    except Exception as e:
        print(f"❌ Ошибка Google Image API: {e}")
        traceback.print_exc()

    if not image_saved or not os.path.exists(img_path) or os.path.getsize(img_path) == 0:
        print("⚠️ Создание цветного фона для кадра")
        cmd_fallback = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x111827:s=1080x1920:d={duration}:r=25",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            output_clip_path
        ]
        proc = await asyncio.create_subprocess_exec(*cmd_fallback, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.communicate()
        return output_clip_path

    print(f"🎬 Анимация кадра ({duration:.2f} сек)...")
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

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", img_path,
        "-vf", f"scale=1080:1920,setsar=1,zoompan=z='{zoom_expr}':x='{style['x']}':y='{style['y']}':d={total_frames}:s=1080x1920:fps=25,setpts=PTS-STARTPTS",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-t", str(duration), "-pix_fmt", "yuv420p",
        output_clip_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()

    if os.path.exists(img_path):
        try:
            os.remove(img_path)
        except Exception:
            pass

    return output_clip_path
