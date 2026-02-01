"""
Kassa AI (Freekassa API api.fk.life).
API: https://docs.freekassa.net/
- Запросы POST на https://api.fk.life/v1/ в JSON; API ключ из ЛК.
- Подпись API: сортировка параметров по ключам, значения через "|", HMAC-SHA256(строка, api_key).
- createOrder: https://docs.freekassa.net/#operation/createOrder
- Вебхук: подпись MD5 (раздел 1.7): MERCHANT_ID:AMOUNT:WEBHOOK_SECRET:MERCHANT_ORDER_ID
"""
import hashlib
import hmac
import os
import time
from typing import Optional, Tuple

import requests

from .base import get_callback_url

API_BASE = "https://api.fk.life/v1"
API_URL_ORDERS_CREATE = f"{API_BASE}/orders/create"

# Способы оплаты: 44 — СБП, 36 — карты РФ, 43 — SberPay
PAYMENT_METHOD_SBP = 44
PAYMENT_METHOD_CARD_RU = 36
PAYMENT_METHOD_SBER_PAY = 43

KASSA_AI_ALLOWED_IPS = {
    "168.119.157.136",
    "168.119.60.227",
    "178.154.197.79",
    "51.250.54.238",
}

_cached_public_ip: Optional[str] = None


def _get_public_ip() -> str:
    """Публичный IP сервера (Freekassa может сверять подпись с ним)."""
    global _cached_public_ip
    if _cached_public_ip:
        return _cached_public_ip
    env_ip = (os.getenv("SERVER_PUBLIC_IP") or "").strip()
    if env_ip:
        _cached_public_ip = env_ip
        return env_ip
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            r = requests.get(url, timeout=3)
            if r.status_code == 200 and r.text:
                ip = r.text.strip()
                if ip and len(ip.split(".")) == 4:
                    _cached_public_ip = ip
                    return ip
        except Exception:
            pass
    _cached_public_ip = "127.0.0.1"
    return _cached_public_ip


def _get_kassa_ai_config():
    """Конфиг из env (FREEEKASSA_* для Kassa AI)."""
    api_key = (os.getenv("FREEEKASSA_API_KEY") or "").strip()
    shop_raw = (os.getenv("FREEEKASSA_SHOP_ID") or "").strip()
    webhook_secret = (os.getenv("FREEEKASSA_WEBHOOK_SECRET") or "").strip()
    secret1 = (os.getenv("FREEEKASSA_SECRET1") or "").strip()
    use_api_key = (os.getenv("FREEEKASSA_USE_API_KEY_FOR_SIGN") or "").strip().lower() in ("1", "true", "yes")
    use_secret1 = (os.getenv("FREEEKASSA_USE_SECRET1_FOR_SIGN") or "").strip().lower() in ("1", "true", "yes")
    if not api_key or not shop_raw:
        return None
    try:
        shop_id = int(shop_raw)
    except ValueError:
        return None
    sign_key = (secret1 if (secret1 and use_secret1) else api_key) if use_secret1 else api_key
    return {
        "api_key": api_key,
        "shop_id": shop_id,
        "webhook_secret": webhook_secret,
        "sign_key": sign_key,
    }


