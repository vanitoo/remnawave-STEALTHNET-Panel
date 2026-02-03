"""
API публичные эндпоинты

- GET /api/public/tariffs - Список тарифов
- GET /api/public/tariff-features - Функции тарифов
- GET /api/public/system-settings - Системные настройки
- GET /api/public/branding - Брендинг
- GET /api/public/currency-rates - Курсы валют
- GET /api/public/nodes - Публичные ноды
- GET /api/public/system-info - Информация о системе
- GET /api/public/telegram-auth-enabled - Проверка Telegram авторизации
- GET /api/public/server-domain - Домен сервера
- GET /api/health - Health check
- GET /api/public/health - Public health check
"""

from flask import request, jsonify
from datetime import datetime, timezone
import json
import os

from modules.core import get_app, get_db, get_cache
from modules.models.tariff import Tariff
from modules.models.tariff_feature import TariffFeatureSetting
from modules.models.tariff_level import TariffLevel
from modules.models.system import SystemSetting
from modules.models.branding import BrandingSetting
from modules.models.bot_config import BotConfig
from modules.models.currency import CurrencyRate
from modules.models.option import PurchaseOption

app = get_app()
db = get_db()
cache = get_cache()


# ============================================================================
# TARIFFS
# ============================================================================

@app.route('/api/public/tariffs', methods=['GET'])
@cache.cached(timeout=3600)
def public_tariffs():
    """Публичный список тарифов"""
    try:
        tariffs = Tariff.query.all()
        result = []
        for t in tariffs:
            # Получаем squad_ids через метод get_squad_ids
            squad_ids = []
            if hasattr(t, 'get_squad_ids'):
                squad_ids = t.get_squad_ids()
            elif hasattr(t, 'squad_ids') and t.squad_ids:
                try:
                    squad_ids = json.loads(t.squad_ids) if isinstance(t.squad_ids, str) else t.squad_ids
                except:
                    squad_ids = []
            # Если squad_ids пустой, но есть squad_id - используем его для обратной совместимости
            if not squad_ids and t.squad_id:
                squad_ids = [t.squad_id]
            
            result.append({
                'id': t.id,
                'name': t.name,
                'duration_days': t.duration_days,
                'price_uah': t.price_uah,
                'price_rub': t.price_rub,
                'price_usd': t.price_usd,
                'squad_id': t.squad_id,  # Для обратной совместимости
                'squad_ids': squad_ids,  # Новое поле с массивом сквадов
                'traffic_limit_bytes': t.traffic_limit_bytes,
                'traffic_limit_gb': round(t.traffic_limit_bytes / (1024 ** 3), 2) if t.traffic_limit_bytes else None,
                'hwid_device_limit': t.hwid_device_limit,
                'tier': t.tier,
                'badge': t.badge,
                'bonus_days': t.bonus_days,
                'price_per_day_usd': round(t.price_usd / t.duration_days, 4) if t.duration_days > 0 else 0
            })
        return jsonify(result), 200
    except Exception as e:
        print(f"Error in public_tariffs: {e}")
        return jsonify({"message": "Internal Server Error"}), 500


@app.route('/api/public/tariff-levels', methods=['GET'])
@cache.cached(timeout=3600)
def get_public_tariff_levels():
    """Публичные уровни тарифов"""
    try:
        levels = TariffLevel.query.filter_by(is_active=True).order_by(TariffLevel.display_order, TariffLevel.id).all()
        return jsonify([level.to_dict() for level in levels]), 200
    except Exception as e:
        print(f"Error in get_public_tariff_levels: {e}")
        # Фолбэк для инстансов, где миграция ещё не применена
        return jsonify([
            {"id": 1, "code": "basic", "name": "Базовый", "display_order": 1, "is_default": True, "is_active": True},
            {"id": 2, "code": "pro", "name": "Премиум", "display_order": 2, "is_default": True, "is_active": True},
            {"id": 3, "code": "elite", "name": "Элитный", "display_order": 3, "is_default": True, "is_active": True},
        ]), 200


