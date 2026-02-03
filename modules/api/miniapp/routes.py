"""
API эндпоинты Telegram Mini App

- POST /miniapp/subscription - Данные подписки
- POST /miniapp/maintenance/status - Статус техобслуживания
- POST /miniapp/subscription/trial - Активация триала
- POST /miniapp/payments/methods - Методы оплаты
- POST /miniapp/payments/create - Создание платежа
- GET /miniapp/app-config.json - Конфигурация приложения
"""

from flask import request, jsonify
from datetime import datetime, timezone, timedelta
import math
import requests
import json
import os
import urllib.parse
import re
import uuid

from modules.core import get_app, get_db, get_cache, get_limiter, get_fernet
from modules.models.user import User
from modules.models.tariff import Tariff
from modules.models.promo import PromoCode
from modules.models.payment import Payment, PaymentSetting
from modules.models.referral import ReferralSetting
from modules.models.branding import BrandingSetting
from modules.models.user_config import UserConfig
from modules.models.option import PurchaseOption

app = get_app()
db = get_db()
cache = get_cache()
limiter = get_limiter()


def decrypt_key(key):
    """
    Расшифровка ключа из PaymentSetting.
    Поддерживает TEXT (str) и варианты PostgreSQL (memoryview/bytes).
    """
    if not key:
        return ""

    fernet = get_fernet()

    try:
        # PostgreSQL может вернуть memoryview
        if isinstance(key, memoryview):
            key = bytes(key)

        # Если ключ хранится как строка (PostgreSQL TEXT)
        if isinstance(key, str):
            # Если строка уже не зашифрована — возвращаем как есть
            if not key.startswith('gAAAAAB'):
                return key
            # Зашифрована, но нет fernet — не можем расшифровать
            if not fernet:
                return ""
            return fernet.decrypt(key.encode('utf-8')).decode('utf-8')

        # Если bytes — пробуем расшифровать
        if isinstance(key, (bytes, bytearray)):
            if not fernet:
                return ""
            return fernet.decrypt(bytes(key)).decode('utf-8')

        # Прочие типы — пытаемся привести к bytes
        if not fernet:
            return str(key)
        return fernet.decrypt(bytes(key)).decode('utf-8')
    except Exception:
        return ""


def parse_telegram_init_data(init_data):
    """Парсит initData из Telegram"""
    if not init_data:
        return None, None
    
    try:
        if isinstance(init_data, dict):
            parsed_data = init_data
        else:
            parsed_data = urllib.parse.parse_qs(init_data)
        
        user_str = parsed_data.get('user', [''])[0] if isinstance(parsed_data.get('user'), list) else parsed_data.get('user')
        
        if not user_str:
            return None, None
        
        if isinstance(user_str, str):
            user_data = json.loads(urllib.parse.unquote(user_str))
        else:
            user_data = user_str
        
        return user_data.get('id'), user_data
    except:
        return None, None


def get_referral_settings():
    return ReferralSetting.query.first()


def get_branding_settings():
    return BrandingSetting.query.first()


def get_remnawave_headers():
    """Получить заголовки для RemnaWave API"""
    headers = {}
    cookies = {}
    
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
    if ADMIN_TOKEN:
        headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"
    
    REMNAWAVE_COOKIES_STR = os.getenv("REMNAWAVE_COOKIES", "")
    if REMNAWAVE_COOKIES_STR:
        try:
            cookies = json.loads(REMNAWAVE_COOKIES_STR)
        except json.JSONDecodeError:
            pass
    
    return headers, cookies


# ============================================================================
# SUBSCRIPTION
# ============================================================================

