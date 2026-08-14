"""
Kinomotor — payments.py
Пополнение баланса через Т-Банк (интернет-эквайринг).
"""

import os
import uuid
import hashlib
import requests
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from db import get_db
from auth import get_current_user

router = APIRouter(prefix="/api/payment", tags=["payment"])

TINKOFF_TERMINAL_KEY = os.getenv("TINKOFF_TERMINAL_KEY")
TINKOFF_PASSWORD = os.getenv("TINKOFF_PASSWORD")
TINKOFF_INIT_URL = "https://securepay.tinkoff.ru/v2/Init"

SITE_URL = "https://kinomotor.com"


def make_token(params: dict) -> str:
    """
    Формирует подпись запроса по алгоритму Т-Банка:
    добавить пароль, отсортировать по ключу, склеить значения, SHA-256.
    Логические значения (True/False) переводим в нижний регистр,
    как того требует банк (true/false, а не Python True/False).
    """
    data = dict(params)
    data["Password"] = TINKOFF_PASSWORD
    sorted_items = sorted(data.items(), key=lambda x: x[0])
    parts = []
    for _, v in sorted_items:
        if isinstance(v, bool):
            parts.append("true" if v else "false")
        else:
            parts.append(str(v))
    concatenated = "".join(parts)
    return hashlib.sha256(concatenated.encode("utf-8")).hexdigest()


class CreatePaymentBody(BaseModel):
    amount_rub: int


@router.post("/create")
async def create_payment(body: CreatePaymentBody, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    if body.amount_rub < 10:
        return JSONResponse({"error": "min_amount", "message": "Минимальная сумма пополнения — 10 ₽"}, status_code=400)

    amount_kop = body.amount_rub * 100
    order_id = f"topup_{uuid.uuid4().hex[:12]}"

    payload = {
        "TerminalKey": TINKOFF_TERMINAL_KEY,
        "Amount": amount_kop,
        "OrderId": order_id,
        "Description": "Пополнение баланса Kinomotor",
        "NotificationURL": f"{SITE_URL}/api/payment/webhook",
        "SuccessURL": f"{SITE_URL}/account?payment=success",
        "FailURL": f"{SITE_URL}/account?payment=fail",
    }
    token = make_token(payload)
    payload["Token"] = token

    response = requests.post(TINKOFF_INIT_URL, json=payload, timeout=15, verify="/etc/ssl/certs/ca-certificates.crt")
    data = response.json()

    if not data.get("Success"):
        return JSONResponse({"error": "tinkoff_error", "message": data.get("Message", "Ошибка платёжной системы")}, status_code=502)

    db = get_db()
    try:
        db.execute(
            "INSERT INTO payments (user_id, order_id, amount_kop, status, payment_id) VALUES (?, ?, ?, ?, ?)",
            (user["id"], order_id, amount_kop, "NEW", data.get("PaymentId")),
        )
        db.commit()
    finally:
        db.close()

    return {"payment_url": data.get("PaymentURL")}


@router.post("/webhook")
async def payment_webhook(request: Request):
    body = await request.json()

    received_token = body.get("Token", "")
    check_params = {
        k: v for k, v in body.items()
        if k not in ("Token", "Data", "Receipt") and not isinstance(v, (dict, list))
    }
    expected_token = make_token(check_params)

    if received_token != expected_token:
        print(f"Webhook: неверная подпись, отклонено. OrderId={body.get('OrderId')}", flush=True)
        return PlainTextResponse("OK")

    order_id = body.get("OrderId")
    status = body.get("Status")
    payment_id = body.get("PaymentId")

    db = get_db()
    try:
        payment = db.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchone()

        if not payment:
            print(f"Webhook: платёж не найден в базе, OrderId={order_id}", flush=True)
            return PlainTextResponse("OK")

        if payment["status"] == "CONFIRMED":
            return PlainTextResponse("OK")

        db.execute(
            "UPDATE payments SET status = ?, payment_id = ?, updated_at = ? WHERE order_id = ?",
            (status, payment_id, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), order_id),
        )

        if status == "CONFIRMED":
            db.execute(
                "UPDATE users SET balance_kop = balance_kop + ? WHERE id = ?",
                (payment["amount_kop"], payment["user_id"]),
            )
            print(f"Баланс пополнен: user_id={payment['user_id']}, +{payment['amount_kop']} коп.", flush=True)

        db.commit()
    finally:
        db.close()

    return PlainTextResponse("OK")
