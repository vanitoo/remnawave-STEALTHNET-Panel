"""
API вебхуков платежных систем

- POST /api/webhook/heleket - Heleket webhook
- POST /api/webhook/yookassa - YooKassa webhook
- POST /api/webhook/yoomoney - YooMoney webhook (payment buttons / quickpay notifications)
- POST /api/webhook/telegram - Telegram Stars webhook
- POST /api/webhook/telegram-stars - Telegram Stars webhook (alt)
- POST /api/webhook/freekassa - FreeKassa webhook
- POST /api/webhook/kassa_ai - Kassa AI (Freekassa api.fk.life) webhook
- POST /api/webhook/robokassa - Robokassa webhook
"""

from flask import request, jsonify
from datetime import datetime, timezone, timedelta
import requests
import json
import os
import threading
import hashlib

from modules.core import get_app, get_db, get_cache, get_fernet
from modules.models.payment import Payment, PaymentSetting
from modules.models.user import User
from modules.models.tariff import Tariff
from modules.models.promo import PromoCode
from modules.models.referral import ReferralSetting
from modules.currency import convert_to_usd
from modules.models.option import PurchaseOption

app = get_app()
db = get_db()
cache = get_cache()

BOT_API_URL = os.getenv("BOT_API_URL", "")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")


def add_referral_commission(user, amount_usd, is_tariff_purchase=True):
    """
    Начисляет реферальную комиссию рефереру пользователя
    
    Args:
        user: Пользователь, который совершил покупку/пополнение
        amount_usd: Сумма в USD
        is_tariff_purchase: True если покупка тарифа, False если пополнение баланса
    """
    try:
        # Проверяем тип реферальной системы
        referral_settings = ReferralSetting.query.first()
        if not referral_settings:
            return
        
        # Если система на днях, не начисляем проценты
        if referral_settings.referral_type != 'PERCENT':
            return
        
        # Проверяем наличие реферера
        if not user.referrer_id:
            return
        
        referrer = db.session.get(User, user.referrer_id)
        if not referrer:
            return
        
        # Получаем процент реферала (индивидуальный или дефолтный)
        # Если у реферера установлен индивидуальный процент - используем его, иначе глобальный
        referral_percent = referrer.referral_percent if referrer.referral_percent is not None else referral_settings.default_referral_percent
        
        # Вычисляем комиссию
        commission_usd = (amount_usd * referral_percent) / 100.0
        
        # Начисляем на баланс реферера
        current_balance = float(referrer.balance) if referrer.balance else 0.0
        referrer.balance = current_balance + commission_usd
        
        print(f"[REFERRAL] Начислено {commission_usd:.2f} USD ({referral_percent}%) рефереру {referrer.id} за покупку пользователя {user.id}")
        
    except Exception as e:
        print(f"[REFERRAL] Ошибка начисления комиссии: {e}")
        import traceback
        traceback.print_exc()


def get_remnawave_headers(additional_headers=None):
    headers = {}
    cookies = {}
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
    if ADMIN_TOKEN:
        headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"
    REMNAWAVE_COOKIES_STR = os.getenv("REMNAWAVE_COOKIES", "")
    if REMNAWAVE_COOKIES_STR:
        try:
            cookies = json.loads(REMNAWAVE_COOKIES_STR)
        except:
            pass
    if additional_headers:
        headers.update(additional_headers)
    return headers, cookies


def decrypt_key(key):
    fernet = get_fernet()
    if not key or not fernet:
        return ""
    try:
        return fernet.decrypt(key).decode('utf-8')
    except:
        return ""


def sync_subscription_to_bot(app_context, remnawave_uuid):
    """Синхронизация подписки в бота"""
    with app_context:
        try:
            if not BOT_API_URL or not BOT_API_TOKEN:
                return
            bot_api_url = BOT_API_URL.rstrip('/')
            requests.post(
                f"{bot_api_url}/remnawave/sync/from-panel",
                headers={"X-API-Key": BOT_API_TOKEN, "Content-Type": "application/json"},
                json={},
                timeout=60
            )
        except Exception as e:
            print(f"Background sync error: {e}")


def _resolve_target_remnawave_uuid(payment: Payment, user: User) -> str:
    """
    Определить RemnaWave UUID, к которому применять опцию.
    Приоритет:
    - payment.user_config_id (если задан)
    - user.remnawave_uuid (если похоже на UUID)
    - primary UserConfig пользователя
    - первый UserConfig пользователя
    """
    try:
        if getattr(payment, 'user_config_id', None):
            from modules.models.user_config import UserConfig
            cfg = UserConfig.query.get(payment.user_config_id)
            if cfg and cfg.remnawave_uuid:
                return cfg.remnawave_uuid
    except Exception:
        pass

    try:
        ruuid = getattr(user, 'remnawave_uuid', None)
        if ruuid and isinstance(ruuid, str):
            # в проекте встречались "email" в remnawave_uuid при переходах → фильтруем
            if '@' not in ruuid and len(ruuid) >= 16:
                return ruuid
    except Exception:
        pass

    try:
        from modules.models.user_config import UserConfig
        primary = UserConfig.query.filter_by(user_id=user.id, is_primary=True).first()
        if primary and primary.remnawave_uuid:
            return primary.remnawave_uuid
        any_cfg = UserConfig.query.filter_by(user_id=user.id).order_by(UserConfig.created_at.asc()).first()
        if any_cfg and any_cfg.remnawave_uuid:
            return any_cfg.remnawave_uuid
    except Exception:
        pass

    return getattr(user, 'remnawave_uuid', None) or ""


def process_option_purchase(payment, user):
    """Обработка успешной покупки опции (трафик, устройства, сквад)"""
    API_URL = os.getenv("API_URL")

    try:
        if not getattr(payment, 'description', None) or not str(payment.description).startswith('OPTION:'):
            print(f"[OPTION] Invalid payment description: {getattr(payment, 'description', None)}")
            return False

        parts = str(payment.description).split(':')
        if len(parts) < 2:
            print(f"[OPTION] Invalid description format: {payment.description}")
            return False

        option_id = int(parts[1])
        option = PurchaseOption.query.get(option_id)
        if not option:
            print(f"[OPTION] Option not found: {option_id}")
            return False

        target_uuid = _resolve_target_remnawave_uuid(payment, user)
        if not target_uuid:
            print(f"[OPTION] No target remnawave_uuid for user_id={user.id}, payment_id={payment.id}")
            return False

        # Получаем текущие данные пользователя из RemnaWave
        h, c = get_remnawave_headers({"Content-Type": "application/json"})
        resp = requests.get(f"{API_URL}/api/users/{target_uuid}", headers=h, cookies=c, timeout=15)
        if resp.status_code != 200:
            print(f"[OPTION] Failed to get user data: {resp.status_code} - {resp.text[:200]}")
            return False

        user_data = resp.json().get('response', {}) if isinstance(resp.json(), dict) else {}

        patch_payload = {"uuid": target_uuid}
        option_type = option.option_type
        option_value = option.value

        if option_type == 'traffic':
            try:
                gb_to_add = float(option_value)
                bytes_to_add = int(gb_to_add * 1024 * 1024 * 1024)
                current_limit = user_data.get('trafficLimitBytes', 0) or 0
                patch_payload["trafficLimitBytes"] = int(current_limit) + bytes_to_add
                patch_payload["trafficLimitStrategy"] = user_data.get('trafficLimitStrategy', 'NO_RESET')
                print(f"[OPTION] Adding traffic: +{gb_to_add}GB to {target_uuid}")
            except Exception:
                print(f"[OPTION] Invalid traffic value: {option_value}")
                return False

        elif option_type == 'devices':
            try:
                devices_to_add = int(float(option_value))
                current_limit = user_data.get('hwidDeviceLimit', 0) or 0
                patch_payload["hwidDeviceLimit"] = int(current_limit) + devices_to_add
                print(f"[OPTION] Adding devices: +{devices_to_add} to {target_uuid}")
            except Exception:
                print(f"[OPTION] Invalid devices value: {option_value}")
                return False

        elif option_type == 'squad':
            squad_uuid = option.squad_uuid if option.squad_uuid else option_value
            if not squad_uuid:
                print(f"[OPTION] No squad UUID found for option {option_id}")
                return False
            current_squads = user_data.get('activeInternalSquads', []) or []
            if squad_uuid not in current_squads:
                patch_payload["activeInternalSquads"] = current_squads + [squad_uuid]
            print(f"[OPTION] Adding squad: {squad_uuid} to {target_uuid}")

        else:
            print(f"[OPTION] Unknown option type: {option_type}")
            return False

        patch_resp = requests.patch(f"{API_URL}/api/users", headers=h, cookies=c, json=patch_payload, timeout=20)
        if not patch_resp.ok:
            print(f"[OPTION] Failed to update user: {patch_resp.status_code} - {patch_resp.text[:200]}")
            return False

        payment.status = 'PAID'
        db.session.commit()

        # Очищаем кэш
        try:
            cache.delete(f'live_data_{target_uuid}')
            cache.delete(f'nodes_{target_uuid}')
            cache.delete(f'miniapp_nodes_{target_uuid}')
        except Exception:
            pass

        # Реферальная комиссия (если PERCENT)
        try:
            amount_usd = convert_to_usd(payment.amount, payment.currency)
            add_referral_commission(user, amount_usd, is_tariff_purchase=False)
            db.session.commit()
        except Exception as e:
            print(f"[OPTION] Referral commission error: {e}")

        print(f"[OPTION] Successfully processed option purchase: user_id={user.id}, option_id={option.id}")
        return True

    except Exception as e:
        print(f"[OPTION] Error processing option purchase: {e}")
        import traceback
        traceback.print_exc()
        return False


