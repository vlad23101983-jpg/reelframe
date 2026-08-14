"""
Kinomotor — app/zimage_generator.py
Генерация кадров через собственный RunPod (Z-Image-Turbo, ComfyUI API).
Экспериментальный источник — работает независимо от Gemini/Veo, чтобы
ничего не задеть в уже проверенных сценариях.

Требует в .env:
    RUNPOD_COMFYUI_URL=https://<pod-id>-8188.proxy.runpod.net
(без слэша на конце; актуальный адрес виден на странице пода в RunPod)
"""

import os
import time
import uuid
import random
import asyncio
import requests

RUNPOD_COMFYUI_URL = os.getenv("RUNPOD_COMFYUI_URL", "").rstrip("/")

POLL_INTERVAL_SECONDS = 2
MAX_WAIT_SECONDS = 120

# Workflow в API-формате, экспортированный из ComfyUI (граф "image_z_image_turbo").
# Промпт, размеры и seed подставляются перед каждым запросом.
WORKFLOW_TEMPLATE = {
    "9": {
        "inputs": {"filename_prefix": "z-image-turbo", "images": ["57:8", 0]},
        "class_type": "SaveImage",
    },
    "57:30": {
        "inputs": {"clip_name": "qwen_3_4b.safetensors", "type": "lumina2", "device": "default"},
        "class_type": "CLIPLoader",
    },
    "57:29": {
        "inputs": {"vae_name": "ae.safetensors"},
        "class_type": "VAELoader",
    },
    "57:33": {
        "inputs": {"conditioning": ["57:27", 0]},
        "class_type": "ConditioningZeroOut",
    },
    "57:8": {
        "inputs": {"samples": ["57:3", 0], "vae": ["57:29", 0]},
        "class_type": "VAEDecode",
    },
    "57:28": {
        "inputs": {"unet_name": "z_image_turbo_bf16.safetensors", "weight_dtype": "default"},
        "class_type": "UNETLoader",
    },
    "57:27": {
        "inputs": {"text": "", "clip": ["57:30", 0]},
        "class_type": "CLIPTextEncode",
    },
    "57:13": {
        "inputs": {"width": 720, "height": 1280, "batch_size": 1},
        "class_type": "EmptySD3LatentImage",
    },
    "57:11": {
        "inputs": {"shift": 3, "model": ["57:28", 0]},
        "class_type": "ModelSamplingAuraFlow",
    },
    "57:3": {
        "inputs": {
            "seed": 0,
            "steps": 8,
            "cfg": 1,
            "sampler_name": "res_multistep",
            "scheduler": "simple",
            "denoise": 1,
            "model": ["57:11", 0],
            "positive": ["57:27", 0],
            "negative": ["57:33", 0],
            "latent_image": ["57:13", 0],
        },
        "class_type": "KSampler",
    },
}


def _build_workflow(prompt: str, width: int, height: int) -> dict:
    import copy
    workflow = copy.deepcopy(WORKFLOW_TEMPLATE)
    workflow["57:27"]["inputs"]["text"] = prompt
    workflow["57:13"]["inputs"]["width"] = width
    workflow["57:13"]["inputs"]["height"] = height
    workflow["57:3"]["inputs"]["seed"] = random.randint(0, 2**32 - 1)
    return workflow


def _generate_image_bytes_sync(prompt: str, width: int = 720, height: int = 1280) -> bytes:
    """Отправляет workflow в ComfyUI на RunPod, ждёт результат, скачивает картинку."""
    if not RUNPOD_COMFYUI_URL:
        raise RuntimeError("RUNPOD_COMFYUI_URL не задан в .env")

    client_id = uuid.uuid4().hex
    workflow = _build_workflow(prompt, width, height)

    resp = requests.post(
        f"{RUNPOD_COMFYUI_URL}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=20,
    )
    resp.raise_for_status()
    prompt_id = resp.json().get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI не вернул prompt_id")

    start_time = time.time()
    while time.time() - start_time < MAX_WAIT_SECONDS:
        hist_resp = requests.get(f"{RUNPOD_COMFYUI_URL}/history/{prompt_id}", timeout=15)
        hist_resp.raise_for_status()
        history = hist_resp.json()

        if prompt_id in history:
            outputs = history[prompt_id].get("outputs", {})
            images = outputs.get("9", {}).get("images", [])
            if images:
                image_info = images[0]
                view_resp = requests.get(
                    f"{RUNPOD_COMFYUI_URL}/view",
                    params={
                        "filename": image_info["filename"],
                        "subfolder": image_info.get("subfolder", ""),
                        "type": image_info.get("type", "output"),
                    },
                    timeout=30,
                )
                view_resp.raise_for_status()
                return view_resp.content

        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(f"RunPod не ответил за {MAX_WAIT_SECONDS} сек")


async def generate_zimage_clip(prompt: str, output_clip_path: str, duration: float = 3.75, motion_style_index: int = 0) -> str:
    """
    1. Генерирует картинку 9:16 через собственный RunPod (Z-Image-Turbo).
    2. Анимирует кадр (тот же ZoomPan-эффект, что и у остальных источников).
    Если RunPod недоступен — бросает исключение (без автоматического fallback
    на Gemini, чтобы ошибка была явной во время тестирования).
    """
    img_path = output_clip_path.replace(".mp4", ".png")

    image_bytes = await asyncio.to_thread(_generate_image_bytes_sync, prompt, 720, 1280)
    with open(img_path, "wb") as f:
        f.write(image_bytes)

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
        output_clip_path,
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()

    if os.path.exists(img_path):
        try:
            os.remove(img_path)
        except Exception:
            pass

    if not os.path.exists(output_clip_path) or os.path.getsize(output_clip_path) == 0:
        raise RuntimeError("Не удалось собрать клип из картинки RunPod")

    return output_clip_path
