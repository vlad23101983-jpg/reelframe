"""
Kinomotor — pages.py
Простые роуты, которые просто отдают HTML-страницы.
"""

from fastapi import APIRouter, Request
import os
import subprocess
from datetime import datetime
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="templates")


SITE_URL = os.getenv("SITE_URL", "https://kinomotor.com").rstrip("/")

# В карту сайта идут только страницы, открытые для индексации. Всё, что
# закрыто в robots.txt (кабинет, вход, поддержка, админка), сюда попадать
# не должно — иначе поисковик получает противоречивые указания.
PUBLIC_PAGES = [
    {"path": "/", "template": "index.html", "priority": "1.0", "changefreq": "weekly"},
    {"path": "/create", "template": "create.html", "priority": "0.8", "changefreq": "monthly"},
]


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /account\n"
        "Disallow: /login\n"
        "Disallow: /support\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        "\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )


@router.get("/sitemap.xml", response_class=Response)
async def sitemap_xml():
    """
    Карта сайта для поисковиков.

    Дата изменения берётся из времени правки шаблона страницы — так она
    отражает реальные изменения, а не подставляется текущим числом каждый
    день. Постоянно свежая дата на неизменившейся странице подрывает
    доверие поисковика к остальным датам в карте.
    """
    entries = []
    for page in PUBLIC_PAGES:
        lastmod = ""
        template_path = os.path.join("templates", page["template"])
        try:
            mtime = os.path.getmtime(template_path)
            lastmod = f"    <lastmod>{datetime.utcfromtimestamp(mtime).strftime('%Y-%m-%d')}</lastmod>\n"
        except OSError:
            pass

        entries.append(
            "  <url>\n"
            f"    <loc>{SITE_URL}{page['path']}</loc>\n"
            f"{lastmod}"
            f"    <changefreq>{page['changefreq']}</changefreq>\n"
            f"    <priority>{page['priority']}</priority>\n"
            "  </url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "examples": get_examples()})


@router.get("/create", response_class=HTMLResponse)
async def create_page(request: Request):
    return templates.TemplateResponse("create.html", {"request": request})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    return templates.TemplateResponse("account.html", {"request": request})

def static_version(path):
    """Возвращает время изменения файла — используется как версия в ссылках на CSS/JS,
    чтобы браузер сам подхватывал новый файл после каждой правки, без ручного ?v=N."""
    full_path = os.path.join("static", path)
    try:
        return int(os.path.getmtime(full_path))
    except OSError:
        return 0

templates.env.globals["static_version"] = static_version

def get_examples():
    """
    Сканирует media/examples/ на видеофайлы.
    Для каждого — генерирует превью-картинку (если ещё нет) и достаёт длительность.
    Возвращает список для карусели на главной.
    """
    examples_dir = os.path.join("media", "examples")
    thumbs_dir = os.path.join(examples_dir, "thumbs")
    os.makedirs(examples_dir, exist_ok=True)
    os.makedirs(thumbs_dir, exist_ok=True)

    result = []
    for filename in sorted(os.listdir(examples_dir)):
        if not filename.lower().endswith(".mp4"):
            continue

        video_path = os.path.join(examples_dir, filename)
        base_name = os.path.splitext(filename)[0]
        thumb_filename = f"{base_name}.jpg"
        thumb_path = os.path.join(thumbs_dir, thumb_filename)

        if not os.path.exists(thumb_path):
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01",
                 "-vframes", "1", "-vf", "scale=400:-1", thumb_path],
                capture_output=True,
            )

        duration = "00:00"
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True,
            )
            seconds = int(float(probe.stdout.strip()))
            duration = f"00:{seconds:02d}"
        except Exception:
            pass

        title = base_name.replace("_", " ").replace("-", " ")

        result.append({
            "video_url": f"/media/examples/{filename}",
            "thumb_url": f"/media/examples/thumbs/{thumb_filename}" if os.path.exists(thumb_path) else "",
            "duration": duration,
            "title": title,
        })

    return result
