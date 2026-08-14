"""
Kinomotor — admin.py
Админка: пользователи/балансы, платежи, ошибки генераций, статистика.
Доступ только для email, указанного в .env как ADMIN_EMAIL — без него раздел
ведёт себя как несуществующая страница (404), чтобы не выдавать посторонним
даже сам факт существования /admin.
"""

import os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from db import get_db
from auth import get_current_user

router = APIRouter(tags=["admin"])
templates = Jinja2Templates(directory="templates")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")


def _is_admin(request: Request) -> bool:
    user = get_current_user(request)
    if not user or not ADMIN_EMAIL:
        return False
    return user["email"] == ADMIN_EMAIL


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not _is_admin(request):
        return HTMLResponse("Not Found", status_code=404)
    return templates.TemplateResponse("admin.html", {"request": request})


@router.get("/api/admin/users")
async def admin_users(request: Request):
    if not _is_admin(request):
        return JSONResponse({"error": "not_found"}, status_code=404)

    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT u.id, u.email, u.balance_kop, u.created_at,
                   (SELECT COUNT(*) FROM generations g WHERE g.user_id = u.id) AS generations_count
            FROM users u
            ORDER BY u.id DESC
            """
        ).fetchall()
    finally:
        db.close()

    return {"users": [dict(r) for r in rows]}


@router.get("/api/admin/payments")
async def admin_payments(request: Request):
    if not _is_admin(request):
        return JSONResponse({"error": "not_found"}, status_code=404)

    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT p.id, u.email, p.amount_kop, p.status, p.created_at
            FROM payments p
            JOIN users u ON u.id = p.user_id
            ORDER BY p.id DESC
            LIMIT 200
            """
        ).fetchall()
    finally:
        db.close()

    return {"payments": [dict(r) for r in rows]}


@router.get("/api/admin/errors")
async def admin_errors(request: Request):
    if not _is_admin(request):
        return JSONResponse({"error": "not_found"}, status_code=404)

    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT g.id, u.email, g.topic, g.source, g.duration, g.error_message, g.created_at
            FROM generations g
            JOIN users u ON u.id = g.user_id
            WHERE g.status = 'error'
            ORDER BY g.id DESC
            LIMIT 200
            """
        ).fetchall()
    finally:
        db.close()

    return {"errors": [dict(r) for r in rows]}


@router.get("/api/admin/stats")
async def admin_stats(request: Request):
    if not _is_admin(request):
        return JSONResponse({"error": "not_found"}, status_code=404)

    db = get_db()
    try:
        total_users = db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        total_generations = db.execute("SELECT COUNT(*) AS c FROM generations").fetchone()["c"]
        today_generations = db.execute(
            "SELECT COUNT(*) AS c FROM generations WHERE date(created_at) = date('now')"
        ).fetchone()["c"]
        total_errors = db.execute(
            "SELECT COUNT(*) AS c FROM generations WHERE status = 'error'"
        ).fetchone()["c"]
        total_revenue_kop = db.execute(
            "SELECT COALESCE(SUM(amount_kop), 0) AS s FROM payments WHERE status = 'CONFIRMED'"
        ).fetchone()["s"]
    finally:
        db.close()

    error_rate = round((total_errors / total_generations * 100), 1) if total_generations else 0

    return {
        "total_users": total_users,
        "total_generations": total_generations,
        "today_generations": today_generations,
        "total_revenue_rub": total_revenue_kop // 100,
        "total_errors": total_errors,
        "error_rate_percent": error_rate,
    }


class TopUpBody(BaseModel):
    user_id: int
    amount_rub: int


@router.post("/api/admin/topup")
async def admin_topup(body: TopUpBody, request: Request):
    if not _is_admin(request):
        return JSONResponse({"error": "not_found"}, status_code=404)

    if body.amount_rub <= 0:
        return JSONResponse({"error": "invalid_amount", "message": "Сумма должна быть больше нуля"}, status_code=400)

    db = get_db()
    try:
        db.execute(
            "UPDATE users SET balance_kop = balance_kop + ? WHERE id = ?",
            (body.amount_rub * 100, body.user_id),
        )
        db.commit()
    finally:
        db.close()

    return {"ok": True}
