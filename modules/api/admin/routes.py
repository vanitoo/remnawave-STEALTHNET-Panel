"""
API эндпоинты администратора

- GET/POST /api/admin/users - Управление пользователями
- GET /api/admin/statistics - Статистика
- GET/POST /api/admin/system-settings - Системные настройки
- GET/POST /api/admin/branding - Брендинг
- GET/POST /api/admin/bot-config - Конфигурация бота
- GET /api/admin/squads - Список сквадов
- GET /api/admin/nodes - Список нод
- GET/POST /api/admin/tariffs - Тарифы
- GET/POST /api/admin/promo-codes - Промокоды
- GET/POST /api/admin/referral-settings - Настройки рефералов
- GET/POST /api/admin/trial-settings - Настройки триала
- GET/POST /api/admin/tariff-features - Функции тарифов
- GET/POST /api/admin/currency-rates - Курсы валют
- POST /api/admin/broadcast - Рассылка
"""

from flask import jsonify, request
from datetime import datetime, timezone, timedelta
import requests
import json
import os

from modules.core import get_app, get_db, get_cache, get_bcrypt
from modules.auth import admin_required
from modules.models.user import User
from modules.models.payment import Payment, PaymentSetting
from modules.models.tariff import Tariff
from modules.models.promo import PromoCode
from modules.models.ticket import Ticket, TicketMessage
from modules.models.system import SystemSetting
from modules.models.branding import BrandingSetting
from modules.models.bot_config import BotConfig
from modules.models.referral import ReferralSetting
from modules.models.tariff_feature import TariffFeatureSetting
from modules.models.currency import CurrencyRate
from modules.models.auto_broadcast import AutoBroadcastMessage, AutoBroadcastSettings
from modules.models.trial import TrialSettings
from modules.models.tariff_level import TariffLevel
from modules.models.option import PurchaseOption
from modules.models.email_setting import EmailSetting

app = get_app()
db = get_db()
cache = get_cache()
bcrypt = get_bcrypt()


def _get_site_name():
    """Имя сервиса из брендинга для fallback."""
    try:
        b = BrandingSetting.query.first()
        return (b.site_name or "").strip() if b else ""
    except Exception:
        return ""


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
# USERS
# ============================================================================

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def get_admin_users(current_admin):
    """Получение списка пользователей"""
    try:
        local_users = User.query.all()
        live_map = cache.get('all_live_users_map')
        
        if not live_map:
            headers, cookies = get_remnawave_headers()
            try:
                # RemnaWave /api/users поддерживает пагинацию (size/start). Без параметров может вернуться
                # только первая страница, из-за чего большинство пользователей будет "не найдено".
                users_list = []
                start = 0
                size = 1000
                total = None
                while True:
                    resp = requests.get(
                        f"{os.getenv('API_URL')}/api/users",
                        params={"size": size, "start": start},
                        headers=headers,
                        cookies=cookies,
                        timeout=15
                    )
                    payload = resp.json() if resp is not None else {}
                    data = payload.get('response', payload) if isinstance(payload, dict) else payload

                    if isinstance(data, dict):
                        chunk = data.get('users', []) or []
                        if total is None:
                            try:
                                total = int(data.get('total')) if data.get('total') is not None else None
                            except Exception:
                                total = None
                    elif isinstance(data, list):
                        chunk = data
                    else:
                        chunk = []

                    if not isinstance(chunk, list) or len(chunk) == 0:
                        break

                    users_list.extend(chunk)
                    start += size

                    if total is not None and len(users_list) >= total:
                        break
                    # Safety to avoid infinite loop if API behaves unexpectedly
                    if start > 50000:
                        break
                # Создаем два индекса: по UUID и по email/username
                live_map = {u['uuid']: u for u in users_list if isinstance(u, dict) and 'uuid' in u}
                # Дополнительный индекс по email для поиска, если UUID не совпадает
                live_map_by_email = {}
                for u in users_list:
                    if isinstance(u, dict):
                        # Пробуем разные поля для email/username
                        email_key = u.get('email') or u.get('username') or u.get('name')
                        if email_key:
                            live_map_by_email[email_key.lower()] = u
                cache.set('all_live_users_map', live_map, timeout=60)
                cache.set('all_live_users_map_by_email', live_map_by_email, timeout=60)
            except Exception as e:
                print(f"Warning: Could not fetch live users: {e}")
                live_map = {}
                live_map_by_email = {}
        else:
            live_map_by_email = cache.get('all_live_users_map_by_email') or {}
        
        from modules.currency import convert_from_usd
        
        combined = []
        for u in local_users:
            balance_usd = float(u.balance) if u.balance else 0.0
            balance_converted = convert_from_usd(balance_usd, u.preferred_currency or 'uah')
            
            # Пытаемся найти пользователя в RemnaWave
            live_data = None
            fetch_error = None
            primary_uuid = None
            
            # Если включены несколько конфигов (UserConfig), то remnawave_uuid у User должен быть равен UUID основного конфига.
            # Ранее здесь был авто-апдейт UUID по email/username из RemnaWave, но при нескольких конфигурациях это опасно:
            # - email может совпадать у разных конфигов
            # - live_map_by_email становится неоднозначным
            # и в итоге u.remnawave_uuid начинает "прыгать", ломая оплаты/уведомления.
            try:
                from modules.models.user_config import UserConfig
                primary_cfg = UserConfig.query.filter_by(user_id=u.id, is_primary=True).first()
                if primary_cfg and primary_cfg.remnawave_uuid:
                    primary_uuid = primary_cfg.remnawave_uuid
                    if u.remnawave_uuid != primary_uuid:
                        u.remnawave_uuid = primary_uuid
            except Exception:
                primary_uuid = None
            
            # Сначала ищем по UUID
            uuid_for_lookup = primary_uuid or u.remnawave_uuid
            if uuid_for_lookup:
                live_data = live_map.get(uuid_for_lookup)
            
            # Если не нашли по UUID, пробуем найти по email
            if not live_data and u.email:
                # Пробуем разные варианты email для поиска
                email_variants = [
                    u.email.lower(),
                    u.email.replace('@', '_').lower(),  # admin@stealthnet.app -> admin_stealthnet_app
                    u.email.split('@')[0].lower()  # admin@stealthnet.app -> admin
                ]
                for email_var in email_variants:
                    if email_var in live_map_by_email:
                        live_data = live_map_by_email[email_var]
                        break
            
            if u.remnawave_uuid and not live_data:
                fetch_error = "User not found in RemnaWave"
            
            combined.append({
                "id": u.id, 
                "email": u.email, 
                "role": u.role, 
                "remnawave_uuid": u.remnawave_uuid,
                "referral_code": u.referral_code, 
                "referrer_id": u.referrer_id, 
                "is_verified": u.is_verified,
                "balance": balance_converted,
                "balance_usd": balance_usd,
                "preferred_currency": u.preferred_currency or 'uah',
                "telegram_id": u.telegram_id,
                "telegram_username": u.telegram_username,
                "is_blocked": getattr(u, 'is_blocked', False),
                "block_reason": getattr(u, 'block_reason', None) or "",
                "blocked_at": u.blocked_at.isoformat() if hasattr(u, 'blocked_at') and u.blocked_at else None,
                "live_data": {"response": live_data},
                "fetch_error": fetch_error
            })
        
        # Коммитим все обновления UUID одним разом
        try:
            db.session.commit()
        except Exception as e:
            print(f"Error committing UUID updates: {e}")
            db.session.rollback()
        
        return jsonify(combined), 200
        
    except Exception as e:
        print(f"Error in get_admin_users: {e}")
        return jsonify({"message": "Internal Server Error"}), 500


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_user(current_admin, user_id):
    """Удаление пользователя с обработкой связанных данных"""
    try:
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({"message": "User not found"}), 404
        
        # Проверяем связанные данные
        payments_count = Payment.query.filter_by(user_id=user_id).count()
        tickets_count = Ticket.query.filter_by(user_id=user_id).count()
        ticket_messages_count = TicketMessage.query.filter_by(sender_id=user_id).count()
        referrals_count = User.query.filter_by(referrer_id=user_id).count()
        user_configs_count = 0
        casino_games_count = 0
        try:
            from modules.models.user_config import UserConfig
            user_configs_count = UserConfig.query.filter_by(user_id=user_id).count()
        except Exception:
            user_configs_count = 0
        try:
            from modules.models.casino import CasinoGame
            casino_games_count = CasinoGame.query.filter_by(user_id=user_id).count()
        except Exception:
            casino_games_count = 0
        
        # Если есть связанные данные, удаляем их каскадно
        if payments_count > 0:
            Payment.query.filter_by(user_id=user_id).delete()
        
        if tickets_count > 0:
            # Сначала удаляем сообщения в тикетах
            tickets = Ticket.query.filter_by(user_id=user_id).all()
            for ticket in tickets:
                TicketMessage.query.filter_by(ticket_id=ticket.id).delete()
            # Затем удаляем тикеты
            Ticket.query.filter_by(user_id=user_id).delete()
        
        if ticket_messages_count > 0:
            TicketMessage.query.filter_by(sender_id=user_id).delete()
        
        # Обнуляем referrer_id у пользователей, которые были приглашены этим пользователем
        if referrals_count > 0:
            User.query.filter_by(referrer_id=user_id).update({'referrer_id': None})

        # Удаляем конфиги пользователя (иначе при db.session.delete(user) SQLAlchemy попытается выставить user_id=NULL)
        if user_configs_count > 0:
            try:
                from modules.models.user_config import UserConfig
                UserConfig.query.filter_by(user_id=user_id).delete()
            except Exception as e:
                print(f"Warning: failed to delete user configs for user {user_id}: {e}")

        # Удаляем историю казино пользователя
        if casino_games_count > 0:
            try:
                from modules.models.casino import CasinoGame
                CasinoGame.query.filter_by(user_id=user_id).delete()
            except Exception as e:
                print(f"Warning: failed to delete casino games for user {user_id}: {e}")
        
        # Сохраняем UUID перед удалением для удаления из RemnaWave
        remnawave_uuid = user.remnawave_uuid
        
        # Удаляем пользователя из RemnaWave перед удалением из локальной БД
        if remnawave_uuid:
            try:
                headers, cookies = get_remnawave_headers()
                delete_response = requests.delete(
                    f"{os.getenv('API_URL')}/api/users/{remnawave_uuid}",
                    headers=headers,
                    cookies=cookies,
                    timeout=10
                )
                if delete_response.status_code in [200, 204]:
                    print(f"✓ User {user_id} deleted from RemnaWave (UUID: {remnawave_uuid})")
                else:
                    print(f"⚠️  Warning: Failed to delete user from RemnaWave: {delete_response.status_code} - {delete_response.text[:100]}")
            except Exception as e:
                print(f"⚠️  Warning: Error deleting user from RemnaWave: {e}")
                # Продолжаем удаление из локальной БД даже если не удалось удалить из RemnaWave
        
        # Очищаем кэш
        if remnawave_uuid:
            cache.delete(f'live_data_{remnawave_uuid}')
        cache.delete('all_live_users_map')
        
        # Удаляем пользователя из локальной БД
        db.session.delete(user)
        db.session.commit()
        
        return jsonify({
            "message": "User deleted successfully",
            "deleted_data": {
                "payments": payments_count,
                "tickets": tickets_count,
                "ticket_messages": ticket_messages_count,
                "referrals_cleared": referrals_count,
                "user_configs": user_configs_count,
                "casino_games": casino_games_count,
                "remnawave_deleted": remnawave_uuid is not None
            }
        }), 200
    except Exception as e:
        db.session.rollback()
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error deleting user {user_id}: {e}")
        print(error_trace)
        return jsonify({
            "message": "Internal Server Error",
            "error": str(e),
            "trace": error_trace if os.getenv('FLASK_DEBUG') == 'True' else None
        }), 500


@app.route('/api/admin/users/<int:user_id>/balance', methods=['PUT', 'PATCH'])
@admin_required
def update_user_balance(current_admin, user_id):
    """Обновить баланс пользователя (добавить или установить)"""
    try:
        from modules.currency import convert_to_usd, convert_from_usd
        
        u = db.session.get(User, user_id)
        if not u:
            return jsonify({"message": "Пользователь не найден"}), 404
        
        data = request.json
        action = data.get('action', 'set')  # 'set' - установить, 'add' - добавить, 'subtract' - списать
        amount = data.get('amount', 0)
        description = data.get('description', 'Изменение баланса администратором')
        
        if amount is None or amount < 0:
            return jsonify({"message": "Сумма не может быть отрицательной"}), 400
        
        # Получаем валюту из запроса или используем валюту пользователя по умолчанию
        currency = data.get('currency', u.preferred_currency or 'uah').upper()
        
        # Конвертируем сумму в USD (баланс всегда хранится в USD)
        amount_usd = convert_to_usd(float(amount), currency)
        
        current_balance_usd = float(u.balance) if u.balance else 0.0
        
        if action == 'set':
            new_balance_usd = amount_usd
        elif action == 'add':
            new_balance_usd = current_balance_usd + amount_usd
        elif action == 'subtract':
            new_balance_usd = current_balance_usd - amount_usd
            if new_balance_usd < 0:
                return jsonify({"message": "Недостаточно средств на балансе"}), 400
        else:
            return jsonify({"message": "Неверное действие. Используйте: set, add, subtract"}), 400
        
        u.balance = new_balance_usd
        db.session.commit()
        
        # Очищаем кэш пользователя
        cache.delete(f'live_data_{u.remnawave_uuid}')
        cache.delete('all_live_users_map')
        
        # Конвертируем баланс обратно в валюту пользователя для отображения
        balance_display = convert_from_usd(new_balance_usd, u.preferred_currency or 'uah')
        previous_balance_display = convert_from_usd(current_balance_usd, u.preferred_currency or 'uah')
        change_display = convert_from_usd(new_balance_usd - current_balance_usd, u.preferred_currency or 'uah')
        
        return jsonify({
            "message": "Баланс успешно обновлен",
            "balance": balance_display,
            "previous_balance": previous_balance_display,
            "change": change_display,
            "balance_usd": float(new_balance_usd),
            "currency": u.preferred_currency or 'uah'
        }), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error updating balance: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Internal Server Error", "error": str(e)}), 500


@app.route('/api/admin/users/<int:user_id>/change-password', methods=['POST'])
@admin_required
def admin_change_user_password(current_admin, user_id):
    """Изменение пароля пользователя"""
    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"message": "User not found"}), 404
        data = request.json
        new_password = data.get('new_password')
        if not new_password:
            return jsonify({"message": "New password is required"}), 400
        user.password_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')
        db.session.commit()
        return jsonify({"message": "Password changed successfully"}), 200
    except Exception as e:
        return jsonify({"message": "Internal Error"}), 500


@app.route('/api/admin/users/<int:user_id>/referral-percent', methods=['PUT', 'PATCH'])
@admin_required
def update_user_referral_percent(current_admin, user_id):
    """Обновить процент реферала для пользователя"""
    try:
        u = db.session.get(User, user_id)
        if not u:
            return jsonify({"message": "Пользователь не найден"}), 404
        
        data = request.json
        referral_percent = data.get('referral_percent') if isinstance(data, dict) else None

        # NULL/пусто => использовать процент по умолчанию (динамически)
        if referral_percent is None or referral_percent == "":
            u.referral_percent = None
            db.session.commit()
            return jsonify({
                "message": "Процент реферала сброшен (используется процент по умолчанию)",
                "referral_percent": None
            }), 200
        
        try:
            referral_percent = float(referral_percent)
            if referral_percent < 0 or referral_percent > 100:
                return jsonify({"message": "Процент должен быть от 0 до 100"}), 400
        except (ValueError, TypeError):
            return jsonify({"message": "Неверный формат процента"}), 400
        
        u.referral_percent = referral_percent
        db.session.commit()
        
        return jsonify({
            "message": "Процент реферала успешно обновлен",
            "referral_percent": referral_percent
        }), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error updating referral percent: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Internal Server Error", "error": str(e)}), 500


@app.route('/api/admin/referral-settings/reset-user-percents', methods=['POST'])
@admin_required
def reset_all_user_referral_percents(current_admin):
    """
    Сбросить индивидуальные referral_percent у пользователей (сделать NULL),
    чтобы у всех начал применяться referral_setting.default_referral_percent динамически.
    """
    try:
        # Обновляем только там, где значение не NULL (иначе бессмысленно)
        affected = User.query.filter(User.referral_percent.isnot(None)).update(
            {"referral_percent": None},
            synchronize_session=False
        )
        db.session.commit()
        return jsonify({"message": "User referral percents reset", "affected": int(affected or 0)}), 200
    except Exception as e:
        db.session.rollback()
        print(f"Error resetting user referral percents: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Failed to reset user referral percents"}), 500


@app.route('/api/admin/users/<int:user_id>/block', methods=['POST'])
@admin_required
def block_user(current_admin, user_id):
    """Заблокировать пользователя"""
    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"message": "User not found"}), 404
        
        if user.role == 'ADMIN':
            return jsonify({"message": "Cannot block admin user"}), 400
        
        data = request.json or {}
        block_reason = data.get('block_reason', '')
        
        user.is_blocked = True
        user.block_reason = block_reason
        user.blocked_at = datetime.now(timezone.utc)
        
        db.session.commit()
        
        return jsonify({
            "message": "User blocked successfully",
            "is_blocked": True,
            "block_reason": block_reason,
            "blocked_at": user.blocked_at.isoformat()
        }), 200
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Internal Server Error", "error": str(e)}), 500


@app.route('/api/admin/users/<int:user_id>/unblock', methods=['POST'])
@admin_required
def unblock_user(current_admin, user_id):
    """Разблокировать пользователя"""
    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"message": "User not found"}), 404
        
        user.is_blocked = False
        user.block_reason = None
        user.blocked_at = None
        
        db.session.commit()
        
        # Очищаем кэш пользователя, чтобы данные обновились
        if user.remnawave_uuid:
            cache.delete(f'live_data_{user.remnawave_uuid}')
        cache.delete('all_live_users_map')
        
        return jsonify({
            "message": "User unblocked successfully",
            "is_blocked": False
        }), 200
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Internal Server Error", "error": str(e)}), 500


@app.route('/api/admin/users/<int:user_id>/telegram-id', methods=['PUT', 'PATCH'])
@admin_required
def update_user_telegram_id(current_admin, user_id):
    """Обновить telegram_id пользователя"""
    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"message": "User not found"}), 404
        
        data = request.json
        telegram_id = data.get('telegram_id')
        
        # Если telegram_id пустой или None, устанавливаем None
        if telegram_id == '' or telegram_id is None:
            telegram_id = None
        else:
            telegram_id = str(telegram_id)
        
        old_telegram_id = user.telegram_id
        user.telegram_id = telegram_id
        db.session.commit()
        
        # Обновляем telegramId в RemnaWave, если есть UUID
        if user.remnawave_uuid:
            try:
                headers = {"Authorization": f"Bearer {os.getenv('ADMIN_TOKEN')}"}
                requests.patch(
                    f"{os.getenv('API_URL')}/api/users",
                    headers=headers,
                    json={"uuid": user.remnawave_uuid, "telegramId": telegram_id},
                    timeout=10
                )
                cache.delete(f'live_data_{user.remnawave_uuid}')
            except Exception as e:
                print(f"Warning: Failed to update telegramId in RemnaWave: {e}")
                # Не возвращаем ошибку, т.к. локальное обновление уже выполнено
        
        return jsonify({
            "message": "Telegram ID updated successfully",
            "telegram_id": telegram_id
        }), 200
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Internal Server Error", "error": str(e)}), 500


