"""
Kinomotor — payments.py
Пополнение баланса через Т-Банк (интернет-эквайринг).
"""

import os
import uuid
import asyncio
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


def apply_payment_status(order_id: str, status: str, payment_id=None, source: str = "webhook") -> None:
    """
    Единственное место, где платёж переводится в CONFIRMED и пополняется баланс.
    Вызывается из двух мест — вебхука и фоновой сверки, — поэтому защита от
    повторного начисления обязательна.

    Защита устроена так: перевод в CONFIRMED делается одним запросом с условием
    status != 'CONFIRMED'. Баланс пополняется, только если этот запрос реально
    изменил строку (rowcount == 1). Кто пришёл вторым — увидит rowcount == 0
    и не начислит ничего. Сумма всегда берётся из своей базы, а не из ответа
    банка, чтобы её нельзя было подменить снаружи.
    """
    if not order_id or not status:
        return

    db = get_db()
    try:
        payment = db.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,)).fetchone()
        if not payment:
            print(f"[{source}] Платёж не найден в базе, OrderId={order_id}", flush=True)
            return

        if payment["status"] == "CONFIRMED":
            return

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if status != "CONFIRMED":
            # Промежуточные и отменённые статусы просто фиксируем, денег не трогаем.
            db.execute(
                "UPDATE payments SET status = ?, payment_id = COALESCE(?, payment_id), updated_at = ? "
                "WHERE order_id = ? AND status != 'CONFIRMED'",
                (status, payment_id, now, order_id),
            )
            db.commit()
            return

        cur = db.execute(
            "UPDATE payments SET status = 'CONFIRMED', payment_id = COALESCE(?, payment_id), updated_at = ? "
            "WHERE order_id = ? AND status != 'CONFIRMED'",
            (payment_id, now, order_id),
        )

        if cur.rowcount != 1:
            # Кто-то другой уже подтвердил этот платёж — начислять второй раз нельзя.
            db.commit()
            return

        db.execute(
            "UPDATE users SET balance_kop = balance_kop + ? WHERE id = ?",
            (payment["amount_kop"], payment["user_id"]),
        )
        db.commit()
        print(
            f"[{source}] Баланс пополнен: user_id={payment['user_id']}, "
            f"+{payment['amount_kop']} коп., OrderId={order_id}",
            flush=True,
        )
    finally:
        db.close()


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

    apply_payment_status(
        order_id=body.get("OrderId"),
        status=body.get("Status"),
        payment_id=body.get("PaymentId"),
        source="webhook",
    )

    return PlainTextResponse("OK")


# ---------------------------------------------------------------------------
# Фоновая сверка платежей
# ---------------------------------------------------------------------------
# Вебхук может не дойти: сервис перезапускался, был недоступен, банк не достучался.
# Тогда деньги с карты списаны, а баланс не пополнен, и починить это можно только
# руками через админку. Поэтому раз в несколько минут сами спрашиваем у банка
# статус платежей, которые ещё не в конечном состоянии.

TINKOFF_GETSTATE_URL = "https://securepay.tinkoff.ru/v2/GetState"

RECONCILE_INTERVAL_SECONDS = 180   # как часто спрашивать банк
RECONCILE_MAX_AGE_HOURS = 72       # платежи старше уже не изменятся

# Статусы, после которых платёж больше не меняется — их не опрашиваем.
FINAL_STATUSES = (
    "CONFIRMED", "REJECTED", "CANCELED", "DEADLINE_EXPIRED",
    "REVERSED", "REFUNDED", "PARTIAL_REFUNDED",
)


def _fetch_payment_state(payment_id: str):
    """Спрашивает у банка текущий статус платежа. Возвращает статус или None."""
    payload = {"TerminalKey": TINKOFF_TERMINAL_KEY, "PaymentId": str(payment_id)}
    payload["Token"] = make_token(payload)

    response = requests.post(
        TINKOFF_GETSTATE_URL, json=payload, timeout=15,
        verify="/etc/ssl/certs/ca-certificates.crt",
    )
    data = response.json()

    if not data.get("Success"):
        print(f"[сверка] Банк вернул ошибку по PaymentId={payment_id}: {data.get('Message')}", flush=True)
        return None

    return data.get("Status")


async def _reconcile_once():
    placeholders = ",".join("?" for _ in FINAL_STATUSES)
    db = get_db()
    try:
        rows = db.execute(
            f"""
            SELECT order_id, payment_id FROM payments
            WHERE payment_id IS NOT NULL
              AND status NOT IN ({placeholders})
              AND created_at > datetime('now', '-{RECONCILE_MAX_AGE_HOURS} hours')
            """,
            FINAL_STATUSES,
        ).fetchall()
    finally:
        db.close()

    for row in rows:
        try:
            # Сетевой запрос уводим в отдельный поток, чтобы не блокировать сервер.
            status = await asyncio.to_thread(_fetch_payment_state, row["payment_id"])
        except Exception as e:
            print(f"[сверка] Не удалось спросить банк по OrderId={row['order_id']}: {e}", flush=True)
            continue

        if status:
            apply_payment_status(
                order_id=row["order_id"],
                status=status,
                payment_id=row["payment_id"],
                source="сверка",
            )


async def run_payment_reconcile_loop():
    """Запускается один раз при старте приложения (main.py)."""
    if not TINKOFF_TERMINAL_KEY or not TINKOFF_PASSWORD:
        print("[сверка] Ключи Т-Банка не заданы — фоновая сверка платежей не запущена", flush=True)
        return

    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            await _reconcile_once()
        except Exception as e:
            # Сверка ни при каких условиях не должна ронять сервис.
            print(f"[сверка] Ошибка в цикле сверки платежей: {e}", flush=True)