@app.route('/api/public/tariff-features', methods=['GET'])
@cache.cached(timeout=3600)
def get_public_tariff_features():
    """Публичные функции тарифов"""
    default_features = {
        'basic': ['Безлимитный трафик', 'До 5 устройств', 'Базовый анти-DPI'],
        'pro': ['Приоритетная скорость', 'До 10 устройств', 'Ротация IP'],
        'elite': ['VIP-поддержка 24/7', 'Статический IP', 'Автообновление']
    }

    # Возвращаем словарь {tier_code: [features...]} по активным уровням
    try:
        levels = TariffLevel.query.filter_by(is_active=True).order_by(TariffLevel.display_order, TariffLevel.id).all()
        level_codes = [l.code for l in levels if getattr(l, 'code', None)]
    except Exception as e:
        print(f"Error loading TariffLevel for features: {e}")
        level_codes = []

    if not level_codes:
        level_codes = ['basic', 'pro', 'elite']

    result = {}

    for code in level_codes:
        setting = TariffFeatureSetting.query.filter_by(tier=code).first()
        if setting:
            try:
                parsed = json.loads(setting.features) if isinstance(setting.features, str) else setting.features
                if isinstance(parsed, list) and len(parsed) > 0:
                    result[code] = parsed
                else:
                    result[code] = default_features.get(code, [])
            except Exception:
                result[code] = default_features.get(code, [])
        else:
            result[code] = default_features.get(code, [])

    return jsonify(result), 200


# ============================================================================
# PURCHASE OPTIONS (Дополнительные опции для покупки)
# ============================================================================