def process_successful_payment(payment, user, tariff):
    """Обработка успешного платежа"""
    API_URL = os.getenv("API_URL")
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
    DEFAULT_SQUAD_ID = os.getenv("DEFAULT_SQUAD_ID")
    
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    
    try:
        # Если нужно создать новый конфиг, создаем его перед обработкой платежа
        if hasattr(payment, 'create_new_config') and payment.create_new_config:
            from modules.models.user_config import UserConfig
            
            # Генерируем уникальный username для нового аккаунта
            base_username = None
            if user.email:
                base_username = user.email.replace("@", "_").replace(".", "_")
            else:
                base_username = f"tg_{user.telegram_id}"
            
            # Находим все существующие конфиги пользователя
            existing_configs = UserConfig.query.filter_by(user_id=user.id).all()
            existing_usernames = set()
            existing_emails = set()
            
            # Пытаемся получить username из Remna для существующих конфигов
            for config in existing_configs:
                try:
                    resp = requests.get(
                        f"{API_URL}/api/users/{config.remnawave_uuid}",
                        headers=headers,
                        timeout=5
                    )
                    if resp.status_code == 200:
                        user_data = resp.json().get('response', {})
                        username = user_data.get('username')
                        if username:
                            existing_usernames.add(username)
                        email = user_data.get('email')
                        if email:
                            try:
                                existing_emails.add(str(email).strip().lower())
                            except Exception:
                                pass
                except:
                    pass
            
            # Генерируем уникальный username
            new_username = base_username
            suffix_num = 1
            while new_username in existing_usernames:
                new_username = f"{base_username}_{suffix_num}"
                suffix_num += 1
                if suffix_num > 100:
                    import random
                    import string
                    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
                    new_username = f"{base_username}_{random_suffix}"
                    break
            
            # Генерируем email и пароль
            # ВАЖНО: email должен быть уникальным между конфигами.
            # Если использовать один и тот же email для нескольких Remna-аккаунтов, админка (и любая логика поиска по email)
            # начинает путаться, а UUID у пользователя может "прыгать" между конфигами → ломаются оплаты/уведомления.
            cfg_idx = len(existing_configs) + 1
            if user.email and "@" in user.email:
                local_part, domain_part = user.email.split("@", 1)
                candidate = f"{local_part}+cfg{cfg_idx}@{domain_part}"
            else:
                candidate = f"tg_{user.telegram_id}_cfg{cfg_idx}@telegram.local"

            candidate_norm = candidate.strip().lower()
            email_suffix = 1
            while candidate_norm in existing_emails:
                if user.email and "@" in user.email:
                    local_part, domain_part = user.email.split("@", 1)
                    candidate = f"{local_part}+cfg{cfg_idx}_{email_suffix}@{domain_part}"
                else:
                    candidate = f"tg_{user.telegram_id}_cfg{cfg_idx}_{email_suffix}@telegram.local"
                candidate_norm = candidate.strip().lower()
                email_suffix += 1
                if email_suffix > 100:
                    break

            new_email = candidate
            import random
            import string
            new_password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
            
            # Создаем новый аккаунт в Remna
            expire_date = datetime.now(timezone.utc).isoformat()
            payload_create = {
                "email": new_email,
                "password": new_password,
                "username": new_username,
                "expireAt": expire_date
            }
            
            # Добавляем telegramId если есть
            if user.telegram_id:
                try:
                    telegram_id_int = int(user.telegram_id) if isinstance(user.telegram_id, (str, int)) else user.telegram_id
                    payload_create["telegramId"] = telegram_id_int
                except (ValueError, TypeError):
                    payload_create["telegramId"] = str(user.telegram_id)
            
            create_resp = requests.post(
                f"{API_URL}/api/users",
                headers=headers,
                json=payload_create,
                timeout=30
            )
            
            if create_resp.status_code not in [200, 201]:
                print(f"Failed to create new Remna account: {create_resp.status_code}")
                return False
            
            remnawave_uuid = create_resp.json().get('response', {}).get('uuid')
            if not remnawave_uuid:
                print("Failed to get UUID from newly created Remna account")
                return False
            
            # Создаем UserConfig
            config_name = f'Конфиг {len(existing_configs) + 1}'
            new_config = UserConfig(
                user_id=user.id,
                remnawave_uuid=remnawave_uuid,
                config_name=config_name,
                is_primary=False
            )
            db.session.add(new_config)
            db.session.flush()
            
            # Обновляем payment с user_config_id
            payment.user_config_id = new_config.id
            db.session.commit()
            
            print(f"✅ Создан новый конфиг {new_config.id} для пользователя {user.id} после оплаты")
        else:
            # Определяем, какой конфиг обновлять.
            # Правило: если у платежа нет user_config_id — обновляем основной конфиг (is_primary=True),
            # чтобы бот/сайт (которые по умолчанию работают с основным) всегда видели результат оплаты.
            remnawave_uuid = user.remnawave_uuid
            primary_config = None
            try:
                from modules.models.user_config import UserConfig
                primary_config = UserConfig.query.filter_by(user_id=user.id, is_primary=True).first()
                if primary_config and primary_config.remnawave_uuid:
                    remnawave_uuid = primary_config.remnawave_uuid
            except Exception as e:
                print(f"Warning: failed to resolve primary config for user {user.id}: {e}")
            
            # Если указан user_config_id, используем его (поверх primary)
            if payment.user_config_id:
                from modules.models.user_config import UserConfig
                user_config = db.session.get(UserConfig, payment.user_config_id)
                if user_config and user_config.user_id == user.id:
                    remnawave_uuid = user_config.remnawave_uuid
                else:
                    print(f"Warning: user_config_id {payment.user_config_id} not found or doesn't belong to user {user.id}, using primary config")
        
        resp = requests.get(f"{API_URL}/api/users/{remnawave_uuid}", headers=headers)
        if resp.status_code != 200:
            print(
                f"Failed to get user data: {resp.status_code} (uuid={remnawave_uuid}, payment={getattr(payment,'order_id',None)}, user_id={getattr(user,'id',None)})"
            )
            return False
            
        user_data = resp.json().get('response', {})
        current_expire = user_data.get('expireAt')
        current_squads = user_data.get('activeInternalSquads', [])
        
        # Дни по тарифу: базовые + бонусные (чётко начисляются при оплате)
        days_to_add = tariff.duration_days + (getattr(tariff, 'bonus_days', None) or 0)

        if current_expire:
            # Обработка формата с 'Z'
            if isinstance(current_expire, str) and current_expire.endswith('Z'):
                current_expire = current_expire[:-1] + '+00:00'
            current_expire_dt = datetime.fromisoformat(current_expire)
            if current_expire_dt.tzinfo is None:
                current_expire_dt = current_expire_dt.replace(tzinfo=timezone.utc)
            new_expire_dt = max(datetime.now(timezone.utc), current_expire_dt) + timedelta(days=days_to_add)
        else:
            new_expire_dt = datetime.now(timezone.utc) + timedelta(days=days_to_add)
        
        # Получаем список сквадов из тарифа
        squad_ids = []
        if hasattr(tariff, 'get_squad_ids'):
            squad_ids = tariff.get_squad_ids()
        elif hasattr(tariff, 'squad_ids') and tariff.squad_ids:
            try:
                import json
                squad_ids = json.loads(tariff.squad_ids) if isinstance(tariff.squad_ids, str) else tariff.squad_ids
            except:
                squad_ids = []
        
        # Если сквады не указаны, используем дефолтный/текущие
        if not squad_ids:
            if tariff.squad_id:
                squad_ids = [tariff.squad_id]
            else:
                # Если DEFAULT_SQUAD_ID не задан — сохраняем текущие сквады, чтобы подписка не стала "неактивной"
                if DEFAULT_SQUAD_ID:
                    squad_ids = [DEFAULT_SQUAD_ID]
                else:
                    squad_ids = current_squads or []
        
        patch_payload = {
            "uuid": remnawave_uuid,
            "expireAt": new_expire_dt.isoformat(),
            "activeInternalSquads": squad_ids
        }
        
        if tariff.traffic_limit_bytes and tariff.traffic_limit_bytes > 0:
            patch_payload["trafficLimitBytes"] = tariff.traffic_limit_bytes
            patch_payload["trafficLimitStrategy"] = "NO_RESET"
        
        # Устанавливаем лимит устройств, если он указан в тарифе
        if hasattr(tariff, 'hwid_device_limit') and tariff.hwid_device_limit is not None and tariff.hwid_device_limit > 0:
            patch_payload["hwidDeviceLimit"] = tariff.hwid_device_limit
        
        h, c = get_remnawave_headers({"Content-Type": "application/json"})
        patch_resp = requests.patch(f"{API_URL}/api/users", headers=h, cookies=c, json=patch_payload)
        
        if not patch_resp.ok:
            print(f"Failed to update user: {patch_resp.status_code}")
            return False
        
        # Списываем промокод
        if payment.promo_code_id:
            promo = db.session.get(PromoCode, payment.promo_code_id)
            if promo and promo.uses_left > 0:
                promo.uses_left -= 1
        
        # Если это покупка с баланса, баланс уже списан в purchase_with_balance
        # Здесь только проверяем, что баланс достаточен (если еще не списан)
        if payment.payment_provider == 'balance':
            from modules.currency import convert_to_usd
            amount_usd = convert_to_usd(payment.amount, payment.currency)
            current_balance_usd = float(user.balance) if user.balance else 0.0
            # Баланс должен быть уже списан в purchase_with_balance, но проверяем на всякий случай
            if current_balance_usd < amount_usd:
                print(f"Warning: Balance may not be sufficient for payment {payment.order_id}")
                # Не возвращаем False, так как баланс уже списан в purchase_with_balance
        
        payment.status = 'PAID'
        db.session.commit()

        # Уведомления должны приходить даже если дальше упадут "не критичные" шаги (рефералка/кэш/синхронизация).
        # Поэтому шлем их сразу после успешного коммита статуса платежа.
        try:
            from modules.notifications import notify_payment
            notify_payment(payment, user, tariff, is_balance_topup=False)
        except Exception as e:
            print(f"Error sending payment notification: {e}")

        try:
            # Важно: шлем синхронно, чтобы не терять уведомления из daemon-треда
            from modules.notifications import send_user_payment_notification
            ok, err = send_user_payment_notification(
                user,
                is_successful=True,
                tariff_name=tariff.name,
                is_balance_topup=False,
                payment_order_id=payment.order_id,
                payment=payment,
            )
            if not ok and err:
                print(f"User payment notification failed (sync): {err}")
        except Exception as e:
            print(f"Error sending user payment notification: {e}")
        
        # Начисляем реферальную комиссию
        try:
            amount_usd = convert_to_usd(payment.amount, payment.currency)
            add_referral_commission(user, amount_usd, is_tariff_purchase=True)
            db.session.commit()
        except Exception as e:
            # Не блокируем результат оплаты/уведомления из-за ошибок рефералки
            print(f"Warning: referral commission failed for payment {payment.order_id}: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass
        
        # Очищаем кэш для всех конфигов пользователя
        try:
            cache.delete(f'live_data_{remnawave_uuid}')
            cache.delete(f'nodes_{remnawave_uuid}')
            cache.delete('all_live_users_map')
        except Exception as e:
            print(f"Warning: cache clear failed for uuid {remnawave_uuid}: {e}")
        
        # Очищаем кэш для основного конфига, если это был дополнительный
        if payment.user_config_id and remnawave_uuid != user.remnawave_uuid:
            try:
                cache.delete(f'live_data_{user.remnawave_uuid}')
                cache.delete(f'nodes_{user.remnawave_uuid}')
            except Exception as e:
                print(f"Warning: cache clear failed for primary uuid {user.remnawave_uuid}: {e}")
        
        # Синхронизация с ботом
        if BOT_API_URL and BOT_API_TOKEN:
            threading.Thread(
                target=sync_subscription_to_bot,
                args=(app.app_context(), user.remnawave_uuid),
                daemon=True
            ).start()
        
        return True
        
    except Exception as e:
        print(f"Error processing payment: {e}")
        return False


# ============================================================================
# WEBHOOKS
# ============================================================================

@app.route('/api/webhook/heleket', methods=['POST'])
def heleket_webhook():
    """Heleket webhook"""
    try:
        data = request.json
        print(f"[HELEKET] Received: {json.dumps(data, indent=2)}")
        
        order_id = data.get('order_id')
        status = data.get('status')
        
        if not order_id or not status:
            return jsonify({"status": "error", "message": "Missing parameters"}), 400
        
        payment = Payment.query.filter_by(order_id=order_id).first()
        if not payment:
            return jsonify({"status": "error", "message": "Payment not found"}), 404
        
        payment.status = status.upper()
        payment.payment_system_id = data.get('payment_id')
        db.session.commit()
        
        if status.upper() == 'PAID':
            user = User.query.get(payment.user_id)
            is_option_purchase = bool(getattr(payment, 'description', None)) and str(payment.description).startswith('OPTION:')
            if user and is_option_purchase:
                process_option_purchase(payment, user)
            else:
                tariff = Tariff.query.get(payment.tariff_id)
                if user and tariff:
                    process_successful_payment(payment, user, tariff)
        
        return jsonify({"status": "success"}), 200
        
    except Exception as e:
        print(f"[HELEKET] Error: {e}")
        return jsonify({"status": "error", "message": str(e)[:200]}), 500


@app.route('/api/webhook/yookassa', methods=['GET', 'POST'])
def yookassa_webhook():
    """YooKassa webhook"""
    # YooKassa может отправлять GET запрос для проверки доступности webhook
    if request.method == 'GET':
        return jsonify({"status": "ok", "message": "YooKassa webhook is available"}), 200
    
    try:
        data = request.json
        print(f"[YOOKASSA] 📥 Webhook received: {json.dumps(data, indent=2, ensure_ascii=False)}")
        
        # YooKassa может отправлять разные типы событий
        event_type = data.get('event', '')
        object_data = data.get('object')
        
        if not object_data:
            print(f"[YOOKASSA] ❌ No object data in webhook")
            return jsonify({"status": "error", "message": "No object data"}), 400
        
        # Обработка событий возврата (refund.succeeded)
        if event_type == 'refund.succeeded':
            # Для возвратов ищем платеж по payment_id из объекта возврата
            payment_id = object_data.get('payment_id')
            if not payment_id:
                print(f"[YOOKASSA] ❌ Missing payment_id in refund object")
                return jsonify({"status": "error", "message": "Missing payment_id in refund"}), 400
            
            # Ищем платеж по payment_system_id (который равен payment_id из YooKassa)
            payment = Payment.query.filter_by(payment_system_id=payment_id).first()
            if not payment:
                print(f"[YOOKASSA] ⚠️ Payment not found for refund payment_id: {payment_id} (ignoring)")
                # Возвращаем успех, чтобы YooKassa не повторял запрос
                return jsonify({"status": "success", "message": "Refund processed (payment not found)"}), 200
            
            # Обрабатываем возврат только если платеж был успешным
            if payment.status != 'PAID':
                print(f"[YOOKASSA] ⚠️ Payment {payment_id} is not PAID (status={payment.status}), skipping refund")
                return jsonify({"status": "success", "message": "Refund ignored (payment not paid)"}), 200
            
            user = User.query.get(payment.user_id)
            if not user:
                print(f"[YOOKASSA] ⚠️ User not found for refund payment {payment_id} (ignoring)")
                return jsonify({"status": "success", "message": "Refund processed (user not found)"}), 200
            
            refund_amount = float(object_data.get('amount', {}).get('value', 0))
            refund_currency = object_data.get('amount', {}).get('currency', 'RUB')
            
            print(f"[YOOKASSA] 🔄 Processing refund: payment_id={payment_id}, amount={refund_amount} {refund_currency}, user_id={user.id}")
            
            # Откатываем изменения
            is_option_purchase = bool(getattr(payment, 'description', None)) and str(payment.description).startswith('OPTION:')
            if payment.tariff_id is None and not is_option_purchase:
                # Это было пополнение баланса - вычитаем сумму
                current_balance_usd = float(user.balance) if user.balance else 0.0
                refund_amount_usd = convert_to_usd(refund_amount, refund_currency)
                new_balance = max(0.0, current_balance_usd - refund_amount_usd)  # Не даем балансу уйти в минус
                user.balance = new_balance
                payment.status = 'REFUNDED'
                db.session.commit()
                
                cache.delete(f'live_data_{user.remnawave_uuid}')
                cache.delete('all_live_users_map')
                
                print(f"[YOOKASSA] ✅ Balance refund processed: user_id={user.id}, refund={refund_amount_usd} USD, new_balance={new_balance} USD")
            else:
                # Это была покупка тарифа или опции - отмечаем как refunded (без изменения баланса)
                payment.status = 'REFUNDED'
                db.session.commit()
                
                # TODO: Можно добавить логику отмены тарифа через RemnaWave API, если нужно
                if is_option_purchase:
                    print(f"[YOOKASSA] ✅ Option purchase refunded: user_id={user.id}, payment_id={payment.id}")
                else:
                    print(f"[YOOKASSA] ✅ Tariff purchase refunded: user_id={user.id}, tariff_id={payment.tariff_id}")
            
            return jsonify({"status": "success"}), 200
        
        # Для обычных платежей получаем order_id из metadata
        metadata = object_data.get('metadata', {})
        order_id = metadata.get('order_id')
        status = object_data.get('status', '').lower()
        
        print(f"[YOOKASSA] 🔍 Parsed: event={event_type}, order_id={order_id}, status={status}")
        
        if not order_id:
            print(f"[YOOKASSA] ❌ Missing order_id in metadata: {metadata}")
            # Для событий payment.succeeded пробуем найти по payment_system_id
            if event_type == 'payment.succeeded':
                payment_system_id = object_data.get('id')
                if payment_system_id:
                    payment = Payment.query.filter_by(payment_system_id=payment_system_id).first()
                    if payment:
                        print(f"[YOOKASSA] ✅ Found payment by payment_system_id: {payment_system_id}")
                        order_id = payment.order_id  # Используем order_id из найденного платежа
                    else:
                        print(f"[YOOKASSA] ❌ Payment not found by payment_system_id: {payment_system_id}")
                        return jsonify({"status": "error", "message": "Payment not found"}), 404
                else:
                    return jsonify({"status": "error", "message": "Missing order_id in metadata"}), 400
            else:
                return jsonify({"status": "error", "message": "Missing order_id in metadata"}), 400
        
        if not status:
            print(f"[YOOKASSA] ❌ Missing status in object")
            return jsonify({"status": "error", "message": "Missing status"}), 400
        
        payment = Payment.query.filter_by(order_id=order_id).first()
        if not payment:
            print(f"[YOOKASSA] ❌ Payment not found for order_id: {order_id}")
            # Попробуем найти по payment_system_id
            payment_id = object_data.get('id')
            if payment_id:
                payment = Payment.query.filter_by(payment_system_id=payment_id).first()
                if payment:
                    print(f"[YOOKASSA] ✅ Found payment by payment_system_id: {payment_id}")
            if not payment:
                return jsonify({"status": "error", "message": "Payment not found"}), 404
        
        print(f"[YOOKASSA] 💳 Payment found: id={payment.id}, user_id={payment.user_id}, tariff_id={payment.tariff_id}, current_status={payment.status}")
        
        # Проверяем, не был ли платеж уже обработан (до изменения статуса)
        if payment.status == 'PAID':
            print(f"[YOOKASSA] ⚠️ Payment {order_id} already processed (status=PAID)")
            return jsonify({"status": "success", "message": "Payment already processed"}), 200
        
        # Сохраняем payment_system_id (ID платежа в YooKassa)
        payment_system_id = object_data.get('id')
        if payment_system_id:
            payment.payment_system_id = payment_system_id
            db.session.commit()
            print(f"[YOOKASSA] 💾 Saved payment_system_id: {payment_system_id}")
        
        # YooKassa отправляет статус 'succeeded' для успешных платежей
        # Также обрабатываем статус 'succeeded' из события 'payment.succeeded'
        if status == 'succeeded':
            user = User.query.get(payment.user_id)
            if not user:
                print(f"[YOOKASSA] User not found for payment {order_id}")
                return jsonify({"status": "error", "message": "User not found"}), 404
            
            print(f"[YOOKASSA] Processing payment: order_id={order_id}, user_id={user.id}, tariff_id={payment.tariff_id}, amount={payment.amount} {payment.currency}")
            
            # Если это пополнение баланса (tariff_id == None)
            if payment.tariff_id is None:
                is_option_purchase = bool(getattr(payment, 'description', None)) and str(payment.description).startswith('OPTION:')
                if is_option_purchase:
                    ok = process_option_purchase(payment, user)
                    if ok:
                        print(f"[YOOKASSA] ✅ Option purchase successful: user_id={user.id}, payment_id={payment.id}")
                    else:
                        print(f"[YOOKASSA] ❌ Failed to process option purchase: user_id={user.id}, payment_id={payment.id}")
                    return jsonify({"status": "success"}), 200

                current_balance_usd = float(user.balance) if user.balance else 0.0
                amount_usd = convert_to_usd(payment.amount, payment.currency)
                new_balance = current_balance_usd + amount_usd
                user.balance = new_balance
                payment.status = 'PAID'
                db.session.commit()
                
                # Начисляем реферальную комиссию
                add_referral_commission(user, amount_usd, is_tariff_purchase=False)
                db.session.commit()
                
                cache.delete(f'live_data_{user.remnawave_uuid}')
                cache.delete('all_live_users_map')
                
                # Отправляем уведомление админам
                try:
                    from modules.notifications import notify_payment
                    notify_payment(payment, user, is_balance_topup=True)
                except Exception as e:
                    print(f"Error sending payment notification: {e}")
                
                # Отправляем уведомление пользователю в бот
                try:
                    from modules.notifications import send_user_payment_notification_async
                    send_user_payment_notification_async(user, is_successful=True, is_balance_topup=True, payment=payment)
                except Exception as e:
                    print(f"Error sending user payment notification: {e}")
                
                print(f"[YOOKASSA] ✅ Balance top-up successful: user_id={user.id}, amount={amount_usd} USD, new_balance={new_balance} USD")
            else:
                # Покупка тарифа
                tariff = Tariff.query.get(payment.tariff_id)
                if tariff:
                    # process_successful_payment уже отправляет уведомления админам и пользователю
                    success = process_successful_payment(payment, user, tariff)
                    if success:
                        print(f"[YOOKASSA] ✅ Tariff purchase successful: user_id={user.id}, tariff_id={tariff.id}, tariff_name={tariff.name}")
                    else:
                        print(f"[YOOKASSA] ❌ Failed to process tariff purchase: user_id={user.id}, tariff_id={tariff.id}")
                else:
                    print(f"[YOOKASSA] ❌ Warning: Tariff not found for payment {payment.order_id}, tariff_id={payment.tariff_id}")
        else:
            # Логируем другие статусы для отладки
            print(f"[YOOKASSA] Payment status: {status} (not processing, waiting for 'succeeded')")
        
        return jsonify({"status": "success"}), 200
        
    except Exception as e:
        print(f"[YOOKASSA] Error: {e}")
        return jsonify({"status": "error", "message": str(e)[:200]}), 500


@app.route('/api/webhook/yoomoney', methods=['GET', 'POST'])
def yoomoney_webhook():
    """
    YooMoney HTTP notifications (payment buttons / quickpay)

    Документация:
    - https://yoomoney.ru/docs/payment-buttons/using-api/notifications
    - sha1_hash = SHA1("notification_type&operation_id&amount&currency&datetime&sender&codepro&notification_secret&label")
    """
    if request.method == 'GET':
        return jsonify({"status": "ok", "message": "YooMoney webhook is available"}), 200

    try:
        # YooMoney обычно шлет application/x-www-form-urlencoded
        data = {}
        if request.is_json:
            data = request.json or {}
        elif request.form:
            # request.form может содержать MultiDict
            data = {k: (v[0] if isinstance(v, list) else v) for k, v in request.form.to_dict(flat=False).items()}
        else:
            # fallback: попробуем распарсить raw как querystring-like
            try:
                raw = request.data.decode('utf-8', errors='ignore')
                if raw:
                    from urllib.parse import parse_qs
                    parsed = parse_qs(raw, keep_blank_values=True)
                    data = {k: (vals[0] if isinstance(vals, list) and vals else "") for k, vals in parsed.items()}
            except Exception:
                data = {}

        print(f"[YOOMONEY] 📥 Webhook received: {json.dumps(data, indent=2, ensure_ascii=False)}")

        notification_type = (data.get('notification_type') or '').strip()
        operation_id = (data.get('operation_id') or '').strip()
        amount = (data.get('amount') or '').strip()
        currency = (data.get('currency') or '').strip()  # обычно "643"
        dt = (data.get('datetime') or '').strip()
        sender = (data.get('sender') or '').strip()
        codepro = (data.get('codepro') or '').strip()  # обычно "false"
        label = (data.get('label') or '').strip()
        sha1_hash = (data.get('sha1_hash') or '').strip().lower()
        unaccepted = (data.get('unaccepted') or '').strip().lower()

        if not label:
            return jsonify({"status": "error", "message": "Missing label"}), 400

        # Проверка подписи
        s = PaymentSetting.query.first()
        secret = ""
        if s and getattr(s, 'yoomoney_notification_secret', None):
            try:
                from modules.models.payment import decrypt_key as decrypt_key_model
                secret = (decrypt_key_model(getattr(s, 'yoomoney_notification_secret', None)) or "").strip()
            except Exception:
                secret = ""

        if secret:
            base = f"{notification_type}&{operation_id}&{amount}&{currency}&{dt}&{sender}&{codepro}&{secret}&{label}"
            calc = hashlib.sha1(base.encode('utf-8')).hexdigest().lower()
            if not sha1_hash or calc != sha1_hash:
                print(f"[YOOMONEY] ❌ Invalid sha1_hash: got={sha1_hash}, expected={calc}")
                return jsonify({"status": "error", "message": "Invalid signature"}), 403
        else:
            # Без секрета невозможно надежно проверять уведомления
            print("[YOOMONEY] ⚠️ notification_secret is not configured; refusing to process payment for security")
            return jsonify({"status": "error", "message": "notification_secret is not configured"}), 500

        # Базовые флаги
        if codepro.lower() != 'false':
            return jsonify({"status": "success", "message": "Ignored (codepro=true)"}), 200
        if unaccepted and unaccepted != 'false':
            return jsonify({"status": "success", "message": "Ignored (unaccepted=true)"}), 200

        payment = Payment.query.filter_by(order_id=label).first()
        if not payment and operation_id:
            payment = Payment.query.filter_by(payment_system_id=operation_id).first()

        if not payment:
            print(f"[YOOMONEY] ⚠️ Payment not found for label={label} (ignoring)")
            return jsonify({"status": "success", "message": "Payment not found"}), 200

        if payment.status == 'PAID':
            return jsonify({"status": "success", "message": "Payment already processed"}), 200

        # Сохраняем operation_id
        if operation_id:
            payment.payment_system_id = operation_id
            db.session.commit()

        user = User.query.get(payment.user_id)
        if not user:
            return jsonify({"status": "success", "message": "User not found"}), 200

        # Пополнение баланса
        if payment.tariff_id is None:
            is_option_purchase = bool(getattr(payment, 'description', None)) and str(payment.description).startswith('OPTION:')
            if is_option_purchase:
                ok = process_option_purchase(payment, user)
                return jsonify({"status": "success", "processed": bool(ok)}), 200

            current_balance_usd = float(user.balance) if user.balance else 0.0
            amount_usd = convert_to_usd(payment.amount, payment.currency)
            user.balance = current_balance_usd + amount_usd
            payment.status = 'PAID'
            db.session.commit()

            # Рефералка
            try:
                add_referral_commission(user, amount_usd, is_tariff_purchase=False)
                db.session.commit()
            except Exception as e:
                print(f"[YOOMONEY] Warning: referral commission failed: {e}")
                try:
                    db.session.rollback()
                except Exception:
                    pass

            # Уведомления
            try:
                from modules.notifications import notify_payment
                notify_payment(payment, user, is_balance_topup=True)
            except Exception as e:
                print(f"[YOOMONEY] Error sending payment notification: {e}")

            try:
                from modules.notifications import send_user_payment_notification_async
                send_user_payment_notification_async(user, is_successful=True, is_balance_topup=True, payment=payment)
            except Exception as e:
                print(f"[YOOMONEY] Error sending user payment notification: {e}")

            try:
                cache.delete(f'live_data_{user.remnawave_uuid}')
                cache.delete('all_live_users_map')
            except Exception:
                pass

            return jsonify({"status": "success"}), 200

        # Покупка тарифа
        tariff = Tariff.query.get(payment.tariff_id)
        if not tariff:
            return jsonify({"status": "success", "message": "Tariff not found"}), 200

        # process_successful_payment сам выставит статус и уведомления
        ok = process_successful_payment(payment, user, tariff)
        return jsonify({"status": "success", "processed": bool(ok)}), 200

    except Exception as e:
        print(f"[YOOMONEY] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)[:200]}), 500


@app.route('/api/webhook/telegram', methods=['POST'])
@app.route('/api/webhook/telegram-stars', methods=['POST'])
def telegram_webhook():
    """Telegram Stars webhook"""
    try:
        update = request.json
        if not update:
            return jsonify({"ok": True}), 200
        
        # PreCheckoutQuery
        if 'pre_checkout_query' in update:
            pre_checkout = update['pre_checkout_query']
            order_id = pre_checkout.get('invoice_payload')
            query_id = pre_checkout.get('id')
            
            s = PaymentSetting.query.first()
            bot_token = decrypt_key(s.telegram_bot_token) if s else None
            
            if not bot_token:
                return jsonify({"ok": True}), 200
            
            p = Payment.query.filter_by(order_id=order_id).first()
            if p and p.status == 'PENDING':
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/answerPreCheckoutQuery",
                    json={"pre_checkout_query_id": query_id, "ok": True},
                    timeout=5
                )
            else:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/answerPreCheckoutQuery",
                    json={"pre_checkout_query_id": query_id, "ok": False, "error_message": "Payment not found"},
                    timeout=5
                )
            
            return jsonify({"ok": True}), 200
        
        # Successful payment
        if 'message' in update and 'successful_payment' in update['message']:
            successful_payment = update['message']['successful_payment']
            order_id = successful_payment.get('invoice_payload')
            
            p = Payment.query.filter_by(order_id=order_id).first()
            if not p:
                p = Payment.query.filter_by(payment_system_id=order_id).first()
            
            if not p or p.status == 'PAID':
                return jsonify({"ok": True}), 200
            
            u = db.session.get(User, p.user_id)
            if not u:
                return jsonify({"ok": True}), 200
            
        # Пополнение баланса
        if p.tariff_id is None:
            is_option_purchase = bool(getattr(p, 'description', None)) and str(p.description).startswith('OPTION:')
            if is_option_purchase:
                process_option_purchase(p, u)
                return jsonify({"ok": True}), 200

            current_balance = float(u.balance) if u.balance else 0.0
            amount_usd = convert_to_usd(p.amount, p.currency)
            u.balance = current_balance + amount_usd
            p.status = 'PAID'
            db.session.commit()
            
            # Начисляем реферальную комиссию
            add_referral_commission(u, amount_usd, is_tariff_purchase=False)
            db.session.commit()
            
            # Отправляем уведомление админам
            try:
                from modules.notifications import notify_payment
                notify_payment(p, u, is_balance_topup=True)
            except Exception as e:
                print(f"Error sending payment notification: {e}")
            
            # Отправляем уведомление пользователю в бот
            try:
                from modules.notifications import send_user_payment_notification_async
                send_user_payment_notification_async(u, is_successful=True, is_balance_topup=True, payment=p)
            except Exception as e:
                print(f"Error sending user payment notification: {e}")
            
            cache.delete(f'live_data_{u.remnawave_uuid}')
            return jsonify({"ok": True}), 200
        
        # Покупка тарифа
        t = db.session.get(Tariff, p.tariff_id)
        if not t:
            return jsonify({"ok": True}), 200
        
        # process_successful_payment уже отправляет уведомление пользователю
        process_successful_payment(p, u, t)
        
        return jsonify({"ok": True}), 200
        
    except Exception as e:
        print(f"[TELEGRAM] Error: {e}")
        return jsonify({"ok": True}), 200