@app.route('/api/admin/users/<int:user_id>/update', methods=['POST'])
@admin_required
def admin_update_user(current_admin, user_id):
    """Обновление пользователя: выдача тарифа, триал, лимит устройств"""
    try:
        user = User.query.get(user_id)
        if not user:
            return jsonify({"message": "User not found"}), 404
        
        if not user.remnawave_uuid or not str(user.remnawave_uuid).strip():
            return jsonify({"message": "User has no RemnaWave UUID"}), 400
        
        # Если включены несколько конфигов — применяем изменения к основному (primary) uuid
        target_uuid = str(user.remnawave_uuid).strip()
        try:
            from modules.models.user_config import UserConfig
            primary_cfg = UserConfig.query.filter_by(user_id=user.id, is_primary=True).first()
            if primary_cfg and primary_cfg.remnawave_uuid and str(primary_cfg.remnawave_uuid).strip():
                target_uuid = str(primary_cfg.remnawave_uuid).strip()
                if user.remnawave_uuid != target_uuid:
                    user.remnawave_uuid = target_uuid
                    db.session.commit()
        except Exception:
            target_uuid = str(user.remnawave_uuid).strip()

        data = request.json
        if not data:
            return jsonify({"message": "Request body is required"}), 400
            
        action = data.get('action')
        if not action:
            return jsonify({"message": "Action is required. Valid actions: grant_tariff, grant_trial, set_device_limit"}), 400
        
        headers, cookies = get_remnawave_headers()

        def _parse_dt(value):
            """Parse ISO datetime string (handles trailing Z). Returns aware dt in UTC or None."""
            if not value or not isinstance(value, str):
                return None
            try:
                s = value
                if s.endswith('Z'):
                    s = s[:-1] + '+00:00'
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                return None

        def _extract_squad_uuids(active_squads):
            """Normalize activeInternalSquads from RemnaWave into list of UUID strings."""
            out = []
            if not isinstance(active_squads, list):
                return out
            for item in active_squads:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
                elif isinstance(item, dict):
                    u = item.get('uuid') or item.get('id')
                    if isinstance(u, str) and u.strip():
                        out.append(u.strip())
            # uniq preserving order
            seen = set()
            uniq = []
            for x in out:
                if x in seen:
                    continue
                uniq.append(x)
                seen.add(x)
            return uniq

        if action == 'grant_tariff':
            tariff_id = data.get('tariff_id')
            if not tariff_id:
                return jsonify({"message": "Tariff ID is required"}), 400
            tariff = Tariff.query.get(tariff_id)
            if not tariff:
                return jsonify({"message": "Tariff not found"}), 404
            
            # Если days явно передали — можно использовать как override, иначе берём duration_days(+bonus_days)
            days_override = data.get('days')
            try:
                days_override = int(days_override) if days_override not in (None, "") else None
                if days_override is not None and days_override <= 0:
                    days_override = None
            except Exception:
                days_override = None

            base_days = int(getattr(tariff, 'duration_days', 0) or 0)
            bonus_days = int(getattr(tariff, 'bonus_days', 0) or 0)
            days_to_add = days_override if days_override is not None else (base_days + bonus_days)
            if days_to_add <= 0:
                days_to_add = 30

            resp = requests.get(
                f"{os.getenv('API_URL')}/api/users/{target_uuid}",
                headers=headers,
                cookies=cookies,
                timeout=10
            )
            if resp.status_code != 200:
                return jsonify({"message": "Failed to get user data"}), 500

            user_data = (resp.json() or {}).get('response', {}) if isinstance(resp.json(), dict) else {}
            current_expire_dt = _parse_dt(user_data.get('expireAt'))
            now = datetime.now(timezone.utc)
            base_dt = max(now, current_expire_dt) if current_expire_dt else now
            new_expire_dt = base_dt + timedelta(days=days_to_add)

            # squads from tariff (or fallback)
            squad_ids = []
            try:
                if hasattr(tariff, 'get_squad_ids'):
                    squad_ids = tariff.get_squad_ids()
                elif getattr(tariff, 'squad_ids', None):
                    squad_ids = json.loads(tariff.squad_ids) if isinstance(tariff.squad_ids, str) else tariff.squad_ids
            except Exception:
                squad_ids = []

            current_squads = _extract_squad_uuids(user_data.get('activeInternalSquads', []) or [])
            if not squad_ids:
                if getattr(tariff, 'squad_id', None):
                    squad_ids = [tariff.squad_id]
                else:
                    default_squad = os.getenv("DEFAULT_SQUAD_ID")
                    squad_ids = [default_squad] if default_squad else (current_squads or [])

            patch_payload = {
                "uuid": target_uuid,
                "expireAt": new_expire_dt.isoformat(),
                "activeInternalSquads": squad_ids
            }

            # traffic / device limits from tariff
            try:
                if getattr(tariff, 'traffic_limit_bytes', 0) and int(tariff.traffic_limit_bytes) > 0:
                    patch_payload["trafficLimitBytes"] = int(tariff.traffic_limit_bytes)
                    patch_payload["trafficLimitStrategy"] = "NO_RESET"
            except Exception:
                pass

            try:
                if hasattr(tariff, 'hwid_device_limit') and tariff.hwid_device_limit is not None:
                    patch_payload["hwidDeviceLimit"] = int(tariff.hwid_device_limit)
            except Exception:
                pass

            patch_resp = requests.patch(
                f"{os.getenv('API_URL')}/api/users",
                headers=headers,
                cookies=cookies,
                json=patch_payload,
                timeout=10
            )
            if not patch_resp.ok:
                return jsonify({"message": "Failed to update user in RemnaWave"}), 500

            cache.delete(f'live_data_{target_uuid}')
            cache.delete('all_live_users_map')
            return jsonify({
                "message": "Tariff granted successfully",
                "user_email": user.email,
                "action": "grant_tariff",
                "tariff_id": int(tariff_id),
                "expireAt": new_expire_dt.isoformat(),
                "activeInternalSquads": squad_ids
            }), 200

        elif action == 'grant_trial':
            days = data.get('days', 3)
            try:
                days = int(days)
            except Exception:
                days = 3
            if days <= 0:
                days = 3

            resp = requests.get(
                f"{os.getenv('API_URL')}/api/users/{target_uuid}",
                headers=headers,
                cookies=cookies,
                timeout=10
            )
            if resp.status_code != 200:
                return jsonify({"message": "Failed to get user data"}), 500

            payload = resp.json() or {}
            user_data = payload.get('response', payload) if isinstance(payload, dict) else {}
            current_expire_dt = _parse_dt(user_data.get('expireAt'))
            now = datetime.now(timezone.utc)
            base_dt = max(now, current_expire_dt) if current_expire_dt else now
            new_expire_dt = base_dt + timedelta(days=days)

            current_squads = _extract_squad_uuids(user_data.get('activeInternalSquads', []) or [])
            # Если у пользователя уже есть сквады — сохраняем. Иначе ставим trial squad / default squad.
            trial_squad = None
            try:
                referral_settings = ReferralSetting.query.first()
                if referral_settings and getattr(referral_settings, 'trial_squad_id', None):
                    trial_squad = referral_settings.trial_squad_id
            except Exception:
                trial_squad = None
            if not trial_squad:
                trial_squad = os.getenv("DEFAULT_SQUAD_ID")

            patch_payload = {
                "uuid": target_uuid,
                "expireAt": new_expire_dt.isoformat(),
                "activeInternalSquads": current_squads if current_squads else ([trial_squad] if trial_squad else [])
            }

            patch_resp = requests.patch(
                f"{os.getenv('API_URL')}/api/users",
                headers=headers,
                cookies=cookies,
                json=patch_payload,
                timeout=10
            )
            if not patch_resp.ok:
                return jsonify({"message": "Failed to update user in RemnaWave"}), 500

            cache.delete(f'live_data_{target_uuid}')
            cache.delete('all_live_users_map')
            return jsonify({
                "message": "Trial granted successfully",
                "user_email": user.email,
                "action": "grant_trial",
                "expireAt": new_expire_dt.isoformat(),
                "activeInternalSquads": patch_payload.get("activeInternalSquads", [])
            }), 200

        elif action == 'set_device_limit':
            device_limit = data.get('device_limit', 0)
            try:
                device_limit = int(device_limit)
            except Exception:
                device_limit = 0
            if device_limit < 0:
                device_limit = 0

            patch_resp = requests.patch(
                f"{os.getenv('API_URL')}/api/users",
                headers=headers,
                cookies=cookies,
                json={"uuid": target_uuid, "hwidDeviceLimit": device_limit},
                timeout=10
            )
            if not patch_resp.ok:
                return jsonify({"message": "Failed to update user in RemnaWave"}), 500

            cache.delete(f'live_data_{target_uuid}')
            cache.delete('all_live_users_map')
            return jsonify({
                "message": "Device limit updated successfully",
                "user_email": user.email,
                "action": "set_device_limit",
                "hwidDeviceLimit": device_limit
            }), 200

        return jsonify({"message": "Invalid action"}), 400

    except Exception as e:
        return jsonify({"message": "Internal Error"}), 500


@app.route('/api/admin/users/emails', methods=['GET'])
@admin_required
def get_users_emails(current_admin):
    """Получить список email для рассылки"""
    try:
        users = User.query.filter_by(role='CLIENT').all()
        emails = [{"email": u.email, "is_verified": u.is_verified} for u in users if u.email]
        return jsonify(emails), 200
    except Exception as e:
        return jsonify({"message": str(e)}), 500


# ============================================================================
# STATISTICS
# ============================================================================