@app.route('/miniapp/subscription', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_subscription():
    """Данные подписки пользователя"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response

    try:
        data = {}
        if request.is_json:
            data = request.json or {}
        elif request.form:
            data = dict(request.form)
        elif request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except:
                pass

        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)

        if not telegram_id:
            print(f"[MINIAPP] Missing or invalid initData: {init_data[:100] if init_data else 'None'}")
            return jsonify({
                "detail": {"title": "Authorization Error", "message": "Missing or invalid initData"}
            }), 401

        # Ищем пользователя по telegram_id (как строка)
        telegram_id_str = str(telegram_id)
        user = User.query.filter_by(telegram_id=telegram_id_str).first()
        if not user:
            print(f"[MINIAPP] User not found for telegram_id: {telegram_id_str}")
            # Возвращаем 404, чтобы старый мини-апп показал сообщение о регистрации
            return jsonify({
                "detail": {"title": "User Not Found", "message": "Please register in the bot first"}
            }), 404
        
        print(f"[MINIAPP] User found: id={user.id}, telegram_id={user.telegram_id}, email={user.email}")

        # Получаем данные из кэша
        cache_key = f'live_data_{user.remnawave_uuid}'
        cached = cache.get(cache_key)

        def adapt_data(data_dict, user_obj):
            expire_at = data_dict.get('expireAt')
            has_active = False
            if expire_at:
                try:
                    exp_raw = expire_at
                    if isinstance(exp_raw, str):
                        exp_raw = exp_raw.replace('Z', '+00:00')
                        expire_dt = datetime.fromisoformat(exp_raw)
                    else:
                        expire_dt = exp_raw
                    if hasattr(expire_dt, 'tzinfo') and expire_dt.tzinfo is None:
                        expire_dt = expire_dt.replace(tzinfo=timezone.utc)
                    has_active = expire_dt > datetime.now(timezone.utc)
                except:
                    pass

            from modules.currency import convert_from_usd
            balance_display = convert_from_usd(float(user_obj.balance) if user_obj.balance else 0.0, user_obj.preferred_currency or 'uah')
            
            # Получаем активные сквады из данных RemnaWave
            active_squads = data_dict.get('activeInternalSquads', [])

            # Нормализуем трафик: в RemnaWave usedTraffic обычно лежит в userTraffic.usedTrafficBytes
            used_traffic = data_dict.get('usedTrafficBytes', None)
            if used_traffic is None:
                ut = data_dict.get('userTraffic', {})
                if isinstance(ut, dict):
                    used_traffic = ut.get('usedTrafficBytes', 0)
            traffic_limit = data_dict.get('trafficLimitBytes', 0)
            traffic_strategy = data_dict.get('trafficLimitStrategy')
            
            # Формируем данные пользователя (совместимо со старым мини-апп)
            user_data = {
                'id': user_obj.telegram_id,
                'telegram_id': user_obj.telegram_id,
                'username': user_obj.telegram_username or f"user_{user_obj.telegram_id}",
                'email': user_obj.email,
                'uuid': data_dict.get('uuid') or user_obj.remnawave_uuid,
                'has_active_subscription': has_active,
                'subscription_status': 'active' if has_active else 'inactive',
                'expireAt': expire_at,
                'referral_code': user_obj.referral_code,
                # legacy snake_case (встречается в старых сборках miniapp)
                'traffic_used': used_traffic or 0,
                'traffic_limit': traffic_limit or 0,
                # camelCase как в RemnaWave / и в коде miniapp UI
                'usedTrafficBytes': used_traffic or 0,
                'trafficLimitBytes': traffic_limit or 0,
                'trafficLimitStrategy': traffic_strategy,
                'balance': balance_display,
                'balance_usd': float(user_obj.balance) if user_obj.balance else 0.0,
                'currency': user_obj.preferred_currency or 'uah',
                'preferred_currency': user_obj.preferred_currency or 'uah',
                'activeInternalSquads': active_squads,  # Для старого мини-апп
                'hwidDeviceLimit': data_dict.get('hwidDeviceLimit'),  # Лимит устройств из тарифа
            }
            
            # Возвращаем в формате, совместимом со старым мини-апп
            # Старый мини-апп ожидает: subscriptionData.response || subscriptionData
            # И проверяет userData.activeInternalSquads
            return {
                'response': user_data,  # Для совместимости со старым мини-апп
                'user': user_data,  # Для нового мини-апп
                'subscription_url': data_dict.get('subscriptionUrl'),
                'subscription_missing': not has_active,
                'uuid': data_dict.get('uuid') or user_obj.remnawave_uuid,
                'expireAt': expire_at,
                'activeInternalSquads': active_squads  # Для совместимости
            }

        if cached:
            response = jsonify(adapt_data(cached.copy(), user))
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 200

        # Запрос к RemnaWave
        try:
            resp = requests.get(
                f"{os.getenv('API_URL')}/api/users/{user.remnawave_uuid}",
                headers={"Authorization": f"Bearer {os.getenv('ADMIN_TOKEN')}"},
                timeout=10
            )

            if resp.status_code != 200:
                return jsonify({
                    "detail": {"title": "Error", "message": f"Failed to fetch data: {resp.status_code}"}
                }), 500

            data = resp.json().get('response', {})
            cache.set(cache_key, data, timeout=300)

            response = jsonify(adapt_data(data, user))
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 200

        except Exception as e:
            return jsonify({
                "detail": {"title": "Error", "message": str(e)}
            }), 500

    except Exception as e:
        return jsonify({
            "detail": {"title": "Error", "message": "Invalid request"}
        }), 401


@app.route('/miniapp/maintenance/status', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_maintenance_status():
    """Статус техобслуживания"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    return jsonify({"isActive": False, "is_active": False, "message": None}), 200


@app.route('/miniapp/subscription/trial', methods=['POST'])
@limiter.limit("10 per minute")
def miniapp_activate_trial():
    """Активация триала"""
    from modules.models.trial import get_trial_settings
    
    try:
        data = request.json or {}
        init_data = data.get('initData', '')
        telegram_id, _ = parse_telegram_init_data(init_data)

        if not telegram_id:
            return jsonify({"success": False, "message": "Missing initData"}), 401

        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            return jsonify({"success": False, "message": "User not registered"}), 404

        # Проверяем, использовал ли пользователь уже триал (обновляем из БД)
        db.session.refresh(user)
        if hasattr(user, 'trial_used') and user.trial_used:
            return jsonify({"success": False, "message": "Trial already used"}), 400

        # Получаем настройки триала из БД
        trial_settings = get_trial_settings()
        
        if not trial_settings.enabled:
            return jsonify({"success": False, "message": "Trial is currently disabled"}), 400
        
        # Определяем, на какой конфиг активировать триал - только на основной!
        from modules.models.user_config import UserConfig
        primary_config = UserConfig.query.filter_by(user_id=user.id, is_primary=True).first()
        
        # Если нет основного конфига, но есть remnawave_uuid - создаем его
        if not primary_config:
            if user.remnawave_uuid and '@' not in user.remnawave_uuid:
                primary_config = UserConfig(
                    user_id=user.id,
                    remnawave_uuid=user.remnawave_uuid,
                    config_name='Основной конфиг',
                    is_primary=True
                )
                db.session.add(primary_config)
                db.session.flush()
            else:
                return jsonify({"success": False, "message": "Primary config not found. Please register first."}), 400
        
        # Используем remnawave_uuid из основного конфига
        remnawave_uuid = primary_config.remnawave_uuid
        
        # Используем настройки из БД
        trial_days = trial_settings.days
        trial_devices = trial_settings.devices
        
        new_exp = (datetime.now(timezone.utc) + timedelta(days=trial_days)).isoformat()

        referral_settings = get_referral_settings()
        trial_squad_id = os.getenv("DEFAULT_SQUAD_ID")
        if referral_settings and referral_settings.trial_squad_id:
            trial_squad_id = referral_settings.trial_squad_id

        # Формируем payload для обновления пользователя (только основной конфиг!)
        patch_payload = {
            "uuid": remnawave_uuid,
            "expireAt": new_exp,
            "activeInternalSquads": [trial_squad_id],
            "hwidDeviceLimit": trial_devices
        }
        
        # Если установлен лимит трафика, добавляем его
        if trial_settings.traffic_limit_bytes > 0:
            patch_payload["trafficLimitBytes"] = trial_settings.traffic_limit_bytes

        resp = requests.patch(
            f"{os.getenv('API_URL')}/api/users",
            headers={"Authorization": f"Bearer {os.getenv('ADMIN_TOKEN')}"},
            json=patch_payload,
            timeout=10
        )

        if resp.status_code != 200:
            return jsonify({"success": False, "message": "Failed to activate trial"}), 500

        # Отмечаем, что пользователь использовал триал (обновляем в БД)
        user.trial_used = True
        db.session.commit()

        # Очищаем кэш для основного конфига
        cache.delete(f'live_data_{remnawave_uuid}')
        
        # Очищаем кэш для основного конфига пользователя (если отличается)
        if remnawave_uuid != user.remnawave_uuid:
            cache.delete(f'live_data_{user.remnawave_uuid}')
            cache.delete(f'nodes_{user.remnawave_uuid}')
        
        # Форматируем сообщение об успешной активации
        lang = user.preferred_lang or 'ru'
        activation_message = getattr(trial_settings, f'activation_message_{lang}', None)
        if not activation_message:
            activation_message = trial_settings.activation_message_ru or f"Trial activated! +{trial_days} days"
        
        # Заменяем {days} на актуальное значение
        message = activation_message.replace("{days}", str(trial_days))
        
        return jsonify({"success": True, "message": message}), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": "Internal error"}), 500


# ============================================================================
# PAYMENTS
# ============================================================================

@app.route('/miniapp/payments/methods', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_payment_methods():
    """Методы оплаты"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response

    try:
        s = PaymentSetting.query.first()
        available = []

        # Методы из админки (PaymentSetting) — только если запись есть
        if s:
            if s.crystalpay_api_key and decrypt_key(s.crystalpay_api_key):
                available.append({"id": "crystalpay", "name": "CrystalPay", "type": "redirect"})
            if s.heleket_api_key and decrypt_key(s.heleket_api_key):
                available.append({"id": "heleket", "name": "Heleket (Крипто)", "type": "crypto"})
            if s.yookassa_shop_id and s.yookassa_secret_key and decrypt_key(s.yookassa_shop_id) and decrypt_key(s.yookassa_secret_key):
                available.append({"id": "yookassa", "name": "YooKassa", "type": "redirect"})
            if getattr(s, 'yoomoney_receiver', None) and getattr(s, 'yoomoney_notification_secret', None) and decrypt_key(getattr(s, 'yoomoney_receiver', None)) and decrypt_key(getattr(s, 'yoomoney_notification_secret', None)):
                available.append({"id": "yoomoney", "name": "YooMoney", "type": "redirect"})
            if s.telegram_bot_token and decrypt_key(s.telegram_bot_token):
                available.append({"id": "telegram_stars", "name": "Telegram Stars", "type": "telegram"})
            if getattr(s, 'platega_api_key', None) and decrypt_key(s.platega_api_key):
                available.append({"id": "platega", "name": "Platega", "type": "redirect"})
                if getattr(s, 'platega_mir_enabled', False):
                    available.append({"id": "platega_mir", "name": "Карты МИР", "type": "redirect"})
            if getattr(s, 'monobank_token', None) and decrypt_key(s.monobank_token):
                available.append({"id": "monobank", "name": "Monobank", "type": "card"})
            if getattr(s, 'freekassa_shop_id', None) and decrypt_key(s.freekassa_shop_id):
                available.append({"id": "freekassa", "name": "Freekassa", "type": "redirect"})
            if getattr(s, 'robokassa_merchant_login', None) and decrypt_key(s.robokassa_merchant_login):
                available.append({"id": "robokassa", "name": "Robokassa", "type": "redirect"})
            if getattr(s, 'mulenpay_api_key', None) and decrypt_key(s.mulenpay_api_key):
                available.append({"id": "mulenpay", "name": "MulenPay", "type": "redirect"})
            if getattr(s, 'urlpay_api_key', None) and decrypt_key(s.urlpay_api_key):
                available.append({"id": "urlpay", "name": "UrlPay", "type": "redirect"})
            if getattr(s, 'tribute_api_key', None) and decrypt_key(s.tribute_api_key):
                available.append({"id": "tribute", "name": "Tribute", "type": "redirect"})
            if getattr(s, 'btcpayserver_api_key', None) and decrypt_key(s.btcpayserver_api_key):
                available.append({"id": "btcpayserver", "name": "BTCPay (Bitcoin)", "type": "crypto"})

        # Kassa AI из env — доступен и без настроек в админке
        if (os.getenv("FREEEKASSA_API_KEY") or "").strip() and (os.getenv("FREEEKASSA_SHOP_ID") or "").strip():
            try:
                int((os.getenv("FREEEKASSA_SHOP_ID") or "").strip())
                available.append({"id": "kassa_ai", "name": "Kassa AI", "type": "redirect"})
            except ValueError:
                pass

        response = jsonify({"methods": available})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
    except Exception as e:
        return jsonify({"methods": []}), 200


@app.route('/miniapp/payments/create', methods=['POST', 'OPTIONS'])
@limiter.limit("10 per minute")
def miniapp_create_payment():
    """Создание платежа"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response

    try:
        data = request.json or {}
        init_data = data.get('initData') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)

        if not telegram_id:
            # Попробуем initDataUnsafe
            unsafe = data.get('initDataUnsafe', {})
            if isinstance(unsafe, dict) and unsafe.get('user'):
                telegram_id = unsafe['user'].get('id')

        if not telegram_id:
            return jsonify({
                "detail": {"title": "Authorization Error", "message": "Missing initData"}
            }), 401

        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            return jsonify({
                "detail": {"title": "User Not Found", "message": "Please register first"}
            }), 404

        tariff_id = data.get('tariff_id') or data.get('tariffId')
        amount = data.get('amount')  # Для пополнения баланса
        payment_provider = data.get('payment_provider') or data.get('paymentProvider', 'crystalpay')
        config_id = data.get('config_id') or data.get('configId')  # ID конфига для оплаты
        create_new_config = data.get('create_new_config') or data.get('createNewConfig', False)  # Флаг создания нового конфига
        
        # Обработка промокода - опциональный параметр
        promo_code_raw = data.get('promo_code') or data.get('promoCode') or ''
        promo_code_str = promo_code_raw.strip().upper() if promo_code_raw and promo_code_raw.strip() else None
        
        currency = data.get('currency') or user.preferred_currency or 'rub'
        
        # Проверяем config_id, если указан
        user_config = None
        if config_id:
            user_config = UserConfig.query.filter_by(id=config_id, user_id=user.id).first()
            if not user_config:
                return jsonify({
                    "detail": {"title": "Invalid Config", "message": "Config not found or doesn't belong to user"}
                }), 404

        # Проверяем, это пополнение баланса или покупка тарифа
        is_balance_topup = not tariff_id and amount
        
        if not tariff_id and not amount:
            return jsonify({
                "detail": {"title": "Invalid Request", "message": "tariff_id or amount is required"}
            }), 400

        if is_balance_topup:
            # Пополнение баланса
            try:
                final_amount = float(amount)
                if final_amount <= 0:
                    return jsonify({
                        "detail": {"title": "Invalid Amount", "message": "Amount must be greater than 0"}
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    "detail": {"title": "Invalid Amount", "message": "Invalid amount format"}
                }), 400
            
            # Определяем валюту
            currency_map = {"uah": "UAH", "rub": "RUB", "usd": "USD"}
            currency_code = currency_map.get(currency, currency_map.get(user.preferred_currency, "RUB"))
            
            # Создаем запись о платеже на пополнение баланса
            import uuid
            order_id = f"SN-{uuid.uuid4().hex[:12].upper()}"
            
            payment_db = Payment(
                order_id=order_id,
                user_id=user.id,
                tariff_id=None,
                amount=final_amount,
                currency=currency_code,
                payment_provider=payment_provider,
                promo_code_id=None,
                status='PENDING'
            )
            
            db.session.add(payment_db)
            db.session.commit()
        else:
            # Покупка тарифа
            tariff = db.session.get(Tariff, int(tariff_id))
            if not tariff:
                return jsonify({
                    "detail": {"title": "Not Found", "message": "Tariff not found"}
                }), 404

            # Определяем цену (используем валюту из запроса или preferred_currency пользователя)
            price_map = {"uah": {"a": tariff.price_uah, "c": "UAH"}, "rub": {"a": tariff.price_rub, "c": "RUB"}, "usd": {"a": tariff.price_usd, "c": "USD"}}
            info = price_map.get(currency, price_map.get(user.preferred_currency, price_map['rub']))

            final_amount = info['a']
            promo_code_obj = None

            # Промокод
            if promo_code_str:
                promo = PromoCode.query.filter_by(code=promo_code_str).first()
                if not promo:
                    return jsonify({
                        "detail": {"title": "Invalid Promo Code", "message": "Invalid or expired promo code"}
                    }), 400
                
                if promo.uses_left <= 0:
                    return jsonify({
                        "detail": {"title": "Invalid Promo Code", "message": "Promo code is no longer valid"}
                    }), 400
                
                # Применяем промокод в зависимости от типа
                if promo.promo_type == 'PERCENT':
                    # Процентная скидка
                    discount = (promo.value / 100.0) * final_amount
                    final_amount = max(0, final_amount - discount)
                    promo_code_obj = promo
                elif promo.promo_type == 'FIXED':
                    # Фиксированная скидка
                    discount = float(promo.value)
                    final_amount = max(0, final_amount - discount)
                    promo_code_obj = promo
                elif promo.promo_type == 'DAYS':
                    # Промокод на бесплатные дни - не применяется к цене
                    return jsonify({
                        "detail": {"title": "Invalid Promo Code", "message": "This promo code is for free days and should be activated separately"}
                    }), 400
                else:
                    # Неизвестный тип промокода
                    return jsonify({
                        "detail": {"title": "Invalid Promo Code", "message": "Unknown promo code type"}
                    }), 400

            # Если указан флаг create_new_config, не создаем конфиг сейчас - создадим после оплаты
            # Если config_id не указан и не нужно создавать новый конфиг, используем основной конфиг
            if not create_new_config:
                if not user_config:
                    primary_config = UserConfig.query.filter_by(user_id=user.id, is_primary=True).first()
                    if not primary_config:
                        # Если нет основного конфига, но есть remnawave_uuid - создаем его
                        if user.remnawave_uuid and '@' not in user.remnawave_uuid:
                            primary_config = UserConfig(
                                user_id=user.id,
                                remnawave_uuid=user.remnawave_uuid,
                                config_name='Основной конфиг',
                                is_primary=True
                            )
                            db.session.add(primary_config)
                            db.session.flush()
                            user_config = primary_config
                        else:
                            # Если нет remnawave_uuid, возвращаем ошибку
                            return jsonify({
                                "detail": {"title": "Error", "message": "User not registered in Remna. Please register first."}
                            }), 400
                    else:
                        user_config = primary_config
            
            # Создаем запись о платеже в БД
            import uuid
            order_id = f"SN-{uuid.uuid4().hex[:12].upper()}"
            
            payment_db = Payment(
                order_id=order_id,
                user_id=user.id,
                tariff_id=tariff.id,
                amount=final_amount,
                currency=info['c'],
                payment_provider=payment_provider,
                promo_code_id=promo_code_obj.id if promo_code_obj else None,
                user_config_id=user_config.id if user_config else None,
                create_new_config=create_new_config,  # Флаг создания нового конфига после оплаты
                status='PENDING'
            )
            
            db.session.add(payment_db)
            db.session.commit()
            currency_code = info['c']

        # Создаем платеж через провайдера
        from modules.api.payments import create_payment as create_payment_provider
        
        payment_url, payment_system_id = create_payment_provider(
            provider=payment_provider,
            amount=final_amount,
            currency=currency_code if is_balance_topup else info['c'],
            order_id=order_id,
            user_email=user.email,
            source='miniapp',
            miniapp_type='v1'  # Старый мини-апп использует /miniapp/
        )

        if not payment_url:
            error_msg = payment_system_id or "Failed to create payment"
            return jsonify({
                "detail": {"title": "Payment Error", "message": error_msg}
            }), 500

        # Обновляем payment_system_id в БД
        if payment_db and payment_system_id:
            payment_db.payment_system_id = payment_system_id
            db.session.commit()

        response = jsonify({
            "payment_url": payment_url,
            "payment_system_id": payment_system_id,
            "order_id": order_id
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response, 200

    except Exception as e:
        print(f"Error in miniapp_create_payment: {e}")
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {"title": "Payment Error", "message": "Internal server error"}
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# CONFIG
# ============================================================================

@app.route('/miniapp/app-config.json', methods=['GET'])
@app.route('/app-config.json', methods=['GET'])
def miniapp_app_config():
    """Конфигурация приложения"""
    import json
    from modules.models.system import SystemSetting
    
    # Получаем активные языки из настроек
    active_languages = ["ru", "ua", "en", "cn"]
    try:
        settings = SystemSetting.query.first()
        if settings and hasattr(settings, 'active_languages') and settings.active_languages:
            try:
                active_languages = json.loads(settings.active_languages) if isinstance(settings.active_languages, str) else settings.active_languages
            except:
                pass
    except:
        pass
    
    # Маппинг языков для мини-аппа (ru -> ru, ua -> ua, en -> en, cn -> zh)
    locale_mapping = {
        "ru": "ru",
        "ua": "ua", 
        "en": "en",
        "cn": "zh"
    }
    additional_locales = [locale_mapping.get(lang, lang) for lang in active_languages if lang in locale_mapping]
    
    config_data = {
        "config": {
            "additionalLocales": additional_locales if additional_locales else ["ru"],
            "branding": {
                "name": "",
                "logoUrl": "",
                "supportUrl": "https://t.me"
            }
        },
        "platforms": {
            "ios": [], "android": [], "macos": [],
            "windows": [], "linux": [], "androidTV": [], "appleTV": []
        }
    }

    try:
        branding = get_branding_settings()
        if branding:
            config_data['config']['branding']['name'] = branding.site_name or ""
            if branding.logo_url:
                config_data['config']['branding']['logoUrl'] = branding.logo_url
    except:
        pass

    # Казино: показывать ли вкладку в мини-аппе (из ENV CASINO_ENABLED)
    try:
        config_data['config']['casino'] = {
            'enabled': os.environ.get('CASINO_ENABLED', 'false').lower() == 'true'
        }
    except Exception:
        config_data['config']['casino'] = {'enabled': False}

    response = jsonify(config_data)
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Content-Type', 'application/json')
    return response


# ============================================================================
# PAYMENT STATUS
# ============================================================================

@app.route('/miniapp/payments/status', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_payment_status():
    """Получить статус платежа для miniapp"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    try:
        data = {}
        if request.is_json:
            data = request.json or {}
        elif request.form:
            data = dict(request.form)
        elif request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except:
                pass
        
        payment_id = data.get('payment_id') or data.get('paymentId') or data.get('order_id') or data.get('orderId')
        
        if not payment_id:
            response = jsonify({
                "detail": {
                    "title": "Invalid Request",
                    "message": "payment_id is required"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        # Находим платеж
        p = Payment.query.filter_by(order_id=payment_id).first()
        if not p:
            p = Payment.query.filter_by(payment_system_id=payment_id).first()
        
        if not p:
            response = jsonify({
                "status": "not_found",
                "paid": False
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 200
        
        # Если платеж Platega со статусом PENDING, проверяем статус через API
        if p.payment_provider in ('platega', 'platega_mir') and p.status == 'PENDING' and p.payment_system_id:
            try:
                from modules.models.payment import PaymentSetting, decrypt_key
                import requests
                import re
                
                settings = PaymentSetting.query.first()
                if settings:
                    platega_key = decrypt_key(settings.platega_api_key) if settings.platega_api_key else None
                    platega_merchant_raw = decrypt_key(settings.platega_merchant_id) if settings.platega_merchant_id else None
                    
                    if platega_key and platega_merchant_raw:
                        # Обработка Merchant ID
                        platega_merchant = platega_merchant_raw.strip()
                        if platega_merchant.startswith('live_'):
                            platega_merchant = platega_merchant[5:]
                        uuid_pattern = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
                        uuid_match = re.search(uuid_pattern, platega_merchant)
                        if uuid_match:
                            platega_merchant = uuid_match.group(0)
                        
                        # Проверяем статус через API Platega
                        api_url = f"https://app.platega.io/transaction/{p.payment_system_id}"
                        headers = {
                            "X-MerchantId": platega_merchant,
                            "X-Secret": platega_key,
                            "Content-Type": "application/json"
                        }
                        
                        resp = requests.get(api_url, headers=headers, timeout=10)
                        if resp.status_code == 200:
                            api_data = resp.json()
                            api_status = api_data.get('status', '').upper()
                            
                            # Если статус CONFIRMED, обрабатываем платеж
                            if api_status == 'CONFIRMED' and p.status != 'PAID':
                                from modules.models.user import User
                                from modules.models.tariff import Tariff
                                
                                user = db.session.get(User, p.user_id)
                                tariff = db.session.get(Tariff, p.tariff_id) if p.tariff_id else None
                                
                                if user:
                                    p.status = 'PAID'
                                    # Если это пополнение баланса
                                    if not tariff:
                                        user.balance = (user.balance or 0) + float(p.amount)
                                        print(f"[PLATEGA] Auto-processed balance topup {p.order_id}, new balance: {user.balance}")
                                    else:
                                        # Обрабатываем покупку тарифа
                                        from modules.api.webhooks.routes import process_successful_payment
                                        process_successful_payment(p, user, tariff)
                                        print(f"[PLATEGA] Auto-processed tariff purchase {p.order_id}")
                                    
                                    db.session.commit()
            except Exception as e:
                print(f"[PLATEGA] Error checking status via API: {e}")
        
        response = jsonify({
            "status": p.status.lower(),
            "paid": p.status == 'PAID',
            "order_id": p.order_id,
            "amount": p.amount,
            "currency": p.currency
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "status": "error",
            "paid": False
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200


# ============================================================================
# PROMO CODES
# ============================================================================

@app.route('/miniapp/promo-codes/activate', methods=['POST', 'OPTIONS'])
@limiter.limit("10 per minute")
def miniapp_activate_promocode():
    """Активировать промокод через miniapp"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    try:
        # Парсим initData
        data = {}
        if request.is_json:
            data = request.json or {}
        elif request.form:
            data = dict(request.form)
        elif request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except:
                pass
        
        init_data = data.get('initData') or request.headers.get('X-Telegram-Init-Data') or request.headers.get('X-Init-Data') or request.args.get('initData')
        
        if not init_data:
            init_data_unsafe = data.get('initDataUnsafe', {})
            if isinstance(init_data_unsafe, dict) and init_data_unsafe.get('user'):
                user_data = init_data_unsafe['user']
                telegram_id = user_data.get('id')
            else:
                response = jsonify({
                    "detail": {
                        "title": "Authorization Error",
                        "message": "Missing initData. Please open the mini app from Telegram."
                    }
                })
                response.headers.add('Access-Control-Allow-Origin', '*')
                return response, 401
        else:
            telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {
                    "title": "Authorization Error",
                    "message": "Telegram ID not found in initData."
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        # Находим пользователя
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {
                    "title": "User Not Found",
                    "message": "User not registered. Please register in the bot first."
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Получаем промокод
        promo_code_str = data.get('promo_code') or data.get('promoCode', '').strip().upper()
        if not promo_code_str:
            response = jsonify({
                "detail": {
                    "title": "Invalid Request",
                    "message": "promo_code is required"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        # Активируем промокод
        promo = PromoCode.query.filter_by(code=promo_code_str).first()
        if not promo:
            response = jsonify({
                "detail": {
                    "title": "Invalid Promo Code",
                    "message": "Неверный промокод"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        if promo.uses_left <= 0:
            response = jsonify({
                "detail": {
                    "title": "Invalid Promo Code",
                    "message": "Промокод больше не действителен"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        # Применяем промокод (упрощенная версия - только для DAYS)
        if promo.promo_type == 'DAYS':
            API_URL = os.getenv('API_URL')
            headers, cookies = get_remnawave_headers()
            
            try:
                live = requests.get(f"{API_URL}/api/users/{user.remnawave_uuid}", headers=headers, cookies=cookies, timeout=10).json().get('response', {})
                curr_exp_str = live.get('expireAt')
                if curr_exp_str:
                    try:
                        curr_exp = datetime.fromisoformat(curr_exp_str.replace('Z', '+00:00'))
                    except:
                        curr_exp = datetime.now(timezone.utc)
                else:
                    curr_exp = datetime.now(timezone.utc)
                
                new_exp = max(datetime.now(timezone.utc), curr_exp) + timedelta(days=promo.value)
                
                # Проверяем наличие сквада у пользователя
                user_squads = live.get('activeInternalSquads', [])
                has_squad = user_squads and len(user_squads) > 0
                
                # Формируем payload для обновления пользователя
                patch_payload = {
                    "uuid": user.remnawave_uuid,
                    "expireAt": new_exp.isoformat()
                }
                
                # Если у пользователя нет сквада и в промокоде указан squad_id - выдаем сквад
                if not has_squad and promo.squad_id:
                    patch_payload["activeInternalSquads"] = [promo.squad_id]
                # Если у пользователя уже есть сквад - просто добавляем дни (не меняем сквад)
                
                patch_resp = requests.patch(
                    f"{API_URL}/api/users",
                    headers={"Content-Type": "application/json", **headers},
                    json=patch_payload,
                    timeout=10
                )
                
                if not patch_resp.ok:
                    response = jsonify({
                        "detail": {
                            "title": "Internal Server Error",
                            "message": "Failed to activate promo code"
                        }
                    })
                    response.headers.add('Access-Control-Allow-Origin', '*')
                    return response, 500
                
                # Списываем использование промокода
                promo.uses_left -= 1
                db.session.commit()
                
                cache.delete(f'live_data_{user.remnawave_uuid}')
                cache.delete('all_live_users_map')
                
                response = jsonify({
                    "message": "Промокод активирован",
                    "days_added": promo.value
                })
                response.headers.add('Access-Control-Allow-Origin', '*')
                return response, 200
            except Exception as e:
                import traceback
                traceback.print_exc()
                response = jsonify({
                    "detail": {
                        "title": "Internal Server Error",
                        "message": str(e)
                    }
                })
                response.headers.add('Access-Control-Allow-Origin', '*')
                return response, 500
        else:
            response = jsonify({
                "detail": {
                    "title": "Invalid Promo Code",
                    "message": "Неподдерживаемый тип промокода"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {
                "title": "Internal Server Error",
                "message": str(e)
            }
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# NODES
# ============================================================================

@app.route('/miniapp/nodes', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_nodes():
    """Получить список серверов для miniapp"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or data.get('data') or ''
        
        if not init_data:
            response = jsonify({
                "detail": {
                    "title": "Authorization Error",
                    "message": "Missing initData"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {
                    "title": "Authorization Error",
                    "message": "Invalid initData format"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        # Находим пользователя
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {
                    "title": "User Not Found",
                    "message": "User not registered"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Получаем серверы
        API_URL = os.getenv('API_URL')
        headers, cookies = get_remnawave_headers()
        resp = requests.get(f"{API_URL}/api/users/{user.remnawave_uuid}/accessible-nodes", headers=headers, cookies=cookies, timeout=10)
        
        if resp.status_code == 200:
            nodes_data = resp.json()
            response = jsonify(nodes_data)
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 200
        else:
            response = jsonify({
                "detail": {
                    "title": "Error",
                    "message": "Failed to fetch nodes"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 500
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {
                "title": "Error",
                "message": str(e)
            }
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# TARIFFS
# ============================================================================

@app.route('/miniapp/tariffs', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_tariffs():
    """Получить список тарифов для miniapp"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    try:
        tariffs = Tariff.query.all()
        tariffs_list = []
        for t in tariffs:
            tariff_data = {
                "id": t.id, 
                "name": t.name, 
                "duration_days": t.duration_days, 
                "price_uah": t.price_uah, 
                "price_rub": t.price_rub, 
                "price_usd": t.price_usd,
                "squad_id": t.squad_id,  # Для обратной совместимости
                "squad_ids": t.get_squad_ids() if hasattr(t, 'get_squad_ids') else (t.squad_ids if hasattr(t, 'squad_ids') else None),
                "traffic_limit_bytes": t.traffic_limit_bytes or 0,
                "hwid_device_limit": t.hwid_device_limit if hasattr(t, 'hwid_device_limit') else None,
                "tier": t.tier,
                "badge": t.badge,
                "bonus_days": t.bonus_days if hasattr(t, 'bonus_days') else 0
            }
            tariffs_list.append(tariff_data)
        
        response = jsonify({"tariffs": tariffs_list})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response, 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({"tariffs": []})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200


# ============================================================================
# SUBSCRIPTION RENEWAL OPTIONS
# ============================================================================

@app.route('/miniapp/subscription/renewal/options', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_subscription_renewal_options():
    """Получить опции продления подписки для miniapp"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    try:
        # Парсим initData для получения пользователя
        data = {}
        if request.is_json:
            data = request.json or {}
        elif request.form:
            data = dict(request.form)
        elif request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except:
                pass
        
        init_data = data.get('initData') or request.headers.get('X-Telegram-Init-Data') or request.headers.get('X-Init-Data') or request.args.get('initData')
        
        if not init_data:
            init_data_unsafe = data.get('initDataUnsafe', {})
            if isinstance(init_data_unsafe, dict) and init_data_unsafe.get('user'):
                user_data = init_data_unsafe['user']
                telegram_id = user_data.get('id')
            else:
                response = jsonify({
                    "detail": {
                        "title": "Authorization Error",
                        "message": "Missing initData. Please open the mini app from Telegram."
                    }
                })
                response.headers.add('Access-Control-Allow-Origin', '*')
                return response, 401
        else:
            telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {
                    "title": "Authorization Error",
                    "message": "Telegram ID not found in initData."
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {
                    "title": "User Not Found",
                    "message": "User not registered"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Получаем тарифы
        tariffs = Tariff.query.all()
        options = [{
            "id": t.id,
            "name": t.name,
            "duration_days": t.duration_days,
            "price_uah": t.price_uah,
            "price_rub": t.price_rub,
            "price_usd": t.price_usd
        } for t in tariffs]
        
        response = jsonify({"options": options})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({"options": []})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200


# ============================================================================
# SUBSCRIPTION SETTINGS
# ============================================================================

@app.route('/miniapp/subscription/settings', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_subscription_settings():
    """Получить настройки подписки для miniapp"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    try:
        # Парсим initData
        data = {}
        if request.is_json:
            data = request.json or {}
        elif request.form:
            data = dict(request.form)
        elif request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except:
                pass
        
        init_data = data.get('initData') or request.headers.get('X-Telegram-Init-Data') or request.headers.get('X-Init-Data') or request.args.get('initData')
        
        if not init_data:
            init_data_unsafe = data.get('initDataUnsafe', {})
            if isinstance(init_data_unsafe, dict) and init_data_unsafe.get('user'):
                user_data = init_data_unsafe['user']
                telegram_id = user_data.get('id')
            else:
                response = jsonify({
                    "detail": {
                        "title": "Authorization Error",
                        "message": "Missing initData. Please open the mini app from Telegram."
                    }
                })
                response.headers.add('Access-Control-Allow-Origin', '*')
                return response, 401
        else:
            telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {
                    "title": "Authorization Error",
                    "message": "Telegram ID not found in initData."
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {
                    "title": "User Not Found",
                    "message": "User not registered"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Возвращаем настройки подписки (упрощенная версия)
        response = jsonify({
            "auto_renewal": False,
            "notifications": True
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {
                "title": "Error",
                "message": str(e)
            }
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# PROMO OFFERS
# ============================================================================

@app.route('/miniapp/promo-offers/<offer_id>/claim', methods=['POST', 'OPTIONS'])
@limiter.limit("10 per minute")
def miniapp_claim_promo_offer(offer_id):
    """Активировать промо-оффер через miniapp (алиас для промокода)"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    try:
        # Используем offer_id как код промокода
        data = {}
        if request.is_json:
            data = request.json or {}
        elif request.form:
            data = dict(request.form)
        elif request.data:
            try:
                data = json.loads(request.data.decode('utf-8'))
            except:
                pass
        
        # Используем offer_id как код промокода
        data['promo_code'] = offer_id
        
        # Вызываем функцию активации промокода
        return miniapp_activate_promocode()
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {
                "title": "Internal Server Error",
                "message": "An error occurred while processing the request."
            }
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# CONFIGS (для нового интерфейса в стиле StealthSurf)
# ============================================================================

@app.route('/miniapp/configs', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_configs():
    """
    Получить список всех конфигов пользователя.
    
    Возвращает все конфиги пользователя, включая основной и дополнительные.
    """
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {"title": "Authorization Error", "message": "Missing initData"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {"title": "User Not Found", "message": "User not registered"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Получаем все конфиги пользователя
        user_configs = UserConfig.query.filter_by(user_id=user.id).order_by(UserConfig.is_primary.desc(), UserConfig.created_at.asc()).all()
        
        API_URL = os.getenv('API_URL')
        headers, cookies = get_remnawave_headers()
        
        # Получаем названия уровней тарифов (TariffLevel), fallback на branding для базовых
        from modules.models.branding import BrandingSetting
        from modules.models.tariff_level import TariffLevel

        branding = BrandingSetting.query.first()
        basic_name = getattr(branding, 'tariff_tier_basic_name', None) or 'Базовый'
        pro_name = getattr(branding, 'tariff_tier_pro_name', None) or 'Премиум'
        elite_name = getattr(branding, 'tariff_tier_elite_name', None) or 'Элитный'

        tier_names = {lvl.code.lower(): (lvl.name or lvl.code) for lvl in TariffLevel.query.filter_by(is_active=True).all()}
        tier_names.setdefault('basic', basic_name)
        tier_names.setdefault('pro', pro_name)
        tier_names.setdefault('elite', elite_name)
        
        configs = []
        
        for user_config in user_configs:
            # Получаем данные из Remna для каждого конфига
            cache_key = f'live_data_{user_config.remnawave_uuid}'
            cached = cache.get(cache_key)
            
            if not cached:
                try:
                    resp = requests.get(
                        f"{API_URL}/api/users/{user_config.remnawave_uuid}",
                        headers=headers,
                        cookies=cookies,
                        timeout=10
                    )
                    if resp.status_code == 200:
                        cached = resp.json().get('response', {})
                        cache.set(cache_key, cached, timeout=300)
                except:
                    cached = {}
            
            subscription_url = cached.get('subscriptionUrl') if cached else None
            expire_at = cached.get('expireAt') if cached else None
            has_active = False
            
            if expire_at:
                try:
                    expire_dt = datetime.fromisoformat(expire_at.replace('Z', '+00:00')) if isinstance(expire_at, str) else expire_at
                    has_active = expire_dt > datetime.now(timezone.utc)
                except:
                    pass
            
            # Получаем информацию о тарифе из последнего оплаченного платежа для этого конфига
            # Ищем платежи, связанные с этим remnawave_uuid через user_config_id (если будет добавлено)
            # Пока используем последний платеж пользователя
            last_payment = Payment.query.filter_by(
                user_id=user.id,
                status='PAID'
            ).order_by(Payment.created_at.desc()).first()
            
            tariff_name = user_config.config_name or (f'Конфиг {user_config.id}' if not user_config.is_primary else 'Основной конфиг')
            tariff_tier = None
            tariff_duration = None
            device_limit = None
            traffic_limit_bytes = None
            
            if last_payment and last_payment.tariff_id:
                tariff = db.session.get(Tariff, last_payment.tariff_id)
                if tariff:
                    tariff_tier = tariff.tier
                    device_limit = tariff.hwid_device_limit if hasattr(tariff, 'hwid_device_limit') else None
                    traffic_limit_bytes = tariff.traffic_limit_bytes if hasattr(tariff, 'traffic_limit_bytes') else None
                    
                    if tariff.tier:
                        tariff_name = tier_names.get(tariff.tier.lower(), tariff.tier)
                    else:
                        tariff_name = tariff.name
                    tariff_duration = tariff.duration_days
            
            configs.append({
                "id": user_config.id,
                "config_id": user_config.id,  # Для обратной совместимости
                "name": tariff_name,
                "config_name": user_config.config_name or tariff_name,
                "tier": tariff_tier,
                "subscription_url": subscription_url,
                "expire_at": expire_at,
                "is_active": has_active,
                "is_primary": user_config.is_primary,
                "tariff_duration_days": tariff_duration,
                "device_limit": device_limit,
                "traffic_limit_bytes": traffic_limit_bytes,
                "remnawave_uuid": user_config.remnawave_uuid
            })
        
        # Если у пользователя нет конфигов, но есть старый remnawave_uuid - создаем основной конфиг
        if not configs and user.remnawave_uuid and '@' not in user.remnawave_uuid:
            # Создаем основной конфиг для обратной совместимости
            primary_config = UserConfig.query.filter_by(user_id=user.id, remnawave_uuid=user.remnawave_uuid).first()
            if not primary_config:
                primary_config = UserConfig(
                    user_id=user.id,
                    remnawave_uuid=user.remnawave_uuid,
                    config_name='Основной конфиг',
                    is_primary=True
                )
                db.session.add(primary_config)
                db.session.commit()
                # Рекурсивно вызываем себя для получения данных
                return miniapp_configs()
        
        response = jsonify({"configs": configs})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {"title": "Error", "message": str(e)}
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# Эндпоинт /miniapp/configs/create удален
# Создание конфига теперь происходит только после успешной оплаты тарифа
# При нажатии "Добавить конфиг" открывается страница тарифов


# ============================================================================
# CONFIGS MANAGEMENT (rename/delete additional configs)
# ============================================================================

@app.route('/miniapp/configs/rename', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_configs_rename():
    """Переименовать дополнительный конфиг пользователя."""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response

    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)

        if not telegram_id:
            response = jsonify({"detail": {"title": "Authorization Error", "message": "Missing initData"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401

        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({"detail": {"title": "User Not Found", "message": "User not registered"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404

        config_id = data.get('config_id') or data.get('configId') or data.get('configID') or data.get('id')
        try:
            config_id_int = int(config_id)
        except Exception:
            response = jsonify({"detail": {"title": "Bad Request", "message": "Invalid config_id"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400

        new_name = (data.get('name') or data.get('config_name') or data.get('configName') or '').strip()
        if not new_name:
            response = jsonify({"detail": {"title": "Bad Request", "message": "Name is required"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400

        if len(new_name) > 100:
            response = jsonify({"detail": {"title": "Bad Request", "message": "Name is too long"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400

        user_config = UserConfig.query.filter_by(id=config_id_int, user_id=user.id).first()
        if not user_config:
            response = jsonify({"detail": {"title": "Not Found", "message": "Config not found"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404

        if user_config.is_primary:
            response = jsonify({"detail": {"title": "Forbidden", "message": "Primary config cannot be renamed"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 403

        user_config.config_name = new_name
        db.session.commit()

        response = jsonify({
            "message": "Renamed",
            "config": {
                "id": user_config.id,
                "config_id": user_config.id,
                "config_name": user_config.config_name,
                "is_primary": user_config.is_primary
            }
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({"detail": {"title": "Error", "message": str(e)}})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


@app.route('/miniapp/configs/delete', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_configs_delete():
    """Удалить дополнительный конфиг пользователя."""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response

    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)

        if not telegram_id:
            response = jsonify({"detail": {"title": "Authorization Error", "message": "Missing initData"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401

        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({"detail": {"title": "User Not Found", "message": "User not registered"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404

        config_id = data.get('config_id') or data.get('configId') or data.get('configID') or data.get('id')
        try:
            config_id_int = int(config_id)
        except Exception:
            response = jsonify({"detail": {"title": "Bad Request", "message": "Invalid config_id"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400

        user_config = UserConfig.query.filter_by(id=config_id_int, user_id=user.id).first()
        if not user_config:
            response = jsonify({"detail": {"title": "Not Found", "message": "Config not found"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404

        if user_config.is_primary:
            response = jsonify({"detail": {"title": "Forbidden", "message": "Primary config cannot be deleted"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 403

        remnawave_uuid = user_config.remnawave_uuid

        # Убираем ссылки из платежей, чтобы не упереться в FK constraint
        try:
            Payment.query.filter_by(user_config_id=user_config.id).update(
                {Payment.user_config_id: None},
                synchronize_session=False
            )
        except Exception:
            pass

        # Чистим кеши по этому UUID
        if remnawave_uuid:
            cache.delete(f'live_data_{remnawave_uuid}')
            cache.delete(f'nodes_{remnawave_uuid}')

        # Пытаемся удалить пользователя в RemnaWave (не блокируем удаление в БД при ошибке)
        remote_deleted = False
        remote_status = None
        API_URL = os.getenv('API_URL')
        if API_URL and remnawave_uuid:
            try:
                headers, cookies = get_remnawave_headers()
                resp = requests.delete(
                    f"{API_URL}/api/users/{remnawave_uuid}",
                    headers=headers,
                    cookies=cookies,
                    timeout=10
                )
                remote_status = resp.status_code
                remote_deleted = resp.status_code in [200, 204]
            except Exception:
                remote_status = None

        db.session.delete(user_config)
        db.session.commit()

        response = jsonify({
            "message": "Deleted",
            "remote_deleted": remote_deleted,
            "remote_status": remote_status
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({"detail": {"title": "Error", "message": str(e)}})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# REFERRALS (расширенная информация)
# ============================================================================

@app.route('/miniapp/referrals/info', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_referrals_info():
    """Получить информацию о реферальной программе"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {"title": "Authorization Error", "message": "Missing initData"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {"title": "User Not Found", "message": "User not registered"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Генерируем реферальную ссылку
        YOUR_SERVER_IP_OR_DOMAIN = os.getenv("YOUR_SERVER_IP_OR_DOMAIN", os.getenv("YOUR_SERVER_IP", ""))
        referral_code = user.referral_code or f"REF{user.id}"
        
        # Обновляем referral_code если его нет
        if not user.referral_code:
            user.referral_code = referral_code
            db.session.commit()
        
        referral_link_direct = f"{YOUR_SERVER_IP_OR_DOMAIN}/ref/{referral_code}" if YOUR_SERVER_IP_OR_DOMAIN else ""
        # Приоритет: TELEGRAM_BOT_NAME_V2 -> TELEGRAM_BOT_NAME -> BOT_USERNAME -> fallback
        bot_username = os.getenv("TELEGRAM_BOT_NAME_V2") or os.getenv("TELEGRAM_BOT_NAME") or os.getenv("BOT_USERNAME", "stealthnet_vpn_bot")
        referral_link_telegram = f"https://t.me/{bot_username}?start={referral_code}"
        
        # Получаем настройки реферальной программы
        ref_settings = get_referral_settings()
        referral_type = getattr(ref_settings, 'referral_type', 'DAYS') if ref_settings else 'DAYS'
        default_referral_percent = getattr(ref_settings, 'default_referral_percent', 10.0) if ref_settings else 10.0
        # Если у пользователя установлен индивидуальный процент - используем его, иначе глобальный
        user_referral_percent = user.referral_percent if user.referral_percent is not None else default_referral_percent
        
        # Информация в зависимости от типа системы
        referral_info = {}
        if referral_type == 'DAYS':
            invitee_days = ref_settings.invitee_bonus_days if ref_settings else 3
            referrer_days = ref_settings.referrer_bonus_days if ref_settings else 3
            referral_info = {
                "type": "DAYS",
                "title": "Реферальная программа на дни",
                "description": "Приглашайте друзей и получайте бесплатные дни подписки!",
                "invitee_bonus": f"{invitee_days} бесплатных дней",
                "referrer_bonus": f"{referrer_days} бесплатных дней за каждого приглашенного",
                "how_it_works": [
                    "Ваш друг регистрируется по вашей реферальной ссылке",
                    f"Он получает {invitee_days} бесплатных дней подписки",
                    f"Вы получаете {referrer_days} бесплатных дней за каждого приглашенного"
                ]
            }
        else:  # PERCENT
            referral_info = {
                "type": "PERCENT",
                "title": "Реферальная программа с процентами",
                "description": "Приглашайте друзей и получайте процент с их покупок на свой баланс!",
                "your_percent": f"{user_referral_percent}%",
                "default_percent": f"{default_referral_percent}%",
                "how_it_works": [
                    "Ваш друг регистрируется по вашей реферальной ссылке",
                    "Он покупает тариф или пополняет баланс",
                    f"Вы получаете {user_referral_percent}% от суммы на свой баланс",
                    "Средства можно использовать для покупки тарифов или вывести"
                ]
            }
        
        response_data = {
            "referral_code": referral_code,
            "referral_link_direct": referral_link_direct,
            "referral_link_telegram": referral_link_telegram,
            "referral_info": referral_info,
            "referrals_count": User.query.filter_by(referrer_id=user.id).count()
        }
        
        response = jsonify(response_data)
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {"title": "Error", "message": str(e)}
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


@app.route('/miniapp/referrals/stats', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_referrals_stats():
    """Получить статистику рефералов пользователя"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {"title": "Authorization Error", "message": "Missing initData"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {"title": "User Not Found", "message": "User not registered"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Подсчитываем рефералов
        referrals_count = User.query.filter_by(referrer_id=user.id).count()
        
        # Подсчитываем баланс рефералов (можно расширить через Payment модель)
        total_earnings = 0.0  # TODO: Реализовать подсчет через платежи рефералов
        
        response_data = {
            "referrals_count": referrals_count,
            "total_earnings": total_earnings,
            "available_for_withdrawal": total_earnings,
            "referrals": []  # Список рефералов (можно расширить)
        }
        
        response = jsonify(response_data)
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {"title": "Error", "message": str(e)}
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# PROFILE (расширенные данные профиля)
# ============================================================================

@app.route('/miniapp/profile', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_profile():
    """Получить данные профиля пользователя для отображения"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, user_data_parsed = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {"title": "Authorization Error", "message": "Missing initData"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {"title": "User Not Found", "message": "User not registered"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Получаем данные подписки
        cache_key = f'live_data_{user.remnawave_uuid}'
        cached = cache.get(cache_key)
        
        if not cached:
            API_URL = os.getenv('API_URL')
            headers, cookies = get_remnawave_headers()
            try:
                resp = requests.get(
                    f"{API_URL}/api/users/{user.remnawave_uuid}",
                    headers=headers,
                    cookies=cookies,
                    timeout=10
                )
                if resp.status_code == 200:
                    cached = resp.json().get('response', {})
                    cache.set(cache_key, cached, timeout=300)
            except:
                cached = {}
        
        expire_at = cached.get('expireAt') if cached else None
        has_active = False
        if expire_at:
            try:
                expire_dt = datetime.fromisoformat(expire_at.replace('Z', '+00:00')) if isinstance(expire_at, str) else expire_at
                has_active = expire_dt > datetime.now(timezone.utc)
            except:
                pass
        
        # Конвертируем баланс из USD в выбранную валюту пользователя
        from modules.currency import convert_from_usd
        balance_usd = float(user.balance) if user.balance else 0.0
        balance_display = convert_from_usd(balance_usd, user.preferred_currency or 'uah')
        
        profile_data = {
            "telegram_id": str(user.telegram_id),
            "username": user.telegram_username or (user_data_parsed.get('username') if user_data_parsed else None) or f"user_{user.telegram_id}",
            "email": user.email,
            "referral_code": user.referral_code,
            "has_active_subscription": has_active,
            "subscription_expire_at": expire_at,
            "balance": balance_display,
            "balance_usd": balance_usd,
            "currency": user.preferred_currency or 'uah',
            "preferred_currency": user.preferred_currency or 'uah',
            "password_hash": user.password_hash or '',  # Для проверки наличия пароля
            "has_password": bool(user.password_hash and user.password_hash != '')
        }
        
        response = jsonify(profile_data)
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {"title": "Error", "message": str(e)}
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


@app.route('/miniapp/settings', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_settings():
    """Обновить настройки пользователя (валюта, язык)"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {"title": "Authorization Error", "message": "Missing initData"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {"title": "User Not Found", "message": "User not registered"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Установка пароля (для пользователей из бота)
        if data.get('action') == 'set_password' and 'new_password' in data:
            new_password = data['new_password']
            if len(new_password) < 6:
                response = jsonify({
                    "detail": {"title": "Validation Error", "message": "Password must be at least 6 characters"}
                })
                response.headers.add('Access-Control-Allow-Origin', '*')
                return response, 400
            
            from modules.core import bcrypt, get_fernet
            user.password_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')
            # Сохраняем зашифрованный пароль для бота
            fernet = get_fernet()
            if fernet:
                try:
                    user.encrypted_password = fernet.encrypt(new_password.encode()).decode()
                except:
                    pass
            db.session.commit()
            
            response = jsonify({
                "message": "Password set successfully"
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 200

        # Обновляем валюту
        currency_changed = False
        if 'preferred_currency' in data:
            currency = data['preferred_currency']
            # Проверяем, что валюта активна
            from modules.models.system import SystemSetting
            import json
            settings = SystemSetting.query.first()
            active_currencies = ['uah', 'rub', 'usd']
            if settings and hasattr(settings, 'active_currencies') and settings.active_currencies:
                try:
                    active_currencies = json.loads(settings.active_currencies) if isinstance(settings.active_currencies, str) else settings.active_currencies
                except:
                    pass
            
            if currency in active_currencies:
                if user.preferred_currency != currency:
                    currency_changed = True
                user.preferred_currency = currency
                db.session.commit()
                # Очищаем кэш при изменении валюты, чтобы баланс пересчитался
                if currency_changed:
                    cache.delete(f'live_data_{user.remnawave_uuid}')
                    cache.delete('all_live_users_map')
        
        # Обновляем язык
        if 'preferred_lang' in data:
            lang = data['preferred_lang']
            # Проверяем, что язык активен
            from modules.models.system import SystemSetting
            import json
            settings = SystemSetting.query.first()
            active_languages = ['ru', 'ua', 'en', 'cn']
            if settings and hasattr(settings, 'active_languages') and settings.active_languages:
                try:
                    active_languages = json.loads(settings.active_languages) if isinstance(settings.active_languages, str) else settings.active_languages
                except:
                    pass
            
            if lang in active_languages:
                user.preferred_lang = lang
                db.session.commit()
        
        response = jsonify({"success": True, "message": "Settings updated"})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {"title": "Error", "message": str(e)}
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# PAID OPTIONS (платные опции)
# ============================================================================

@app.route('/miniapp/options', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_options():
    """Получить список платных опций"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        # Получаем опции (пока статичный список, можно расширить через БД)
        options = [
            {
                "id": "white_internet",
                "name": "Белый интернет",
                "description": "Увеличивает скорость мобильного интернета",
                "price_monthly_rub": 499,
                "price_monthly_uah": 0,
                "price_monthly_usd": 0,
                "is_enabled": False  # TODO: Проверять через RemnaWave API или отдельную модель
            }
        ]
        
        # Если есть telegram_id, проверяем статус опций пользователя
        if telegram_id:
            user = User.query.filter_by(telegram_id=str(telegram_id)).first()
            if user:
                # TODO: Реализовать проверку активных опций через RemnaWave API
                pass
        
        response = jsonify({"options": options})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({"options": []})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200


# ============================================================================
# PURCHASE OPTIONS (Дополнительные опции: трафик, устройства, сквады)
# ============================================================================

@app.route('/miniapp/purchase-options', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_get_purchase_options():
    """Получить список доступных опций для покупки (трафик, устройства, сквады)"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response

    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)

        if not telegram_id:
            response = jsonify({"detail": {"title": "Authorization Error", "message": "Missing initData"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401

        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({"detail": {"title": "User Not Found", "message": "User not registered"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404

        options = PurchaseOption.query.filter_by(is_active=True).order_by(
            PurchaseOption.option_type,
            PurchaseOption.sort_order,
            PurchaseOption.id
        ).all()

        options_by_type = {'traffic': [], 'devices': [], 'squad': []}
        for opt in options:
            opt_data = {
                'id': opt.id,
                'name': opt.name,
                'description': opt.description,
                'value': opt.value,
                'unit': opt.unit,
                'price_uah': opt.price_uah,
                'price_rub': opt.price_rub,
                'price_usd': opt.price_usd,
                'icon': opt.icon
            }
            if opt.option_type in options_by_type:
                options_by_type[opt.option_type].append(opt_data)

        response = jsonify({"options": options_by_type, "user_currency": user.preferred_currency or 'rub'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({"options": {"traffic": [], "devices": [], "squad": []}})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200


@app.route('/miniapp/options/purchase', methods=['POST', 'OPTIONS'])
@limiter.limit("10 per minute")
def miniapp_purchase_option():
    """Покупка дополнительной опции"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response

    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)

        # Fallback для initDataUnsafe (для тестирования вне Telegram)
        if not telegram_id:
            unsafe = data.get('initDataUnsafe', {})
            if isinstance(unsafe, dict) and unsafe.get('user'):
                telegram_id = unsafe['user'].get('id')

        if not telegram_id:
            response = jsonify({"detail": {"title": "Authorization Error", "message": "Missing initData"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401

        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({"detail": {"title": "User Not Found", "message": "User not registered"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404

        option_id = data.get('option_id') or data.get('optionId')
        payment_provider = data.get('payment_provider') or data.get('paymentProvider', 'crystalpay')
        currency = data.get('currency') or user.preferred_currency or 'rub'
        config_id = data.get('config_id') or data.get('configId')

        if not option_id:
            response = jsonify({"detail": {"title": "Invalid Request", "message": "option_id is required"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400

        option = PurchaseOption.query.get(int(option_id))
        if not option or not option.is_active:
            response = jsonify({"detail": {"title": "Option Not Found", "message": "Option not found or inactive"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404

        currency_map = {
            'uah': ('UAH', option.price_uah),
            'rub': ('RUB', option.price_rub),
            'usd': ('USD', option.price_usd)
        }
        currency_code, final_amount = currency_map.get(str(currency).lower(), ('RUB', option.price_rub))

        if not final_amount or final_amount <= 0:
            response = jsonify({"detail": {"title": "Price Error", "message": f"Price not set for currency {currency_code}"}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400

        order_id = f"OPT-{uuid.uuid4().hex[:12].upper()}"

        # Оплата с баланса — обрабатываем сразу, без внешнего провайдера
        if payment_provider == 'balance':
            from modules.currency import convert_to_usd, convert_from_usd
            from modules.api.webhooks.routes import process_option_purchase

            current_balance_usd = float(user.balance) if user.balance else 0.0
            final_amount_usd = convert_to_usd(final_amount, currency_code)

            if current_balance_usd < final_amount_usd:
                current_balance_display = convert_from_usd(current_balance_usd, currency_code.lower() if currency_code else 'rub')
                response = jsonify({
                    "detail": {
                        "title": "Insufficient Balance",
                        "message": f"Insufficient balance. Required: {final_amount:.2f} {currency_code}, available: {current_balance_display:.2f} {currency_code}"
                    }
                })
                response.headers.add('Access-Control-Allow-Origin', '*')
                return response, 400

            user.balance = current_balance_usd - final_amount_usd

            payment_db = Payment(
                order_id=order_id,
                user_id=user.id,
                tariff_id=None,
                amount=final_amount,
                currency=currency_code,
                payment_provider='balance',
                promo_code_id=None,
                status='PENDING'
            )
            payment_db.description = f"OPTION:{option.id}"
            try:
                if config_id:
                    payment_db.user_config_id = int(config_id)
            except Exception:
                pass

            db.session.add(payment_db)
            db.session.commit()

            success = process_option_purchase(payment_db, user)
            if success:
                payment_db.status = 'PAID'
                db.session.commit()
                db.session.refresh(user)
                response = jsonify({
                    "payment_url": None,
                    "payment_system_id": None,
                    "order_id": order_id,
                    "success": True,
                    "message": "Option purchased successfully",
                    "option_name": option.name,
                    "amount": final_amount,
                    "currency": currency_code,
                    "balance": float(user.balance) if user.balance else 0.0
                })
            else:
                user.balance = current_balance_usd
                db.session.rollback()
                response = jsonify({"detail": {"title": "Error", "message": "Failed to process option purchase"}})
                response.headers.add('Access-Control-Allow-Origin', '*')
                return response, 500

            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 200

        # Обычный платёжный провайдер
        payment_db = Payment(
            order_id=order_id,
            user_id=user.id,
            tariff_id=None,
            amount=final_amount,
            currency=currency_code,
            payment_provider=payment_provider,
            promo_code_id=None,
            status='PENDING'
        )
        payment_db.description = f"OPTION:{option.id}"

        try:
            if config_id:
                payment_db.user_config_id = int(config_id)
        except Exception:
            pass

        db.session.add(payment_db)
        db.session.commit()

        from modules.api.payments import create_payment as create_payment_provider
        payment_url, payment_system_id = create_payment_provider(
            provider=payment_provider,
            amount=final_amount,
            currency=currency_code,
            order_id=order_id,
            user_email=user.email,
            source='miniapp'
        )

        if not payment_url:
            error_msg = payment_system_id or "Failed to create payment"
            response = jsonify({"detail": {"title": "Payment Error", "message": error_msg}})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 500

        if payment_system_id:
            payment_db.payment_system_id = payment_system_id
            db.session.commit()

        response = jsonify({
            "payment_url": payment_url,
            "payment_system_id": payment_system_id,
            "order_id": order_id,
            "option_name": option.name,
            "amount": final_amount,
            "currency": currency_code
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({"detail": {"title": "Error", "message": str(e)}})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# SUPPORT TICKETS
# ============================================================================

@app.route('/miniapp/support/tickets', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_support_tickets():
    """Получить список тикетов или создать новый тикет"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {
                    "title": "Authentication Error",
                    "message": "Invalid or missing Telegram initData"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        # Получаем пользователя (преобразуем telegram_id в строку, т.к. в БД это VARCHAR)
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {
                    "title": "User Not Found",
                    "message": "User not registered. Please register first."
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        from modules.models.ticket import Ticket, TicketMessage
        
        # GET - список тикетов
        if not data.get('subject') and not data.get('message'):
            tickets = Ticket.query.filter_by(user_id=user.id).order_by(Ticket.created_at.desc()).all()
            result = [{
                'id': t.id,
                'subject': t.subject,
                'status': t.status,
                'created_at': t.created_at.isoformat() if t.created_at else None
            } for t in tickets]
            
            response = jsonify({"tickets": result})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 200
        
        # POST - создание тикета
        subject = data.get('subject', '').strip()
        message = data.get('message', '').strip()
        
        if not subject:
            response = jsonify({
                "detail": {
                    "title": "Validation Error",
                    "message": "Subject is required"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        ticket = Ticket(
            user_id=user.id,
            subject=subject,
            status='OPEN',
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(ticket)
        db.session.flush()
        
        if message:
            ticket_message = TicketMessage(
                ticket_id=ticket.id,
                sender_id=user.id,
                message=message,
                created_at=datetime.now(timezone.utc)
            )
            db.session.add(ticket_message)
        
        db.session.commit()
        
        # Отправляем уведомление админам в группу
        try:
            from modules.notifications import notify_support_ticket
            notify_support_ticket(ticket, user, message, is_new_ticket=True)
        except Exception as e:
            print(f"Error sending support ticket notification: {e}")
        
        response = jsonify({
            "message": "Ticket created successfully",
            "ticket_id": ticket.id
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 201
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error in miniapp_support_tickets: {e}")
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {
                "title": "Internal Error",
                "message": str(e)
            }
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


@app.route('/miniapp/support/tickets/<int:ticket_id>', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_support_ticket_detail(ticket_id):
    """Получить детали тикета"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {
                    "title": "Authentication Error",
                    "message": "Invalid or missing Telegram initData"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        # Получаем пользователя (преобразуем telegram_id в строку, т.к. в БД это VARCHAR)
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {
                    "title": "User Not Found",
                    "message": "User not registered. Please register first."
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        from modules.models.ticket import Ticket, TicketMessage
        
        # Получаем тикет
        ticket = Ticket.query.filter_by(id=ticket_id, user_id=user.id).first()
        if not ticket:
            response = jsonify({
                "detail": {
                    "title": "Ticket Not Found",
                    "message": "Ticket not found or access denied"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Получаем сообщения
        messages = TicketMessage.query.filter_by(ticket_id=ticket_id).order_by(TicketMessage.created_at.asc()).all()
        
        result = {
            'id': ticket.id,
            'subject': ticket.subject,
            'status': ticket.status,
            'created_at': ticket.created_at.isoformat() if ticket.created_at else None,
            'messages': [{
                'id': m.id,
                'message': m.message,
                'is_admin': m.is_admin if hasattr(m, 'is_admin') else (m.sender.role == 'ADMIN' if m.sender else False),
                'created_at': m.created_at.isoformat() if m.created_at else None
            } for m in messages]
        }
        
        response = jsonify({"ticket": result})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        app.logger.error(f"Error in miniapp_support_ticket_detail: {e}")
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {
                "title": "Internal Error",
                "message": str(e)
            }
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


@app.route('/miniapp/support/tickets/<int:ticket_id>/reply', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_support_ticket_reply(ticket_id):
    """Ответить на тикет"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {
                    "title": "Authentication Error",
                    "message": "Invalid or missing Telegram initData"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        # Получаем пользователя (преобразуем telegram_id в строку, т.к. в БД это VARCHAR)
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {
                    "title": "User Not Found",
                    "message": "User not registered. Please register first."
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        from modules.models.ticket import Ticket, TicketMessage
        
        # Проверяем, что тикет принадлежит пользователю
        ticket = Ticket.query.filter_by(id=ticket_id, user_id=user.id).first()
        if not ticket:
            response = jsonify({
                "detail": {
                    "title": "Ticket Not Found",
                    "message": "Ticket not found or access denied"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        message_text = data.get('message', '').strip()
        if not message_text:
            response = jsonify({
                "detail": {
                    "title": "Validation Error",
                    "message": "Message is required"
                }
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        # Создаем сообщение
        ticket_message = TicketMessage(
            ticket_id=ticket_id,
            sender_id=user.id,
            message=message_text,
            is_admin=False,
            created_at=datetime.now(timezone.utc)
        )
        db.session.add(ticket_message)
        db.session.commit()
        
        # Отправляем уведомление админам в группу
        try:
            from modules.notifications import notify_support_ticket
            notify_support_ticket(ticket, user, message_text, is_new_ticket=False)
        except Exception as e:
            print(f"Error sending support ticket notification: {e}")
        
        # Отправляем уведомление админам в оба бота (если ответил пользователь)
        # Получаем всех админов с telegram_id
        from modules.models.user import User
        admins = User.query.filter_by(role='ADMIN').filter(User.telegram_id != None).all()
        
        if admins:
            # Импортируем функцию отправки сообщений
            from modules.api.admin.routes import send_telegram_message
            
            # Получаем токены ботов
            old_bot_token = os.getenv("CLIENT_BOT_TOKEN")
            new_bot_token = os.getenv("CLIENT_BOT_V2_TOKEN") or os.getenv("CLIENT_BOT_TOKEN")
            
            # Формируем текст уведомления
            notification_text = (
                f"<b>📩 Новый ответ от пользователя в тикете</b>\n\n"
                f"<b>Тема:</b> {ticket.subject}\n"
                f"<b>Пользователь:</b> {user.email or f'ID: {user.id}'}\n"
                f"<b>Ответ:</b> {message_text[:200]}{'...' if len(message_text) > 200 else ''}\n\n"
                f"💬 Тикет #{ticket_id}"
            )
            
            # Отправляем всем админам в оба бота
            import threading
            
            def send_notification(bot_token, telegram_id, text):
                if bot_token:
                    try:
                        send_telegram_message(bot_token, telegram_id, text)
                    except Exception as e:
                        print(f"Failed to send ticket notification to admin: {e}")
            
            for admin in admins:
                if old_bot_token:
                    threading.Thread(
                        target=send_notification,
                        args=(old_bot_token, admin.telegram_id, notification_text)
                    ).start()
                
                if new_bot_token and new_bot_token != old_bot_token:
                    threading.Thread(
                        target=send_notification,
                        args=(new_bot_token, admin.telegram_id, notification_text)
                    ).start()
        
        response = jsonify({
            "message": "Reply sent successfully",
            "message_id": ticket_message.id
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 201
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error in miniapp_support_ticket_reply: {e}")
        import traceback
        traceback.print_exc()
        response = jsonify({
            "detail": {
                "title": "Internal Error",
                "message": str(e)
            }
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


# ============================================================================
# PAYMENT HISTORY
# ============================================================================

@app.route('/miniapp/payments/history', methods=['POST', 'OPTIONS'])
@limiter.limit("30 per minute")
def miniapp_payments_history():
    """Получить историю платежей пользователя"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response
    
    try:
        data = request.json or {}
        init_data = data.get('initData') or data.get('init_data') or ''
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({
                "detail": {"title": "Authorization Error", "message": "Missing initData"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({
                "detail": {"title": "User Not Found", "message": "User not registered"}
            })
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Получаем названия уровней тарифов (TariffLevel), fallback на branding для базовых
        from modules.models.branding import BrandingSetting
        from modules.models.tariff_level import TariffLevel

        branding = BrandingSetting.query.first()
        basic_name = getattr(branding, 'tariff_tier_basic_name', None) or 'Базовый'
        pro_name = getattr(branding, 'tariff_tier_pro_name', None) or 'Премиум'
        elite_name = getattr(branding, 'tariff_tier_elite_name', None) or 'Элитный'

        tier_names = {lvl.code.lower(): (lvl.name or lvl.code) for lvl in TariffLevel.query.filter_by(is_active=True).all()}
        tier_names.setdefault('basic', basic_name)
        tier_names.setdefault('pro', pro_name)
        tier_names.setdefault('elite', elite_name)
        
        # Получаем платежи пользователя
        payments = Payment.query.filter_by(user_id=user.id).order_by(Payment.created_at.desc()).limit(50).all()
        
        payments_list = []
        for p in payments:
            tariff = db.session.get(Tariff, p.tariff_id) if p.tariff_id else None
            tariff_name = None
            if tariff:
                # Используем tier для отображения (Basic, Pro, Elite), если он есть
                if tariff.tier:
                    tariff_name = tier_names.get(tariff.tier.lower(), tariff.tier)
                else:
                    # Проверяем, содержит ли name период (месяц, дней и т.д.)
                    period_patterns = [
                        r'\d+\s*(месяц|месяца|месяцев|Месяц|Месяца|Месяцев)',
                        r'\d+\s*(день|дня|дней|День|Дня|Дней)',
                        r'\d+\s*(day|days|Day|Days)',
                        r'\d+\s*(мес|Мес)'
                    ]
                    has_period = any(re.search(pattern, tariff.name or '') for pattern in period_patterns)
                    if has_period:
                        # Если name содержит период, определяем tier по duration_days
                        if tariff.duration_days >= 180:
                            tariff_name = elite_name
                        elif tariff.duration_days >= 90:
                            tariff_name = pro_name
                        else:
                            tariff_name = basic_name
                    else:
                        tariff_name = tariff.name
            
            payments_list.append({
                "id": p.id,
                "order_id": p.order_id,
                "amount": float(p.amount) if p.amount else 0.0,
                "currency": p.currency,
                "status": p.status.lower(),
                "payment_provider": p.payment_provider,
                "tariff_name": tariff_name,
                "tariff_duration_days": tariff.duration_days if tariff else None,
                "created_at": p.created_at.isoformat() if p.created_at else None
            })
        
        response = jsonify({"payments": payments_list})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({"payments": []})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200


# ============================================================================
# КАЗИНО (Колесо Фортуны)
# ============================================================================

import random
from modules.models.casino import CasinoGame, CasinoStats

def get_casino_config():
    """Получить конфигурацию казино из ENV"""
    return {
        'enabled': os.environ.get('CASINO_ENABLED', 'false').lower() == 'true',
        'min_bet': int(os.environ.get('CASINO_MIN_BET', '1')),
        'max_bet': int(os.environ.get('CASINO_MAX_BET', '30')),
        'max_games_per_day': int(os.environ.get('CASINO_MAX_GAMES_PER_DAY', '10')),
        'chances': {
            0: int(os.environ.get('CASINO_CHANCE_X0', '40')),
            0.5: int(os.environ.get('CASINO_CHANCE_X05', '15')),
            1: int(os.environ.get('CASINO_CHANCE_X1', '15')),
            1.5: int(os.environ.get('CASINO_CHANCE_X15', '12')),
            2: int(os.environ.get('CASINO_CHANCE_X2', '10')),
            3: int(os.environ.get('CASINO_CHANCE_X3', '5')),
            5: int(os.environ.get('CASINO_CHANCE_X5', '3')),
        }
    }


def spin_wheel(chances):
    """Крутит колесо и возвращает множитель"""
    # Создаём список секторов с учётом шансов
    sectors = []
    for multiplier, chance in chances.items():
        sectors.extend([multiplier] * chance)
    
    # Перемешиваем и выбираем случайный
    random.shuffle(sectors)
    return random.choice(sectors)


def get_user_days_remaining(user):
    """Получить количество оставшихся дней подписки (частичный день считаем как 1 день)."""
    if not user or not user.remnawave_uuid:
        return 0

    def days_from_expire(expire_at):
        if not expire_at:
            return 0
        try:
            if isinstance(expire_at, str):
                expire_at = expire_at.replace('Z', '+00:00')
            expire_date = datetime.fromisoformat(expire_at) if isinstance(expire_at, str) else expire_at
            if hasattr(expire_date, 'tzinfo') and expire_date.tzinfo is None:
                expire_date = expire_date.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = expire_date - now
            if delta.total_seconds() <= 0:
                return 0
            return max(0, math.ceil(delta.total_seconds() / 86400))
        except Exception:
            return 0

    # Сначала пробуем кэш (те же данные, что видит пользователь в подписке)
    try:
        cache_key = f'live_data_{user.remnawave_uuid}'
        cached = cache.get(cache_key)
        if cached and isinstance(cached, dict):
            d = days_from_expire(cached.get('expireAt'))
            if d > 0:
                return d
    except Exception:
        pass

    try:
        api_url = os.environ.get('API_URL', '')
        admin_token = os.environ.get('ADMIN_TOKEN', '')
        if not api_url or not admin_token:
            return 0

        response = requests.get(
            f"{api_url}/api/users/{user.remnawave_uuid}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json().get('response', {})
            return days_from_expire(data.get('expireAt'))
    except Exception:
        pass

    return 0


def update_user_subscription(user, days_delta):
    """Обновить подписку пользователя (добавить/убавить дни). days_delta может быть float (для x0.5)."""
    if not user or not user.remnawave_uuid:
        return False

    try:
        api_url = os.environ.get('API_URL', '')
        admin_token = os.environ.get('ADMIN_TOKEN', '')
        if not api_url or not admin_token:
            return False

        # Получаем текущую дату окончания из API (всегда свежие данные)
        response = requests.get(
            f"{api_url}/api/users/{user.remnawave_uuid}",
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10
        )

        if response.status_code != 200:
            return False

        data = response.json().get('response', {})
        current_expire = data.get('expireAt')

        if current_expire:
            if isinstance(current_expire, str):
                current_expire = current_expire.replace('Z', '+00:00')
            expire_date = datetime.fromisoformat(current_expire) if isinstance(current_expire, str) else current_expire
        else:
            expire_date = datetime.now(timezone.utc)

        if hasattr(expire_date, 'tzinfo') and expire_date.tzinfo is None:
            expire_date = expire_date.replace(tzinfo=timezone.utc)

        # Добавляем/убавляем дни (поддержка дробных для x0.5)
        days_delta_val = float(days_delta)
        new_expire = expire_date + timedelta(days=days_delta_val)

        update_response = requests.patch(
            f"{api_url}/api/users",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            },
            json={"uuid": user.remnawave_uuid, "expireAt": new_expire.isoformat()},
            timeout=10
        )

        if update_response.status_code != 200:
            print(f"Error updating subscription: Status {update_response.status_code}, Response: {update_response.text[:200]}")
            return False

        # Сбрасываем кэш, чтобы get_user_days_remaining и подписка в мини-аппе видели новый баланс
        try:
            cache.delete(f'live_data_{user.remnawave_uuid}')
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"Error updating subscription: {e}")
        return False


@app.route('/miniapp/casino/config', methods=['GET', 'POST', 'OPTIONS'])
def casino_config():
    """Получить конфигурацию казино"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', '*')
        response.headers.add('Access-Control-Allow-Methods', '*')
        return response, 200
    
    config = get_casino_config()
    
    # Преобразуем шансы в массив для фронтенда
    wheel_sectors = [
        {'multiplier': 0, 'label': 'x0', 'color': '#ff4444', 'chance': config['chances'][0]},
        {'multiplier': 0.5, 'label': 'x0.5', 'color': '#ff8844', 'chance': config['chances'][0.5]},
        {'multiplier': 1, 'label': 'x1', 'color': '#ffbb44', 'chance': config['chances'][1]},
        {'multiplier': 1.5, 'label': 'x1.5', 'color': '#88cc44', 'chance': config['chances'][1.5]},
        {'multiplier': 2, 'label': 'x2', 'color': '#44cc88', 'chance': config['chances'][2]},
        {'multiplier': 3, 'label': 'x3', 'color': '#44aacc', 'chance': config['chances'][3]},
        {'multiplier': 5, 'label': 'x5', 'color': '#aa44cc', 'chance': config['chances'][5]},
    ]
    
    response = jsonify({
        'enabled': config['enabled'],
        'min_bet': config['min_bet'],
        'max_bet': config['max_bet'],
        'max_games_per_day': config['max_games_per_day'],
        'wheel_sectors': wheel_sectors
    })
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response, 200


@app.route('/miniapp/casino/play', methods=['POST', 'OPTIONS'])
def casino_play():
    """Играть в казино"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', '*')
        response.headers.add('Access-Control-Allow-Methods', '*')
        return response, 200
    
    config = get_casino_config()
    
    if not config['enabled']:
        response = jsonify({'error': 'Фортуна отключена'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 400
    
    try:
        data = request.json or {}
        init_data = data.get('initData', '')
        bet_days = int(data.get('bet', 1))
        
        # Парсим данные пользователя
        telegram_id, user_data = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({'error': 'Не авторизован'})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 401
        
        # Находим пользователя
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({'error': 'Пользователь не найден'})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 404
        
        # Проверяем ставку
        if bet_days < config['min_bet'] or bet_days > config['max_bet']:
            response = jsonify({'error': f'Ставка должна быть от {config["min_bet"]} до {config["max_bet"]} дней'})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        # Проверяем лимит игр в день
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        games_today = CasinoGame.query.filter(
            CasinoGame.user_id == user.id,
            CasinoGame.created_at >= today_start
        ).count()
        
        if games_today >= config['max_games_per_day']:
            response = jsonify({'error': f'Лимит игр на сегодня исчерпан ({config["max_games_per_day"]} игр)'})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400
        
        # Проверяем баланс дней
        balance_before = get_user_days_remaining(user)
        if balance_before < bet_days:
            response = jsonify({'error': f'Недостаточно дней для ставки. У вас {balance_before} дней'})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 400

        # Крутим колесо (до списания, чтобы результат не зависел от порядка запросов)
        multiplier = spin_wheel(config['chances'])

        # Выигрыш: bet_days * multiplier дней (float для x0.5 — половина обратно)
        if multiplier == 0:
            win_days = 0.0
        else:
            win_days = float(bet_days) * multiplier

        # Одно атомарное обновление: списание ставки и начисление выигрыша (нет гонки между двумя PATCH)
        net_delta = -bet_days + win_days
        success = update_user_subscription(user, net_delta)
        if not success:
            response = jsonify({'error': 'Ошибка обновления подписки (списание/начисление дней)'})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 500

        # Финальный баланс дней после обновления
        balance_after = get_user_days_remaining(user)
        
        # Чистый выигрыш/проигрыш для статистики
        net_win_days = win_days - bet_days
        
        # Логируем для отладки (списание и выдача дней в одном PATCH: net_delta = -bet_days + win_days)
        print(f"Casino: bet={bet_days}, multiplier={multiplier}, win_days={win_days}, net_delta={net_delta}, balance_before={balance_before}, balance_after={balance_after}")
        
        # Сохраняем игру в историю (в БД — целые числа)
        net_win_int = int(round(net_win_days))
        game = CasinoGame(
            user_id=user.id,
            bet_days=bet_days,
            multiplier=multiplier,
            win_days=net_win_int,
            balance_before=balance_before,
            balance_after=balance_after
        )
        db.session.add(game)
        
        # Обновляем статистику казино
        stats = CasinoStats.query.first()
        if not stats:
            stats = CasinoStats(
                total_games=0,
                total_bet_days=0,
                total_win_days=0,
                total_lost_days=0,
                house_profit_days=0
            )
            db.session.add(stats)
        
        # Инициализируем None значения
        if stats.total_games is None:
            stats.total_games = 0
        if stats.total_bet_days is None:
            stats.total_bet_days = 0
        if stats.total_win_days is None:
            stats.total_win_days = 0
        if stats.total_lost_days is None:
            stats.total_lost_days = 0
        
        stats.total_games += 1
        stats.total_bet_days += bet_days
        
        if net_win_days > 0:
            stats.total_win_days += max(0, int(round(net_win_days)))
        else:
            stats.total_lost_days += max(0, int(round(abs(net_win_days))))
        
        stats.house_profit_days = stats.total_lost_days - stats.total_win_days
        
        db.session.commit()
        
        # Определяем результат
        if multiplier == 0:
            result_text = 'Вы проиграли!'
            result_type = 'lose'
        elif multiplier == 1:
            result_text = 'Ставка возвращена'
            result_type = 'neutral'
        elif multiplier < 1:
            result_text = 'Частичный возврат'
            result_type = 'partial'
        else:
            result_text = 'Вы выиграли!'
            result_type = 'win'
        
        response = jsonify({
            'success': True,
            'multiplier': multiplier,
            'bet_days': bet_days,
            'win_days': win_days,  # Количество дней, которое было добавлено
            'net_win_days': net_win_days,  # Чистый выигрыш/проигрыш (выигрыш - ставка)
            'balance_before': balance_before,
            'balance_after': balance_after,
            'result_text': result_text,
            'result_type': result_type,
            'games_remaining_today': config['max_games_per_day'] - games_today - 1
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({'error': str(e)})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


@app.route('/miniapp/casino/history', methods=['GET', 'POST', 'OPTIONS'])
def casino_history():
    """Получить историю игр пользователя"""
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', '*')
        response.headers.add('Access-Control-Allow-Methods', '*')
        return response, 200
    
    try:
        data = request.json or {}
        init_data = data.get('initData', '')
        
        telegram_id, _ = parse_telegram_init_data(init_data)
        
        if not telegram_id:
            response = jsonify({'games': []})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 200
        
        user = User.query.filter_by(telegram_id=str(telegram_id)).first()
        if not user:
            response = jsonify({'games': []})
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response, 200
        
        # Последние 20 игр
        games = CasinoGame.query.filter_by(user_id=user.id).order_by(
            CasinoGame.created_at.desc()
        ).limit(20).all()
        
        # Статистика пользователя
        total_games = CasinoGame.query.filter_by(user_id=user.id).count()
        total_bet = db.session.query(db.func.sum(CasinoGame.bet_days)).filter_by(user_id=user.id).scalar() or 0
        total_win = db.session.query(db.func.sum(CasinoGame.win_days)).filter_by(user_id=user.id).scalar() or 0
        
        response = jsonify({
            'games': [g.to_dict() for g in games],
            'stats': {
                'total_games': total_games,
                'total_bet': total_bet,
                'total_profit': total_win  # Может быть отрицательным
            }
        })
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({'games': [], 'stats': {}})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 200