@app.route('/api/internal/process-telegram-payment', methods=['POST'])
def process_telegram_payment_internal():
    """Внутренний API для обработки платежей Telegram Stars от бота"""
    try:
        # Проверяем внутренний ключ (простая защита)
        internal_key = request.headers.get('X-Internal-Key')
        if internal_key != 'telegram-stars-internal':
            return jsonify({"success": False, "message": "Unauthorized"}), 401
        
        data = request.json or {}
        order_id = data.get('order_id')
        telegram_id = data.get('telegram_id')
        
        print(f"[TELEGRAM-INTERNAL] Processing payment: order_id={order_id}, telegram_id={telegram_id}")
        
        if not order_id:
            return jsonify({"success": False, "message": "Missing order_id"}), 400
        
        # Ищем платеж
        p = Payment.query.filter_by(order_id=order_id).first()
        if not p:
            p = Payment.query.filter_by(payment_system_id=order_id).first()
        
        if not p:
            print(f"[TELEGRAM-INTERNAL] Payment not found: {order_id}")
            return jsonify({"success": False, "message": "Payment not found"}), 404
        
        if p.status == 'PAID':
            return jsonify({"success": True, "message": "Платеж уже обработан"}), 200
        
        u = db.session.get(User, p.user_id)
        if not u:
            return jsonify({"success": False, "message": "User not found"}), 404
        
        # Пополнение баланса
        if p.tariff_id is None:
            is_option_purchase = bool(getattr(p, 'description', None)) and str(p.description).startswith('OPTION:')
            if is_option_purchase:
                ok = process_option_purchase(p, u)
                return jsonify({"success": True, "processed": bool(ok)}), 200

            current_balance = float(u.balance) if u.balance else 0.0
            amount_usd = convert_to_usd(p.amount, p.currency)
            u.balance = current_balance + amount_usd
            p.status = 'PAID'
            db.session.commit()
            
            # Начисляем реферальную комиссию
            try:
                add_referral_commission(u, amount_usd, is_tariff_purchase=False)
                db.session.commit()
            except Exception as e:
                print(f"[TELEGRAM-INTERNAL] Referral commission error: {e}")
            
            # Отправляем уведомление админам
            try:
                from modules.notifications import notify_payment
                notify_payment(p, u, is_balance_topup=True)
            except Exception as e:
                print(f"[TELEGRAM-INTERNAL] Notification error: {e}")
            
            cache.delete(f'live_data_{u.remnawave_uuid}')
            print(f"[TELEGRAM-INTERNAL] Balance topped up: user={u.id}, amount={amount_usd} USD")
            return jsonify({
                "success": True, 
                "message": f"Баланс пополнен на {p.amount} {p.currency}"
            }), 200
        
        # Покупка тарифа
        t = db.session.get(Tariff, p.tariff_id)
        if not t:
            return jsonify({"success": False, "message": "Tariff not found"}), 404
        
        # process_successful_payment обработает платеж
        try:
            process_successful_payment(p, u, t)
            print(f"[TELEGRAM-INTERNAL] Tariff activated: user={u.id}, tariff={t.name}")
            return jsonify({
                "success": True, 
                "message": f"Подписка '{t.name}' активирована!"
            }), 200
        except Exception as e:
            print(f"[TELEGRAM-INTERNAL] Tariff activation error: {e}")
            return jsonify({"success": False, "message": str(e)}), 500
        
    except Exception as e:
        print(f"[TELEGRAM-INTERNAL] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/webhook/freekassa', methods=['POST', 'GET'])
def freekassa_webhook():
    """FreeKassa webhook"""
    try:
        data = request.values.to_dict()
        print(f"[FREEKASSA] Received: {data}")
        
        order_id = data.get('MERCHANT_ORDER_ID')
        if not order_id:
            return "NO", 400
        
        payment = Payment.query.filter_by(order_id=order_id).first()
        if not payment:
            return "NO", 404
        
        if payment.status != 'PAID':
            payment.status = 'PAID'
            payment.payment_system_id = data.get('intid')
            db.session.commit()
            
            user = User.query.get(payment.user_id)
            tariff = Tariff.query.get(payment.tariff_id)
            
            if user and tariff:
                process_successful_payment(payment, user, tariff)
        
        return "YES", 200
        
    except Exception as e:
        print(f"[FREEKASSA] Error: {e}")
        return "NO", 500


@app.route('/api/webhook/kassa_ai', methods=['POST', 'GET'])
def kassa_ai_webhook():
    """Kassa AI (Freekassa api.fk.life) webhook — подпись MD5: MERCHANT_ID:AMOUNT:WEBHOOK_SECRET:MERCHANT_ORDER_ID"""
    try:
        from modules.api.payments.kassa_ai import verify_kassa_ai_webhook
        order_id, err = verify_kassa_ai_webhook(request)
        if err:
            print(f"[KASSA_AI] Webhook verify failed: {err}")
            return "NO", 400 if err in ("missing_params", "wrong_sign") else 403
        data = request.values.to_dict()
        print(f"[KASSA_AI] Received: order_id={order_id}, data={data}")
        payment = Payment.query.filter_by(order_id=order_id).first()
        if not payment:
            return "NO", 404
        if payment.status != 'PAID':
            payment.status = 'PAID'
            payment.payment_system_id = data.get("intid") or data.get("MERCHANT_ORDER_ID") or order_id
            db.session.commit()
            user = User.query.get(payment.user_id)
            tariff = Tariff.query.get(payment.tariff_id)
            is_option = bool(getattr(payment, 'description', None)) and str(payment.description).startswith("OPTION:")
            if user and is_option:
                process_option_purchase(payment, user)
            elif user and tariff:
                process_successful_payment(payment, user, tariff)
        return "YES", 200
    except Exception as e:
        print(f"[KASSA_AI] Error: {e}")
        import traceback
        traceback.print_exc()
        return "NO", 500


@app.route('/api/webhook/robokassa', methods=['POST', 'GET'])
def robokassa_webhook():
    """Robokassa webhook"""
    try:
        data = request.values.to_dict()
        print(f"[ROBOKASSA] Received: {data}")
        
        order_id = data.get('InvId') or data.get('inv_id')
        if not order_id:
            return "NO", 400
        
        payment = Payment.query.filter_by(order_id=str(order_id)).first()
        if not payment:
            return "NO", 404
        
        if payment.status != 'PAID':
            payment.status = 'PAID'
            db.session.commit()
            
            user = User.query.get(payment.user_id)
            is_option_purchase = bool(getattr(payment, 'description', None)) and str(payment.description).startswith('OPTION:')
            if user and is_option_purchase:
                process_option_purchase(payment, user)
            else:
                tariff = Tariff.query.get(payment.tariff_id)
                if user and tariff:
                    process_successful_payment(payment, user, tariff)
        
        return f"OK{order_id}", 200
        
    except Exception as e:
        print(f"[ROBOKASSA] Error: {e}")
        return "NO", 500


def parse_iso_datetime(iso_string):
    """Парсит ISO формат даты, поддерживая как стандартный формат, так и формат с 'Z' (UTC)"""
    if not iso_string:
        raise ValueError("Empty ISO string")
    
    # Заменяем 'Z' на '+00:00' для совместимости с fromisoformat
    if iso_string.endswith('Z'):
        iso_string = iso_string[:-1] + '+00:00'
    
    return datetime.fromisoformat(iso_string)


# ============================================================================
# CRYSTALPAY WEBHOOK
# ============================================================================

@app.route('/api/webhook/crystalpay', methods=['POST'])
def crystalpay_webhook():
    """Webhook для обработки уведомлений от CrystalPay"""
    try:
        d = request.json
        if d.get('state') != 'payed':
            return jsonify({"error": False}), 200
        
        p = Payment.query.filter_by(order_id=d.get('extra')).first()
        if not p or p.status == 'PAID':
            return jsonify({"error": False}), 200
        
        u = db.session.get(User, p.user_id)
        if not u:
            return jsonify({"error": False}), 200
        
        # Если это пополнение баланса (tariff_id == None)
        if p.tariff_id is None:
            is_option_purchase = bool(getattr(p, 'description', None)) and str(p.description).startswith('OPTION:')
            if is_option_purchase:
                process_option_purchase(p, u)
                return jsonify({"error": False}), 200

            current_balance_usd = float(u.balance) if u.balance else 0.0
            amount_usd = convert_to_usd(p.amount, p.currency)
            u.balance = current_balance_usd + amount_usd
            p.status = 'PAID'
            db.session.commit()
            
            # Начисляем реферальную комиссию
            add_referral_commission(u, amount_usd, is_tariff_purchase=False)
            db.session.commit()
            
            # Отправляем уведомление админам
            try:
                from modules.notifications import notify_payment
                notify_payment(p, u, is_balance_topup=True)
            except Exception as e:
                print(f"Error sending payment notification: {e}")
            
            # Отправляем уведомление пользователю в бот
            try:
                from modules.notifications import send_user_payment_notification_async
                send_user_payment_notification_async(u, is_successful=True, is_balance_topup=True, payment=p)
            except Exception as e:
                print(f"Error sending user payment notification: {e}")
            
            cache.delete(f'live_data_{u.remnawave_uuid}')
            cache.delete('all_live_users_map')
            
            return jsonify({"error": False}), 200
        
        # Обычная покупка тарифа
        t = db.session.get(Tariff, p.tariff_id)
        if not t:
            return jsonify({"error": False}), 200
        
        # process_successful_payment уже отправляет уведомление пользователю
        process_successful_payment(p, u, t)
        
        return jsonify({"error": False}), 200
        
    except Exception as e:
        print(f"[CRYSTALPAY] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": False}), 200


