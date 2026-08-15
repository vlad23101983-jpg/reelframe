"""
Kinomotor — support.py
Тикеты поддержки: пользователь создаёт обращение и переписывается с администратором.
Ответы админ даёт через вкладку "Поддержка" в /admin.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

from db import get_db
from auth import get_current_user
from pages import templates

router = APIRouter(tags=["support"])


@router.get("/support", response_class=HTMLResponse)
async def support_page(request: Request):
    return templates.TemplateResponse("support.html", {"request": request})


@router.get("/api/support/tickets")
async def list_tickets(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT id, subject, status, created_at, updated_at
            FROM support_tickets
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user["id"],),
        ).fetchall()
    finally:
        db.close()

    return {"tickets": [dict(r) for r in rows]}


class NewTicketBody(BaseModel):
    subject: str
    message: str


@router.post("/api/support/tickets")
async def create_ticket(body: NewTicketBody, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    subject = body.subject.strip()
    message = body.message.strip()
    if not subject or not message:
        return JSONResponse({"error": "invalid", "message": "Заполните тему и сообщение"}, status_code=400)

    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO support_tickets (user_id, subject) VALUES (?, ?)",
            (user["id"], subject),
        )
        ticket_id = cur.lastrowid
        db.execute(
            "INSERT INTO support_messages (ticket_id, sender, message) VALUES (?, 'user', ?)",
            (ticket_id, message),
        )
        db.commit()
    finally:
        db.close()

    return {"ok": True, "ticket_id": ticket_id}


@router.get("/api/support/tickets/{ticket_id}")
async def get_ticket(ticket_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    db = get_db()
    try:
        ticket = db.execute(
            "SELECT id, subject, status, created_at FROM support_tickets WHERE id = ? AND user_id = ?",
            (ticket_id, user["id"]),
        ).fetchone()
        if not ticket:
            return JSONResponse({"error": "not_found"}, status_code=404)

        messages = db.execute(
            "SELECT sender, message, created_at FROM support_messages WHERE ticket_id = ? ORDER BY id ASC",
            (ticket_id,),
        ).fetchall()
    finally:
        db.close()

    return {"ticket": dict(ticket), "messages": [dict(m) for m in messages]}


class ReplyBody(BaseModel):
    message: str


@router.post("/api/support/tickets/{ticket_id}/reply")
async def reply_ticket(ticket_id: int, body: ReplyBody, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    message = body.message.strip()
    if not message:
        return JSONResponse({"error": "invalid", "message": "Введите сообщение"}, status_code=400)

    db = get_db()
    try:
        ticket = db.execute(
            "SELECT id FROM support_tickets WHERE id = ? AND user_id = ?",
            (ticket_id, user["id"]),
        ).fetchone()
        if not ticket:
            return JSONResponse({"error": "not_found"}, status_code=404)

        db.execute(
            "INSERT INTO support_messages (ticket_id, sender, message) VALUES (?, 'user', ?)",
            (ticket_id, message),
        )
        db.execute(
            "UPDATE support_tickets SET status = 'open', updated_at = datetime('now') WHERE id = ?",
            (ticket_id,),
        )
        db.commit()
    finally:
        db.close()

    return {"ok": True}
