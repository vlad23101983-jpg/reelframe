"""
Kinomotor — auth.py
Вход по коду на email.
"""

import random
import string
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Response, Request
from pydantic import BaseModel, EmailStr

from db import get_db
from mailer import send_code_email

router = APIRouter(prefix="/api/auth", tags=["auth"])

CODE_TTL_MINUTES = 10
SESSION_TTL_DAYS = 30
SESSION_COOKIE_NAME = "session_token"


class RequestCodeBody(BaseModel):
    email: EmailStr


class VerifyCodeBody(BaseModel):
    email: EmailStr
    code: str


def generate_code() -> str:
    return "".join(random.choices(string.digits, k=6))


@router.post("/request-code")
async def request_code(body: RequestCodeBody):
    code = generate_code()
    expires_at = (datetime.utcnow() + timedelta(minutes=CODE_TTL_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")

    db = get_db()
    try:
        db.execute(
            "INSERT INTO login_codes (email, code, expires_at) VALUES (?, ?, ?)",
            (body.email, code, expires_at),
        )
        db.commit()
    finally:
        db.close()

    print(f"[DEV] Код для {body.email}: {code}", flush=True)

    try:
        send_code_email(body.email, code)
    except Exception as e:
        print(f"Письмо не доставлено на {body.email} (код всё равно рабочий): {e}", flush=True)

    return {"ok": True}


@router.post("/verify-code")
async def verify_code(body: VerifyCodeBody, response: Response):
    db = get_db()
    try:
        row = db.execute(
            """
            SELECT id FROM login_codes
            WHERE email = ? AND code = ? AND used = 0 AND expires_at > datetime('now')
            ORDER BY id DESC LIMIT 1
            """,
            (body.email, body.code),
        ).fetchone()

        if not row:
            return {"ok": False, "error": "Код неверный или истёк"}

        db.execute("UPDATE login_codes SET used = 1 WHERE id = ?", (row["id"],))

        user = db.execute("SELECT id FROM users WHERE email = ?", (body.email,)).fetchone()
        if user:
            user_id = user["id"]
        else:
            cur = db.execute("INSERT INTO users (email) VALUES (?)", (body.email,))
            user_id = cur.lastrowid

        db.commit()

        token = secrets.token_urlsafe(32)
        expires_at = (datetime.utcnow() + timedelta(days=SESSION_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
        db.commit()
    finally:
        db.close()

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        samesite="lax",
    )
    return {"ok": True}


def get_current_user(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    db = get_db()
    try:
        row = db.execute(
            """
            SELECT users.id, users.email, users.balance_kop
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND sessions.expires_at > datetime('now')
            """,
            (token,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


@router.get("/me")
async def me(request: Request):
    user = get_current_user(request)
    if not user:
        return {"logged_in": False}
    return {"logged_in": True, "email": user["email"], "balance_kop": user["balance_kop"]}

@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        db = get_db()
        try:
            db.execute("DELETE FROM sessions WHERE token = ?", (token,))
            db.commit()
        finally:
            db.close()
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}