# ============================================================================
# PLATEGA WEBHOOK
# ============================================================================

@app.route('/api/webhook/platega', methods=['POST'])
def platega_webhook():
    """
    Webhook для обработки уведомлений от Platega
    
    Согласно документации Platega API:
    - Endpoint должен принимать JSON-запросы
    - Всегда возвращать статус 200 OK для своевременных обновлений
    - Статусы: PENDING, CANCELED, CONFIRMED, CHARGEBACKED
    - Успешный платеж: CONFIRMED
    - Структура webhook может содержать:
      - id (UUID транзакции) - на верхнем уровне или в transaction
      - status (PENDING, CANCELED, CONFIRMED, CHARGEBACKED)
      - transaction.id или id
      - paymentDetails (amount, currency)
    """
    # Всегда возвращаем 200 OK, даже при ошибках, чтобы Platega не повторял запрос
    try:
        # Проверяем, что запрос содержит JSON
        if not request.is_json:
            # Пробуем распарсить как JSON вручную
            try:
                if request.data:
                    import json as json_lib
                    webhook_data = json_lib.loads(request.data.decode('utf-8'))
                else:
                    print("[PLATEGA] No JSON data in request")
                    return jsonify({"status": "ok"}), 200
            except Exception as parse_error:
                print(f"[PLATEGA] Failed to parse JSON: {parse_error}")
                return jsonify({"status": "ok"}), 200
        else:
            webhook_data = request.json
        
        if not webhook_data:
            print("[PLATEGA] Empty webhook data")
            return jsonify({"status": "ok"}), 200
        
        # Логируем входящий webhook для отладки
        print(f"[PLATEGA] Webhook received: {json.dumps(webhook_data, indent=2)}")
        
        # Получаем статус (может быть на верхнем уровне или в transaction)
        status = webhook_data.get('status', '')
        transaction = webhook_data.get('transaction', {})
        
        # Если статус в transaction, используем его
        if not status and transaction:
            status = transaction.get('status', '')
        
        # Нормализуем статус (документация использует верхний регистр: CONFIRMED)
        status_upper = status.upper() if status else ''
        
        # Согласно документации Platega, успешный платеж имеет статус CONFIRMED
        # Также поддерживаем старые варианты для обратной совместимости
        if status_upper not in ['CONFIRMED', 'PAID', 'SUCCESS', 'COMPLETED']:
            print(f"[PLATEGA] Ignoring status: {status_upper}")
            return jsonify({"status": "ok"}), 200
        
        # Получаем ID транзакции
        # Может быть на верхнем уровне (id) или в transaction (id)
        transaction_id = webhook_data.get('id') or transaction.get('id')
        
        # Также проверяем externalId или invoiceId для обратной совместимости
        external_id = webhook_data.get('externalId') or transaction.get('externalId')
        invoice_id = webhook_data.get('invoiceId') or transaction.get('invoiceId')
        
        print(f"[PLATEGA] Transaction ID: {transaction_id}, External ID: {external_id}, Invoice ID: {invoice_id}")
        
        # Согласно документации Platega, проверяем статус через API для подтверждения
        # GET /transaction/{id} - проверка статуса оплаты платежа
        verified_status = None
        if transaction_id:
            try:
                from modules.models.payment import PaymentSetting, decrypt_key
                import requests
                
                settings = PaymentSetting.query.first()
                if settings:
                    platega_key = decrypt_key(getattr(settings, 'platega_api_key', None)) if settings else None
                    platega_merchant_raw = decrypt_key(getattr(settings, 'platega_merchant_id', None)) if settings else None
                    
                    if platega_key and platega_merchant_raw:
                        # Обработка Merchant ID (убираем префикс 'live_' если есть)
                        import re
                        import uuid as uuid_lib
                        platega_merchant = platega_merchant_raw.strip()
                        if platega_merchant.startswith('live_'):
                            platega_merchant = platega_merchant[5:]
                        uuid_pattern = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
                        uuid_match = re.search(uuid_pattern, platega_merchant)
                        if uuid_match:
                            platega_merchant = uuid_match.group(0)
                        
                        # Проверяем статус через API Platega
                        api_url = f"https://app.platega.io/transaction/{transaction_id}"
                        headers = {
                            "X-MerchantId": platega_merchant,
                            "X-Secret": platega_key,
                            "Content-Type": "application/json"
                        }
                        
                        resp = requests.get(api_url, headers=headers, timeout=10)
                        if resp.status_code == 200:
                            api_data = resp.json()
                            verified_status = api_data.get('status', '').upper()
                            print(f"[PLATEGA] Verified status from API: {verified_status}, full response: {json.dumps(api_data, indent=2)}")
                        elif resp.status_code == 404:
                            print(f"[PLATEGA] Transaction {transaction_id} not found in Platega API (404)")
                        else:
                            print(f"[PLATEGA] Failed to verify status via API: {resp.status_code} - {resp.text[:200]}")
            except Exception as api_error:
                print(f"[PLATEGA] Error verifying status via API: {api_error}")
        
        # Используем проверенный статус из API, если доступен, иначе из webhook
        if verified_status:
            status_upper = verified_status
            print(f"[PLATEGA] Using verified status from API: {status_upper}")
        else:
            status_upper = status.upper() if status else ''
            print(f"[PLATEGA] Using status from webhook: {status_upper}")
        
        # Ищем платеж по transaction_id (это payment_system_id в нашей БД)
        p = None
        if transaction_id:
            p = Payment.query.filter_by(payment_system_id=str(transaction_id)).first()
        
        # Если не нашли, пробуем по externalId или invoiceId (это может быть order_id)
        if not p and external_id:
            p = Payment.query.filter_by(order_id=str(external_id)).first()
        
        if not p and invoice_id:
            p = Payment.query.filter_by(order_id=str(invoice_id)).first()
        
        if not p:
            print(f"[PLATEGA] Payment not found for transaction_id={transaction_id}, external_id={external_id}, invoice_id={invoice_id}")
            return jsonify({"status": "ok"}), 200
        
        # Если платеж уже обработан, игнорируем
        if p.status == 'PAID':
            print(f"[PLATEGA] Payment {p.order_id} already processed")
            return jsonify({"status": "ok"}), 200
        
        # Получаем пользователя и тариф
        u = db.session.get(User, p.user_id)
        t = db.session.get(Tariff, p.tariff_id) if p.tariff_id else None
        
        if not u:
            print(f"[PLATEGA] User not found for payment {p.order_id}")
            return jsonify({"status": "ok"}), 200
        
        # Если это пополнение баланса (нет тарифа), обрабатываем отдельно
        if not t:
            # Для пополнения баланса обновляем статус и пополняем баланс
            p.status = 'PAID'
            # Пополняем баланс пользователя
            u.balance = (u.balance or 0) + float(p.amount)
            db.session.commit()
            print(f"[PLATEGA] Balance topup payment {p.order_id} marked as PAID, balance updated: {u.balance}")
            return jsonify({"status": "ok"}), 200
        
        # Обрабатываем успешный платеж за тариф
        if process_successful_payment(p, u, t):
            print(f"[PLATEGA] Successfully processed payment {p.order_id}")
            return jsonify({"status": "ok"}), 200
        else:
            print(f"[PLATEGA] Failed to process payment {p.order_id}")
            return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        print(f"[PLATEGA] Error: {e}")
        import traceback
        traceback.print_exc()
        # Всегда возвращаем 200 OK с JSON ответом, чтобы Platega не повторял запрос
        # Это важно для своевременных обновлений статуса транзакций
        return jsonify({"status": "ok"}), 200