@app.route('/api/admin/statistics', methods=['GET'])
@admin_required
def get_statistics(current_admin):
    """Получение статистики системы"""
    try:
        total_users = User.query.count()
        active_users = User.query.filter(User.is_verified == True).count()
        total_payments = Payment.query.count()
        successful_payments = Payment.query.filter(Payment.status == 'PAID').count()
        total_tariffs = Tariff.query.count()
        
        # Подсчет продаж (только успешные платежи)
        total_sales_count = successful_payments
        
        # Подсчет общей прибыли по валютам
        paid_payments = Payment.query.filter(Payment.status == 'PAID').all()
        total_revenue = {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
        
        for payment in paid_payments:
            currency = payment.currency or 'USD'
            amount = float(payment.amount) if payment.amount else 0.0
            
            # Конвертируем в USD для общего подсчета, если нужно
            if currency == 'USD':
                total_revenue['USD'] += amount
            elif currency == 'UAH':
                total_revenue['UAH'] += amount
            elif currency == 'RUB':
                total_revenue['RUB'] += amount
        
        # Подсчет прибыли за сегодня
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_payments = Payment.query.filter(
            Payment.status == 'PAID',
            Payment.created_at >= today_start
        ).all()
        
        today_revenue = {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
        for payment in today_payments:
            currency = payment.currency or 'USD'
            amount = float(payment.amount) if payment.amount else 0.0
            
            if currency == 'USD':
                today_revenue['USD'] += amount
            elif currency == 'UAH':
                today_revenue['UAH'] += amount
            elif currency == 'RUB':
                today_revenue['RUB'] += amount

        return jsonify({
            'total_users': total_users,
            'active_users': active_users,
            'total_payments': total_payments,
            'successful_payments': successful_payments,
            'payment_success_rate': round(successful_payments / total_payments * 100, 2) if total_payments > 0 else 0,
            'total_tariffs': total_tariffs,
            'total_sales_count': total_sales_count,
            'total_revenue': total_revenue,
            'today_revenue': today_revenue
        }), 200

    except Exception as e:
        print(f"Error in get_statistics: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Internal Server Error"}), 500


@app.route('/api/admin/analytics', methods=['GET'])
@admin_required
def get_analytics(current_admin):
    """Получение расширенной аналитики с группировкой по дням, неделям, месяцам"""
    try:
        from sqlalchemy import func, extract, case, text
        from modules.models.user_config import UserConfig
        
        # Определяем тип БД
        is_postgresql = app.config.get('USE_POSTGRESQL', False)
        period = request.args.get('period', 'days')  # days, weeks, months
        
        # Фильтры по датам (опционально)
        start_date_param = request.args.get('start_date')
        end_date_param = request.args.get('end_date')
        custom_date_range = None
        if start_date_param and end_date_param:
            try:
                custom_start = datetime.fromisoformat(start_date_param.replace('Z', '+00:00'))
                custom_end = datetime.fromisoformat(end_date_param.replace('Z', '+00:00'))
                custom_date_range = (custom_start, custom_end)
            except:
                pass
        
        # Определяем диапазон дат для общей статистики
        if custom_date_range:
            stats_start_date, stats_end_date = custom_date_range
        else:
            # Используем период по умолчанию
            if period == 'days':
                stats_end_date = datetime.now(timezone.utc)
                stats_start_date = stats_end_date - timedelta(days=30)
            elif period == 'weeks':
                stats_end_date = datetime.now(timezone.utc)
                stats_start_date = stats_end_date - timedelta(weeks=12)
            else:  # months
                stats_end_date = datetime.now(timezone.utc)
                stats_start_date = stats_end_date - timedelta(days=365)
        
        # Общая статистика с учетом выбранного периода/диапазона
        total_users = User.query.filter_by(role='CLIENT').filter(
            User.created_at >= stats_start_date,
            User.created_at <= stats_end_date
        ).count()
        verified_users = User.query.filter_by(role='CLIENT', is_verified=True).filter(
            User.created_at >= stats_start_date,
            User.created_at <= stats_end_date
        ).count()
        
        # Конфиги созданные в выбранном периоде
        total_configs = UserConfig.query.filter(
            UserConfig.created_at >= stats_start_date,
            UserConfig.created_at <= stats_end_date
        ).count()
        
        total_payments = Payment.query.filter(
            Payment.created_at >= stats_start_date,
            Payment.created_at <= stats_end_date
        ).count()
        successful_payments = Payment.query.filter(
            Payment.status == 'PAID',
            Payment.created_at >= stats_start_date,
            Payment.created_at <= stats_end_date
        ).count()
        
        # Статистика по пользователям в боте (с telegram_id) в выбранном периоде
        users_with_telegram = User.query.filter_by(role='CLIENT').filter(
            User.telegram_id.isnot(None),
            User.created_at >= stats_start_date,
            User.created_at <= stats_end_date
        ).count()
        
        # Статистика по сайтам (конфигам) - пользователи с конфигами в выбранном периоде
        configs_by_user = db.session.query(
            func.count(UserConfig.id).label('config_count')
        ).filter(
            UserConfig.created_at >= stats_start_date,
            UserConfig.created_at <= stats_end_date
        ).group_by(UserConfig.user_id).all()
        users_with_configs = len(configs_by_user)
        
        # Подсчет прибыли по валютам в выбранном периоде
        paid_payments = Payment.query.filter(
            Payment.status == 'PAID',
            Payment.created_at >= stats_start_date,
            Payment.created_at <= stats_end_date
        ).all()
        total_revenue = {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
        
        for payment in paid_payments:
            currency = payment.currency or 'USD'
            amount = float(payment.amount) if payment.amount else 0.0
            if currency in total_revenue:
                total_revenue[currency] += amount
        
        # Группировка по периодам
        revenue_by_period = []
        user_registrations_by_period = []
        payments_by_period = []
        
        if period == 'days':
            # Используем кастомный диапазон или последние 30 дней
            if custom_date_range:
                start_date, end_date = custom_date_range
            else:
                end_date = datetime.now(timezone.utc)
                start_date = end_date - timedelta(days=30)
            
            if is_postgresql:
                # Доходы по дням (PostgreSQL)
                revenue_query = db.session.query(
                    func.date(Payment.created_at).label('date'),
                    Payment.currency,
                    func.sum(Payment.amount).label('total')
                ).filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).group_by(
                    func.date(Payment.created_at),
                    Payment.currency
                ).order_by('date').all()
                
                # Регистрации по дням
                registrations_query = db.session.query(
                    func.date(User.created_at).label('date'),
                    func.count(User.id).label('count')
                ).filter(
                    User.role == 'CLIENT',
                    User.created_at >= start_date,
                    User.created_at <= end_date
                ).group_by(func.date(User.created_at)).order_by('date').all()
                
                # Платежи по дням
                payments_count_query = db.session.query(
                    func.date(Payment.created_at).label('date'),
                    func.count(Payment.id).label('count')
                ).filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).group_by(func.date(Payment.created_at)).order_by('date').all()
            else:
                # SQLite - используем strftime
                revenue_query = db.session.query(
                    func.strftime('%Y-%m-%d', Payment.created_at).label('date'),
                    Payment.currency,
                    func.sum(Payment.amount).label('total')
                ).filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).group_by(
                    func.strftime('%Y-%m-%d', Payment.created_at),
                    Payment.currency
                ).order_by('date').all()
                
                registrations_query = db.session.query(
                    func.strftime('%Y-%m-%d', User.created_at).label('date'),
                    func.count(User.id).label('count')
                ).filter(
                    User.role == 'CLIENT',
                    User.created_at >= start_date,
                    User.created_at <= end_date
                ).group_by(func.strftime('%Y-%m-%d', User.created_at)).order_by('date').all()
                
                payments_count_query = db.session.query(
                    func.strftime('%Y-%m-%d', Payment.created_at).label('date'),
                    func.count(Payment.id).label('count')
                ).filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).group_by(func.strftime('%Y-%m-%d', Payment.created_at)).order_by('date').all()
            
            # Формируем данные по дням
            date_dict = {}
            for item in revenue_query:
                if len(item) == 3:
                    date, currency, total = item
                    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
                    if date_str not in date_dict:
                        date_dict[date_str] = {'date': date_str, 'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
                    if currency in date_dict[date_str]:
                        date_dict[date_str][currency] += float(total)
            
            revenue_by_period = list(date_dict.values())
            
            # Регистрации
            reg_dict = {str(item[0]): item[1] for item in registrations_query}
            user_registrations_by_period = [
                {'date': date, 'count': reg_dict.get(date, 0)}
                for date in date_dict.keys()
            ]
            
            # Платежи
            payments_dict = {str(item[0]): item[1] for item in payments_count_query}
            payments_by_period = [
                {'date': date, 'count': payments_dict.get(date, 0)}
                for date in date_dict.keys()
            ]
            
        elif period == 'weeks':
            # Используем кастомный диапазон или последние 12 недель
            if custom_date_range:
                start_date, end_date = custom_date_range
            else:
                end_date = datetime.now(timezone.utc)
                start_date = end_date - timedelta(weeks=12)
            
            # Для PostgreSQL используем date_trunc, для SQLite - группируем в Python
            if is_postgresql:
                # Доходы по неделям (PostgreSQL)
                revenue_query = db.session.query(
                    func.date_trunc('week', Payment.created_at).label('week'),
                    Payment.currency,
                    func.sum(Payment.amount).label('total')
                ).filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).group_by(
                    func.date_trunc('week', Payment.created_at),
                    Payment.currency
                ).order_by('week').all()
                
                # Регистрации по неделям
                registrations_query = db.session.query(
                    func.date_trunc('week', User.created_at).label('week'),
                    func.count(User.id).label('count')
                ).filter(
                    User.role == 'CLIENT',
                    User.created_at >= start_date,
                    User.created_at <= end_date
                ).group_by(func.date_trunc('week', User.created_at)).order_by('week').all()
                
                # Платежи по неделям
                payments_count_query = db.session.query(
                    func.date_trunc('week', Payment.created_at).label('week'),
                    func.count(Payment.id).label('count')
                ).filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).group_by(func.date_trunc('week', Payment.created_at)).order_by('week').all()
            else:
                # SQLite - получаем все данные и группируем в Python
                all_payments = Payment.query.filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).all()
                
                all_users = User.query.filter(
                    User.role == 'CLIENT',
                    User.created_at >= start_date,
                    User.created_at <= end_date
                ).all()
                
                # Группируем по неделям в Python
                week_dict_rev = {}
                week_dict_reg = {}
                week_dict_pay = {}
                
                for payment in all_payments:
                    if payment.created_at:
                        # Находим начало недели (понедельник)
                        week_start = payment.created_at - timedelta(days=payment.created_at.weekday())
                        week_key = week_start.date().isoformat()
                        
                        if week_key not in week_dict_rev:
                            week_dict_rev[week_key] = {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
                        currency = payment.currency or 'USD'
                        if currency in week_dict_rev[week_key]:
                            week_dict_rev[week_key][currency] += float(payment.amount or 0)
                        
                        if week_key not in week_dict_pay:
                            week_dict_pay[week_key] = 0
                        week_dict_pay[week_key] += 1
                
                for user in all_users:
                    if user.created_at:
                        week_start = user.created_at - timedelta(days=user.created_at.weekday())
                        week_key = week_start.date().isoformat()
                        if week_key not in week_dict_reg:
                            week_dict_reg[week_key] = 0
                        week_dict_reg[week_key] += 1
                
                revenue_query = [
                    (week_key, currency, amount)
                    for week_key, currencies in week_dict_rev.items()
                    for currency, amount in currencies.items()
                    if amount > 0
                ]
                registrations_query = [(week_key, count) for week_key, count in week_dict_reg.items()]
                payments_count_query = [(week_key, count) for week_key, count in week_dict_pay.items()]
            
            # Формируем данные
            week_dict = {}
            for item in revenue_query:
                if len(item) == 3:
                    week, currency, total = item
                    week_str = week.isoformat() if hasattr(week, 'isoformat') else str(week)
                    if week_str not in week_dict:
                        week_dict[week_str] = {'date': week_str, 'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
                    if currency in week_dict[week_str]:
                        week_dict[week_str][currency] += float(total)
            
            revenue_by_period = list(week_dict.values())
            
            reg_dict = {str(item[0]): item[1] for item in registrations_query}
            user_registrations_by_period = [
                {'date': date, 'count': reg_dict.get(date, 0)}
                for date in week_dict.keys()
            ]
            
            payments_dict = {str(item[0]): item[1] for item in payments_count_query}
            payments_by_period = [
                {'date': date, 'count': payments_dict.get(date, 0)}
                for date in week_dict.keys()
            ]
            
        elif period == 'months':
            # Используем кастомный диапазон или последние 12 месяцев
            if custom_date_range:
                start_date, end_date = custom_date_range
            else:
                end_date = datetime.now(timezone.utc)
                start_date = end_date - timedelta(days=365)
            
            if is_postgresql:
                # Доходы по месяцам (PostgreSQL)
                revenue_query = db.session.query(
                    func.date_trunc('month', Payment.created_at).label('month'),
                    Payment.currency,
                    func.sum(Payment.amount).label('total')
                ).filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).group_by(
                    func.date_trunc('month', Payment.created_at),
                    Payment.currency
                ).order_by('month').all()
                
                # Регистрации по месяцам
                registrations_query = db.session.query(
                    func.date_trunc('month', User.created_at).label('month'),
                    func.count(User.id).label('count')
                ).filter(
                    User.role == 'CLIENT',
                    User.created_at >= start_date,
                    User.created_at <= end_date
                ).group_by(func.date_trunc('month', User.created_at)).order_by('month').all()
                
                # Платежи по месяцам
                payments_count_query = db.session.query(
                    func.date_trunc('month', Payment.created_at).label('month'),
                    func.count(Payment.id).label('count')
                ).filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).group_by(func.date_trunc('month', Payment.created_at)).order_by('month').all()
            else:
                # SQLite - группируем в Python
                all_payments = Payment.query.filter(
                    Payment.status == 'PAID',
                    Payment.created_at >= start_date,
                    Payment.created_at <= end_date
                ).all()
                
                all_users = User.query.filter(
                    User.role == 'CLIENT',
                    User.created_at >= start_date,
                    User.created_at <= end_date
                ).all()
                
                month_dict_rev = {}
                month_dict_reg = {}
                month_dict_pay = {}
                
                for payment in all_payments:
                    if payment.created_at:
                        month_key = payment.created_at.replace(day=1).date().isoformat()
                        if month_key not in month_dict_rev:
                            month_dict_rev[month_key] = {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
                        currency = payment.currency or 'USD'
                        if currency in month_dict_rev[month_key]:
                            month_dict_rev[month_key][currency] += float(payment.amount or 0)
                        
                        if month_key not in month_dict_pay:
                            month_dict_pay[month_key] = 0
                        month_dict_pay[month_key] += 1
                
                for user in all_users:
                    if user.created_at:
                        month_key = user.created_at.replace(day=1).date().isoformat()
                        if month_key not in month_dict_reg:
                            month_dict_reg[month_key] = 0
                        month_dict_reg[month_key] += 1
                
                revenue_query = [
                    (month_key, currency, amount)
                    for month_key, currencies in month_dict_rev.items()
                    for currency, amount in currencies.items()
                    if amount > 0
                ]
                registrations_query = [(month_key, count) for month_key, count in month_dict_reg.items()]
                payments_count_query = [(month_key, count) for month_key, count in month_dict_pay.items()]
            
            # Формируем данные
            month_dict = {}
            for item in revenue_query:
                if len(item) == 3:
                    month, currency, total = item
                    month_str = month.isoformat() if hasattr(month, 'isoformat') else str(month)
                    if month_str not in month_dict:
                        month_dict[month_str] = {'date': month_str, 'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
                    if currency in month_dict[month_str]:
                        month_dict[month_str][currency] += float(total)
            
            revenue_by_period = list(month_dict.values())
            
            reg_dict = {str(item[0]): item[1] for item in registrations_query}
            user_registrations_by_period = [
                {'date': date, 'count': reg_dict.get(date, 0)}
                for date in month_dict.keys()
            ]
            
            payments_dict = {str(item[0]): item[1] for item in payments_count_query}
            payments_by_period = [
                {'date': date, 'count': payments_dict.get(date, 0)}
                for date in month_dict.keys()
            ]
        
        # Статистика по провайдерам платежей в выбранном периоде
        provider_stats = db.session.query(
            Payment.payment_provider,
            func.count(Payment.id).label('count'),
            func.sum(Payment.amount).label('total')
        ).filter(
            Payment.status == 'PAID',
            Payment.created_at >= stats_start_date,
            Payment.created_at <= stats_end_date
        ).group_by(Payment.payment_provider).all()
        
        payment_providers = [
            {
                'provider': provider or 'unknown',
                'count': count,
                'total': float(total) if total else 0.0
            }
            for provider, count, total in provider_stats
        ]
        
        # Дополнительные метрики
        # ARPU и средний чек по валютам
        arpu_by_currency = {
            'USD': round(total_revenue['USD'] / total_users, 2) if total_users > 0 else 0.0,
            'UAH': round(total_revenue['UAH'] / total_users, 2) if total_users > 0 else 0.0,
            'RUB': round(total_revenue['RUB'] / total_users, 2) if total_users > 0 else 0.0
        }
        
        # Средний чек на платеж по валютам
        avg_payment_by_currency = {
            'USD': round(total_revenue['USD'] / successful_payments, 2) if successful_payments > 0 else 0.0,
            'UAH': round(total_revenue['UAH'] / successful_payments, 2) if successful_payments > 0 else 0.0,
            'RUB': round(total_revenue['RUB'] / successful_payments, 2) if successful_payments > 0 else 0.0
        }
        
        # Конверсия: регистрации -> платежи
        conversion_rate = round((successful_payments / total_users * 100), 2) if total_users > 0 else 0.0
        
        # Статистика по тарифам (по валютам отдельно) в выбранном периоде
        tariff_stats = db.session.query(
            Tariff.id,
            Tariff.name,
            Payment.currency,
            func.count(Payment.id).label('count'),
            func.sum(Payment.amount).label('total')
        ).join(
            Payment, Payment.tariff_id == Tariff.id
        ).filter(
            Payment.status == 'PAID',
            Payment.created_at >= stats_start_date,
            Payment.created_at <= stats_end_date
        ).group_by(Tariff.id, Tariff.name, Payment.currency).all()
        
        # Группируем по тарифам и валютам
        tariffs_dict = {}
        for tariff_id, name, currency, count, total in tariff_stats:
            if tariff_id not in tariffs_dict:
                tariffs_dict[tariff_id] = {
                    'id': tariff_id,
                    'name': name,
                    'count': 0,
                    'revenue_by_currency': {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
                }
            tariffs_dict[tariff_id]['count'] += count
            if currency in tariffs_dict[tariff_id]['revenue_by_currency']:
                tariffs_dict[tariff_id]['revenue_by_currency'][currency] += float(total) if total else 0.0
        
        tariffs_analytics = sorted(
            list(tariffs_dict.values()),
            key=lambda x: x['count'],
            reverse=True
        )
        
        # Статистика по промокодам (по валютам отдельно) в выбранном периоде
        promo_stats = db.session.query(
            PromoCode.code,
            Payment.currency,
            func.count(Payment.id).label('count'),
            func.sum(Payment.amount).label('total')
        ).join(
            Payment, Payment.promo_code_id == PromoCode.id
        ).filter(
            Payment.status == 'PAID',
            Payment.created_at >= stats_start_date,
            Payment.created_at <= stats_end_date
        ).group_by(PromoCode.code, Payment.currency).all()
        
        # Группируем по промокодам и валютам
        promos_dict = {}
        for code, currency, count, total in promo_stats:
            if code not in promos_dict:
                promos_dict[code] = {
                    'code': code,
                    'count': 0,
                    'revenue_by_currency': {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
                }
            promos_dict[code]['count'] += count
            if currency in promos_dict[code]['revenue_by_currency']:
                promos_dict[code]['revenue_by_currency'][currency] += float(total) if total else 0.0
        
        promocodes_analytics = sorted(
            list(promos_dict.values()),
            key=lambda x: x['count'],
            reverse=True
        )
        
        # Статистика по триалам в выбранном периоде
        trial_users = User.query.filter_by(role='CLIENT', trial_used=True).filter(
            User.created_at >= stats_start_date,
            User.created_at <= stats_end_date
        ).count()
        trial_usage_rate = round((trial_users / total_users * 100), 2) if total_users > 0 else 0.0
        
        # Статистика по реферальной программе в выбранном периоде
        users_with_referrer = User.query.filter_by(role='CLIENT').filter(
            User.referrer_id.isnot(None),
            User.created_at >= stats_start_date,
            User.created_at <= stats_end_date
        ).count()
        referral_rate = round((users_with_referrer / total_users * 100), 2) if total_users > 0 else 0.0
        
        # Новые пользователи за последние периоды для сравнения (всегда за последние периоды, независимо от выбранного диапазона)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)
        
        new_users_today = User.query.filter_by(role='CLIENT').filter(User.created_at >= today).count()
        new_users_yesterday = User.query.filter_by(role='CLIENT').filter(
            User.created_at >= yesterday,
            User.created_at < today
        ).count()
        new_users_this_week = User.query.filter_by(role='CLIENT').filter(User.created_at >= week_ago).count()
        new_users_last_week = User.query.filter_by(role='CLIENT').filter(
            User.created_at >= (week_ago - timedelta(days=7)),
            User.created_at < week_ago
        ).count()
        new_users_this_month = User.query.filter_by(role='CLIENT').filter(User.created_at >= month_ago).count()
        new_users_last_month = User.query.filter_by(role='CLIENT').filter(
            User.created_at >= (month_ago - timedelta(days=30)),
            User.created_at < month_ago
        ).count()
        
        # Платежи за периоды для сравнения
        payments_today = Payment.query.filter(
            Payment.status == 'PAID',
            Payment.created_at >= today
        ).count()
        payments_yesterday = Payment.query.filter(
            Payment.status == 'PAID',
            Payment.created_at >= yesterday,
            Payment.created_at < today
        ).count()
        payments_this_week = Payment.query.filter(
            Payment.status == 'PAID',
            Payment.created_at >= week_ago
        ).count()
        payments_last_week = Payment.query.filter(
            Payment.status == 'PAID',
            Payment.created_at >= (week_ago - timedelta(days=7)),
            Payment.created_at < week_ago
        ).count()
        
        # Доходы за периоды
        today_payments = Payment.query.filter(
            Payment.status == 'PAID',
            Payment.created_at >= today
        ).all()
        revenue_today = {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
        for payment in today_payments:
            currency = payment.currency or 'USD'
            if currency in revenue_today:
                revenue_today[currency] += float(payment.amount or 0)
        
        yesterday_payments = Payment.query.filter(
            Payment.status == 'PAID',
            Payment.created_at >= yesterday,
            Payment.created_at < today
        ).all()
        revenue_yesterday = {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0}
        for payment in yesterday_payments:
            currency = payment.currency or 'USD'
            if currency in revenue_yesterday:
                revenue_yesterday[currency] += float(payment.amount or 0)
        
        # Расширенная статистика по триалам в выбранном периоде
        trial_stats = {
            'total_used': trial_users,
            'usage_rate': trial_usage_rate,
            'not_used': total_users - trial_users,
            'conversion_from_trial': 0  # Сколько из триалов конвертировались в платных
        }
        # Пользователи, которые использовали триал и потом сделали платеж в выбранном периоде
        trial_users_with_payments = db.session.query(User.id).join(
            Payment, Payment.user_id == User.id
        ).filter(
            User.role == 'CLIENT',
            User.trial_used == True,
            Payment.status == 'PAID',
            User.created_at >= stats_start_date,
            User.created_at <= stats_end_date,
            Payment.created_at >= stats_start_date,
            Payment.created_at <= stats_end_date
        ).distinct().count()
        trial_stats['conversion_from_trial'] = round((trial_users_with_payments / trial_users * 100), 2) if trial_users > 0 else 0
        
        # Детальная статистика по реферальной программе в выбранном периоде
        referral_stats = {
            'total_referrers': User.query.filter_by(role='CLIENT').filter(
                User.referrer_id.isnot(None),
                User.created_at >= stats_start_date,
                User.created_at <= stats_end_date
            ).count(),
            'total_referrals': db.session.query(func.count(User.id)).filter(
                User.role == 'CLIENT',
                User.referrer_id.isnot(None),
                User.created_at >= stats_start_date,
                User.created_at <= stats_end_date
            ).scalar(),
            'top_referrers': []
        }
        
        # Топ рефереров (пользователи с наибольшим количеством рефералов) в выбранном периоде
        # Используем правильный способ для self-referential relationship
        from sqlalchemy.orm import aliased
        Referral = aliased(User)
        
        top_referrers_query = db.session.query(
            User.id,
            User.email,
            User.telegram_username,
            func.count(Referral.id).label('referrals_count')
        ).join(
            Referral, Referral.referrer_id == User.id
        ).filter(
            User.role == 'CLIENT',
            Referral.role == 'CLIENT',
            Referral.created_at >= stats_start_date,
            Referral.created_at <= stats_end_date
        ).group_by(
            User.id, User.email, User.telegram_username
        ).order_by(
            func.count(Referral.id).desc()
        ).limit(10).all()
        
        referral_stats['top_referrers'] = [
            {
                'id': ref_id,
                'email': email or 'N/A',
                'telegram_username': telegram_username,
                'referrals_count': count
            }
            for ref_id, email, telegram_username, count in top_referrers_query
        ]
        
        # Воронка конверсии (уже использует отфильтрованные данные)
        conversion_funnel = {
            'registrations': total_users,
            'verified': verified_users,
            'with_telegram': users_with_telegram,
            'used_trial': trial_users,
            'made_payment': successful_payments,
            'active_subscription': users_with_configs
        }
        
        # Проценты конверсии на каждом этапе
        conversion_funnel['rates'] = {
            'verification_rate': round((verified_users / total_users * 100), 2) if total_users > 0 else 0,
            'telegram_rate': round((users_with_telegram / total_users * 100), 2) if total_users > 0 else 0,
            'trial_rate': round((trial_users / total_users * 100), 2) if total_users > 0 else 0,
            'payment_rate': round((successful_payments / total_users * 100), 2) if total_users > 0 else 0,
            'subscription_rate': round((users_with_configs / total_users * 100), 2) if total_users > 0 else 0
        }
        
        # Топ пользователей по тратам (по валютам отдельно) в выбранном периоде
        top_users_by_spending = db.session.query(
            User.id,
            User.email,
            User.telegram_username,
            Payment.currency,
            func.sum(Payment.amount).label('total_spent'),
            func.count(Payment.id).label('payments_count')
        ).join(
            Payment, Payment.user_id == User.id
        ).filter(
            User.role == 'CLIENT',
            Payment.status == 'PAID',
            Payment.created_at >= stats_start_date,
            Payment.created_at <= stats_end_date
        ).group_by(
            User.id, User.email, User.telegram_username, Payment.currency
        ).order_by(
            func.sum(Payment.amount).desc()
        ).all()
        
        # Группируем по пользователям и валютам
        users_dict = {}
        for user_id, email, telegram_username, currency, total_spent, payments_count in top_users_by_spending:
            if user_id not in users_dict:
                users_dict[user_id] = {
                    'id': user_id,
                    'email': email or 'N/A',
                    'telegram_username': telegram_username,
                    'spending_by_currency': {'USD': 0.0, 'UAH': 0.0, 'RUB': 0.0},
                    'payments_count': 0
                }
            if currency in users_dict[user_id]['spending_by_currency']:
                users_dict[user_id]['spending_by_currency'][currency] += float(total_spent) if total_spent else 0.0
            users_dict[user_id]['payments_count'] += payments_count
        
        # Сортируем по общей сумме (сумма всех валют)
        top_users = sorted(
            [
                {
                    **user_data,
                    'total_spent': sum(user_data['spending_by_currency'].values())
                }
                for user_data in users_dict.values()
            ],
            key=lambda x: x['total_spent'],
            reverse=True
        )[:20]
        
        return jsonify({
            'overview': {
                'total_users': total_users,
                'verified_users': verified_users,
                'users_with_telegram': users_with_telegram,
                'total_configs': total_configs,
                'users_with_configs': users_with_configs,
                'total_payments': total_payments,
                'successful_payments': successful_payments,
                'payment_success_rate': round(successful_payments / total_payments * 100, 2) if total_payments > 0 else 0,
                'total_revenue': total_revenue,
                # Новые метрики
                'arpu_by_currency': arpu_by_currency,  # Average Revenue Per User по валютам
                'avg_payment_by_currency': avg_payment_by_currency,  # Средний чек по валютам
                'conversion_rate': conversion_rate,  # Конверсия регистраций в платежи
                'trial_users': trial_users,
                'trial_usage_rate': trial_usage_rate,
                'users_with_referrer': users_with_referrer,
                'referral_rate': referral_rate
            },
            'revenue_by_period': revenue_by_period,
            'user_registrations_by_period': user_registrations_by_period,
            'payments_by_period': payments_by_period,
            'payment_providers': payment_providers,
            'tariffs_analytics': tariffs_analytics,
            'promocodes_analytics': promocodes_analytics,
            'comparison': {
                'users': {
                    'today': new_users_today,
                    'yesterday': new_users_yesterday,
                    'this_week': new_users_this_week,
                    'last_week': new_users_last_week,
                    'this_month': new_users_this_month,
                    'last_month': new_users_last_month
                },
                'payments': {
                    'today': payments_today,
                    'yesterday': payments_yesterday,
                    'this_week': payments_this_week,
                    'last_week': payments_last_week
                },
                'revenue': {
                    'today': revenue_today,
                    'yesterday': revenue_yesterday
                }
            },
            'trial_stats': trial_stats,
            'referral_stats': referral_stats,
            'conversion_funnel': conversion_funnel,
            'top_users': top_users,
            'custom_date_range': custom_date_range is not None,
            'period': period
        }), 200
        
    except Exception as e:
        print(f"Error in get_analytics: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Internal Server Error", "error": str(e)}), 500


@app.route('/api/admin/sales', methods=['GET'])
@admin_required
def get_sales(current_admin):
    """Получить список всех продаж с информацией о пользователе и тарифе"""
    try:
        limit = request.args.get('limit', type=int) or 50
        offset = request.args.get('offset', type=int) or 0
        
        # Получаем платежи с информацией о пользователе и тарифе (включая пополнения баланса)
        payments = db.session.query(
            Payment,
            User,
            Tariff,
            PromoCode
        ).join(
            User, Payment.user_id == User.id
        ).outerjoin(
            Tariff, Payment.tariff_id == Tariff.id
        ).outerjoin(
            PromoCode, Payment.promo_code_id == PromoCode.id
        ).filter(
            Payment.status == 'PAID'
        ).order_by(
            Payment.created_at.desc()
        ).limit(limit).offset(offset).all()
        
        sales_list = []
        for payment, user, tariff, promo in payments:
            # Если это пополнение баланса (tariff_id == None)
            if payment.tariff_id is None:
                sales_list.append({
                    "id": payment.id,
                    "order_id": payment.order_id,
                    "date": payment.created_at.isoformat() if payment.created_at else None,
                    "amount": payment.amount,
                    "currency": payment.currency,
                    "status": payment.status,
                    "payment_provider": payment.payment_provider or 'crystalpay',
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "telegram_id": user.telegram_id,
                        "telegram_username": user.telegram_username
                    },
                    "tariff": None,  # Пополнение баланса
                    "is_balance_topup": True,  # Флаг пополнения баланса
                    "promo_code": promo.code if promo else None
                })
            else:
                # Обычная покупка тарифа
                sales_list.append({
                    "id": payment.id,
                    "order_id": payment.order_id,
                    "date": payment.created_at.isoformat() if payment.created_at else None,
                    "amount": payment.amount,
                    "currency": payment.currency,
                    "status": payment.status,
                    "payment_provider": payment.payment_provider or 'crystalpay',
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "telegram_id": user.telegram_id,
                        "telegram_username": user.telegram_username
                    },
                    "tariff": {
                        "id": tariff.id if tariff else None,
                        "name": tariff.name if tariff else None,
                        "duration_days": tariff.duration_days if tariff else None
                    },
                    "is_balance_topup": False,
                    "promo_code": promo.code if promo else None
                })
        
        # Логируем для диагностики
        print(f"Sales query: found {len(sales_list)} sales, total payments with PAID status: {Payment.query.filter(Payment.status == 'PAID').count()}")
        
        return jsonify(sales_list), 200
    except Exception as e:
        print(f"Error getting sales: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Failed to get sales", "message": str(e)}), 500


# ============================================================================
# SQUADS & NODES
# ============================================================================

@app.route('/api/admin/squads', methods=['GET'])
@admin_required
def get_squads(current_admin):
    """Получить список сквадов"""
    try:
        headers = {"Authorization": f"Bearer {os.getenv('ADMIN_TOKEN')}"}
        resp = requests.get(f"{os.getenv('API_URL')}/api/internal-squads", headers=headers, timeout=10)
        resp.raise_for_status()
        
        data = resp.json()
        if isinstance(data, dict) and 'response' in data:
            response_data = data['response']
            if isinstance(response_data, dict) and 'internalSquads' in response_data:
                squads_list = response_data['internalSquads']
            else:
                squads_list = response_data if isinstance(response_data, list) else []
        elif isinstance(data, list):
            squads_list = data
        else:
            squads_list = []
        
        cache.set('squads_list', squads_list, timeout=300)
        return jsonify(squads_list), 200
    except requests.exceptions.RequestException:
        cached = cache.get('squads_list')
        return jsonify(cached if cached else []), 200
    except Exception:
        cached = cache.get('squads_list')
        return jsonify(cached if cached else []), 200


@app.route('/api/admin/internal-squads', methods=['GET'])
@admin_required
def get_internal_squads(current_admin):
    """Альтернативный путь для сквадов"""
    return get_squads(current_admin)


@app.route('/api/admin/nodes', methods=['GET'])
@admin_required
def get_nodes(current_admin):
    """Получить список нод"""
    try:
        headers = {"Authorization": f"Bearer {os.getenv('ADMIN_TOKEN')}"}
        resp = requests.get(f"{os.getenv('API_URL')}/api/nodes", headers=headers, timeout=10)
        resp.raise_for_status()
        
        data = resp.json()
        nodes_list = data.get('response', data) if isinstance(data, dict) else data
        if not isinstance(nodes_list, list):
            nodes_list = []
        
        cache.set('nodes_list', nodes_list, timeout=300)
        return jsonify(nodes_list), 200
    except requests.exceptions.RequestException:
        cached = cache.get('nodes_list')
        return jsonify(cached if cached else []), 200
    except Exception:
        cached = cache.get('nodes_list')
        return jsonify(cached if cached else []), 200


@app.route('/api/admin/nodes/<uuid>/restart', methods=['POST'])
@admin_required
def restart_node(current_admin, uuid):
    """Перезапустить ноду"""
    try:
        headers, cookies = get_remnawave_headers()
        requests.post(f"{os.getenv('API_URL')}/api/nodes/{uuid}/restart", headers=headers, cookies=cookies)
        return jsonify({"message": "Node restart initiated"}), 200
    except Exception:
        return jsonify({"message": "Failed to restart node"}), 500


@app.route('/api/admin/nodes/restart-all', methods=['POST'])
@admin_required
def restart_all_nodes(current_admin):
    """Перезапустить все ноды"""
    try:
        headers, cookies = get_remnawave_headers()
        requests.post(f"{os.getenv('API_URL')}/api/nodes/restart-all", headers=headers, cookies=cookies)
        return jsonify({"message": "All nodes restart initiated"}), 200
    except Exception:
        return jsonify({"message": "Failed to restart all nodes"}), 500


@app.route('/api/admin/nodes/<uuid>/enable', methods=['POST'])
@admin_required
def enable_node(current_admin, uuid):
    """Включить конкретную ноду"""
    try:
        headers = {"Authorization": f"Bearer {os.getenv('ADMIN_TOKEN')}"}
        resp = requests.post(
            f"{os.getenv('API_URL')}/api/nodes/{uuid}/actions/enable",
            headers=headers,
            timeout=30
        )
        resp.raise_for_status()
        
        # Очищаем кэш нод после изменения
        cache.delete('nodes_list')
        
        data = resp.json()
        return jsonify({"message": "Node enabled", "response": data}), 200
    except requests.exceptions.RequestException as e:
        return jsonify({"error": "Failed to enable node", "message": str(e)}), 500
    except Exception as e:
        return jsonify({"error": "Internal error", "message": str(e)}), 500


@app.route('/api/admin/nodes/<uuid>/disable', methods=['POST'])
@admin_required
def disable_node(current_admin, uuid):
    """Отключить конкретную ноду"""
    try:
        headers = {"Authorization": f"Bearer {os.getenv('ADMIN_TOKEN')}"}
        resp = requests.post(
            f"{os.getenv('API_URL')}/api/nodes/{uuid}/actions/disable",
            headers=headers,
            timeout=30
        )
        resp.raise_for_status()
        
        # Очищаем кэш нод после изменения
        cache.delete('nodes_list')
        
        data = resp.json()
        return jsonify({"message": "Node disabled", "response": data}), 200
    except requests.exceptions.RequestException as e:
        return jsonify({"error": "Failed to disable node", "message": str(e)}), 500
    except Exception as e:
        return jsonify({"error": "Internal error", "message": str(e)}), 500


# ============================================================================
# SYSTEM SETTINGS
# ============================================================================

@app.route('/api/admin/system-settings', methods=['GET', 'POST'])
@admin_required
def system_settings(current_admin):
    """Системные настройки"""
    import json
    
    s = SystemSetting.query.first() or SystemSetting(id=1)
    if not s.id: 
        db.session.add(s)
        db.session.commit()
        # Устанавливаем значения по умолчанию
        if s.show_language_currency_switcher is None:
            s.show_language_currency_switcher = True
        if not s.active_languages or s.active_languages.strip() == '':
            s.active_languages = '["ru","ua","en","cn"]'
        if not s.active_currencies or s.active_currencies.strip() == '':
            s.active_currencies = '["uah","rub","usd"]'
        db.session.commit()
    
    if request.method == 'GET':
        # Парсим JSON массивы
        try:
            active_languages = json.loads(s.active_languages) if s.active_languages else ["ru", "ua", "en", "cn"]
        except:
            active_languages = ["ru", "ua", "en", "cn"]
        
        try:
            active_currencies = json.loads(s.active_currencies) if s.active_currencies else ["uah", "rub", "usd"]
        except:
            active_currencies = ["uah", "rub", "usd"]
        
        # Автозаполнение NULL значений в БД
        needs_save = False
        if not s.active_languages:
            s.active_languages = '["ru","ua","en","cn"]'
            needs_save = True
        if not s.active_currencies:
            s.active_currencies = '["uah","rub","usd"]'
            needs_save = True
        if needs_save:
            try:
                db.session.commit()
            except:
                pass
        
        return jsonify({
            "default_language": s.default_language,
            "default_currency": s.default_currency,
            "show_language_currency_switcher": s.show_language_currency_switcher if s.show_language_currency_switcher is not None else True,
            "active_languages": active_languages,
            "active_currencies": active_currencies,
            # Цвета светлой темы
            "theme_primary_color": getattr(s, 'theme_primary_color', '#3f69ff') or '#3f69ff',
            "theme_bg_primary": getattr(s, 'theme_bg_primary', '#f8fafc') or '#f8fafc',
            "theme_bg_secondary": getattr(s, 'theme_bg_secondary', '#eef2ff') or '#eef2ff',
            "theme_text_primary": getattr(s, 'theme_text_primary', '#0f172a') or '#0f172a',
            "theme_text_secondary": getattr(s, 'theme_text_secondary', '#94a3b8') or '#94a3b8',
            # Цвета темной темы
            "theme_primary_color_dark": getattr(s, 'theme_primary_color_dark', '#3f69ff') or '#3f69ff',
            "theme_bg_primary_dark": getattr(s, 'theme_bg_primary_dark', '#0f172a') or '#0f172a',
            "theme_bg_secondary_dark": getattr(s, 'theme_bg_secondary_dark', '#1e293b') or '#1e293b',
            "theme_text_primary_dark": getattr(s, 'theme_text_primary_dark', '#f1f5f9') or '#f1f5f9',
            "theme_text_secondary_dark": getattr(s, 'theme_text_secondary_dark', '#94a3b8') or '#94a3b8'
        }), 200
    
    # POST - обновление
    try:
        data = request.json
        if data is None:
            return jsonify({"message": "Request body is required (JSON)"}), 400
        # Убеждаемся, что запись есть в БД (иначе commit ничего не сохранит)
        db.session.add(s)
        db.session.flush()
        if 'default_language' in data and data['default_language'] not in (None, ''):
            val = data['default_language']
            if val not in ['ru', 'ua', 'cn', 'en']:
                msg = "Invalid language"
                print(f"POST /api/admin/system-settings 400: {msg}")
                return jsonify({"message": msg}), 400
            s.default_language = val
        if 'default_currency' in data and data['default_currency'] not in (None, ''):
            val = data['default_currency']
            if val not in ['uah', 'rub', 'usd']:
                msg = "Invalid currency"
                print(f"POST /api/admin/system-settings 400: {msg}")
                return jsonify({"message": msg}), 400
            s.default_currency = val
        if 'show_language_currency_switcher' in data:
            s.show_language_currency_switcher = bool(data['show_language_currency_switcher'])
        if 'active_languages' in data:
            raw = data['active_languages']
            if raw is None:
                pass  # не меняем
            else:
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        raw = None
                if isinstance(raw, list):
                    valid_langs = ['ru', 'ua', 'en', 'cn']
                    filtered_langs = [lang for lang in raw if lang in valid_langs]
                    if len(filtered_langs) == 0:
                        msg = "At least one language must be active"
                        print(f"POST /api/admin/system-settings 400: {msg}")
                        return jsonify({"message": msg}), 400
                    s.active_languages = json.dumps(filtered_langs)
                elif raw is not None:
                    msg = "active_languages must be an array"
                    print(f"POST /api/admin/system-settings 400: {msg} (got {type(raw).__name__})")
                    return jsonify({"message": msg}), 400
        if 'active_currencies' in data:
            raw = data['active_currencies']
            if raw is None:
                pass
            else:
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        raw = None
                if isinstance(raw, list):
                    valid_currs = ['uah', 'rub', 'usd']
                    filtered_currs = [curr for curr in raw if curr in valid_currs]
                    if len(filtered_currs) == 0:
                        msg = "At least one currency must be active"
                        print(f"POST /api/admin/system-settings 400: {msg}")
                        return jsonify({"message": msg}), 400
                    s.active_currencies = json.dumps(filtered_currs)
                elif raw is not None:
                    msg = "active_currencies must be an array"
                    print(f"POST /api/admin/system-settings 400: {msg} (got {type(raw).__name__})")
                    return jsonify({"message": msg}), 400
        
        # Обработка цветов темы
        def is_valid_hex(color):
            return color and color.startswith('#') and len(color) in [4, 7]
        
        # Светлая тема
        if 'theme_primary_color' in data and is_valid_hex(data['theme_primary_color']):
            s.theme_primary_color = data['theme_primary_color']
        if 'theme_bg_primary' in data and is_valid_hex(data['theme_bg_primary']):
            s.theme_bg_primary = data['theme_bg_primary']
        if 'theme_bg_secondary' in data and is_valid_hex(data['theme_bg_secondary']):
            s.theme_bg_secondary = data['theme_bg_secondary']
        if 'theme_text_primary' in data and is_valid_hex(data['theme_text_primary']):
            s.theme_text_primary = data['theme_text_primary']
        if 'theme_text_secondary' in data and is_valid_hex(data['theme_text_secondary']):
            s.theme_text_secondary = data['theme_text_secondary']
        
        # Темная тема
        if 'theme_primary_color_dark' in data and is_valid_hex(data['theme_primary_color_dark']):
            s.theme_primary_color_dark = data['theme_primary_color_dark']
        if 'theme_bg_primary_dark' in data and is_valid_hex(data['theme_bg_primary_dark']):
            s.theme_bg_primary_dark = data['theme_bg_primary_dark']
        if 'theme_bg_secondary_dark' in data and is_valid_hex(data['theme_bg_secondary_dark']):
            s.theme_bg_secondary_dark = data['theme_bg_secondary_dark']
        if 'theme_text_primary_dark' in data and is_valid_hex(data['theme_text_primary_dark']):
            s.theme_text_primary_dark = data['theme_text_primary_dark']
        if 'theme_text_secondary_dark' in data and is_valid_hex(data['theme_text_secondary_dark']):
            s.theme_text_secondary_dark = data['theme_text_secondary_dark']
        
        db.session.commit()
        print(f"[admin/system-settings] Saved default_language={s.default_language} default_currency={s.default_currency}")
        return jsonify({"message": "System settings updated successfully"}), 200

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Internal Server Error: {str(e)}"}), 500


# ============================================================================
# BRANDING
# ============================================================================

@app.route('/api/admin/branding', methods=['GET', 'POST'])
@admin_required
def admin_branding_settings(current_admin):
    """Настройки брендинга"""
    b = BrandingSetting.query.first()
    if not b:
        b = BrandingSetting(id=1)
        db.session.add(b)
        db.session.commit()
    
    if request.method == 'GET':
        # Парсим JSON для названий функций тарифов
        tariff_features_names = {}
        if hasattr(b, 'tariff_features_names') and b.tariff_features_names:
            try:
                tariff_features_names = json.loads(b.tariff_features_names)
            except:
                pass
        
        return jsonify({
            # Основные настройки
            "logo_url": b.logo_url or "",
            "favicon_url": getattr(b, 'favicon_url', None) or "",
            "site_name": b.site_name or "",
            "site_subtitle": b.site_subtitle or "",
            
            # Тексты для авторизации
            "login_welcome_text": b.login_welcome_text or "",
            "register_welcome_text": b.register_welcome_text or "",
            "footer_text": b.footer_text or "",
            
            # Дашборд - секции
            "dashboard_servers_title": b.dashboard_servers_title or "",
            "dashboard_servers_description": b.dashboard_servers_description or "",
            "dashboard_tariffs_title": b.dashboard_tariffs_title or "",
            "dashboard_tariffs_description": b.dashboard_tariffs_description or "",
            "dashboard_tagline": b.dashboard_tagline or "",
            "dashboard_referrals_title": getattr(b, 'dashboard_referrals_title', None) or "",
            "dashboard_referrals_description": getattr(b, 'dashboard_referrals_description', None) or "",
            "dashboard_support_title": getattr(b, 'dashboard_support_title', None) or "",
            "dashboard_support_description": getattr(b, 'dashboard_support_description', None) or "",
            
            # Названия для тарифов
            "tariff_tier_basic_name": getattr(b, 'tariff_tier_basic_name', None) or "",
            "tariff_tier_pro_name": getattr(b, 'tariff_tier_pro_name', None) or "",
            "tariff_tier_elite_name": getattr(b, 'tariff_tier_elite_name', None) or "",
            
            # Названия функций тарифов
            "tariff_features_names": tariff_features_names,
            
            # Тексты кнопок
            "button_subscribe_text": getattr(b, 'button_subscribe_text', None) or "",
            "button_buy_text": getattr(b, 'button_buy_text', None) or "",
            "button_connect_text": getattr(b, 'button_connect_text', None) or "",
            "button_share_text": getattr(b, 'button_share_text', None) or "",
            "button_copy_text": getattr(b, 'button_copy_text', None) or "",
            
            # Мета-теги
            "meta_title": getattr(b, 'meta_title', None) or "",
            "meta_description": getattr(b, 'meta_description', None) or "",
            "meta_keywords": getattr(b, 'meta_keywords', None) or "",
            
            # Быстрое скачивание
            "quick_download_enabled": getattr(b, 'quick_download_enabled', True),
            "quick_download_windows_url": getattr(b, 'quick_download_windows_url', '') or "",
            "quick_download_android_url": getattr(b, 'quick_download_android_url', '') or "",
            "quick_download_macos_url": getattr(b, 'quick_download_macos_url', '') or "",
            "quick_download_ios_url": getattr(b, 'quick_download_ios_url', '') or "",
            "quick_download_profile_deeplink": getattr(b, 'quick_download_profile_deeplink', '') or "",
            
            # Дополнительные тексты
            "subscription_active_text": getattr(b, 'subscription_active_text', None) or "",
            "subscription_expired_text": getattr(b, 'subscription_expired_text', None) or "",
            "subscription_trial_text": getattr(b, 'subscription_trial_text', None) or "",
            "balance_label_text": getattr(b, 'balance_label_text', None) or "",
            "referral_code_label_text": getattr(b, 'referral_code_label_text', None) or "",
        }), 200
    
    try:
        data = request.json
        
        # Убеждаемся, что объект b существует и добавлен в сессию
        if not b:
            b = BrandingSetting(id=1)
            db.session.add(b)
            db.session.flush()  # Получаем id без коммита
        
        # Если объект новый (без id), сначала сохраняем его
        if not b.id:
            db.session.add(b)
            db.session.flush()  # Получаем id без коммита
        
        # Список всех полей для обновления
        fields = [
            'logo_url', 'favicon_url', 'site_name', 'site_subtitle',
            'login_welcome_text', 'register_welcome_text', 'footer_text',
            'dashboard_servers_title', 'dashboard_servers_description',
            'dashboard_tariffs_title', 'dashboard_tariffs_description',
            'dashboard_tagline', 'dashboard_referrals_title',
            'dashboard_referrals_description', 'dashboard_support_title',
            'dashboard_support_description',
            'tariff_tier_basic_name', 'tariff_tier_pro_name', 'tariff_tier_elite_name',
            'button_subscribe_text', 'button_buy_text', 'button_connect_text',
            'button_share_text', 'button_copy_text',
            'meta_title', 'meta_description', 'meta_keywords',
            'quick_download_enabled', 'quick_download_windows_url',
            'quick_download_android_url', 'quick_download_macos_url',
            'quick_download_ios_url', 'quick_download_profile_deeplink',
            'subscription_active_text', 'subscription_expired_text',
            'subscription_trial_text', 'balance_label_text', 'referral_code_label_text'
        ]
        
        # Булевые поля, которые не должны быть None
        boolean_fields = {'quick_download_enabled'}
        
        for key in fields:
            if key in data:
                # Для булевых полей сохраняем значение как есть (включая False)
                if key in boolean_fields:
                    setattr(b, key, bool(data[key]) if data[key] is not None else True)
                else:
                    setattr(b, key, data[key] if data[key] else None)
        
        # Обработка JSON поля для названий функций тарифов
        if 'tariff_features_names' in data:
            if isinstance(data['tariff_features_names'], dict):
                b.tariff_features_names = json.dumps(data['tariff_features_names'], ensure_ascii=False)
            elif isinstance(data['tariff_features_names'], str):
                b.tariff_features_names = data['tariff_features_names']
            else:
                b.tariff_features_names = None
        
        # Используем merge для гарантии, что объект в сессии
        db.session.merge(b)
        db.session.commit()
        app.logger.info(f"✅ Branding settings saved successfully (ID: {b.id})")
        return jsonify({"message": "Branding settings updated successfully"}), 200
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating branding: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Internal Server Error: {str(e)}"}), 500


# ============================================================================
# EMAIL SETTINGS (шаблоны писем и имя отправителя)
# ============================================================================

def _read_default_email_template(template_name):
    """Прочитать содержимое шаблона из папки templates (для «по умолчанию»)."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, 'templates', template_name)
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
    except Exception:
        pass
    return ""


@app.route('/api/admin/email-settings', methods=['GET', 'POST'])
@admin_required
def admin_email_settings(current_admin):
    """Настройки почты: имя отправителя и шаблоны писем (верификация, рассылка)."""
    es = EmailSetting.query.first()
    if not es:
        es = EmailSetting(id=1)
        db.session.add(es)
        db.session.commit()

    if request.method == 'GET':
        default_verification = _read_default_email_template('email_verification.html')
        default_broadcast = _read_default_email_template('email_broadcast.html')
        return jsonify({
            "mail_sender_name": es.mail_sender_name or "",
            "verification_subject": es.verification_subject or "Подтвердите email",
            "verification_body_html": es.verification_body_html or "",
            "broadcast_body_html": es.broadcast_body_html or "",
            "default_verification_body_html": default_verification,
            "default_broadcast_body_html": default_broadcast,
        }), 200

    # POST
    try:
        data = request.json or {}
        if "mail_sender_name" in data:
            es.mail_sender_name = (data["mail_sender_name"] or "").strip() or None
        if "verification_subject" in data:
            es.verification_subject = (data["verification_subject"] or "").strip() or None
        if "verification_body_html" in data:
            es.verification_body_html = (data["verification_body_html"] or "").strip() or None
        if "broadcast_body_html" in data:
            es.broadcast_body_html = (data["broadcast_body_html"] or "").strip() or None
        db.session.commit()
        return jsonify({"message": "Email settings saved"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": str(e)}), 500


# ============================================================================
# BOT CONFIG
# ============================================================================

@app.route('/api/admin/bot-config', methods=['GET', 'POST'])
@admin_required
def admin_bot_config_endpoint(current_admin):
    """Конфигурация Telegram бота"""
    config = BotConfig.query.first()
    if not config:
        config = BotConfig(id=1)
        db.session.add(config)
        db.session.commit()
    
    if request.method == 'GET':
        default_buttons_order = ["trial", "connect", "status", "tariffs", "options", "referrals", "support", "settings", "webapp"]
        return jsonify({
            "service_name": config.service_name or _get_site_name() or "Панель",
            "bot_username": config.bot_username or "",
            "support_url": config.support_url or "",
            "support_bot_username": config.support_bot_username or "",
            "show_connect_button": getattr(config, 'show_connect_button', True) if getattr(config, 'show_connect_button', None) is not None else True,
            "show_status_button": getattr(config, 'show_status_button', True) if getattr(config, 'show_status_button', None) is not None else True,
            "show_tariffs_button": getattr(config, 'show_tariffs_button', True) if getattr(config, 'show_tariffs_button', None) is not None else True,
            "show_options_button": getattr(config, 'show_options_button', True) if getattr(config, 'show_options_button', None) is not None else True,
            "show_webapp_button": config.show_webapp_button if config.show_webapp_button is not None else True,
            "show_trial_button": config.show_trial_button if config.show_trial_button is not None else True,
            "show_referral_button": config.show_referral_button if config.show_referral_button is not None else True,
            "show_support_button": config.show_support_button if config.show_support_button is not None else True,
            "show_servers_button": config.show_servers_button if config.show_servers_button is not None else True,
            "show_agreement_button": config.show_agreement_button if config.show_agreement_button is not None else True,
            "show_offer_button": config.show_offer_button if config.show_offer_button is not None else True,
            "show_topup_button": config.show_topup_button if config.show_topup_button is not None else True,
            "show_settings_button": getattr(config, 'show_settings_button', True) if getattr(config, 'show_settings_button', None) is not None else True,
            "trial_days": config.trial_days or 3,
            "translations_ru": json.loads(config.translations_ru) if config.translations_ru else {},
            "translations_ua": json.loads(config.translations_ua) if config.translations_ua else {},
            "translations_en": json.loads(config.translations_en) if config.translations_en else {},
            "translations_cn": json.loads(config.translations_cn) if config.translations_cn else {},
            "welcome_message_ru": config.welcome_message_ru or "",
            "welcome_message_ua": config.welcome_message_ua or "",
            "welcome_message_en": config.welcome_message_en or "",
            "welcome_message_cn": config.welcome_message_cn or "",
            "user_agreement_ru": config.user_agreement_ru or "",
            "user_agreement_ua": config.user_agreement_ua or "",
            "user_agreement_en": config.user_agreement_en or "",
            "user_agreement_cn": config.user_agreement_cn or "",
            "offer_text_ru": config.offer_text_ru or "",
            "offer_text_ua": config.offer_text_ua or "",
            "offer_text_en": config.offer_text_en or "",
            "offer_text_cn": config.offer_text_cn or "",
            "require_channel_subscription": getattr(config, 'require_channel_subscription', False),
            "channel_id": getattr(config, 'channel_id', '') or "",
            "channel_url": getattr(config, 'channel_url', '') or "",
            "channel_subscription_text_ru": getattr(config, 'channel_subscription_text_ru', '') or "",
            "channel_subscription_text_ua": getattr(config, 'channel_subscription_text_ua', '') or "",
            "channel_subscription_text_en": getattr(config, 'channel_subscription_text_en', '') or "",
            "channel_subscription_text_cn": getattr(config, 'channel_subscription_text_cn', '') or "",
            "bot_link_for_miniapp": getattr(config, 'bot_link_for_miniapp', '') or "",
            "buttons_order": json.loads(config.buttons_order) if hasattr(config, 'buttons_order') and config.buttons_order else default_buttons_order,
            "bot_page_logos": json.loads(config.bot_page_logos) if getattr(config, 'bot_page_logos', None) else {},
            "updated_at": config.updated_at.isoformat() if config.updated_at else None
        }), 200
    
    try:
        data = request.json
        
        # Булевые поля, которые должны быть правильно обработаны
        boolean_fields = {
            'show_connect_button', 'show_status_button', 'show_tariffs_button', 'show_options_button', 'show_settings_button',
            'show_webapp_button', 'show_trial_button', 'show_referral_button',
            'show_support_button', 'show_servers_button', 'show_agreement_button',
            'show_offer_button', 'show_topup_button', 'require_channel_subscription'
        }
        
        # Простые поля
        simple_fields = ['service_name', 'bot_username', 'support_url', 'support_bot_username',
                        'show_connect_button', 'show_status_button', 'show_tariffs_button', 'show_options_button', 'show_settings_button',
                        'show_webapp_button', 'show_trial_button', 'show_referral_button',
                        'show_support_button', 'show_servers_button', 'show_agreement_button',
                        'show_offer_button', 'show_topup_button', 'trial_days',
                        'welcome_message_ru', 'welcome_message_ua', 'welcome_message_en', 'welcome_message_cn',
                        'user_agreement_ru', 'user_agreement_ua', 'user_agreement_en', 'user_agreement_cn',
                        'offer_text_ru', 'offer_text_ua', 'offer_text_en', 'offer_text_cn',
                        'require_channel_subscription', 'channel_id', 'channel_url',
                        'channel_subscription_text_ru', 'channel_subscription_text_ua',
                        'channel_subscription_text_en', 'channel_subscription_text_cn',
                        'bot_link_for_miniapp']
        
        for field in simple_fields:
            if field in data:
                # Для булевых полей преобразуем значение в bool
                if field in boolean_fields:
                    value = data[field]
                    # Обрабатываем разные форматы: bool, str "true"/"false", int 0/1
                    if isinstance(value, bool):
                        setattr(config, field, value)
                    elif isinstance(value, str):
                        setattr(config, field, value.lower() in ('true', '1', 'yes', 'on'))
                    elif isinstance(value, (int, float)):
                        setattr(config, field, bool(value))
                    else:
                        setattr(config, field, False)
                elif field == 'channel_id':
                    # Нормализуем channel_id: убираем @ если есть
                    value = data[field]
                    if isinstance(value, str):
                        value = value.strip()
                        if value.startswith('@'):
                            value = value[1:]
                        setattr(config, field, value)
                    else:
                        setattr(config, field, str(value) if value else '')
                else:
                    setattr(config, field, data[field])
        
        # JSON поля
        json_fields = ['translations_ru', 'translations_ua', 'translations_en', 'translations_cn', 'buttons_order', 'bot_page_logos']
        for field in json_fields:
            if field in data:
                setattr(config, field, json.dumps(data[field], ensure_ascii=False) if data[field] else None)
        
        db.session.commit()
        
        # Очищаем кеш конфигурации бота в старом боте
        try:
            import sys
            import os
            # Пытаемся импортировать функцию очистки кеша из client_bot
            # Это работает только если бот запущен в том же процессе
            # В противном случае кеш обновится автоматически через TTL
            bot_module_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'client_bot.py')
            if os.path.exists(bot_module_path):
                # Пытаемся очистить кеш через глобальную переменную
                # Это работает только если модуль уже загружен
                if 'client_bot' in sys.modules:
                    client_bot = sys.modules['client_bot']
                    if hasattr(client_bot, 'clear_bot_config_cache'):
                        client_bot.clear_bot_config_cache()
        except Exception as e:
            # Игнорируем ошибки - кеш обновится автоматически через TTL
            pass
        
        return jsonify({"message": "Bot configuration updated successfully"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Internal Server Error"}), 500


# Ключи страниц бота для логотипов (для загрузки и отображения в админке)
BOT_LOGO_PAGE_KEYS = [
    ("default", "По умолчанию (общий логотип)"),
    ("main_menu", "Главное меню"),
    ("subscription_status", "Статус подписки"),
    ("subscription_menu", "Моя подписка"),
    ("tariffs", "Тарифы"),
    ("options", "Опции"),
    ("referrals", "Рефералка"),
    ("support_menu", "Поддержка"),
    ("settings", "Настройки"),
    ("topup", "Пополнение баланса"),
    ("configs", "Конфиги"),
    ("servers", "Серверы"),
    ("agreement", "Соглашение"),
    ("offer", "Оферта"),
    ("payment", "Оплата"),
    ("trial", "Триал"),
    ("start", "Приветствие /start"),
]


@app.route('/api/admin/bot-logos', methods=['GET'])
@admin_required
def admin_bot_logos_list(current_admin):
    """Список страниц и текущих логотипов бота"""
    config = BotConfig.query.first()
    logos = {}
    if config and getattr(config, 'bot_page_logos', None):
        try:
            logos = json.loads(config.bot_page_logos)
        except Exception:
            logos = {}
    return jsonify({
        "pages": [{"key": k, "label": v} for k, v in BOT_LOGO_PAGE_KEYS],
        "bot_page_logos": logos
    }), 200


@app.route('/api/admin/bot-logos/upload', methods=['POST'])
@admin_required
def admin_bot_logos_upload(current_admin):
    """Загрузка логотипа для страницы бота. Form: page_key, file (image)"""
    page_key = (request.form.get('page_key') or '').strip()
    if not page_key:
        return jsonify({"message": "page_key is required"}), 400
    allowed = {k for k, _ in BOT_LOGO_PAGE_KEYS}
    if page_key not in allowed:
        return jsonify({"message": f"Invalid page_key. Allowed: {sorted(allowed)}"}), 400
    if 'file' not in request.files and 'logo' not in request.files:
        return jsonify({"message": "file or logo is required"}), 400
    file = request.files.get('file') or request.files.get('logo')
    if not file or file.filename == '':
        return jsonify({"message": "No file selected"}), 400
    ext = os.path.splitext(file.filename)[1].lower() or '.png'
    if ext not in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
        ext = '.png'
    # Корень проекта (client_bot.py ищет логотипы в project_root/instance/uploads/bot_logos)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    upload_dir = os.path.join(root, 'instance', 'uploads', 'bot_logos')
    os.makedirs(upload_dir, exist_ok=True)
    safe_key = page_key.replace('/', '_').replace('..', '')
    filename = f"{safe_key}{ext}"
    filepath = os.path.join(upload_dir, filename)
    try:
        file.save(filepath)
    except Exception as e:
        return jsonify({"message": f"Failed to save file: {str(e)}"}), 500
    relative_path = os.path.join('instance', 'uploads', 'bot_logos', filename).replace('\\', '/')
    config = BotConfig.query.first()
    if not config:
        config = BotConfig(id=1)
        db.session.add(config)
        db.session.flush()
    logos = {}
    if getattr(config, 'bot_page_logos', None):
        try:
            logos = json.loads(config.bot_page_logos)
        except Exception:
            pass
    logos[page_key] = relative_path
    config.bot_page_logos = json.dumps(logos, ensure_ascii=False)
    db.session.commit()
    try:
        if 'client_bot' in __import__('sys').modules:
            cb = __import__('sys').modules.get('client_bot')
            if cb and getattr(cb, 'clear_bot_config_cache', None):
                cb.clear_bot_config_cache()
    except Exception:
        pass
    return jsonify({"message": "Logo uploaded", "page_key": page_key, "path": relative_path}), 200


# ============================================================================
# TARIFFS
# ============================================================================

@app.route('/api/admin/tariffs', methods=['GET'])
@admin_required
def admin_tariffs(current_admin):
    """Список тарифов"""
    try:
        tariffs = Tariff.query.all()
        result = []
        for t in tariffs:
            tariff_data = {
                'id': t.id, 'name': t.name, 'duration_days': t.duration_days,
                'price_uah': t.price_uah, 'price_rub': t.price_rub, 'price_usd': t.price_usd,
                'squad_id': t.squad_id,  # Для обратной совместимости
                'squad_ids': t.get_squad_ids() if hasattr(t, 'get_squad_ids') else (t.squad_ids if hasattr(t, 'squad_ids') else None),
                'traffic_limit_bytes': t.traffic_limit_bytes,
                'hwid_device_limit': t.hwid_device_limit, 'tier': t.tier,
                'badge': t.badge, 'bonus_days': t.bonus_days
            }
            result.append(tariff_data)
        return jsonify(result), 200
    except Exception as e:
        print(f"[TARIFF] Error in admin_tariffs: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Internal Server Error: {str(e)}"}), 500


@app.route('/api/admin/tariffs', methods=['POST'])
@admin_required
def create_tariff(current_admin):
    """Создание тарифа"""
    try:
        data = request.json
        required = ['name', 'duration_days', 'price_uah', 'price_rub', 'price_usd']
        for field in required:
            if field not in data:
                return jsonify({"message": f"Field {field} is required"}), 400

        # Валидируем tier (если передан)
        tier = data.get('tier')
        if tier is not None and str(tier).strip() != '':
            tier_code = str(tier).strip().lower()
            level = TariffLevel.query.filter_by(code=tier_code, is_active=True).first()
            if not level:
                return jsonify({"message": f"Unknown tariff level: {tier_code}"}), 400
            tier = tier_code
        else:
            tier = None

        tariff = Tariff(
            name=data['name'], duration_days=data['duration_days'],
            price_uah=data['price_uah'], price_rub=data['price_rub'], price_usd=data['price_usd'],
            squad_id=data.get('squad_id'),  # Для обратной совместимости
            traffic_limit_bytes=data.get('traffic_limit_bytes', 0),
            hwid_device_limit=data.get('hwid_device_limit', 0), tier=tier,
            badge=data.get('badge'), bonus_days=data.get('bonus_days', 0)
        )
        
        # Устанавливаем squad_ids если передан массив
        if 'squad_ids' in data:
            if data['squad_ids'] and len(data['squad_ids']) > 0:
                tariff.set_squad_ids(data['squad_ids'])
            else:
                tariff.squad_ids = None
        elif 'squad_id' in data and data['squad_id']:
            # Обратная совместимость: если передан squad_id, сохраняем его в squad_ids
            tariff.set_squad_ids([data['squad_id']])
        
        db.session.add(tariff)
        db.session.commit()
        
        # Очищаем кэш тарифов (Flask-Caching использует ключи вида 'flask_cache_view//api/public/tariffs')
        cache.delete('flask_cache_view//api/public/tariffs')
        cache.delete('view//api/public/tariffs')
        cache.delete('public_tariffs')
        # Также очищаем все ключи с 'tariff' в названии через Redis напрямую
        try:
            import redis
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", 6379))
            redis_db = int(os.getenv("REDIS_DB", 0))
            redis_password = os.getenv("REDIS_PASSWORD", None)
            r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password, decode_responses=True)
            keys = r.keys('*tariff*')
            if keys:
                r.delete(*keys)
                print(f"[CACHE] Deleted {len(keys)} tariff cache keys")
        except Exception as e:
            print(f"[CACHE] Error clearing cache: {e}")
        
        print(f"[TARIFF] Created tariff: id={tariff.id}, name={tariff.name}, squad_ids={tariff.squad_ids}")
        return jsonify({"message": "Tariff created", "tariff_id": tariff.id}), 201
    except Exception as e:
        db.session.rollback()
        print(f"[TARIFF] Error creating tariff: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Internal Server Error: {str(e)}"}), 500


@app.route('/api/admin/tariffs/<int:tariff_id>', methods=['PATCH'])
@app.route('/api/admin/tariffs/<int:id>', methods=['PATCH'])
@admin_required
def update_tariff(current_admin, tariff_id=None, id=None):
    """Обновление тарифа"""
    try:
        tariff_id = tariff_id or id
        tariff = db.session.get(Tariff, tariff_id)
        if not tariff:
            return jsonify({"message": "Tariff not found"}), 404

        data = request.json
        fields = ['name', 'duration_days', 'price_uah', 'price_rub', 'price_usd',
                  'squad_id', 'traffic_limit_bytes', 'hwid_device_limit', 'tier', 'badge', 'bonus_days']
        for field in fields:
            if field in data:
                if field == 'tier':
                    tier = data.get('tier')
                    if tier is None or str(tier).strip() == '':
                        setattr(tariff, 'tier', None)
                    else:
                        tier_code = str(tier).strip().lower()
                        level = TariffLevel.query.filter_by(code=tier_code, is_active=True).first()
                        if not level:
                            return jsonify({"message": f"Unknown tariff level: {tier_code}"}), 400
                        setattr(tariff, 'tier', tier_code)
                else:
                    setattr(tariff, field, data[field])
        
        # Обрабатываем squad_ids отдельно
        if 'squad_ids' in data:
            if data['squad_ids'] and len(data['squad_ids']) > 0:
                tariff.set_squad_ids(data['squad_ids'])
                print(f"[TARIFF] Updated squad_ids for tariff {tariff_id}: {data['squad_ids']}")
            else:
                tariff.squad_ids = None
                print(f"[TARIFF] Cleared squad_ids for tariff {tariff_id}")
        elif 'squad_id' in data and data['squad_id']:
            # Обратная совместимость
            tariff.set_squad_ids([data['squad_id']])
            print(f"[TARIFF] Updated squad_id (legacy) for tariff {tariff_id}: {data['squad_id']}")

        db.session.commit()
        
        # Очищаем кэш тарифов (Flask-Caching использует ключи вида 'flask_cache_view//api/public/tariffs')
        cache.delete('flask_cache_view//api/public/tariffs')
        cache.delete('view//api/public/tariffs')
        cache.delete('public_tariffs')
        # Также очищаем все ключи с 'tariff' в названии через Redis напрямую
        try:
            import redis
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", 6379))
            redis_db = int(os.getenv("REDIS_DB", 0))
            redis_password = os.getenv("REDIS_PASSWORD", None)
            r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password, decode_responses=True)
            keys = r.keys('*tariff*')
            if keys:
                r.delete(*keys)
                print(f"[CACHE] Deleted {len(keys)} tariff cache keys")
        except Exception as e:
            print(f"[CACHE] Error clearing cache: {e}")
        
        print(f"[TARIFF] Updated tariff: id={tariff.id}, name={tariff.name}, squad_ids={tariff.squad_ids}")
        return jsonify({"message": "Tariff updated successfully"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"[TARIFF] Error updating tariff {tariff_id}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Internal Server Error: {str(e)}"}), 500


@app.route('/api/admin/tariffs/<int:tariff_id>', methods=['DELETE'])
@app.route('/api/admin/tariffs/<int:id>', methods=['DELETE'])
@admin_required
def delete_tariff(current_admin, tariff_id=None, id=None):
    """Удаление тарифа"""
    try:
        tariff_id = tariff_id or id
        tariff = db.session.get(Tariff, tariff_id)
        if not tariff:
            return jsonify({"message": "Tariff not found"}), 404
        
        # Перед удалением тарифа обнуляем tariff_id в связанных платежах
        # Это позволяет сохранить историю платежей, но убрать ссылку на удаляемый тариф
        payments_updated = Payment.query.filter_by(tariff_id=tariff_id).update({Payment.tariff_id: None})
        if payments_updated > 0:
            print(f"[TARIFF] Updated {payments_updated} payment(s) to remove tariff reference")
        
        # Теперь можно безопасно удалить тариф
        db.session.delete(tariff)
        db.session.commit()
        
        # Очищаем кэш тарифов (Flask-Caching использует ключи вида 'flask_cache_view//api/public/tariffs')
        cache.delete('flask_cache_view//api/public/tariffs')
        cache.delete('view//api/public/tariffs')
        cache.delete('public_tariffs')
        # Также очищаем все ключи с 'tariff' в названии через Redis напрямую
        try:
            import redis
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", 6379))
            redis_db = int(os.getenv("REDIS_DB", 0))
            redis_password = os.getenv("REDIS_PASSWORD", None)
            r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password, decode_responses=True)
            keys = r.keys('*tariff*')
            if keys:
                r.delete(*keys)
                print(f"[CACHE] Deleted {len(keys)} tariff cache keys")
        except Exception as e:
            print(f"[CACHE] Error clearing cache: {e}")
        
        print(f"[TARIFF] Deleted tariff: id={tariff_id}")
        return jsonify({"message": "Tariff deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        import traceback
        error_trace = traceback.format_exc()
        print(f"[TARIFF] Error deleting tariff {tariff_id}: {e}")
        print(f"[TARIFF] Traceback: {error_trace}")
        return jsonify({
            "message": f"Internal Server Error: {str(e)}"
        }), 500


# ============================================================================
# PURCHASE OPTIONS (Дополнительные опции для покупки)
# ============================================================================

@app.route('/api/admin/options', methods=['GET'])
@admin_required
def get_options(current_admin):
    """Получить список всех опций"""
    try:
        options = PurchaseOption.query.order_by(PurchaseOption.sort_order, PurchaseOption.id).all()
        return jsonify([opt.to_dict() for opt in options]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/options', methods=['POST'])
@admin_required
def create_option(current_admin):
    """Создать новую опцию"""
    try:
        data = request.get_json(silent=True) or {}

        option = PurchaseOption(
            option_type=data.get('option_type', 'traffic'),
            name=data.get('name', ''),
            description=data.get('description', ''),
            value=str(data.get('value', '')),
            squad_uuid=data.get('squad_uuid'),
            unit=data.get('unit', ''),
            price_uah=float(data.get('price_uah', 0) or 0),
            price_rub=float(data.get('price_rub', 0) or 0),
            price_usd=float(data.get('price_usd', 0) or 0),
            is_active=bool(data.get('is_active', True)),
            sort_order=int(data.get('sort_order', 0) or 0),
            icon=data.get('icon', '📦')
        )

        db.session.add(option)
        db.session.commit()

        # Очищаем кэш публичных опций
        try:
            cache.delete('flask_cache_view//api/public/options')
            cache.delete('view//api/public/options')
            cache.delete('public_options')
            cache.delete('flask_cache_view//api/public/purchase-options')
            cache.delete('view//api/public/purchase-options')
            cache.delete('public_purchase_options_grouped')
        except Exception:
            pass

        return jsonify(option.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/options/<int:option_id>', methods=['GET'])
@admin_required
def get_option(current_admin, option_id):
    """Получить опцию по ID"""
    try:
        option = PurchaseOption.query.get(option_id)
        if not option:
            return jsonify({"error": "Option not found"}), 404
        return jsonify(option.to_dict()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/options/<int:option_id>', methods=['PUT'])
@admin_required
def update_option(current_admin, option_id):
    """Обновить опцию"""
    try:
        option = PurchaseOption.query.get(option_id)
        if not option:
            return jsonify({"error": "Option not found"}), 404

        data = request.get_json(silent=True) or {}

        if 'option_type' in data:
            option.option_type = data['option_type']
        if 'name' in data:
            option.name = data['name']
        if 'description' in data:
            option.description = data['description']
        if 'value' in data:
            option.value = str(data['value'])
        if 'squad_uuid' in data:
            option.squad_uuid = data['squad_uuid']
        if 'unit' in data:
            option.unit = data['unit']
        if 'price_uah' in data:
            option.price_uah = float(data['price_uah'] or 0)
        if 'price_rub' in data:
            option.price_rub = float(data['price_rub'] or 0)
        if 'price_usd' in data:
            option.price_usd = float(data['price_usd'] or 0)
        if 'is_active' in data:
            option.is_active = bool(data['is_active'])
        if 'sort_order' in data:
            option.sort_order = int(data['sort_order'] or 0)
        if 'icon' in data:
            option.icon = data['icon']

        db.session.commit()

        try:
            cache.delete('flask_cache_view//api/public/options')
            cache.delete('view//api/public/options')
            cache.delete('public_options')
            cache.delete('flask_cache_view//api/public/purchase-options')
            cache.delete('view//api/public/purchase-options')
            cache.delete('public_purchase_options_grouped')
        except Exception:
            pass

        return jsonify(option.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/options/<int:option_id>', methods=['DELETE'])
@admin_required
def delete_option(current_admin, option_id):
    """Удалить опцию"""
    try:
        option = PurchaseOption.query.get(option_id)
        if not option:
            return jsonify({"error": "Option not found"}), 404

        db.session.delete(option)
        db.session.commit()

        try:
            cache.delete('flask_cache_view//api/public/options')
            cache.delete('view//api/public/options')
            cache.delete('public_options')
            cache.delete('flask_cache_view//api/public/purchase-options')
            cache.delete('view//api/public/purchase-options')
            cache.delete('public_purchase_options_grouped')
        except Exception:
            pass

        return jsonify({"message": "Option deleted"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/options/<int:option_id>/toggle', methods=['POST'])
@admin_required
def toggle_option(current_admin, option_id):
    """Включить/выключить опцию"""
    try:
        option = PurchaseOption.query.get(option_id)
        if not option:
            return jsonify({"error": "Option not found"}), 404

        option.is_active = not option.is_active
        db.session.commit()

        try:
            cache.delete('flask_cache_view//api/public/options')
            cache.delete('view//api/public/options')
            cache.delete('public_options')
            cache.delete('flask_cache_view//api/public/purchase-options')
            cache.delete('view//api/public/purchase-options')
            cache.delete('public_purchase_options_grouped')
        except Exception:
            pass

        return jsonify(option.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ============================================================================
# REFERRAL SETTINGS
# ============================================================================

@app.route('/api/admin/referral-settings', methods=['GET', 'POST'])
@admin_required
def ref_settings(current_admin):
    """Настройки реферальной программы"""
    if request.method == 'GET':
        s = ReferralSetting.query.first() or ReferralSetting()
        return jsonify({
            "invitee_bonus_days": s.invitee_bonus_days,
            "referrer_bonus_days": s.referrer_bonus_days,
            "trial_squad_id": s.trial_squad_id,
            "referral_type": getattr(s, 'referral_type', 'DAYS'),
            "default_referral_percent": getattr(s, 'default_referral_percent', 10.0)
        }), 200
    
    try:
        data = request.json
        s = ReferralSetting.query.first() or ReferralSetting()
        s.invitee_bonus_days = data.get('invitee_bonus_days', s.invitee_bonus_days)
        s.referrer_bonus_days = data.get('referrer_bonus_days', s.referrer_bonus_days)
        s.trial_squad_id = data.get('trial_squad_id', s.trial_squad_id)
        if 'referral_type' in data:
            s.referral_type = data.get('referral_type', 'DAYS')
        if 'default_referral_percent' in data:
            s.default_referral_percent = float(data.get('default_referral_percent', 10.0))
        db.session.add(s)
        db.session.commit()
        return jsonify({"message": "Referral settings updated"}), 200
    except Exception as e:
        print(f"Error updating referral settings: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Failed to update referral settings"}), 500


# ============================================================================
# TRIAL SETTINGS
# ============================================================================

@app.route('/api/admin/trial-settings', methods=['GET', 'POST'])
@admin_required
def admin_trial_settings(current_admin):
    """Настройки триального периода"""
    from modules.models.trial import get_trial_settings
    
    settings = get_trial_settings()
    
    if request.method == 'GET':
        return jsonify({
            "days": settings.days,
            "devices": settings.devices,
            "traffic_limit_bytes": settings.traffic_limit_bytes,
            "title_ru": settings.title_ru or "",
            "title_ua": settings.title_ua or "",
            "title_en": settings.title_en or "",
            "title_cn": settings.title_cn or "",
            "description_ru": settings.description_ru or "",
            "description_ua": settings.description_ua or "",
            "description_en": settings.description_en or "",
            "description_cn": settings.description_cn or "",
            "button_text_ru": settings.button_text_ru or "",
            "button_text_ua": settings.button_text_ua or "",
            "button_text_en": settings.button_text_en or "",
            "button_text_cn": settings.button_text_cn or "",
            "activation_message_ru": settings.activation_message_ru or "",
            "activation_message_ua": settings.activation_message_ua or "",
            "activation_message_en": settings.activation_message_en or "",
            "activation_message_cn": settings.activation_message_cn or "",
            "enabled": settings.enabled
        }), 200
    
    try:
        data = request.json
        
        if 'days' in data:
            settings.days = int(data.get('days', 3))
        if 'devices' in data:
            settings.devices = int(data.get('devices', 3))
        if 'traffic_limit_bytes' in data:
            settings.traffic_limit_bytes = int(data.get('traffic_limit_bytes', 0))
        if 'title_ru' in data:
            settings.title_ru = data.get('title_ru', '')
        if 'title_ua' in data:
            settings.title_ua = data.get('title_ua', '')
        if 'title_en' in data:
            settings.title_en = data.get('title_en', '')
        if 'title_cn' in data:
            settings.title_cn = data.get('title_cn', '')
        if 'description_ru' in data:
            settings.description_ru = data.get('description_ru', '')
        if 'description_ua' in data:
            settings.description_ua = data.get('description_ua', '')
        if 'description_en' in data:
            settings.description_en = data.get('description_en', '')
        if 'description_cn' in data:
            settings.description_cn = data.get('description_cn', '')
        if 'button_text_ru' in data:
            settings.button_text_ru = data.get('button_text_ru', '')
        if 'button_text_ua' in data:
            settings.button_text_ua = data.get('button_text_ua', '')
        if 'button_text_en' in data:
            settings.button_text_en = data.get('button_text_en', '')
        if 'button_text_cn' in data:
            settings.button_text_cn = data.get('button_text_cn', '')
        if 'activation_message_ru' in data:
            settings.activation_message_ru = data.get('activation_message_ru', '')
        if 'activation_message_ua' in data:
            settings.activation_message_ua = data.get('activation_message_ua', '')
        if 'activation_message_en' in data:
            settings.activation_message_en = data.get('activation_message_en', '')
        if 'activation_message_cn' in data:
            settings.activation_message_cn = data.get('activation_message_cn', '')
        if 'enabled' in data:
            settings.enabled = bool(data.get('enabled', True))
        
        db.session.add(settings)
        db.session.commit()
        
        # Очищаем кэш (если используется)
        try:
            cache.delete('trial_settings')
        except:
            pass
        
        return jsonify({"message": "Trial settings updated"}), 200
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"Error updating trial settings: {e}")
        return jsonify({"message": f"Failed to update trial settings: {str(e)}"}), 500


@app.route('/api/public/trial-settings', methods=['GET'])
def public_trial_settings():
    """Публичный endpoint для получения настроек триала (для фронтенда)"""
    from modules.models.trial import get_trial_settings
    
    settings = get_trial_settings()
    
    # Форматируем текст, заменяя {days} на актуальное значение
    def format_text(text, days):
        if not text:
            return ""
        return text.replace("{days}", str(days))
    
    return jsonify({
        "days": settings.days,
        "devices": settings.devices,
        "traffic_limit_bytes": settings.traffic_limit_bytes,
        "enabled": settings.enabled,
        "title_ru": format_text(settings.title_ru, settings.days),
        "title_ua": format_text(settings.title_ua, settings.days),
        "title_en": format_text(settings.title_en, settings.days),
        "title_cn": format_text(settings.title_cn, settings.days),
        "description_ru": format_text(settings.description_ru, settings.days),
        "description_ua": format_text(settings.description_ua, settings.days),
        "description_en": format_text(settings.description_en, settings.days),
        "description_cn": format_text(settings.description_cn, settings.days),
        "button_text_ru": format_text(settings.button_text_ru, settings.days),
        "button_text_ua": format_text(settings.button_text_ua, settings.days),
        "button_text_en": format_text(settings.button_text_en, settings.days),
        "button_text_cn": format_text(settings.button_text_cn, settings.days),
        "activation_message_ru": format_text(settings.activation_message_ru, settings.days),
        "activation_message_ua": format_text(settings.activation_message_ua, settings.days),
        "activation_message_en": format_text(settings.activation_message_en, settings.days),
        "activation_message_cn": format_text(settings.activation_message_cn, settings.days)
    }), 200


# ============================================================================
# TARIFF LEVELS
# ============================================================================

@app.route('/api/admin/tariff-levels', methods=['GET', 'POST'])
@admin_required
def tariff_levels_settings(current_admin):
    """Управление уровнями тарифов"""
    if request.method == 'GET':
        levels = TariffLevel.query.filter_by(is_active=True).order_by(TariffLevel.display_order, TariffLevel.id).all()
        return jsonify([level.to_dict() for level in levels]), 200

    try:
        data = request.json or {}
        action = data.get('action')

        if action == 'create':
            code = (data.get('code') or '').strip().lower()
            name = (data.get('name') or '').strip()

            if not code or not name:
                return jsonify({"message": "Code and name are required"}), 400

            existing = TariffLevel.query.filter_by(code=code).first()
            if existing:
                return jsonify({"message": f"Level with code '{code}' already exists"}), 400

            max_order = db.session.query(db.func.max(TariffLevel.display_order)).scalar() or 0
            level = TariffLevel(
                code=code,
                name=name,
                display_order=max_order + 1,
                is_default=False,
                is_active=True
            )
            db.session.add(level)
            db.session.commit()

            # Очищаем кэш публичного списка уровней/фич
            try:
                cache.delete('flask_cache_view//api/public/tariff-levels')
                cache.delete('view//api/public/tariff-levels')
                cache.delete('get_public_tariff_levels')
                cache.delete('flask_cache_view//api/public/tariff-features')
                cache.delete('view//api/public/tariff-features')
                cache.delete('get_public_tariff_features')
            except Exception:
                pass

            return jsonify({"message": "Tariff level created successfully", "level": level.to_dict()}), 201

        if action == 'update':
            level_id = data.get('id')
            if not level_id:
                return jsonify({"message": "ID is required"}), 400

            level = TariffLevel.query.get(level_id)
            if not level:
                return jsonify({"message": "Level not found"}), 404

            if 'name' in data:
                level.name = (data.get('name') or '').strip()
            if 'display_order' in data:
                level.display_order = int(data.get('display_order') or 0)
            if 'is_active' in data:
                level.is_active = bool(data.get('is_active'))

            db.session.commit()

            try:
                cache.delete('flask_cache_view//api/public/tariff-levels')
                cache.delete('view//api/public/tariff-levels')
                cache.delete('get_public_tariff_levels')
                cache.delete('flask_cache_view//api/public/tariff-features')
                cache.delete('view//api/public/tariff-features')
                cache.delete('get_public_tariff_features')
            except Exception:
                pass

            return jsonify({"message": "Tariff level updated successfully", "level": level.to_dict()}), 200

        if action == 'delete':
            level_id = data.get('id')
            if not level_id:
                return jsonify({"message": "ID is required"}), 400

            level = TariffLevel.query.get(level_id)
            if not level:
                return jsonify({"message": "Level not found"}), 404

            if level.is_default:
                return jsonify({"message": "Cannot delete default level"}), 400

            tariffs_count = Tariff.query.filter_by(tier=level.code).count()
            if tariffs_count > 0:
                return jsonify({"message": f"Cannot delete level: {tariffs_count} tariff(s) are using it"}), 400

            level.is_active = False
            db.session.commit()

            try:
                cache.delete('flask_cache_view//api/public/tariff-levels')
                cache.delete('view//api/public/tariff-levels')
                cache.delete('get_public_tariff_levels')
                cache.delete('flask_cache_view//api/public/tariff-features')
                cache.delete('view//api/public/tariff-features')
                cache.delete('get_public_tariff_features')
            except Exception:
                pass

            return jsonify({"message": "Tariff level deleted successfully"}), 200

        return jsonify({"message": "Invalid action"}), 400

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Failed to process request: {str(e)}"}), 500


# ============================================================================
# TARIFF FEATURES
# ============================================================================

@app.route('/api/admin/tariff-features', methods=['GET', 'POST'])
@admin_required
def tariff_features_settings(current_admin):
    """Настройки функций тарифов"""
    default_features = {
        'basic': ['Безлимитный трафик', 'До 5 устройств', 'Базовый анти-DPI'],
        'pro': ['Приоритетная скорость', 'До 10 устройств', 'Ротация IP'],
        'elite': ['VIP-поддержка 24/7', 'Статический IP', 'Автообновление']
    }
    
    if request.method == 'GET':
        levels = TariffLevel.query.filter_by(is_active=True).order_by(TariffLevel.display_order, TariffLevel.id).all()
        result = {}
        for level in levels:
            setting = TariffFeatureSetting.query.filter_by(tier=level.code).first()
            if setting:
                try:
                    parsed = json.loads(setting.features) if isinstance(setting.features, str) else setting.features
                    result[level.code] = parsed if isinstance(parsed, list) else default_features.get(level.code, [])
                except Exception:
                    result[level.code] = default_features.get(level.code, [])
            else:
                result[level.code] = default_features.get(level.code, [])
        return jsonify(result), 200
    
    try:
        data = request.json or {}
        for tier_code, features in data.items():
            # Пропускаем несуществующие/неактивные уровни
            level = TariffLevel.query.filter_by(code=tier_code, is_active=True).first()
            if not level:
                continue

            setting = TariffFeatureSetting.query.filter_by(tier=tier_code).first()
            if not setting:
                setting = TariffFeatureSetting(tier=tier_code)
                db.session.add(setting)
            setting.features = json.dumps(features, ensure_ascii=False) if isinstance(features, list) else features
        db.session.commit()
        
        # Очищаем кеш функций тарифов
        cache.delete('flask_cache_view//api/public/tariff-features')
        cache.delete('view//api/public/tariff-features')
        cache.delete('get_public_tariff_features')
        # Также очищаем кэш уровней, т.к. фичи завязаны на уровни
        cache.delete('flask_cache_view//api/public/tariff-levels')
        cache.delete('view//api/public/tariff-levels')
        cache.delete('get_public_tariff_levels')
        # Также очищаем все ключи с 'tariff-feature' в названии через Redis напрямую
        try:
            import redis
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", 6379))
            redis_db = int(os.getenv("REDIS_DB", 0))
            redis_password = os.getenv("REDIS_PASSWORD", None)
            r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, password=redis_password, decode_responses=True)
            keys = r.keys('*tariff-feature*') + r.keys('*tariff-level*')
            if keys:
                r.delete(*keys)
                print(f"[CACHE] Deleted {len(keys)} tariff-feature cache keys")
        except Exception as e:
            print(f"[CACHE] Error clearing tariff-feature cache: {e}")
        
        return jsonify({"message": "Tariff features updated successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Failed to update tariff features"}), 500


# ============================================================================
# CURRENCY RATES
# ============================================================================

@app.route('/api/admin/currency-rates', methods=['GET', 'POST'])
@admin_required
def currency_rates(current_admin):
    """Управление курсами валют"""
    if request.method == 'GET':
        # Получаем все курсы валют
        try:
            rates = CurrencyRate.query.all()
        except:
            # Если таблица еще не создана, возвращаем значения по умолчанию
            rates = []
        
        rates_dict = {}
        for rate in rates:
            rates_dict[rate.currency] = {
                'rate_to_usd': float(rate.rate_to_usd),
                'updated_at': rate.updated_at.isoformat() if rate.updated_at else None
            }
        
        # Добавляем значения по умолчанию для валют, которых нет в БД
        default_rates = {
            'UAH': 40.0,
            'RUB': 100.0,
            'USD': 1.0
        }
        for currency, default_rate in default_rates.items():
            if currency not in rates_dict:
                rates_dict[currency] = {
                    'rate_to_usd': default_rate,
                    'updated_at': None
                }
        
        return jsonify({"rates": rates_dict}), 200
    
    # POST - обновление курсов
    try:
        data = request.json
        rates_data = data.get('rates', {})
        
        for currency, rate_info in rates_data.items():
            currency = currency.upper()
            if currency == 'USD':
                continue  # USD всегда равен 1.0
            
            # rate_info может быть словарем или числом
            rate_value = float(rate_info.get('rate_to_usd', rate_info) if isinstance(rate_info, dict) else rate_info)
            
            if rate_value <= 0:
                return jsonify({"message": f"Курс для {currency} должен быть больше 0"}), 400
            
            # Ищем существующий курс или создаем новый
            rate_obj = CurrencyRate.query.filter_by(currency=currency).first()
            if rate_obj:
                rate_obj.rate_to_usd = rate_value
                rate_obj.updated_at = datetime.now(timezone.utc)
            else:
                rate_obj = CurrencyRate(currency=currency, rate_to_usd=rate_value)
                db.session.add(rate_obj)
        
        db.session.commit()
        return jsonify({"message": "Currency rates updated"}), 200
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Failed to update currency rates: {str(e)}"}), 500


# ============================================================================
# BROADCAST
# ============================================================================

def send_telegram_message(bot_token, chat_id, text, photo_url=None, photo_file=None):
    """
    Отправить сообщение в Telegram через Bot API
    Если указано фото, отправляет фото с caption (одно сообщение)
    Добавляет кнопку "В главное меню" для рассылок
    """
    import requests
    import json
    try:
        # Добавляем кнопку "В главное меню" для рассылок
        reply_markup = {
            "inline_keyboard": [[{
                "text": "🏠 В главное меню",
                "callback_data": "clear_and_main_menu"
            }]]
        }
        
        if photo_url or photo_file:
            # Отправляем фото с caption
            url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            
            # Обрезаем caption до 1024 символов (лимит Telegram)
            caption = text[:1024] if len(text) > 1024 else text
            
            if photo_file:
                # Загружаем файл
                files = {'photo': photo_file}
                data = {
                    "chat_id": chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(reply_markup)
                }
                response = requests.post(url, files=files, data=data, timeout=30)
            elif photo_url:
                # Используем URL
                payload = {
                    "chat_id": chat_id,
                    "photo": photo_url,
                    "caption": caption,
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup
                }
                response = requests.post(url, json=payload, timeout=30)
            else:
                return False, "Photo URL or file required"
        else:
            # Отправляем текстовое сообщение
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": reply_markup
            }
            response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            message_id = result.get('result', {}).get('message_id')
            return True, message_id
        else:
            error_data = response.json() if response.content else {}
            error_msg = error_data.get('description', f'HTTP {response.status_code}')
            return False, error_msg
    except Exception as e:
        return False, str(e)

def pin_telegram_message(bot_token, chat_id, message_id):
    """Закрепить сообщение в Telegram"""
    import requests
    try:
        url = f"https://api.telegram.org/bot{bot_token}/pinChatMessage"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "disable_notification": False
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            return True, None
        else:
            error_data = response.json() if response.content else {}
            error_msg = error_data.get('description', f'HTTP {response.status_code}')
            return False, error_msg
    except Exception as e:
        return False, str(e)

@app.route('/api/admin/broadcast', methods=['POST'])
@admin_required
def send_broadcast(current_admin):
    """Рассылка email и/или Telegram"""
    try:
        # Проверяем, есть ли файл изображения
        photo_file = None
        if 'photo' in request.files:
            photo_file = request.files['photo']
            if photo_file.filename == '':
                photo_file = None
        
        # Получаем данные из form-data или JSON
        if request.is_json:
            data = request.json
            pin_message = data.get('pin_message', False)
        else:
            # FormData
            data = request.form.to_dict()
            # Парсим JSON поля если они есть
            if 'custom_emails' in data and isinstance(data['custom_emails'], str):
                try:
                    import json
                    data['custom_emails'] = json.loads(data['custom_emails'])
                except:
                    data['custom_emails'] = []
            # Обрабатываем pin_message как строку из FormData
            pin_message = data.get('pin_message', 'false').lower() in ('true', '1', 'on')
        
        subject = data.get('subject', '').strip()
        message = data.get('message', '').strip()
        recipient_type = data.get('recipient_type', 'all')
        custom_emails = data.get('custom_emails', [])
        if isinstance(custom_emails, str):
            import json
            try:
                custom_emails = json.loads(custom_emails)
            except:
                custom_emails = [custom_emails] if custom_emails else []
        broadcast_type = data.get('broadcast_type', 'email')  # 'email', 'telegram', 'both'
        bot_type = data.get('bot_type', 'old')  # 'old', 'new'
        
        if not message:
            return jsonify({"message": "Message is required"}), 400
        
        if broadcast_type == 'email' and not subject:
            return jsonify({"message": "Subject is required for email broadcast"}), 400
        
        # Получаем токены ботов
        old_bot_token = os.getenv("CLIENT_BOT_TOKEN")
        new_bot_token = os.getenv("CLIENT_BOT_V2_TOKEN") or os.getenv("CLIENT_BOT_TOKEN")
        
        # Выбираем токен в зависимости от выбранного бота
        bot_token = new_bot_token if bot_type == 'new' else old_bot_token
        
        if broadcast_type in ['telegram', 'both'] and not bot_token:
            return jsonify({"message": f"Bot token for {bot_type} bot is not configured"}), 400
        
        # Определяем получателей
        recipients = []
        if recipient_type == 'all':
            recipients = User.query.filter_by(role='CLIENT').all()
        elif recipient_type == 'active':
            from sqlalchemy import and_
            recipients = User.query.filter(and_(User.role == 'CLIENT', User.remnawave_uuid != None)).all()
        elif recipient_type == 'inactive':
            recipients = User.query.filter_by(role='CLIENT').filter(User.remnawave_uuid == None).all()
        elif recipient_type == 'custom':
            if not custom_emails or not isinstance(custom_emails, list):
                return jsonify({"message": "Custom emails list is required"}), 400
            emails = [email.strip() for email in custom_emails if email.strip()]
            recipients = User.query.filter(User.email.in_(emails)).all()
        
        if not recipients:
            return jsonify({"message": "No recipients found"}), 400
        
        # Статистика
        email_sent = 0
        email_failed = 0
        telegram_sent = 0
        telegram_failed = 0
        failed_emails = []
        failed_telegram = []
        
        import threading
        from flask_mail import Message
        from modules.core import get_mail
        from modules.email_utils import get_mail_sender, get_broadcast_html

        # HTML письма рассылки из шаблона (настройки почты или файл)
        email_html_body = get_broadcast_html(subject, message) if broadcast_type in ['email', 'both'] else None

        def send_email_background(email, subj, html_body):
            """Отправить email в фоновом режиме (имя отправителя из настроек почты)."""
            with app.app_context():
                try:
                    mail_obj = get_mail()
                    m = Message(subj, recipients=[email])
                    m.html = html_body
                    sender = get_mail_sender()
                    if sender:
                        m.sender = sender
                    mail_obj.send(m)
                    return True
                except Exception as e:
                    print(f"Failed to send email to {email}: {e}")
                    return False

        # Формируем текст для Telegram
        telegram_text = f"<b>{subject}</b>\n\n{message}" if subject else message

        # Отправляем сообщения
        for user in recipients:
            # Email рассылка
            if broadcast_type in ['email', 'both']:
                if user.email and not user.email.endswith('@telegram.local'):
                    def send_email_wrapper(u, subj, html_body):
                        nonlocal email_sent, email_failed, failed_emails
                        if send_email_background(u.email, subj, html_body):
                            email_sent += 1
                        else:
                            email_failed += 1
                            failed_emails.append(u.email)

                    threading.Thread(
                        target=send_email_wrapper,
                        args=(user, subject, email_html_body)
                    ).start()
            
            # Telegram рассылка
            if broadcast_type in ['telegram', 'both']:
                if user.telegram_id:
                    def send_telegram_wrapper(u, token, text, photo, pin):
                        nonlocal telegram_sent, telegram_failed, failed_telegram
                        # Отправляем сообщение
                        success, result = send_telegram_message(token, u.telegram_id, text, photo_file=photo)
                        if success:
                            telegram_sent += 1
                            message_id = result
                            
                            # Закрепляем сообщение если нужно
                            if pin and message_id:
                                pin_success, pin_error = pin_telegram_message(token, u.telegram_id, message_id)
                                if not pin_success:
                                    # Логируем ошибку закрепления, но не считаем это критичной ошибкой
                                    print(f"Failed to pin message for user {u.telegram_id}: {pin_error}")
                        else:
                            telegram_failed += 1
                            failed_telegram.append({
                                'telegram_id': u.telegram_id,
                                'email': u.email,
                                'error': result
                            })
                    
                    # Если есть фото, нужно переоткрыть файл для каждого потока
                    photo_for_thread = None
                    if photo_file:
                        # Сохраняем файл во временное место или используем BytesIO
                        from io import BytesIO
                        photo_file.seek(0)  # Возвращаемся в начало файла
                        photo_data = photo_file.read()
                        photo_for_thread = BytesIO(photo_data)
                        photo_file.seek(0)  # Возвращаемся для следующего использования
                    
                    threading.Thread(
                        target=send_telegram_wrapper,
                        args=(user, bot_token, telegram_text, photo_for_thread, pin_message)
                    ).start()
        
        # Ждем немного, чтобы потоки начали работу
        import time
        time.sleep(0.5)
        
        result = {
            "message": "Broadcast initiated",
            "total_recipients": len(recipients),
            "broadcast_type": broadcast_type,
            "bot_type": bot_type
        }
        
        if broadcast_type in ['email', 'both']:
            result["email"] = {
                "sent": email_sent,
                "failed": email_failed,
                "failed_emails": failed_emails[:10]
            }
        
        if broadcast_type in ['telegram', 'both']:
            result["telegram"] = {
                "sent": telegram_sent,
                "failed": telegram_failed,
                "failed_users": failed_telegram[:10]
            }
        
        return jsonify(result), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Failed to send broadcast: {str(e)}"}), 500


# ============================================================================
# SYNC BOT USERS
# ============================================================================

@app.route('/api/admin/sync-bot-users', methods=['POST'])
@admin_required
def sync_bot_users(current_admin):
    """Синхронизация пользователей бота"""
    try:
        bot_config = BotConfig.query.first()
        if not bot_config or not bot_config.bot_api_url or not bot_config.bot_api_token:
            return jsonify({"message": "Bot API not configured"}), 400

        headers = {"Authorization": f"Bearer {bot_config.bot_api_token}"}
        resp = requests.get(f"{bot_config.bot_api_url}/users", headers=headers)

        if resp.status_code != 200:
            return jsonify({"message": "Failed to fetch bot users"}), 500

        bot_users = resp.json().get('response', {}).get('users', [])
        synced_count = 0

        for bot_user in bot_users:
            telegram_id = bot_user.get('telegram_id')
            remnawave_uuid = bot_user.get('remnawave_uuid')

            if telegram_id and remnawave_uuid:
                existing_user = User.query.filter_by(telegram_id=telegram_id).first()
                if not existing_user:
                    new_user = User(
                        telegram_id=telegram_id,
                        telegram_username=bot_user.get('username'),
                        email=f"tg_{telegram_id}@telegram.local",
                        password_hash='',
                        remnawave_uuid=remnawave_uuid,
                        is_verified=True
                    )
                    db.session.add(new_user)
                    db.session.flush()
                    new_user.referral_code = f"REF-{new_user.id}-{str(telegram_id)[:3]}"
                    synced_count += 1
                elif existing_user.remnawave_uuid != remnawave_uuid:
                    existing_user.remnawave_uuid = remnawave_uuid
                    synced_count += 1

        db.session.commit()
        return jsonify({
            "message": "Bot users synchronized successfully",
            "synced_users": synced_count,
            "total_bot_users": len(bot_users)
        }), 200

    except Exception:
        return jsonify({"message": "Internal Error"}), 500


# ============================================================================
# PROMO CODES
# ============================================================================

@app.route('/api/admin/promocodes', methods=['GET', 'POST'])
@admin_required
def handle_promos(current_admin):
    """Управление промокодами"""
    if request.method == 'GET':
        return jsonify([{
            "id": c.id, 
            "code": c.code, 
            "promo_type": c.promo_type,
            "value": c.value,
            "uses_left": c.uses_left,
            "squad_id": c.squad_id if hasattr(c, 'squad_id') else None
        } for c in PromoCode.query.all()]), 200
    
    try:
        d = request.json
        
        # Нормализуем код промокода: убираем пробелы и приводим к верхнему регистру
        promo_code = (d.get('code') or '').strip().upper()
        if not promo_code:
            return jsonify({"message": "Promo code is required"}), 400
        
        # Проверяем, что промокод с таким кодом уже не существует
        existing = PromoCode.query.filter_by(code=promo_code).first()
        if existing:
            return jsonify({"message": f"Promo code '{promo_code}' already exists"}), 400
        
        nc = PromoCode(
            code=promo_code, 
            promo_type=d.get('promo_type', 'PERCENT'), 
            value=int(d.get('value', 0)), 
            uses_left=int(d.get('uses_left', 1)),
            squad_id=d.get('squad_id') if d.get('squad_id') else None
        )
        db.session.add(nc)
        db.session.commit()
        return jsonify({
            "message": "Created",
            "response": {
                "id": nc.id,
                "code": nc.code,
                "promo_type": nc.promo_type,
                "value": nc.value,
                "uses_left": nc.uses_left,
                "squad_id": nc.squad_id if hasattr(nc, 'squad_id') else None
            }
        }), 201
    except Exception as e:
        return jsonify({"message": str(e)}), 500


@app.route('/api/admin/promocodes/<int:id>', methods=['DELETE'])
@admin_required
def delete_promo(current_admin, id):
    """Удаление промокода"""
    try:
        c = db.session.get(PromoCode, id)
        if not c:
            return jsonify({"message": "Promo code not found"}), 404
        
        # Проверяем, используется ли промокод в платежах
        payments_count = Payment.query.filter_by(promo_code_id=id).count()
        
        if payments_count > 0:
            # Обнуляем promo_code_id в связанных платежах перед удалением
            Payment.query.filter_by(promo_code_id=id).update({'promo_code_id': None})
            db.session.commit()
        
        # Удаляем промокод
        db.session.delete(c)
        db.session.commit()
        
        return jsonify({"message": "Deleted"}), 200
    except Exception as e:
        db.session.rollback()
        import traceback
        error_trace = traceback.format_exc()
        print(f"[PROMOCODE] Error deleting promo code {id}: {e}")
        print(f"[PROMOCODE] Traceback: {error_trace}")
        return jsonify({
            "message": f"Failed to delete promo code: {str(e)}"
        }), 500


@app.route('/api/admin/promocodes/<int:id>', methods=['PATCH'])
@admin_required
def update_promo(current_admin, id):
    """Обновление промокода"""
    try:
        c = db.session.get(PromoCode, id)
        if not c:
            return jsonify({"message": "Not found"}), 404
        
        d = request.json
        if 'code' in d:
            # Нормализуем код промокода: убираем пробелы и приводим к верхнему регистру
            new_code = (d['code'] or '').strip().upper()
            if not new_code:
                return jsonify({"message": "Promo code cannot be empty"}), 400
            # Проверяем, что промокод с таким кодом уже не существует (кроме текущего)
            existing = PromoCode.query.filter_by(code=new_code).first()
            if existing and existing.id != id:
                return jsonify({"message": f"Promo code '{new_code}' already exists"}), 400
            c.code = new_code
        if 'promo_type' in d:
            c.promo_type = d['promo_type']
        if 'value' in d:
            c.value = int(d['value'])
        if 'uses_left' in d:
            c.uses_left = int(d['uses_left'])
        if 'squad_id' in d:
            c.squad_id = d['squad_id'] if d['squad_id'] else None
        
        db.session.commit()
        return jsonify({
            "message": "Updated",
            "response": {
                "id": c.id,
                "code": c.code,
                "promo_type": c.promo_type,
                "value": c.value,
                "uses_left": c.uses_left,
                "squad_id": c.squad_id if hasattr(c, 'squad_id') else None
            }
        }), 200
    except Exception as e:
        return jsonify({"message": str(e)}), 500


# ============================================================================
# AUTO BROADCAST MESSAGES
# ============================================================================

@app.route('/api/admin/auto-broadcast-messages', methods=['GET', 'POST'])
@admin_required
def auto_broadcast_messages(current_admin):
    """Управление автоматическими сообщениями"""
    try:
        if request.method == 'GET':
            # Получаем все автосообщения
            messages = AutoBroadcastMessage.query.all()
            result = {}
            for msg in messages:
                result[msg.message_type] = {
                    'id': msg.id,
                    'message_type': msg.message_type,
                    'message_text': msg.message_text,
                    'enabled': msg.enabled,
                    'bot_type': msg.bot_type,
                    'button_text': msg.button_text,
                    'button_url': msg.button_url,
                    'button_action': msg.button_action,
                    'created_at': msg.created_at.isoformat() if msg.created_at else None,
                    'updated_at': msg.updated_at.isoformat() if msg.updated_at else None
                }
            return jsonify(result), 200
        
        elif request.method == 'POST':
            # Обновляем или создаем автосообщения
            data = request.json
            messages_data = data.get('messages', {})
            
            # Список всех возможных типов сообщений
            message_types = [
                'subscription_expiring_3days',
                'trial_expiring',
                'no_subscription',
                'trial_not_used',
                'trial_active'
            ]
            
            for msg_type in message_types:
                msg_data = messages_data.get(msg_type)
                if not msg_data:
                    continue
                
                # Ищем существующее сообщение
                existing_msg = AutoBroadcastMessage.query.filter_by(message_type=msg_type).first()
                
                if existing_msg:
                    # Обновляем существующее
                    if 'message_text' in msg_data:
                        existing_msg.message_text = msg_data['message_text']
                    if 'enabled' in msg_data:
                        existing_msg.enabled = bool(msg_data['enabled'])
                    if 'bot_type' in msg_data:
                        existing_msg.bot_type = msg_data['bot_type']
                    # Обновляем кнопку
                    if 'button_text' in msg_data:
                        existing_msg.button_text = msg_data['button_text'] or None
                    if 'button_url' in msg_data:
                        existing_msg.button_url = msg_data['button_url'] or None
                    if 'button_action' in msg_data:
                        existing_msg.button_action = msg_data['button_action'] or None
                else:
                    # Создаем новое
                    default_texts = {
                        'subscription_expiring_3days': 'Подписка заканчивается через {days} {days_word}, не забудьте продлить',
                        'trial_expiring': 'Тестовый период заканчивается, не желаете купить подписку?',
                        'no_subscription': '🔔 Вы ещё не оформили VPN? Не теряйте время — подключитесь сейчас и защитите свой трафик!',
                        'trial_not_used': '🚀 Бесплатная пробная подписка ждёт вас!\n\nМы заметили, что вы ещё не воспользовались пробным доступом. Активируйте его прямо сейчас и оцените все преимущества VPN! 🔥',
                        'trial_active': '🎉 Ваш пробный доступ ещё активен!\n\nНе упустите возможность протестировать VPN бесплатно! Никаких обязательств — просто подключитесь и наслаждайтесь безопасным интернетом. 🌍'
                    }
                    
                    new_msg = AutoBroadcastMessage(
                        message_type=msg_type,
                        message_text=msg_data.get('message_text', default_texts.get(msg_type, '')),
                        enabled=msg_data.get('enabled', True),
                        bot_type=msg_data.get('bot_type', 'both'),
                        button_text=msg_data.get('button_text') or None,
                        button_url=msg_data.get('button_url') or None,
                        button_action=msg_data.get('button_action') or None
                    )
                    db.session.add(new_msg)
            
            db.session.commit()
            
            # Возвращаем обновленные сообщения
            messages = AutoBroadcastMessage.query.all()
            result = {}
            for msg in messages:
                result[msg.message_type] = {
                    'id': msg.id,
                    'message_type': msg.message_type,
                    'message_text': msg.message_text,
                    'enabled': msg.enabled,
                    'bot_type': msg.bot_type,
                    'button_text': msg.button_text,
                    'button_url': msg.button_url,
                    'button_action': msg.button_action,
                    'created_at': msg.created_at.isoformat() if msg.created_at else None,
                    'updated_at': msg.updated_at.isoformat() if msg.updated_at else None
                }
            
            return jsonify(result), 200
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Error: {str(e)}"}), 500


@app.route('/api/admin/auto-broadcast-settings', methods=['GET', 'POST'])
@admin_required
def auto_broadcast_settings_endpoint(current_admin):
    """Управление настройками планировщика автоматической рассылки"""
    try:
        # Получаем или создаем настройки
        settings = AutoBroadcastSettings.query.first()
        if not settings:
            settings = AutoBroadcastSettings(
                enabled=True,
                hours='9,14,19'
            )
            db.session.add(settings)
            db.session.commit()
        
        if request.method == 'GET':
            return jsonify({
                'enabled': settings.enabled,
                'hours': settings.hours,
                'updated_at': settings.updated_at.isoformat() if settings.updated_at else None
            }), 200
        
        elif request.method == 'POST':
            data = request.json
            
            if 'enabled' in data:
                settings.enabled = bool(data['enabled'])
            if 'hours' in data:
                # Валидация часов
                hours_str = data['hours'].strip()
                try:
                    hours = [int(h.strip()) for h in hours_str.split(',')]
                    # Проверяем, что все часы в диапазоне 0-23
                    for h in hours:
                        if h < 0 or h > 23:
                            return jsonify({"message": f"Час {h} вне диапазона 0-23"}), 400
                    settings.hours = hours_str
                except ValueError:
                    return jsonify({"message": "Неверный формат часов. Используйте числа через запятую, например: 9,14,19"}), 400
            
            db.session.commit()
            
            # Перезапускаем планировщик с новыми настройками
            try:
                from app import restart_scheduler
                restart_scheduler()
            except Exception as e:
                print(f"Warning: Could not restart scheduler: {e}")
            
            return jsonify({
                'message': 'Настройки сохранены',
                'enabled': settings.enabled,
                'hours': settings.hours,
                'updated_at': settings.updated_at.isoformat() if settings.updated_at else None
            }), 200
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"message": f"Error: {str(e)}"}), 500


# ============================================================================
# BOT CONFIG DEFAULT TRANSLATIONS
# ============================================================================

@app.route('/api/admin/bot-config/default-translations', methods=['GET'])
@admin_required
def get_default_translations(current_admin):
    """Дефолтные переводы бота. Ключи кнопок включают смайлик — его можно менять в конструкторе."""
    default_translations = {
        "ru": {
            "main_menu": "Главное меню",
            "main_menu_button": "🔙 Главное меню",
            "back": "🔙 Назад",
            "welcome_bot": "Добро пожаловать в {SERVICE_NAME} VPN Bot!",
            "welcome_user": "Добро пожаловать",
            "stealthnet_bot": "{SERVICE_NAME} VPN Bot",
            "not_registered_text": "Вы еще не зарегистрированы в системе.",
            "register_here": "Вы можете зарегистрироваться прямо здесь в боте или на сайте.",
            "after_register": "После регистрации вы получите логин и пароль для входа на сайте.",
            "subscription_status_title": "Статус подписки",
            "active": "Активна",
            "inactive": "Не активна",
            "balance": "Баланс",
            "traffic_title": "Трафик",
            "unlimited_traffic": "Безлимитный",
            "days": "дней",
            "connect_button": "🚀 Подключиться к VPN",
            "activate_trial_button": "💡 Активировать триал",
            "status_button": "📊 Моя подписка",
            "tariffs_button": "💎 Тарифы",
            "options_button": "📦 Опции",
            "servers_button": "🌐 Серверы",
            "referrals_button": "🎁 Рефералка",
            "support_button": "💬 Поддержка",
            "contact_support_button": "💬 Связаться с поддержкой",
            "support_bot_button": "🤖 Бот Поддержки",
            "administration_button": "👮 Администрация",
            "settings_button": "⚙️ Настройки",
            "top_up_balance": "💰 Пополнить баланс",
            "cabinet_button": "📱 Web Кабинет",
            "configs_button": "🧩 Подписки",
            "webapp_button": "Web-приложение",
            "user_agreement_button": "📄 Соглашение",
            "agreement_button": "Соглашение",
            "offer_button": "📋 Оферта",
            "select_tariff_button": "💎 Выбрать тариф",
            "copy_link": "📋 Копировать ссылку",
            "create_ticket_button": "➕ Создать тикет",
            "go_to_payment_button": "💳 Перейти к оплате",
            "enter_custom_amount": "✏️ Ввести свою сумму",
            "reply_button": "💬 Ответить",
            "back_to_support": "🔙 К поддержке",
            "back_to_tariffs": "🔙 К тарифам",
            "back_to_type": "🔙 К выбору типа",
            "try_again_button": "🔙 Попробовать снова",
            "copy_token_button": "📋 Скопировать токен",
            "my_configs_button": "🧩 Мои подписки",
            "new_subscription_button": "➕ Новая подписка",
            "extend_button": "💎 Продлить",
            "share_button": "📤 Поделиться",
            "language": "🌐 Язык"
        },
        "en": {
            "main_menu": "Main Menu",
            "main_menu_button": "🔙 Main Menu",
            "back": "🔙 Back",
            "welcome_bot": "Welcome to {SERVICE_NAME} VPN Bot!",
            "welcome_user": "Welcome",
            "stealthnet_bot": "{SERVICE_NAME} VPN Bot",
            "not_registered_text": "You are not registered in the system yet.",
            "register_here": "You can register right here in the bot or on the website.",
            "after_register": "After registration you will receive login credentials.",
            "subscription_status_title": "Subscription Status",
            "active": "Active",
            "inactive": "Inactive",
            "balance": "Balance",
            "traffic_title": "Traffic",
            "unlimited_traffic": "Unlimited",
            "days": "days",
            "connect_button": "🚀 Connect to VPN",
            "activate_trial_button": "💡 Activate Trial",
            "status_button": "📊 My Subscription",
            "tariffs_button": "💎 Tariffs",
            "options_button": "📦 Options",
            "servers_button": "🌐 Servers",
            "referrals_button": "🎁 Referrals",
            "support_button": "💬 Support",
            "contact_support_button": "💬 Contact Support",
            "support_bot_button": "🤖 Support Bot",
            "administration_button": "👮 Administration",
            "settings_button": "⚙️ Settings",
            "top_up_balance": "💰 Top Up Balance",
            "cabinet_button": "📱 Web Cabinet",
            "configs_button": "🧩 Subscriptions",
            "webapp_button": "Web App",
            "user_agreement_button": "📄 Agreement",
            "agreement_button": "Agreement",
            "offer_button": "📋 Offer",
            "select_tariff_button": "💎 Select Tariff",
            "copy_link": "📋 Copy Link",
            "create_ticket_button": "➕ Create Ticket",
            "go_to_payment_button": "💳 Go to Payment",
            "enter_custom_amount": "✏️ Enter Custom Amount",
            "reply_button": "💬 Reply",
            "back_to_support": "🔙 To Support",
            "back_to_tariffs": "🔙 To Tariffs",
            "back_to_type": "🔙 Back to Type Selection",
            "try_again_button": "🔙 Try Again",
            "copy_token_button": "📋 Copy Token",
            "my_configs_button": "🧩 My Subscriptions",
            "new_subscription_button": "➕ New Subscription",
            "extend_button": "💎 Extend",
            "share_button": "📤 Share",
            "language": "🌐 Language"
        },
        "ua": {
            "main_menu": "Головне меню",
            "main_menu_button": "🔙 Головне меню",
            "back": "🔙 Назад",
            "welcome_bot": "Ласкаво просимо до {SERVICE_NAME} VPN Bot!",
            "welcome_user": "Ласкаво просимо",
            "stealthnet_bot": "{SERVICE_NAME} VPN Bot",
            "not_registered_text": "Ви ще не зареєстровані в системі.",
            "register_here": "Ви можете зареєструватися прямо тут у боті або на сайті.",
            "after_register": "Після реєстрації ви отримаєте логін та пароль для входу.",
            "subscription_status_title": "Статус підписки",
            "active": "Активна",
            "inactive": "Не активна",
            "balance": "Баланс",
            "traffic_title": "Трафік",
            "unlimited_traffic": "Безлімітний",
            "days": "днів",
            "connect_button": "🚀 Підключитися до VPN",
            "activate_trial_button": "💡 Активувати тріал",
            "status_button": "📊 Моя підписка",
            "tariffs_button": "💎 Тарифи",
            "options_button": "📦 Опції",
            "servers_button": "🌐 Сервери",
            "referrals_button": "🎁 Рефералка",
            "support_button": "💬 Підтримка",
            "contact_support_button": "💬 Зв'язатися з підтримкою",
            "support_bot_button": "🤖 Бот Підтримки",
            "administration_button": "👮 Адміністрація",
            "settings_button": "⚙️ Налаштування",
            "top_up_balance": "💰 Поповнити баланс",
            "cabinet_button": "📱 Web Кабінет",
            "configs_button": "🧩 Підписки",
            "webapp_button": "Web-додаток",
            "user_agreement_button": "📄 Угода",
            "agreement_button": "Угода",
            "offer_button": "📋 Оферта",
            "select_tariff_button": "💎 Обрати тариф",
            "copy_link": "📋 Копіювати посилання",
            "create_ticket_button": "➕ Створити тікет",
            "go_to_payment_button": "💳 Перейти до оплати",
            "enter_custom_amount": "✏️ Ввести суму",
            "reply_button": "💬 Відповісти",
            "back_to_support": "🔙 До підтримки",
            "back_to_tariffs": "🔙 До тарифів",
            "back_to_type": "🔙 До вибору типу",
            "try_again_button": "🔙 Спробувати знову",
            "copy_token_button": "📋 Скопіювати токен",
            "my_configs_button": "🧩 Мої підписки",
            "new_subscription_button": "➕ Нова підписка",
            "extend_button": "💎 Продовжити",
            "share_button": "📤 Поділитися",
            "language": "🌐 Мова"
        },
        "cn": {
            "main_menu": "主菜单",
            "main_menu_button": "🔙 主菜单",
            "back": "🔙 返回",
            "welcome_bot": "欢迎使用 {SERVICE_NAME} VPN Bot!",
            "welcome_user": "欢迎",
            "stealthnet_bot": "{SERVICE_NAME} VPN Bot",
            "not_registered_text": "您尚未在系统中注册。",
            "register_here": "您可以在此机器人或网站上注册。",
            "after_register": "注册后，您将收到登录凭据。",
            "subscription_status_title": "订阅状态",
            "active": "活跃",
            "inactive": "未激活",
            "balance": "余额",
            "traffic_title": "流量",
            "unlimited_traffic": "无限制",
            "days": "天",
            "connect_button": "🚀 连接VPN",
            "activate_trial_button": "💡 激活试用",
            "status_button": "📊 我的订阅",
            "tariffs_button": "💎 资费",
            "options_button": "📦 选项",
            "servers_button": "🌐 服务器",
            "referrals_button": "🎁 推荐",
            "support_button": "💬 支持",
            "contact_support_button": "💬 联系支持",
            "support_bot_button": "🤖 支持机器人",
            "administration_button": "👮 管理",
            "settings_button": "⚙️ 设置",
            "top_up_balance": "💰 充值",
            "cabinet_button": "📱 Web кабинет",
            "configs_button": "🧩 订阅",
            "webapp_button": "Web应用",
            "user_agreement_button": "📄 协议",
            "agreement_button": "协议",
            "offer_button": "📋 报价",
            "select_tariff_button": "💎 选择资费",
            "copy_link": "📋 复制链接",
            "create_ticket_button": "➕ 创建工单",
            "go_to_payment_button": "💳 去支付",
            "enter_custom_amount": "✏️ 输入金额",
            "reply_button": "💬 回复",
            "back_to_support": "🔙 返回支持",
            "back_to_tariffs": "🔙 返回资费",
            "back_to_type": "🔙 返回类型选择",
            "try_again_button": "🔙 重试",
            "copy_token_button": "📋 复制令牌",
            "my_configs_button": "🧩 我的订阅",
            "new_subscription_button": "➕ 新订阅",
            "extend_button": "💎 续订",
            "share_button": "📤 分享",
            "language": "🌐 语言"
        }
    }
    return jsonify(default_translations), 200


# ============================================================================
# TELEGRAM WEBHOOK MANAGEMENT
# ============================================================================

def decrypt_key(key):
    """Расшифровка ключа"""
    from modules.core import get_fernet
    fernet = get_fernet()
    if not key or not fernet:
        return ""
    try:
        return fernet.decrypt(key).decode('utf-8')
    except:
        return ""


@app.route('/api/admin/telegram-webhook-status', methods=['GET'])
@admin_required
def telegram_webhook_status(current_admin):
    """Проверка статуса webhook для Telegram бота"""
    try:
        s = PaymentSetting.query.first()
        bot_token = decrypt_key(s.telegram_bot_token) if s else None
        
        if not bot_token or bot_token == "DECRYPTION_ERROR":
            return jsonify({"error": "Bot token not configured"}), 400
        
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getWebhookInfo",
            timeout=5
        ).json()
        
        if resp.get('ok'):
            webhook_info = resp.get('result', {})
            return jsonify({
                "url": webhook_info.get('url'),
                "has_custom_certificate": webhook_info.get('has_custom_certificate', False),
                "pending_update_count": webhook_info.get('pending_update_count', 0),
                "last_error_date": webhook_info.get('last_error_date'),
                "last_error_message": webhook_info.get('last_error_message'),
                "max_connections": webhook_info.get('max_connections'),
                "allowed_updates": webhook_info.get('allowed_updates', [])
            }), 200
        else:
            return jsonify({"error": resp.get('description', 'Unknown error')}), 500
            
    except Exception as e:
        print(f"Telegram webhook status error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/telegram-set-webhook', methods=['POST'])
@admin_required
def telegram_set_webhook(current_admin):
    """Настройка webhook для Telegram бота"""
    try:
        s = PaymentSetting.query.first()
        bot_token = decrypt_key(s.telegram_bot_token) if s else None
        
        if not bot_token or bot_token == "DECRYPTION_ERROR":
            return jsonify({"error": "Bot token not configured"}), 400
        
        YOUR_SERVER_IP_OR_DOMAIN = os.getenv("YOUR_SERVER_IP", "https://panel.stealthnet.app")
        if not YOUR_SERVER_IP_OR_DOMAIN.startswith(('http://', 'https://')):
            YOUR_SERVER_IP_OR_DOMAIN = f"https://{YOUR_SERVER_IP_OR_DOMAIN}"
        
        webhook_url = f"{YOUR_SERVER_IP_OR_DOMAIN}/api/webhook/telegram"
        
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/setWebhook",
            json={
                "url": webhook_url,
                "allowed_updates": ["pre_checkout_query", "message"]
            },
            timeout=5
        ).json()
        
        if resp.get('ok'):
            return jsonify({"success": True, "url": webhook_url, "message": "Webhook установлен успешно"}), 200
        else:
            return jsonify({"error": resp.get('description', 'Unknown error')}), 500
            
    except Exception as e:
        print(f"Telegram set webhook error: {e}")
        return jsonify({"error": str(e)}), 500


# Import side-effect routes (e.g. SSH terminal)
from modules.api.admin import ssh_terminal  # noqa: F401
