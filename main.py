"""
Kinomotor — веб-сервис генерации коротких видео.

"Запуск на VPS:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from dotenv import load_dotenv
load_dotenv()

import asyncio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from db import init_db
from auth import router as auth_router
from pages import router as pages_router
from account import router as account_router
from generate import router as generate_router
from payments import router as payments_router
from admin import router as admin_router
from support import router as support_router
from cleanup import run_cleanup_loop

app = FastAPI(title="Kinomotor")


@app.on_event("startup")
async def on_startup():
    init_db()
    asyncio.create_task(run_cleanup_loop())


app.include_router(auth_router)
app.include_router(pages_router)
app.include_router(account_router)
app.include_router(generate_router)
app.include_router(payments_router)
app.include_router(admin_router)
app.include_router(support_router)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/media", StaticFiles(directory="media"), name="media")