# ============================================================================
# MULENPAY WEBHOOK
# ============================================================================

@app.route('/api/webhook/mulenpay', methods=['POST'])
def mulenpay_webhook():
    """Webhook для обработки уведомлений от MulenPay"""
    try:
        webhook_data = request.json
        
        status = webhook_data.get('status', '').lower()
        order_id = webhook_data.get('order_id') or webhook_data.get('orderId')
        
        if status not in ['paid', 'success', 'completed']:
            return jsonify({}), 200
        
        if not order_id:
            return jsonify({}), 200
        
        p = Payment.query.filter_by(order_id=order_id).first()
        if not p or p.status == 'PAID':
            return jsonify({}), 200
        
        u = db.session.get(User, p.user_id)
        t = db.session.get(Tariff, p.tariff_id)
        
        if not u or not t:
            return jsonify({}), 200
        
        if process_successful_payment(p, u, t):
            return jsonify({}), 200
        else:
            return jsonify({}), 200
        
    except Exception as e:
        print(f"[MULENPAY] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({}), 200


# ============================================================================
# URLPAY WEBHOOK
# ============================================================================

@app.route('/api/webhook/urlpay', methods=['POST'])
def urlpay_webhook():
    """Webhook для обработки уведомлений от URLPay"""
    try:
        webhook_data = request.json
        
        status = webhook_data.get('status', '').lower()
        order_id = webhook_data.get('order_id') or webhook_data.get('orderId')
        
        if status not in ['paid', 'success', 'completed']:
            return jsonify({}), 200
        
        if not order_id:
            return jsonify({}), 200
        
        p = Payment.query.filter_by(order_id=order_id).first()
        if not p or p.status == 'PAID':
            return jsonify({}), 200
        
        u = db.session.get(User, p.user_id)
        t = db.session.get(Tariff, p.tariff_id)
        
        if not u or not t:
            return jsonify({}), 200
        
        if process_successful_payment(p, u, t):
            return jsonify({}), 200
        else:
            return jsonify({}), 200
        
    except Exception as e:
        print(f"[URLPAY] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({}), 200


# ============================================================================
# BTCPAYSERVER WEBHOOK
# ============================================================================

@app.route('/api/webhook/btcpayserver', methods=['POST'])
def btcpayserver_webhook():
    """Webhook для обработки уведомлений от BTCPayServer"""
    try:
        webhook_data = request.json
        
        # BTCPayServer отправляет разные типы событий
        event_type = webhook_data.get('type', '')
        
        # Нас интересуют только события оплаты
        if event_type not in ['InvoiceSettled', 'InvoiceReceivedPayment']:
            return jsonify({}), 200
        
        invoice_data = webhook_data.get('data', {})
        invoice_id = invoice_data.get('id') or invoice_data.get('invoiceId')
        
        if not invoice_id:
            return jsonify({}), 200
        
        p = Payment.query.filter_by(order_id=invoice_id).first()
        if not p or p.status == 'PAID':
            return jsonify({}), 200
        
        u = db.session.get(User, p.user_id)
        t = db.session.get(Tariff, p.tariff_id)
        
        if not u or not t:
            return jsonify({}), 200
        
        if process_successful_payment(p, u, t):
            return jsonify({}), 200
        else:
            return jsonify({}), 200
        
    except Exception as e:
        print(f"[BTCPAYSERVER] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({}), 200


# ============================================================================
# TRIBUTE WEBHOOK
# ============================================================================

@app.route('/api/webhook/tribute', methods=['POST'])
def tribute_webhook():
    """Webhook для обработки уведомлений от Tribute"""
    try:
        webhook_data = request.json
        
        status = webhook_data.get('status', '').lower()
        order_id = webhook_data.get('order_id') or webhook_data.get('orderId')
        
        if status not in ['paid', 'success', 'completed']:
            return jsonify({}), 200
        
        if not order_id:
            return jsonify({}), 200
        
        p = Payment.query.filter_by(order_id=order_id).first()
        if not p or p.status == 'PAID':
            return jsonify({}), 200
        
        u = db.session.get(User, p.user_id)
        t = db.session.get(Tariff, p.tariff_id)
        
        if not u or not t:
            return jsonify({}), 200
        
        if process_successful_payment(p, u, t):
            return jsonify({}), 200
        else:
            return jsonify({}), 200
        
    except Exception as e:
        print(f"[TRIBUTE] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({}), 200


# ============================================================================
# MONOBANK WEBHOOK
# ============================================================================

@app.route('/api/webhook/monobank', methods=['POST'])
def monobank_webhook():
    """Webhook для обработки уведомлений от Monobank"""
    try:
        webhook_data = request.json
        
        # Monobank отправляет данные в формате statementItem
        invoice_id = webhook_data.get('invoiceId') or webhook_data.get('invoice_id')
        
        if not invoice_id:
            return jsonify({}), 200
        
        p = Payment.query.filter_by(order_id=invoice_id).first()
        if not p or p.status == 'PAID':
            return jsonify({}), 200
        
        u = db.session.get(User, p.user_id)
        t = db.session.get(Tariff, p.tariff_id)
        
        if not u or not t:
            return jsonify({}), 200
        
        if process_successful_payment(p, u, t):
            return jsonify({}), 200
        else:
            return jsonify({}), 200
        
    except Exception as e:
        print(f"[MONOBANK] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({}), 200