@app.route('/api/public/options', methods=['GET'])
def public_options():
    """Публичный список активных опций"""
    try:
        options = PurchaseOption.query.filter_by(is_active=True).order_by(
            PurchaseOption.sort_order,
            PurchaseOption.id
        ).all()

        return jsonify([{
            'id': opt.id,
            'option_type': opt.option_type,
            'name': opt.name,
            'description': opt.description,
            'value': opt.value,
            'unit': opt.unit,
            'price_uah': opt.price_uah,
            'price_rub': opt.price_rub,
            'price_usd': opt.price_usd,
            'icon': opt.icon
        } for opt in options]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/public/options/<option_type>', methods=['GET'])
def public_options_by_type(option_type):
    """Публичный список активных опций по типу"""
    try:
        options = PurchaseOption.query.filter_by(
            is_active=True,
            option_type=option_type
        ).order_by(
            PurchaseOption.sort_order,
            PurchaseOption.id
        ).all()

        return jsonify([{
            'id': opt.id,
            'option_type': opt.option_type,
            'name': opt.name,
            'description': opt.description,
            'value': opt.value,
            'unit': opt.unit,
            'price_uah': opt.price_uah,
            'price_rub': opt.price_rub,
            'price_usd': opt.price_usd,
            'icon': opt.icon
        } for opt in options]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/public/purchase-options', methods=['GET'])
def public_purchase_options_grouped():
    """Публичный список опций для покупки (сгруппированный по типу)"""
    try:
        options = PurchaseOption.query.filter_by(is_active=True).order_by(
            PurchaseOption.sort_order,
            PurchaseOption.id
        ).all()

        result = {
            'traffic': [],
            'devices': [],
            'squad': []
        }

        for opt in options:
            option_data = {
                'id': opt.id,
                'option_type': opt.option_type,
                'name': opt.name,
                'description': opt.description,
                'value': opt.value,
                'unit': opt.unit,
                'price_uah': opt.price_uah,
                'price_rub': opt.price_rub,
                'price_usd': opt.price_usd,
                'icon': opt.icon
            }
            if opt.option_type in result:
                result[opt.option_type].append(option_data)

        return jsonify({'options': result}), 200
    except Exception as e:
        return jsonify({"error": str(e), "options": {"traffic": [], "devices": [], "squad": []}}), 500


# ============================================================================
# SYSTEM SETTINGS
# ============================================================================

@app.route('/api/public/system-settings', methods=['GET'])
def public_system_settings():
    """Публичные системные настройки"""
    try:
        import json
        settings = SystemSetting.query.first()
        if not settings:
            return jsonify({
                "default_language": "ru",
                "default_currency": "uah",
                "maintenance_mode": False,
                "active_languages": ["ru", "ua", "en", "cn"],
                "active_currencies": ["uah", "rub", "usd"]
            }), 200

        # Парсим active_languages и active_currencies из JSON
        active_languages = ["ru", "ua", "en", "cn"]
        active_currencies = ["uah", "rub", "usd"]
        
        if hasattr(settings, 'active_languages') and settings.active_languages:
            try:
                active_languages = json.loads(settings.active_languages) if isinstance(settings.active_languages, str) else settings.active_languages
            except:
                pass
        
        if hasattr(settings, 'active_currencies') and settings.active_currencies:
            try:
                active_currencies = json.loads(settings.active_currencies) if isinstance(settings.active_currencies, str) else settings.active_currencies
            except:
                pass

        return jsonify({
            "default_language": settings.default_language,
            "default_currency": settings.default_currency,
            "maintenance_mode": getattr(settings, 'maintenance_mode', False),
            "support_email": getattr(settings, 'support_email', ''),
            "telegram_support": getattr(settings, 'telegram_support', ''),
            "active_languages": active_languages,
            "active_currencies": active_currencies
        }), 200

    except Exception as e:
        return jsonify({"message": "Internal Error"}), 500


@app.route('/api/public/branding', methods=['GET'])
def public_branding():
    """Публичный брендинг"""
    try:
        import json
        branding = BrandingSetting.query.first()
        if not branding:
            return jsonify({
                "site_name": "",
                "logo_url": "",
                "site_subtitle": "",
                "login_welcome_text": "",
                "register_welcome_text": "",
                "footer_text": "",
                "dashboard_servers_title": "",
                "dashboard_servers_description": "",
                "dashboard_tariffs_title": "",
                "dashboard_tariffs_description": "",
                "dashboard_tagline": "",
                "dashboard_referrals_title": "",
                "dashboard_referrals_description": "",
                "dashboard_support_title": "",
                "dashboard_support_description": "",
                "tariff_tier_basic_name": "",
                "tariff_tier_pro_name": "",
                "tariff_tier_elite_name": "",
                "tariff_features_names": {},
                "button_subscribe_text": "",
                "button_buy_text": "",
                "button_connect_text": "",
                "button_share_text": "",
                "button_copy_text": "",
                "subscription_active_text": "",
                "subscription_expired_text": "",
                "subscription_trial_text": "",
                "balance_label_text": "",
                "referral_code_label_text": "",
                "favicon_url": "",
                "meta_title": "",
                "meta_description": "",
                "meta_keywords": "",
                "quick_download_enabled": True,
                "quick_download_windows_url": "",
                "quick_download_android_url": "",
                "quick_download_macos_url": "",
                "quick_download_ios_url": "",
                "quick_download_profile_deeplink": ""
            }), 200

        # Парсим JSON для названий функций тарифов
        tariff_features_names = {}
        if hasattr(branding, 'tariff_features_names') and branding.tariff_features_names:
            try:
                tariff_features_names = json.loads(branding.tariff_features_names)
            except:
                pass

        return jsonify({
            "site_name": branding.site_name or "",
            "logo_url": branding.logo_url or "",
            "site_subtitle": branding.site_subtitle or "",
            "login_welcome_text": branding.login_welcome_text or "",
            "register_welcome_text": branding.register_welcome_text or "",
            "footer_text": branding.footer_text or "",
            "dashboard_servers_title": branding.dashboard_servers_title or "",
            "dashboard_servers_description": branding.dashboard_servers_description or "",
            "dashboard_tariffs_title": branding.dashboard_tariffs_title or "",
            "dashboard_tariffs_description": branding.dashboard_tariffs_description or "",
            "dashboard_tagline": branding.dashboard_tagline or "",
            "dashboard_referrals_title": getattr(branding, 'dashboard_referrals_title', None) or "",
            "dashboard_referrals_description": getattr(branding, 'dashboard_referrals_description', None) or "",
            "dashboard_support_title": getattr(branding, 'dashboard_support_title', None) or "",
            "dashboard_support_description": getattr(branding, 'dashboard_support_description', None) or "",
            "tariff_tier_basic_name": getattr(branding, 'tariff_tier_basic_name', None) or "",
            "tariff_tier_pro_name": getattr(branding, 'tariff_tier_pro_name', None) or "",
            "tariff_tier_elite_name": getattr(branding, 'tariff_tier_elite_name', None) or "",
            "tariff_features_names": tariff_features_names,
            "button_subscribe_text": getattr(branding, 'button_subscribe_text', None) or "",
            "button_buy_text": getattr(branding, 'button_buy_text', None) or "",
            "button_connect_text": getattr(branding, 'button_connect_text', None) or "",
            "button_share_text": getattr(branding, 'button_share_text', None) or "",
            "button_copy_text": getattr(branding, 'button_copy_text', None) or "",
            "subscription_active_text": getattr(branding, 'subscription_active_text', None) or "",
            "subscription_expired_text": getattr(branding, 'subscription_expired_text', None) or "",
            "subscription_trial_text": getattr(branding, 'subscription_trial_text', None) or "",
            "balance_label_text": getattr(branding, 'balance_label_text', None) or "",
            "referral_code_label_text": getattr(branding, 'referral_code_label_text', None) or "",
            "favicon_url": getattr(branding, 'favicon_url', None) or "",
            "meta_title": getattr(branding, 'meta_title', None) or "",
            "meta_description": getattr(branding, 'meta_description', None) or "",
            "meta_keywords": getattr(branding, 'meta_keywords', None) or "",
            "quick_download_enabled": getattr(branding, 'quick_download_enabled', True),
            "quick_download_windows_url": getattr(branding, 'quick_download_windows_url', None) or "",
            "quick_download_android_url": getattr(branding, 'quick_download_android_url', None) or "",
            "quick_download_macos_url": getattr(branding, 'quick_download_macos_url', None) or "",
            "quick_download_ios_url": getattr(branding, 'quick_download_ios_url', None) or "",
            "quick_download_profile_deeplink": getattr(branding, 'quick_download_profile_deeplink', None) or ""
        }), 200

    except Exception as e:
        print(f"Error in public_branding: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"message": "Internal Error"}), 500


@app.route('/api/public/currency-rates', methods=['GET'])
def public_currency_rates():
    """Публичные курсы валют"""
    try:
        from modules.currency import get_currency_rate
        currencies = ['USD', 'EUR', 'UAH', 'RUB', 'GBP']
        rates = {}

        for currency in currencies:
            if currency != 'USD':
                rate = get_currency_rate(currency)
                rates[currency] = rate if rate else 1.0

        return jsonify({
            "base_currency": "USD",
            "rates": rates,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }), 200

    except Exception as e:
        return jsonify({"message": "Internal Error"}), 500


@app.route('/api/public/nodes', methods=['GET'])
@cache.cached(timeout=300)
def get_public_nodes():
    """Публичные ноды для лендинга"""
    try:
        import requests
        headers, cookies = {}, {}
        ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
        if ADMIN_TOKEN:
            headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"
        
        resp = requests.get(f"{os.getenv('API_URL')}/api/nodes/public", headers=headers, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json()), 200
    except Exception as e:
        return jsonify({"message": "Failed to fetch public nodes"}), 500


@app.route('/api/public/system-info', methods=['GET'])
def get_system_info():
    """Публичная информация о системе"""
    try:
        system_settings = SystemSetting.query.first()
        branding = BrandingSetting.query.first()
        bot_config = BotConfig.query.first()

        return jsonify({
            "system": {
                "maintenance_mode": system_settings.maintenance_mode if system_settings else False,
                "registration_enabled": getattr(system_settings, 'registration_enabled', True) if system_settings else True,
                "telegram_auth_enabled": getattr(system_settings, 'telegram_auth_enabled', False) if system_settings else False
            },
            "branding": {
                "logo_url": branding.logo_url if branding else "",
                "company_name": (branding.site_name or "").strip() if branding else "",
                "primary_color": getattr(branding, 'primary_color', '#007bff') if branding else "#007bff"
            },
            "bot": {
                "enabled": bool(bot_config and bot_config.bot_username),
                "username": bot_config.bot_username if bot_config else ""
            },
            "server_time": datetime.now(timezone.utc).isoformat()
        }), 200

    except Exception as e:
        return jsonify({
            "system": {"maintenance_mode": False, "registration_enabled": True, "telegram_auth_enabled": False},
            "branding": {"logo_url": "", "company_name": "", "primary_color": "#007bff"},
            "bot": {"enabled": False, "username": ""},
            "server_time": datetime.now(timezone.utc).isoformat()
        }), 200


@app.route('/api/public/telegram-auth-enabled', methods=['GET'])
def telegram_auth_enabled():
    """Проверка Telegram авторизации"""
    try:
        # Приоритет: TELEGRAM_BOT_NAME из .env -> BotConfig из БД
        telegram_bot_name = os.getenv("TELEGRAM_BOT_NAME", "").strip()
        
        if telegram_bot_name:
            return jsonify({
                "enabled": True,
                "bot_name": telegram_bot_name,
                "bot_username": telegram_bot_name
            }), 200
        
        # Fallback: проверяем BotConfig из БД
        bot_config = BotConfig.query.first()
        if bot_config and bot_config.bot_username:
            return jsonify({
                "enabled": True,
                "bot_name": bot_config.bot_username,
                "bot_username": bot_config.bot_username
            }), 200
        
        return jsonify({"enabled": False, "message": "Telegram bot not configured"}), 200
    except Exception as e:
        return jsonify({"enabled": False, "message": "Error checking Telegram auth"}), 200


@app.route('/api/public/server-domain', methods=['GET'])
def server_domain():
    """Получить домен сервера"""
    YOUR_SERVER_IP = os.getenv("YOUR_SERVER_IP")
    if YOUR_SERVER_IP:
        YOUR_SERVER_IP = YOUR_SERVER_IP.strip()
        if not YOUR_SERVER_IP.startswith(('http://', 'https://')):
            YOUR_SERVER_IP = f"https://{YOUR_SERVER_IP}"

    domain = YOUR_SERVER_IP or request.host_url.rstrip('/')
    if domain.startswith('http://') or domain.startswith('https://'):
        domain = domain.split('://', 1)[1]
    domain = domain.rstrip('/')

    full_url = f"https://{domain}" if not domain.startswith('http') else domain

    return jsonify({"domain": domain, "full_url": full_url}), 200


@app.route('/api/public/bot-config', methods=['GET'])
def public_bot_config():
    """Публичный эндпоинт для получения конфигурации бота"""
    from modules.models.bot_config import BotConfig
    
    import json
    import os
    
    config = BotConfig.query.first()
    
    # Получаем bot_username: сначала из BotConfig, потом из .env
    bot_username = ""
    if config and config.bot_username:
        bot_username = config.bot_username
    
    # Если нет в BotConfig или пустой, берем из переменной окружения (для мини-аппа используем V2)
    if not bot_username:
        bot_username = os.getenv('TELEGRAM_BOT_NAME_V2', '') or os.getenv('TELEGRAM_BOT_NAME', '')
    
    # Убираем @ если есть
    if bot_username and bot_username.startswith('@'):
        bot_username = bot_username[1:]
    
    if not config:
        branding = BrandingSetting.query.first()
        fallback_name = (branding.site_name or "").strip() if branding else "Панель"
        return jsonify({
            "service_name": fallback_name,
            "bot_username": bot_username,  # Используем из .env если есть
            "support_url": "",
            "support_bot_username": "",
            "show_connect_button": True,
            "show_status_button": True,
            "show_tariffs_button": True,
            "show_options_button": True,
            "show_webapp_button": True,
            "show_trial_button": True,
            "show_referral_button": True,
            "show_support_button": True,
            "show_servers_button": True,
            "show_agreement_button": True,
            "show_offer_button": True,
            "show_topup_button": True,
            "show_settings_button": True,
            "trial_days": 3,
            "translations": {},
            "welcome_messages": {},
            "user_agreements": {},
            "offer_texts": {},
            "menu_structure": None,
            "require_channel_subscription": False,
            "channel_id": "",
            "channel_url": "",
            "channel_subscription_texts": {"ru": "", "ua": "", "en": "", "cn": ""},
            "bot_link_for_miniapp": "",
            "buttons_order": ["trial", "connect", "status", "tariffs", "options", "referrals", "support", "settings", "webapp"],
            "bot_page_logos": {}
        }), 200
    
    # Все переводы в одном объекте (как в старом коде app.py)
    translations = {
        "ru": json.loads(config.translations_ru) if config.translations_ru else {},
        "ua": json.loads(config.translations_ua) if config.translations_ua else {},
        "en": json.loads(config.translations_en) if config.translations_en else {},
        "cn": json.loads(config.translations_cn) if config.translations_cn else {}
    }
    
    # Приветственные сообщения (как в старом коде app.py)
    welcome_messages = {
        "ru": config.welcome_message_ru or "",
        "ua": config.welcome_message_ua or "",
        "en": config.welcome_message_en or "",
        "cn": config.welcome_message_cn or ""
    }
    
    # Документы (как в старом коде app.py)
    user_agreements = {
        "ru": config.user_agreement_ru or "",
        "ua": config.user_agreement_ua or "",
        "en": config.user_agreement_en or "",
        "cn": config.user_agreement_cn or ""
    }
    offer_texts = {
        "ru": config.offer_text_ru or "",
        "ua": config.offer_text_ua or "",
        "en": config.offer_text_en or "",
        "cn": config.offer_text_cn or ""
    }
    
    # Проверка подписки на канал (как в старом коде app.py)
    channel_subscription_texts = {
        "ru": getattr(config, 'channel_subscription_text_ru', '') or "",
        "ua": getattr(config, 'channel_subscription_text_ua', '') or "",
        "en": getattr(config, 'channel_subscription_text_en', '') or "",
        "cn": getattr(config, 'channel_subscription_text_cn', '') or ""
    }
    
    branding = BrandingSetting.query.first()
    fallback_name = (branding.site_name or "").strip() if branding else "Панель"
    return jsonify({
        "service_name": config.service_name or fallback_name,
        "bot_username": bot_username,  # Добавлено для deep links
        "support_url": (config.support_url or "").strip(),
        "support_bot_username": (config.support_bot_username or "").lstrip("@").strip(),
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
        "translations": translations,
        "welcome_messages": welcome_messages,
        "user_agreements": user_agreements,
        "offer_texts": offer_texts,
        "menu_structure": json.loads(config.menu_structure) if hasattr(config, 'menu_structure') and config.menu_structure else None,
        "require_channel_subscription": config.require_channel_subscription if config.require_channel_subscription is not None else False,
        "channel_id": config.channel_id or "",
        "channel_url": config.channel_url or "",
        "channel_subscription_texts": channel_subscription_texts if channel_subscription_texts else {"ru": "", "ua": "", "en": "", "cn": ""},
        "bot_link_for_miniapp": config.bot_link_for_miniapp or "",
        "buttons_order": json.loads(config.buttons_order) if hasattr(config, 'buttons_order') and config.buttons_order else ["trial", "connect", "status", "tariffs", "options", "referrals", "support", "settings", "webapp"],
        "bot_page_logos": json.loads(config.bot_page_logos) if getattr(config, 'bot_page_logos', None) else {}
    }), 200


# ============================================================================
# HEALTH
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check"""
    return jsonify({"status": "ok", "message": "API is running"}), 200

@app.route('/api/public/health', methods=['GET'])
def public_health_check():
    """Public health check endpoint"""
    return jsonify({"status": "ok", "message": "API is running"}), 200
