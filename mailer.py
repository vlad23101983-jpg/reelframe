"""
Kinomotor — mailer.py
Отправка писем с кодом входа через Resend.
Данные берутся из переменных окружения (.env):
    RESEND_API_KEY
Письма отправляются с адреса noreply@info.kinomotor.com
(поддомен info.kinomotor.com подтверждён в Resend).
"""

import os
import requests

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "Kinomotor <noreply@info.kinomotor.com>")

API_URL = "https://api.resend.com/emails"


def send_code_email(to_email: str, code: str) -> None:
    """
    Отправляет письмо с 6-значным кодом входа через Resend.
    Бросает исключение, если отправка не удалась.
    """
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 400px; margin: 0 auto; padding: 24px;">
      <p style="color: #1C1A16; font-size: 15px;">Ваш код для входа в Kinomotor:</p>
      <p style="font-size: 32px; font-weight: 700; letter-spacing: 4px; color: #C1502E; margin: 16px 0;">{code}</p>
      <p style="color: #8E8877; font-size: 13px;">Код действителен 10 минут. Если вы не запрашивали вход — просто проигнорируйте это письмо.</p>
    </div>
    """

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": f"Код для входа: {code}",
        "html": html_body,
    }

    response = requests.post(API_URL, headers=headers, json=payload, timeout=15)

    if response.status_code >= 400:
        raise RuntimeError(f"Resend ошибка ({response.status_code}): {response.text}")