def _signature(data: dict, sign_key: str) -> str:
    """Подпись по доке 2.2: ksort, implode('|', values), HMAC-SHA256(строка, key)."""
    keys = sorted(k for k in data if k != "signature")
    payload = "|".join(str(data[k]) for k in keys)
    return hmac.new(
        sign_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_kassa_ai_payment(
    amount: float,
    currency: str,
    order_id: str,
    user_email: Optional[str] = None,
    ip: Optional[str] = None,
    payment_method_id: Optional[int] = None,
    notification_url: Optional[str] = None,
    **kwargs,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Создать платёж через Kassa AI (api.fk.life).
    Returns:
        (payment_url, order_id) или (None, error_message)
    """
    cfg = _get_kassa_ai_config()
    if not cfg:
        return None, "Kassa AI не настроен (FREEEKASSA_API_KEY, FREEEKASSA_SHOP_ID)"

    amount = round(amount, 2)
    if amount <= 0:
        return None, "Сумма должна быть больше 0"

    final_amount = int(amount) if float(amount).is_integer() else amount
    nonce = int(time.time_ns())
    i = payment_method_id if payment_method_id is not None else PAYMENT_METHOD_SBP

    use_ip = (ip or "").strip()
    if not use_ip or use_ip in ("127.0.0.1", "localhost", "::1"):
        use_ip = _get_public_ip()

    email = (user_email or "").strip() or f"client{order_id}@telegram.org"
    cp_currency = (currency or "RUB").upper()
    if cp_currency not in ("RUB", "USD", "EUR", "UAH", "KZT"):
        cp_currency = "RUB"

    notification_url = notification_url or get_callback_url("kassa_ai")
    if notification_url and not notification_url.startswith("http"):
        base_url = os.getenv("YOUR_SERVER_IP") or os.getenv("YOUR_SERVER_IP_OR_DOMAIN", "")
        if not base_url.startswith(("http://", "https://")):
            base_url = f"https://{base_url}" if base_url else ""
        notification_url = f"{base_url}{notification_url}" if base_url else ""

    data = {
        "shopId": cfg["shop_id"],
        "nonce": nonce,
        "paymentId": order_id,
        "i": i,
        "email": email,
        "ip": use_ip,
        "amount": final_amount,
        "currency": cp_currency,
    }
    if notification_url:
        data["notification_url"] = notification_url
    data["signature"] = _signature(data, cfg["sign_key"])

    try:
        r = requests.post(API_URL_ORDERS_CREATE, json=data, headers={"Content-Type": "application/json"}, timeout=15)
        resp_text = (r.text or "").strip()[:500]
        if r.status_code != 200:
            err_msg = f"Kassa AI API: {r.status_code}"
            if resp_text:
                try:
                    rb = r.json()
                    err_msg += f" — {rb.get('message') or rb.get('error') or rb.get('msg') or resp_text[:200]}"
                except Exception:
                    err_msg += f" — {resp_text[:200]}"
            return None, err_msg
        resp = r.json()
        if resp.get("type") != "success":
            return None, resp.get("message", "Ошибка создания заказа")
        location = resp.get("location") or (r.headers.get("Location") if r.headers else None)
        if not location:
            return None, "Нет ссылки на оплату в ответе"
        return location, order_id
    except Exception as e:
        return None, str(e)


def verify_kassa_ai_webhook(request) -> Tuple[Optional[str], Optional[str]]:
    """
    Проверяет вебхук Kassa AI (Freekassa).
    Возвращает (MERCHANT_ORDER_ID, None) при успехе или (None, error).
    """
    webhook_secret = (os.getenv("FREEEKASSA_WEBHOOK_SECRET") or "").strip()
    if not webhook_secret:
        return None, "webhook_secret_not_configured"

    remote_ip = (
        request.headers.get("X-Real-IP")
        or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        or getattr(request, "remote_addr", None)
    )
    if remote_ip and remote_ip not in KASSA_AI_ALLOWED_IPS:
        return None, "invalid_ip"

    merchant_id = request.values.get("MERCHANT_ID")
    amount = request.values.get("AMOUNT")
    merchant_order_id = request.values.get("MERCHANT_ORDER_ID")
    sign = request.values.get("SIGN")
    if not all([merchant_id, amount, merchant_order_id, sign]):
        return None, "missing_params"

    expected = hashlib.md5(
        f"{merchant_id}:{amount}:{webhook_secret}:{merchant_order_id}".encode()
    ).hexdigest()
    if sign.lower() != expected.lower():
        return None, "wrong_sign"
    return merchant_order_id, None
