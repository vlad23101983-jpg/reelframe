"""
Kinomotor — account.py
Личный кабинет: баланс + история генераций.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db import get_db
from auth import get_current_user

router = APIRouter(prefix="/api", tags=["account"])


@router.get("/account")
async def api_account(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT topic, source, duration, price_kop, status, created_at
            FROM generations
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (user["id"],),
        ).fetchall()
    finally:
        db.close()

    history = [dict(r) for r in rows]
    return {
        "email": user["email"],
        "balance_kop": user["balance_kop"],
        "history": history,
    }
