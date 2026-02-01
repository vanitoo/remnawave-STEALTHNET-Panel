"""
YooMoney (ЮMoney) - платежи на кошелек через payment buttons (quickpay)

Формирование ссылки на оплату:
https://yoomoney.ru/quickpay/confirm.xml

Подтверждение оплаты через HTTP-уведомления (webhook) реализуется в:
modules/api/webhooks/routes.py -> /api/webhook/yoomoney
"""

from urllib.parse import urlencode

from modules.api.payments.base import get_payment_settings, decrypt_key, get_return_url


YOOMONEY_QUICKPAY_URL = "https://yoomoney.ru/quickpay/confirm.xml"


def create_yoomoney_payment(amount: float, currency: str, order_id: str, **kwargs):
    """
    Создать платеж через YooMoney Quickpay форму (redirect).

    Args:
        amount: сумма
        currency: валюта (поддерживается только RUB)
        order_id: ID заказа (пишем в label)

    Returns:
        tuple: (payment_url, payment_system_id) или (None, error_message)
    """
    settings = get_payment_settings()
    if not settings:
        return None, "Payment settings not configured"

    receiver = decrypt_key(getattr(settings, "yoomoney_receiver", None)) if getattr(settings, "yoomoney_receiver", None) else ""
    receiver = (receiver or "").strip()
    if not receiver:
        return None, "YooMoney receiver is not configured"

    # YooMoney notifications / currency code are RUB (643). Для простоты ограничиваемся RUB.
    if (currency or "").upper() != "RUB":
        return None, "YooMoney supports only RUB currency"

    # По документации payment buttons можно указать paymentType:
    # - AC: банковская карта
    # - PC: кошелек YooMoney
    payment_type = (kwargs.get("payment_type") or kwargs.get("paymentType") or "AC").strip().upper()
    if payment_type not in ("AC", "PC"):
        payment_type = "AC"

    return_url = get_return_url(kwargs.get("source", "miniapp"), kwargs.get("miniapp_type", "v2"))
    targets = kwargs.get("description") or f"StealthNET #{order_id}"

    params = {
        "receiver": receiver,
        "quickpay-form": "shop",
        "targets": targets,
        "sum": f"{float(amount):.2f}",
        "paymentType": payment_type,
        "label": str(order_id)[:64],  # label ограничен (обычно 64 символа)
        "successURL": return_url,
    }

    payment_url = f"{YOOMONEY_QUICKPAY_URL}?{urlencode(params)}"
    # В момент создания операции_id еще нет, поэтому возвращаем order_id (label)
    return payment_url, str(order_id)

