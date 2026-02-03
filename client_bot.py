#!/usr/bin/env python3
"""
Telegram Bot для клиентов StealthNET VPN
Предоставляет функционал Dashboard через Telegram интерфейс
"""

import os
import logging
import requests
import asyncio
import base64
import json
import time
import re
import math
import html
import hashlib
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, 
    KeyboardButton, ReplyKeyboardMarkup, InlineQueryResultArticle, 
    InputTextMessageContent
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    InlineQueryHandler,
    ContextTypes,
    filters
)
from telegram.error import Conflict

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Единый разделитель для сообщений (в одну строку)
SEPARATOR_LINE = "-" * 32

# Эмодзи из .env — только те, что в тексте сообщений (кнопки меняются с сайта).
_DEFAULT_EMOJIS = {
    "HEADER": "🛡", "MAIN_MENU": "👋", "BALANCE": "💰", "STATUS": "📊", "DATE": "📅",
    "TIME": "⏰", "DEVICES": "📱", "TRAFFIC": "📈", "LINK": "🔗", "ACTIVE_GREEN": "🟢",
    "ACTIVE_YELLOW": "🟡", "INACTIVE": "🔴", "TRIAL": "💡", "CONNECT": "🚀",
    "TARIFFS": "💎", "PACKAGE": "📦", "CARD": "💳", "NOTE": "📝", "LOCATION": "📍",
    "PUZZLE": "🧩", "STAR": "⭐", "SERVERS": "🌐", "CROWN": "👑", "DURATION": "⏱️",
}


def get_emoji(key: str) -> str:
    """Возвращает эмодзи по ключу из .env (EMOJI_HEADER, EMOJI_TRIAL, ...) или дефолт."""
    k = key.upper().replace("-", "_")
    return (os.getenv(f"EMOJI_{k}", "") or _DEFAULT_EMOJIS.get(k, "")).strip() or _DEFAULT_EMOJIS.get(k, "")


def get_tg_emoji_html(key: str) -> str:
    """Для премиум: если задан EMOJI_*_TG_ID — возвращает <tg-emoji emoji-id=\"...\">fallback</tg-emoji>, иначе Unicode."""
    emoji = get_emoji(key)
    tg_id = (os.getenv(f"EMOJI_{key.upper().replace('-', '_')}_TG_ID") or "").strip()
    if tg_id:
        return f'<tg-emoji emoji-id="{tg_id}">{emoji}</tg-emoji>'
    return emoji


def welcome_text_to_html_with_tg_emoji(text: str, user_lang: str) -> str:
    """Конвертирует приветствие в HTML и подставляет tg-emoji по emoji-id для всех эмодзи в тексте (премиум-пользователи).
    Кнопки API не поддерживают tg-emoji — кастомные эмодзи только в теле сообщения."""
    text_html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Все эмодзи из текста заменяем на tg-emoji, если задан EMOJI_*_TG_ID
    for key in _DEFAULT_EMOJIS:
        text_html = text_html.replace(get_emoji(key), get_tg_emoji_html(key))
    # Добавляем строку «Подключиться к VPN» под строкой триала (в тексте; кнопка — с сайта)
    act_full = get_text('activate_trial_button', user_lang)
    act_plain = act_full.lstrip(get_emoji("TRIAL") + " ").lstrip("🎁 ").strip() or act_full
    new_line = f"{get_tg_emoji_html('TRIAL')} {act_plain}\n"
    connect_plain = get_text('connect_button', user_lang).replace(get_emoji("CONNECT"), "", 1).strip()
    connect_line = f"{get_tg_emoji_html('CONNECT')} {connect_plain}\n"
    text_html = text_html.replace(new_line + "━━━━━━━━━━━━━━━\n", new_line + connect_line + "━━━━━━━━━━━━━━━\n")
    return text_html


def text_to_html_with_tg_emoji(text: str) -> str:
    """Конвертирует любой текст в HTML и подставляет tg-emoji по emoji-id (для премиум-пользователей)."""
    if not text:
        return text
    text_html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    for key in _DEFAULT_EMOJIS:
        text_html = text_html.replace(get_emoji(key), get_tg_emoji_html(key))
    return text_html


def normalize_ui_text(text: str) -> str:
    """Нормализует оформление текста сообщений (разделители в одну строку)."""
    if not text:
        return text
    # Заменяем любые "жирные" линии из символов ━ на дефисы, одной строкой.
    return re.sub(r'━{5,}', SEPARATOR_LINE, str(text))

# Конфигурация
CLIENT_BOT_TOKEN = os.getenv("CLIENT_BOT_TOKEN")  # Токен бота для клиентов
FLASK_API_URL = os.getenv("FLASK_API_URL", "http://localhost:5000")  # URL Flask API
YOUR_SERVER_IP = os.getenv("YOUR_SERVER_IP", "https://panel.stealthnet.app")  # URL сервера (панель)
MINIAPP_URL = os.getenv("MINIAPP_URL", YOUR_SERVER_IP)  # URL для miniapp
SERVICE_NAME = os.getenv("SERVICE_NAME", "StealthNET")  # Название сервиса (можно менять через env)

# Webhook (опционально): если BOT_USE_WEBHOOK=true, бот принимает обновления по HTTPS вместо polling
BOT_USE_WEBHOOK = os.getenv("BOT_USE_WEBHOOK", "").strip().lower() in ("1", "true", "yes")
BOT_WEBHOOK_BASE_URL = os.getenv("BOT_WEBHOOK_BASE_URL", "").strip().rstrip("/")  # например https://yourdomain.com
BOT_WEBHOOK_PATH = os.getenv("BOT_WEBHOOK_PATH", "webhook/client-bot").strip().lstrip("/")  # путь без ведущего /
BOT_WEBHOOK_PORT = int(os.getenv("BOT_WEBHOOK_PORT", "8443"))

# Путь к логотипу
LOGO_PATH = os.path.join(os.path.dirname(__file__), "logo.png")


_logo_path_logged = False

def _get_logo_path(logo_page: str = None) -> str:
    """Путь к логотипу для страницы бота. logo_page: default, main_menu, subscription_status, tariffs, и т.д."""
    global _logo_path_logged
    page = (logo_page or "default").strip() or "default"
    root = os.path.dirname(os.path.abspath(__file__))
    instance_base = os.environ.get("INSTANCE_PATH") or os.path.join(root, "instance")
    logos_dir = os.path.join(instance_base, "uploads", "bot_logos")

    def _try_in_dir(directory: str, name: str) -> str | None:
        if not directory or not os.path.isdir(directory):
            return None
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            p = os.path.join(directory, name + ext)
            if os.path.isfile(p):
                return p
        return None

    # Логируем один раз при первом вызове — чтобы понять, что видит бот
    if not _logo_path_logged:
        _logo_path_logged = True
        main_png = os.path.join(logos_dir, "main_menu.png")
        try:
            ls = os.listdir(logos_dir) if os.path.isdir(logos_dir) else []
        except Exception as e:
            ls = f"<error: {e}>"
        logger.warning(
            "[logo] root=%s INSTANCE_PATH=%s logos_dir=%s exists=%s main_menu.png exists=%s list=%s",
            root, os.environ.get("INSTANCE_PATH"), logos_dir,
            os.path.isdir(logos_dir), os.path.isfile(main_png), ls
        )

    # Сначала по ключу страницы: subscription_menu → subscription_menu.png, main_menu → main_menu.png
    # Для default — только default.png; если его нет, в конце вернём корневой logo.png
    found = _try_in_dir(logos_dir, page) or _try_in_dir(logos_dir, "default")
    if found:
        return found
    cwd_logos = os.path.join(os.getcwd(), "instance", "uploads", "bot_logos")
    found = _try_in_dir(cwd_logos, page) or _try_in_dir(cwd_logos, "default")
    if found:
        return found

    try:
        config = get_bot_config()
        logos = config.get("bot_page_logos") or {}
        relative = logos.get(page) or logos.get("default")
        if relative and isinstance(relative, str):
            relative = relative.replace("\\", "/").lstrip("/")
            if relative.startswith("instance/"):
                path_via_instance = os.path.normpath(os.path.join(instance_base, relative[len("instance/"):]))
                if os.path.isfile(path_via_instance):
                    return path_via_instance
            abs_path = os.path.normpath(os.path.join(root, relative))
            if os.path.isfile(abs_path):
                return abs_path
    except Exception as e:
        logger.warning(f"_get_logo_path({page}): {e}")
    return LOGO_PATH

# ═══════════════════════════════════════════════════════════════════════════════
# ДИНАМИЧЕСКАЯ КОНФИГУРАЦИЯ БОТА (из админки)
# ═══════════════════════════════════════════════════════════════════════════════

# Кеш конфигурации бота
_bot_config_cache = {
    'data': None,
    'last_update': 0,
    'cache_ttl': 5  # 5 секунд — для быстрого обновления при изменении в админке
}

def clear_bot_config_cache():
    """Очистить кеш конфигурации бота"""
    _bot_config_cache['data'] = None
    _bot_config_cache['last_update'] = 0

def get_bot_config() -> dict:
    """Получить конфигурацию бота из API с кешированием"""
    import time
    
    current_time = time.time()
    
    # Возвращаем из кеша если не истёк
    if _bot_config_cache['data'] and (current_time - _bot_config_cache['last_update']) < _bot_config_cache['cache_ttl']:
        return _bot_config_cache['data']
    
    # Загружаем из API
    try:
        response = requests.get(f"{FLASK_API_URL}/api/public/bot-config", timeout=5)
        if response.status_code == 200:
            config = response.json()
            _bot_config_cache['data'] = config
            _bot_config_cache['last_update'] = current_time
            logger.info("Bot config loaded from API")
            return config
    except Exception as e:
        logger.warning(f"Failed to load bot config from API: {e}")
    
    # Возвращаем кеш даже если истёк (лучше старые данные чем никаких)
    if _bot_config_cache['data']:
        return _bot_config_cache['data']
    
    # Дефолтная конфигурация
    return {
        'service_name': SERVICE_NAME,
        'support_url': '',
        'support_bot_username': '',
        'show_webapp_button': True,
        'show_trial_button': True,
        'show_referral_button': True,
        'show_support_button': True,
        'show_servers_button': True,
        'show_agreement_button': True,
        'show_offer_button': True,
        'show_topup_button': True,
        'trial_days': 3,
        'translations': {},
        'welcome_messages': {},
        'user_agreements': {},
        'offer_texts': {},
        'require_channel_subscription': False,
        'channel_id': '',
        'channel_url': '',
        'channel_subscription_texts': {}
    }

def get_service_name() -> str:
    """Получить название сервиса из конфига или env"""
    config = get_bot_config()
    return config.get('service_name') or SERVICE_NAME

def is_button_visible(button_name: str) -> bool:
    """Проверить, должна ли кнопка отображаться"""
    config = get_bot_config()
    aliases = {
        # исторически в конфиге поле называется show_referral_button (singular),
        # но в меню/порядке используется id 'referrals'
        'referrals': 'referral',
    }
    button_name = aliases.get(button_name, button_name)
    key = f'show_{button_name}_button'
    return config.get(key, True)

def get_custom_translation(key: str, lang: str) -> str:
    """Получить кастомный перевод из конфига (если есть)"""
    config = get_bot_config()
    translations = config.get('translations', {})
    lang_translations = translations.get(lang, {})
    return lang_translations.get(key, '')

def get_custom_welcome_message(lang: str) -> str:
    """Получить кастомное приветственное сообщение"""
    config = get_bot_config()
    messages = config.get('welcome_messages', {})
    return messages.get(lang, '')

def get_custom_user_agreement(lang: str) -> str:
    """Получить кастомное пользовательское соглашение"""
    config = get_bot_config()
    agreements = config.get('user_agreements', {})
    return agreements.get(lang, '')

def get_custom_offer_text(lang: str) -> str:
    """Получить кастомную оферту"""
    config = get_bot_config()
    offers = config.get('offer_texts', {})
    return offers.get(lang, '')

def get_trial_days() -> int:
    """Получить количество дней триала"""
    config = get_bot_config()
    return config.get('trial_days', 3)

# Кеш настроек триала
_trial_settings_cache = {
    'data': None,
    'last_update': 0,
    'cache_ttl': 30  # 30 секунд
}

def clear_trial_settings_cache():
    """Очистить кеш настроек триала"""
    _trial_settings_cache['data'] = None
    _trial_settings_cache['last_update'] = 0

def get_trial_settings() -> dict:
    """Получить настройки триала из API с кешированием"""
    import time
    
    current_time = time.time()
    
    # Возвращаем из кеша если не истёк
    if _trial_settings_cache['data'] and (current_time - _trial_settings_cache['last_update']) < _trial_settings_cache['cache_ttl']:
        return _trial_settings_cache['data']
    
    # Загружаем из API
    try:
        response = requests.get(f"{FLASK_API_URL}/api/public/trial-settings", timeout=5)
        if response.status_code == 200:
            settings = response.json()
            _trial_settings_cache['data'] = settings
            _trial_settings_cache['last_update'] = current_time
            return settings
    except Exception as e:
        logger.warning(f"Failed to load trial settings from API: {e}")
    
    # Возвращаем кеш даже если истёк
    if _trial_settings_cache['data']:
        return _trial_settings_cache['data']
    
    # Дефолтные настройки
    return {
        'days': 3,
        'devices': 3,
        'traffic_limit_bytes': 0,
        'enabled': True,
        'button_text_ru': '🎁 Попробовать бесплатно ({days} дня)',
        'button_text_ua': '🎁 Спробувати безкоштовно ({days} дні)',
        'button_text_en': '🎁 Try Free ({days} Days)',
        'button_text_cn': '🎁 免费试用 ({days} 天)'
    }

def get_trial_button_text(lang: str = 'ru') -> str:
    """Получить текст кнопки триала для указанного языка"""
    settings = get_trial_settings()
    
    if not settings.get('enabled', True):
        # Если триал отключен, возвращаем пустую строку (кнопка не должна отображаться)
        return ''
    
    days = settings.get('days', 3)
    button_text_key = f'button_text_{lang}'
    button_text = settings.get(button_text_key, '')
    
    # Если нет текста для языка, используем русский
    if not button_text and lang != 'ru':
        button_text = settings.get('button_text_ru', '')
    
    # Заменяем {days} на актуальное значение
    if button_text:
        button_text = button_text.replace('{days}', str(days))
    
    # Если текст всё ещё пустой, используем дефолтный
    if not button_text:
        default_texts = {
            'ru': f'🎁 Попробовать бесплатно ({days} дня)',
            'ua': f'🎁 Спробувати безкоштовно ({days} дні)',
            'en': f'🎁 Try Free ({days} Days)',
            'cn': f'🎁 免费试用 ({days} 天)'
        }
        button_text = default_texts.get(lang, default_texts['ru'])
    
    return button_text

def is_channel_subscription_required() -> bool:
    """Проверить, требуется ли подписка на канал"""
    config = get_bot_config()
    return config.get('require_channel_subscription', False)

def get_channel_id() -> str:
    """Получить ID канала для проверки подписки"""
    config = get_bot_config()
    return config.get('channel_id', '')

def get_channel_url() -> str:
    """Получить ссылку на канал"""
    config = get_bot_config()
    return config.get('channel_url', '')

def get_channel_subscription_text(lang: str) -> str:
    """Получить текст о необходимости подписки"""
    config = get_bot_config()
    texts = config.get('channel_subscription_texts', {})
    default_texts = {
        'ru': 'Для регистрации необходимо подписаться на наш канал',
        'ua': 'Для реєстрації необхідно підписатися на наш канал',
        'en': 'You need to subscribe to our channel to register',
        'cn': '您需要订阅我们的频道才能注册'
    }
    return texts.get(lang, '') or default_texts.get(lang, default_texts['ru'])

def get_buttons_order() -> list:
    """Получить порядок кнопок в меню"""
    config = get_bot_config()
    # Дефолт соответствует новому минималистичному меню.
    # (Старые пункты типа topup/servers/agreement/offer всё ещё поддерживаются, если сохранены в БД.)
    default_order = ['trial', 'connect', 'status', 'tariffs', 'options', 'referrals', 'support', 'settings', 'webapp']
    configured = config.get('buttons_order', None)
    # Если в админке задан порядок, он может быть "старым" и не содержать новых кнопок (например, options).
    # Делаем его forward-compatible: сохраняем порядок из админки, но добавляем недостающие дефолтные элементы.
    if isinstance(configured, list) and configured:
        # Убираем дубликаты и нестроковые элементы
        order = []
        for x in configured:
            if not isinstance(x, str):
                continue
            if x not in order:
                order.append(x)

        # Вставляем недостающие кнопки по дефолтной логике (после ближайшего "предыдущего" дефолтного пункта)
        for btn in default_order:
            if btn in order:
                continue
            # Ищем ближайший предыдущий дефолтный пункт, который уже есть в order
            insert_after = None
            idx_in_default = default_order.index(btn)
            for prev in reversed(default_order[:idx_in_default]):
                if prev in order:
                    insert_after = prev
                    break

            if insert_after is None:
                order.append(btn)
            else:
                pos = order.index(insert_after) + 1
                order.insert(pos, btn)

        return order

    return default_order


def _subscription_url_for_copy(url: str) -> str:
    """Ссылка в теге <code> — в Telegram отображается моноширинно и удобно копируется по тапу."""
    if not url or len(url) < 6:
        return url
    return f"<code>{html.escape(url)}</code>"


def build_main_menu_keyboard(user_lang: str, is_active: bool, subscription_url: str, expire_at, trial_used: bool = False) -> list:
    """
    Построить минималистичную клавиатуру главного меню с категориями.

    Требования:
    - Самая верхняя кнопка: триал (пока доступен), иначе "Подключить VPN" (если есть активная подписка).
    - "Статус подписки" переименован в "Моя подписка" и ведёт в подменю с действиями.
    - "Поддержка" ведёт в подменю: тикеты + оферта + соглашение.
    """
    from telegram import InlineKeyboardButton, WebAppInfo

    order = get_buttons_order()

    trial_text = get_trial_button_text(user_lang)

    def should_show(btn_id: str) -> bool:
        if btn_id == "trial":
            return (not is_active or not expire_at) and (not trial_used) and is_button_visible('trial') and bool(trial_text)
        if btn_id == "connect":
            return is_button_visible('connect') and bool(is_active and subscription_url)
        if btn_id == "status":
            return is_button_visible('status')
        if btn_id == "tariffs":
            return is_button_visible('tariffs')
        if btn_id == "options":
            return is_button_visible('options')
        if btn_id == "referrals":
            return is_button_visible('referrals')
        if btn_id == "support":
            return is_button_visible('support')
        if btn_id == "settings":
            return is_button_visible('settings')
        if btn_id == "webapp":
            return MINIAPP_URL and MINIAPP_URL.startswith("https://") and is_button_visible('webapp')

        # backward-compatible (если такие пункты остались в buttons_order)
        if btn_id == "topup":
            return is_button_visible('topup')
        if btn_id == "servers":
            return is_button_visible('servers')
        if btn_id == "configs":
            return True
        if btn_id == "agreement":
            return is_button_visible('agreement')
        if btn_id == "offer":
            return is_button_visible('offer')

        return False

    def make_button(btn_id: str):
        if btn_id == "trial":
            return InlineKeyboardButton(trial_text, callback_data="activate_trial")
        if btn_id == "connect":
            return InlineKeyboardButton(get_text('connect_button', user_lang), url=subscription_url)
        if btn_id == "status":
            return InlineKeyboardButton(get_text('status_button', user_lang), callback_data="subscription_menu")
        if btn_id == "tariffs":
            return InlineKeyboardButton(get_text('tariffs_button', user_lang), callback_data="tariffs")
        if btn_id == "options":
            return InlineKeyboardButton(get_text('options_button', user_lang), callback_data="options")
        if btn_id == "referrals":
            return InlineKeyboardButton(get_text('referrals_button', user_lang), callback_data="referrals")
        if btn_id == "support":
            return InlineKeyboardButton(get_text('support_button', user_lang), callback_data="support_menu")
        if btn_id == "settings":
            return InlineKeyboardButton(get_text('settings_button', user_lang), callback_data="settings")
        if btn_id == "webapp":
            return InlineKeyboardButton(get_text('cabinet_button', user_lang), web_app=WebAppInfo(url=MINIAPP_URL))

        # backward-compatible
        if btn_id == "configs":
            return InlineKeyboardButton(get_text('configs_button', user_lang), callback_data="sub_configs")
        if btn_id == "servers":
            return InlineKeyboardButton(get_text('servers_button', user_lang), callback_data="sub_servers")
        if btn_id == "topup":
            return InlineKeyboardButton(get_text('top_up_balance', user_lang), callback_data="sub_topup")
        if btn_id == "agreement":
            return InlineKeyboardButton(get_text('user_agreement_button', user_lang), callback_data="support_agreement")
        if btn_id == "offer":
            return InlineKeyboardButton(get_text('offer_button', user_lang), callback_data="support_offer")

        return None

    visible_ids = [bid for bid in order if isinstance(bid, str) and should_show(bid)]

    singles = {"trial", "connect", "settings", "webapp"}
    keyboard: list = []
    i = 0
    while i < len(visible_ids):
        b1 = visible_ids[i]
        btn1 = make_button(b1)
        if not btn1:
            i += 1
            continue

        if b1 in singles:
            keyboard.append([btn1])
            i += 1
            continue

        # try pair with next non-single
        if i + 1 < len(visible_ids):
            b2 = visible_ids[i + 1]
            if b2 not in singles:
                btn2 = make_button(b2)
                if btn2:
                    keyboard.append([btn1, btn2])
                    i += 2
                    continue

        keyboard.append([btn1])
        i += 1

    return keyboard


def pop_back_callback(context: ContextTypes.DEFAULT_TYPE, default: str = "main_menu") -> str:
    """Получить и удалить callback_data для кнопки 'Назад' (одноразово)."""
    try:
        cb = (context.user_data or {}).pop("_back_to", None)
        return cb or default
    except Exception:
        return default


async def check_channel_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверить, подписан ли пользователь на канал"""
    is_required = is_channel_subscription_required()
    logger.info(f"Channel subscription check: required={is_required}, user_id={user_id}")
    
    if not is_required:
        logger.info("Channel subscription not required, allowing access")
        return True
    
    channel_id = get_channel_id()
    logger.info(f"Channel ID from config: '{channel_id}'")
    
    if not channel_id:
        logger.warning("Channel ID is empty, allowing access")
        return True
    
    try:
        # Пробуем использовать channel_id как есть (может быть числовым ID или username)
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        is_subscribed = member.status in ['member', 'administrator', 'creator']
        logger.info(f"User {user_id} subscription status: {member.status}, subscribed={is_subscribed}")
        return is_subscribed
    except Exception as e:
        logger.warning(f"Error checking channel subscription for user {user_id}, channel '{channel_id}': {e}")
        return True  # В случае ошибки пропускаем проверку


def escape_markdown_v2(text: str) -> str:
    """Экранирует специальные символы для MarkdownV2"""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text


def has_cards(text: str) -> bool:
    """Проверяет, содержит ли текст карточки (╔═══╗)"""
    return '╔' in text or '║' in text or '╚' in text


def clean_markdown_for_cards(text: str) -> str:
    """Убирает Markdown-форматирование из текста с карточками"""
    # Убираем ** для жирного текста, но оставляем структуру
    result = text.replace('**', '')
    # Убираем ` для моноширинного текста
    result = result.replace('`', '')
    return result


def format_card(title: str, content: str, icon: str = "📋") -> str:
    """Форматирует красивую карточку в современном стиле"""
    return f"{icon} **{title}**\n{content}\n"


def format_info_line(label: str, value: str, icon: str = "") -> str:
    """Форматирует информационную строку"""
    if icon:
        return f"{icon} {label}: {value}\n"
    return f"{label}: {value}\n"


async def reply_with_logo(update: Update, text: str, reply_markup=None, parse_mode=None, context: ContextTypes.DEFAULT_TYPE = None, logo_page: str = None):
    """
    Отправляет сообщение с логотипом сверху.
    logo_page: ключ страницы (default, main_menu, subscription_status, tariffs, ...) — из админки «Логотипы страниц бота».
    """
    logo_path = _get_logo_path(logo_page)
    try:
        def _is_parse_entities_error(err: Exception) -> bool:
            s = str(err).lower()
            return ("can't parse entities" in s) or ("cant parse entities" in s) or ("can't parse" in s) or ("cant parse" in s)

        text = normalize_ui_text(text)

        # Обрезаем текст до 1024 символов, чтобы всегда помещался в caption
        if len(text) > 1024:
            text = text[:1021] + "..."
        
        # Получаем context из update, если не передан
        if context is None:
            # Пытаемся получить context из update (если доступен)
            context = getattr(update, '_context', None)
        
        # Проверяем существование файла логотипа
        if not os.path.exists(logo_path):
            logger.warning(f"Логотип не найден: {logo_path}, отправляем без логотипа")
            sent_message = None
            if update.message:
                sent_message = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            elif update.callback_query and update.callback_query.message:
                sent_message = await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            # Сохраняем message_id
            if sent_message and sent_message.message_id and context:
                user_data = context.user_data if hasattr(context, 'user_data') else {}
                if 'bot_message_ids' not in user_data:
                    user_data['bot_message_ids'] = []
                user_data['bot_message_ids'].append(sent_message.message_id)
                if len(user_data['bot_message_ids']) > 20:
                    user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
            return
        
        # Определяем сообщение для ответа
        message = update.message if update.message else (update.callback_query.message if update.callback_query else None)
        if not message:
            logger.error("Не удалось определить сообщение для ответа")
            return
        
        # Всегда отправляем фото с caption в одном сообщении
        with open(logo_path, 'rb') as logo_file:
            sent_message = await message.reply_photo(
                photo=logo_file,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            # Сохраняем message_id для последующего удаления
            if sent_message and sent_message.message_id and context:
                user_data = context.user_data if hasattr(context, 'user_data') else {}
                if 'bot_message_ids' not in user_data:
                    user_data['bot_message_ids'] = []
                user_data['bot_message_ids'].append(sent_message.message_id)
                # Ограничиваем список последними 20 сообщениями
                if len(user_data['bot_message_ids']) > 20:
                    user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения с логотипом: {e}")
        # Если упали на парсинге Markdown/HTML — пробуем отправить БЕЗ parse_mode
        if parse_mode is not None and _is_parse_entities_error(e):
            try:
                fallback_text = clean_markdown_for_cards(text)
                # 1) пробуем снова фото, но без parse_mode
                if os.path.exists(logo_path):
                    message = update.message if update.message else (update.callback_query.message if update.callback_query else None)
                    if message:
                        with open(logo_path, 'rb') as logo_file:
                            sent_message = await message.reply_photo(
                                photo=logo_file,
                                caption=fallback_text,
                                reply_markup=reply_markup
                            )
                            # Сохраняем message_id
                            if sent_message and sent_message.message_id and context:
                                user_data = context.user_data if hasattr(context, 'user_data') else {}
                                if 'bot_message_ids' not in user_data:
                                    user_data['bot_message_ids'] = []
                                user_data['bot_message_ids'].append(sent_message.message_id)
                                if len(user_data['bot_message_ids']) > 20:
                                    user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
                            return
                # 2) если фото не вышло — обычный текст без parse_mode
                sent_message = None
                if update.message:
                    sent_message = await update.message.reply_text(fallback_text, reply_markup=reply_markup)
                elif update.callback_query and update.callback_query.message:
                    sent_message = await update.callback_query.message.reply_text(fallback_text, reply_markup=reply_markup)
                # Сохраняем message_id
                if sent_message and sent_message.message_id and context:
                    user_data = context.user_data if hasattr(context, 'user_data') else {}
                    if 'bot_message_ids' not in user_data:
                        user_data['bot_message_ids'] = []
                    user_data['bot_message_ids'].append(sent_message.message_id)
                    if len(user_data['bot_message_ids']) > 20:
                        user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
                return
            except Exception as e_fallback:
                logger.error(f"Fallback send without parse_mode failed: {e_fallback}")

        # В случае любой другой ошибки отправляем обычное сообщение (как есть)
        try:
            sent_message = None
            if update.message:
                sent_message = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            elif update.callback_query and update.callback_query.message:
                sent_message = await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            # Сохраняем message_id
            if sent_message and sent_message.message_id and context:
                user_data = context.user_data if hasattr(context, 'user_data') else {}
                if 'bot_message_ids' not in user_data:
                    user_data['bot_message_ids'] = []
                user_data['bot_message_ids'].append(sent_message.message_id)
                if len(user_data['bot_message_ids']) > 20:
                    user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
        except Exception as e2:
            logger.error(f"Ошибка при отправке обычного сообщения: {e2}")

def get_days_text(days: int, lang: str) -> str:
    """Получить правильное склонение для дней на указанном языке"""
    if lang == 'ru':
        if days == 1:
            return f"{days} день"
        elif 2 <= days <= 4:
            return f"{days} дня"
        else:
            return f"{days} дней"
    elif lang == 'ua':
        if days == 1:
            return f"{days} день"
        elif 2 <= days <= 4:
            return f"{days} дні"
        else:
            return f"{days} днів"
    elif lang == 'en':
        return f"{days} day{'s' if days != 1 else ''}"
    elif lang == 'cn':
        return f"{days} 天"
    else:
        return f"{days} {get_text('days', lang)}"


async def safe_edit_or_send_with_logo(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode=None, logo_page: str = None):
    """
    Безопасно редактирует сообщение или отправляет новое с логотипом.
    logo_page: ключ страницы (default, main_menu, subscription_status, tariffs, ...).
    """
    logo_path = _get_logo_path(logo_page)
    query = update.callback_query
    text = normalize_ui_text(text)
    try:
        text = text_to_html_with_tg_emoji(text)
        parse_mode = "HTML"
    except Exception as e:
        logger.debug(f"text_to_html_with_tg_emoji: {e}")
    if not query:
        # Если нет callback_query, просто отправляем новое сообщение
        await reply_with_logo(update, text, reply_markup=reply_markup, parse_mode=parse_mode, context=context, logo_page=logo_page)
        return
    
    message = query.message
    if not message:
        await reply_with_logo(update, text, reply_markup=reply_markup, parse_mode=parse_mode, context=context, logo_page=logo_page)
        return
    
    # Обрезаем текст до 1024 символов для caption
    display_text = text[:1021] + "..." if len(text) > 1024 else text
    
    # Проверяем тип сообщения
    has_photo = message.photo is not None and len(message.photo) > 0
    has_text = message.text is not None
    
    # Если у нас есть логотип и текущее сообщение с фото — всегда удаляем и отправляем новое с нужным логотипом,
    # чтобы при переходе между экранами (main_menu → subscription_menu и т.д.) показывался правильный логотип страницы
    if has_photo and os.path.exists(logo_path):
        try:
            await message.delete()
        except Exception as del_err:
            logger.debug(f"Could not delete old photo message: {del_err}")
        try:
            with open(logo_path, 'rb') as logo_file:
                sent_message = await context.bot.send_photo(
                    chat_id=message.chat.id,
                    photo=logo_file,
                    caption=display_text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
                if sent_message and sent_message.message_id:
                    user_data = context.user_data if hasattr(context, 'user_data') else {}
                    if 'bot_message_ids' not in user_data:
                        user_data['bot_message_ids'] = []
                    user_data['bot_message_ids'].append(sent_message.message_id)
                    if len(user_data['bot_message_ids']) > 20:
                        user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
                return sent_message
        except Exception as e2:
            logger.warning(f"Error sending photo with logo: {e2}")
            try:
                with open(logo_path, 'rb') as logo_file:
                    sent_message = await context.bot.send_photo(
                        chat_id=message.chat.id,
                        photo=logo_file,
                        caption=clean_markdown_for_cards(display_text),
                        reply_markup=reply_markup
                    )
                    if sent_message and sent_message.message_id:
                        user_data = context.user_data if hasattr(context, 'user_data') else {}
                        if 'bot_message_ids' not in user_data:
                            user_data['bot_message_ids'] = []
                        user_data['bot_message_ids'].append(sent_message.message_id)
                        if len(user_data['bot_message_ids']) > 20:
                            user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
                        return sent_message
            except Exception as e3:
                logger.error(f"Failed to send photo: {e3}")
    
    # Если нет логотипа, пробуем отредактировать caption (если это фото)
    elif has_photo:
        try:
            await query.edit_message_caption(
                caption=display_text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            # Сохраняем message_id отредактированного сообщения
            if query.message and query.message.message_id:
                user_data = context.user_data if hasattr(context, 'user_data') else {}
                if 'bot_message_ids' not in user_data:
                    user_data['bot_message_ids'] = []
                if query.message.message_id not in user_data['bot_message_ids']:
                    user_data['bot_message_ids'].append(query.message.message_id)
                    if len(user_data['bot_message_ids']) > 20:
                        user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
            return
        except Exception as e:
            error_str = str(e).lower()
            # Если ошибка парсинга Markdown, пробуем без форматирования
            if "markdown" in error_str or "parse" in error_str or "can't parse" in error_str:
                try:
                    await query.edit_message_caption(
                        caption=clean_markdown_for_cards(display_text),
                        reply_markup=reply_markup
                    )
                    return
                except Exception as e2:
                    logger.warning(f"Failed to edit caption without formatting: {e2}")
            # Если сообщение не изменилось (тот же текст)
            elif "message is not modified" in error_str:
                return  # Просто игнорируем, всё ок
            else:
                logger.warning(f"Failed to edit photo caption: {e}")
    
    # Пробуем отредактировать текстовое сообщение
    # Но если у нас есть логотип и мы хотим его показать, лучше удалить старое и отправить новое
    if has_text:
        # Если есть логотип, удаляем старое текстовое сообщение и отправляем новое с логотипом
        if os.path.exists(logo_path):
            try:
                await message.delete()
            except Exception as e:
                logger.debug(f"Could not delete old text message: {e}")
            
            # Отправляем новое сообщение с логотипом
            try:
                with open(logo_path, 'rb') as logo_file:
                    sent_message = await context.bot.send_photo(
                        chat_id=message.chat.id,
                        photo=logo_file,
                        caption=display_text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                    if sent_message and sent_message.message_id:
                        user_data = context.user_data if hasattr(context, 'user_data') else {}
                        if 'bot_message_ids' not in user_data:
                            user_data['bot_message_ids'] = []
                        user_data['bot_message_ids'].append(sent_message.message_id)
                        if len(user_data['bot_message_ids']) > 20:
                            user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
                    return sent_message
            except Exception as e2:
                logger.warning(f"Error sending photo with logo: {e2}")
                try:
                    with open(logo_path, 'rb') as logo_file:
                        sent_message = await context.bot.send_photo(
                            chat_id=message.chat.id,
                            photo=logo_file,
                            caption=clean_markdown_for_cards(display_text),
                            reply_markup=reply_markup
                        )
                        if sent_message and sent_message.message_id:
                            user_data = context.user_data if hasattr(context, 'user_data') else {}
                            if 'bot_message_ids' not in user_data:
                                user_data['bot_message_ids'] = []
                            user_data['bot_message_ids'].append(sent_message.message_id)
                            if len(user_data['bot_message_ids']) > 20:
                                user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
                            return sent_message
                except Exception as e3:
                    logger.error(f"Failed to send photo: {e3}")
        
        # Если нет логотипа, просто редактируем текст
        try:
            await query.edit_message_text(
                text=display_text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            # Сохраняем message_id отредактированного сообщения
            if query.message and query.message.message_id:
                user_data = context.user_data if hasattr(context, 'user_data') else {}
                if 'bot_message_ids' not in user_data:
                    user_data['bot_message_ids'] = []
                if query.message.message_id not in user_data['bot_message_ids']:
                    user_data['bot_message_ids'].append(query.message.message_id)
                    if len(user_data['bot_message_ids']) > 20:
                        user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
            return query.message  # Возвращаем сообщение для получения message_id
        except Exception as e:
            error_str = str(e).lower()
            # Если ошибка парсинга Markdown, пробуем без форматирования
            if "markdown" in error_str or "parse" in error_str or "can't parse" in error_str:
                try:
                    await query.edit_message_text(
                        text=clean_markdown_for_cards(display_text),
                        reply_markup=reply_markup
                    )
                    return query.message
                except Exception as e2:
                    logger.warning(f"Failed to edit text without formatting: {e2}")
            # Если сообщение не изменилось
            elif "message is not modified" in error_str:
                return query.message  # Просто игнорируем
            else:
                logger.warning(f"Failed to edit text message: {e}")
    
    # Если редактирование не удалось, удаляем старое и отправляем новое
    try:
        await message.delete()
    except Exception as e:
        logger.warning(f"Failed to delete old message: {e}")
    
    # Отправляем новое сообщение с логотипом
    try:
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as logo_file:
                await context.bot.send_photo(
                    chat_id=message.chat.id,
                    photo=logo_file,
                    caption=display_text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
        else:
            sent_message = await context.bot.send_message(
                chat_id=message.chat.id,
                text=display_text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            if sent_message and sent_message.message_id:
                user_data = context.user_data if hasattr(context, 'user_data') else {}
                if 'bot_message_ids' not in user_data:
                    user_data['bot_message_ids'] = []
                user_data['bot_message_ids'].append(sent_message.message_id)
                if len(user_data['bot_message_ids']) > 20:
                    user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
                return sent_message
    except Exception as e2:
        logger.warning(f"Error sending message with logo: {e2}")
        try:
            if os.path.exists(logo_path):
                with open(logo_path, 'rb') as logo_file:
                    await context.bot.send_photo(
                        chat_id=message.chat.id,
                        photo=logo_file,
                        caption=clean_markdown_for_cards(display_text),
                        reply_markup=reply_markup
                    )
            else:
                await context.bot.send_message(
                    chat_id=message.chat.id,
                    text=clean_markdown_for_cards(display_text),
                    reply_markup=reply_markup
                )
        except Exception as e3:
            logger.error(f"Final fallback failed: {e3}")


if not CLIENT_BOT_TOKEN:
    raise ValueError("CLIENT_BOT_TOKEN не установлен в переменных окружения!")

# Проверка URL для miniapp (должен быть HTTPS)
if MINIAPP_URL and not MINIAPP_URL.startswith("https://"):
    logger.warning(f"MINIAPP_URL должен начинаться с https://, текущее значение: {MINIAPP_URL}")


class ClientBotAPI:
    """Класс для взаимодействия с Flask API"""
    
    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip('/')
        self.session = requests.Session()
        
        # Настройка connection pooling для переиспользования соединений
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        # Настройка retry стратегии
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            raise_on_status=False
        )
        
        # Настройка HTTP adapter с connection pooling
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=retry_strategy,
            pool_block=False
        )
        
        # Применяем adapter для HTTP и HTTPS
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Настройка keep-alive заголовков
        self.session.headers.update({
            'Connection': 'keep-alive',
            'Keep-Alive': 'timeout=60, max=100'
        })
    
    def get_user_by_telegram_id(self, telegram_id: int) -> Optional[dict]:
        """Получить пользователя по Telegram ID через API бота или создать JWT"""
        # Сначала пытаемся получить JWT токен через telegram-login эндпоинт
        # Но для бота нам нужен другой подход - создадим специальный эндпоинт
        # Пока используем прямой запрос к БД через Flask API
        
        # Временное решение: используем внутренний эндпоинт для ботов
        try:
            response = self.session.post(
                f"{self.api_url}/api/bot/get-token",
                json={"telegram_id": telegram_id},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("token")
            elif response.status_code == 403:
                # Аккаунт заблокирован
                data = response.json()
                if data.get("code") == "ACCOUNT_BLOCKED":
                    # Возвращаем специальный маркер блокировки
                    return {"blocked": True, "block_reason": data.get("block_reason", "")}
        except Exception as e:
            logger.error(f"Ошибка получения токена: {e}")
        
        return None
    
    def register_user(self, telegram_id: int, telegram_username: str = "", ref_code: str = None, preferred_lang: str = None, preferred_currency: str = None) -> Optional[dict]:
        """Зарегистрировать пользователя через бота"""
        try:
            payload = {
                "telegram_id": telegram_id,
                "telegram_username": telegram_username,
                "ref_code": ref_code
            }
            if preferred_lang:
                payload["preferred_lang"] = preferred_lang
            if preferred_currency:
                payload["preferred_currency"] = preferred_currency
            
            response = self.session.post(
                f"{self.api_url}/api/bot/register",
                json=payload,
                timeout=30
            )
            # 201: created, 200: already registered (returns token), sometimes 400 in older versions
            if response.status_code in (200, 201, 400):
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка регистрации: {e}")
        return None
    
    def get_credentials(self, telegram_id: int) -> Optional[dict]:
        """Получить логин (email) и пароль пользователя для входа на сайте"""
        try:
            response = self.session.post(
                f"{self.api_url}/api/bot/get-credentials",
                json={"telegram_id": telegram_id},
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка получения credentials: {e}")
        return None
    
    def get_user_data(self, token: str, force_refresh: bool = False) -> Optional[dict]:
        """Получить данные пользователя с retry логикой"""
        headers = {
            "Authorization": f"Bearer {token}",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
        # Добавляем timestamp для предотвращения кэширования
        url = f"{self.api_url}/api/client/me"
        if force_refresh:
            url += f"?_t={int(datetime.now().timestamp() * 1000)}"
        
        # Retry логика с экспоненциальной задержкой
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=15  # Увеличено до 15 секунд
                )
                if response.status_code == 200:
                    data = response.json()
                    user_data = data.get("response") or data
                    # Логируем для отладки
                    if user_data:
                        logger.debug(f"User data keys: {list(user_data.keys())[:15]}")
                        logger.debug(f"User preferred_lang: {user_data.get('preferred_lang')}, preferred_currency: {user_data.get('preferred_currency')}")
                    return user_data
                elif response.status_code == 401:
                    # Не валидный токен, не повторяем
                    logger.warning(f"Unauthorized access attempt (401) for get_user_data")
                    return None
                else:
                    logger.warning(f"HTTP {response.status_code} при получении данных пользователя (попытка {attempt + 1}/{max_retries})")
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout при получении данных пользователя (попытка {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2 ** attempt)  # Экспоненциальная задержка: 1s, 2s, 4s
                else:
                    logger.error(f"Превышено максимальное количество попыток при получении данных пользователя")
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Ошибка соединения при получении данных пользователя (попытка {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(2 ** attempt)
                    # Пытаемся пересоздать соединение
                    try:
                        self.session.close()
                        self.session = requests.Session()
                        # Повторно применяем adapter
                        from requests.adapters import HTTPAdapter
                        from urllib3.util.retry import Retry
                        retry_strategy = Retry(
                            total=3,
                            backoff_factor=1,
                            status_forcelist=[429, 500, 502, 503, 504],
                            allowed_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                            raise_on_status=False
                        )
                        adapter = HTTPAdapter(
                            pool_connections=10,
                            pool_maxsize=20,
                            max_retries=retry_strategy,
                            pool_block=False
                        )
                        self.session.mount("http://", adapter)
                        self.session.mount("https://", adapter)
                        self.session.headers.update({
                            'Connection': 'keep-alive',
                            'Keep-Alive': 'timeout=60, max=100'
                        })
                    except Exception as reset_error:
                        logger.error(f"Ошибка при пересоздании сессии: {reset_error}")
                else:
                    logger.error(f"Превышено максимальное количество попыток при ошибке соединения")
            except Exception as e:
                logger.error(f"Неожиданная ошибка при получении данных пользователя: {e}")
                if attempt == max_retries - 1:
                    return None
        
        return None
    
    def get_tariffs(self) -> list:
        """Получить список тарифов"""
        try:
            response = self.session.get(
                f"{self.api_url}/api/public/tariffs",
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка получения тарифов: {e}")
        return []
    
    def get_tariff_features(self) -> dict:
        """Получить функции тарифов по tier"""
        try:
            response = self.session.get(
                f"{self.api_url}/api/public/tariff-features",
                timeout=10
            )
            if response.status_code == 200:
                payload = response.json()
                # Новый формат: dict {tierCode: [features...]}
                if isinstance(payload, dict):
                    cleaned = {}
                    for k, v in payload.items():
                        if not k:
                            continue
                        if isinstance(v, str):
                            try:
                                import json
                                v = json.loads(v)
                            except Exception:
                                v = []
                        cleaned[k] = v if isinstance(v, list) else []
                    return cleaned

                # Старый формат: список объектов [{tier, features}, ...]
                features_list = payload if isinstance(payload, list) else []
                features_dict = {}
                for item in features_list:
                    tier = item.get("tier") if isinstance(item, dict) else None
                    features_json = item.get("features") if isinstance(item, dict) else None
                    if tier is None:
                        continue
                    if features_json is None:
                        features_dict[str(tier)] = []
                        continue
                    try:
                        import json
                        features = json.loads(features_json) if isinstance(features_json, str) else features_json
                        features_dict[str(tier)] = features if isinstance(features, list) else []
                    except Exception:
                        features_dict[str(tier)] = []
                return features_dict
        except Exception as e:
            logger.error(f"Ошибка получения функций тарифов: {e}")
        return {}

    def get_tariff_levels(self) -> list:
        """Получить публичные уровни тарифов"""
        try:
            response = self.session.get(
                f"{self.api_url}/api/public/tariff-levels",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Ошибка получения уровней тарифов: {e}")
        return []
    
    def get_branding(self) -> dict:
        """Получить настройки брендинга (для названий функций)"""
        try:
            response = self.session.get(
                f"{self.api_url}/api/public/branding",
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка получения брендинга: {e}")
        return {}
    
    def get_system_settings(self) -> dict:
        """Получить системные настройки (активные языки и валюты) с кэшированием на 1 минуту"""
        # Используем простой кэш в памяти
        if not hasattr(self, '_system_settings_cache') or not hasattr(self, '_system_settings_cache_time'):
            self._system_settings_cache = None
            self._system_settings_cache_time = 0
        
        # Проверяем кэш (1 минута = 60 секунд)
        current_time = datetime.now().timestamp()
        if self._system_settings_cache and (current_time - self._system_settings_cache_time) < 60:
            return self._system_settings_cache
        
        try:
            response = self.session.get(
                f"{self.api_url}/api/public/system-settings",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                # Сохраняем в кэш
                self._system_settings_cache = data
                self._system_settings_cache_time = current_time
                return data
        except Exception as e:
            logger.error(f"Ошибка получения системных настроек: {e}")
        
        # Возвращаем значения по умолчанию, если не удалось получить
        default_settings = {
            "active_languages": ["ru", "ua", "en", "cn"],
            "active_currencies": ["uah", "rub", "usd"]
        }
        return default_settings
    
    def get_available_payment_methods(self) -> list:
        """Получить список доступных способов оплаты"""
        try:
            response = self.session.get(
                f"{self.api_url}/api/public/available-payment-methods",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("available_methods", [])
        except Exception as e:
            logger.error(f"Ошибка получения способов оплаты: {e}")
        return []

    def get_purchase_options(self) -> dict:
        """Получить опции для покупки (сгруппированные по типу)"""
        try:
            response = self.session.get(
                f"{self.api_url}/api/public/purchase-options",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json() or {}
                return data.get("options", {}) or {}
        except Exception as e:
            logger.error(f"Ошибка получения опций: {e}")
        return {"traffic": [], "devices": [], "squad": []}

    def create_option_payment(
        self,
        token: str,
        option_id: int,
        payment_provider: str,
        config_id: Optional[int] = None
    ) -> dict:
        """Создать платеж за опцию"""
        try:
            payload = {
                "option_id": int(option_id),
                "payment_provider": payment_provider,
                "source": "bot"
            }
            if config_id:
                payload["config_id"] = int(config_id)

            response = self.session.post(
                f"{self.api_url}/api/client/create-option-payment",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
            try:
                return response.json()
            except Exception:
                return {"success": False, "message": f"HTTP {response.status_code}"}
        except Exception as e:
            logger.error(f"Ошибка создания платежа за опцию: {e}")
        return {"success": False, "message": "Ошибка создания платежа"}
    
    def get_nodes(self, token: str) -> list:
        """Получить список серверов"""
        try:
            response = self.session.get(
                f"{self.api_url}/api/client/nodes",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("response", {}).get("activeNodes", [])
        except Exception as e:
            logger.error(f"Ошибка получения серверов: {e}")
        return []
    
    def activate_trial(self, token: str) -> dict:
        """Активировать триал"""
        try:
            response = self.session.post(
                f"{self.api_url}/api/client/activate-trial",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка активации триала: {e}")
        return {"success": False, "message": "Ошибка активации триала"}

    def get_configs(self, token: str, force_refresh: bool = False) -> dict:
        """Получить список конфигов пользователя (primary + дополнительные)"""
        try:
            url = f"{self.api_url}/api/client/configs"
            if force_refresh:
                url += "?force_refresh=true"
            response = self.session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка получения конфигов: {e}")
        return {"configs": []}
    
    def create_payment(
        self,
        token: str,
        tariff_id: int,
        payment_provider: str,
        promo_code: Optional[str] = None,
        config_id: Optional[int] = None,
        create_new_config: bool = False
    ) -> dict:
        """Создать платеж"""
        try:
            payload = {
                "tariff_id": tariff_id,
                "payment_provider": payment_provider,
                "promo_code": promo_code,
                "source": "bot"
            }
            if config_id:
                payload["config_id"] = int(config_id)
            if create_new_config:
                payload["create_new_config"] = True
            response = self.session.post(
                f"{self.api_url}/api/client/create-payment",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка создания платежа: {e}")
        return {"success": False, "message": "Ошибка создания платежа"}
    
    def get_support_tickets(self, token: str) -> list:
        """Получить список тикетов поддержки"""
        try:
            response = self.session.get(
                f"{self.api_url}/api/client/support-tickets",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка получения тикетов: {e}")
        return []
    
    def create_support_ticket(self, token: str, subject: str, message: str) -> dict:
        """Создать тикет поддержки"""
        try:
            response = self.session.post(
                f"{self.api_url}/api/client/support-tickets",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"subject": subject, "message": message},
                timeout=10
            )
            # API возвращает 201 при создании
            if response.status_code in [200, 201]:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка создания тикета: {e}")
        return {"success": False, "message": "Ошибка создания тикета"}
    
    def get_ticket_messages(self, token: str, ticket_id: int) -> dict:
        """Получить сообщения тикета"""
        try:
            response = self.session.get(
                f"{self.api_url}/api/support-tickets/{ticket_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка получения сообщений тикета: {e}")
        return {}
    
    def save_settings(self, token: str, lang: Optional[str] = None, currency: Optional[str] = None) -> dict:
        """Сохранить настройки пользователя (язык, валюта)"""
        try:
            payload = {}
            if lang:
                payload["lang"] = lang
            if currency:
                payload["currency"] = currency
            
            if not payload:
                return {"success": False, "message": "Нет данных для сохранения"}
            
            logger.info(f"Saving settings: {payload}")
            response = self.session.post(
                f"{self.api_url}/api/client/settings",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=10
            )
            logger.info(f"Settings save response: {response.status_code}, {response.text}")
            if response.status_code == 200:
                return {"success": True, "message": "Настройки сохранены"}
            else:
                logger.error(f"Failed to save settings: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек: {e}")
        return {"success": False, "message": "Ошибка сохранения настроек"}
    
    def reply_to_ticket(self, token: str, ticket_id: int, message: str) -> dict:
        """Ответить на тикет"""
        try:
            response = self.session.post(
                f"{self.api_url}/api/support-tickets/{ticket_id}/reply",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"message": message},
                timeout=10
            )
            if response.status_code in [200, 201]:
                return response.json()
        except Exception as e:
            logger.error(f"Ошибка ответа на тикет: {e}")
        return {"success": False, "message": "Ошибка ответа на тикет"}


# Инициализация API клиента
api = ClientBotAPI(FLASK_API_URL)

# Кэш токенов пользователей (в продакшене лучше использовать Redis)
# Формат:
#   user_tokens[telegram_id] = {"token": "<jwt>", "exp": <epoch_seconds>}
user_tokens = {}

# Словари переводов для разных языков
TRANSLATIONS = {
    'ru': {
        'main_menu': 'Главное меню',
        'subscription_status': 'Статус подписки',
        'tariffs': 'Тарифы',
        'servers': 'Серверы',
        'referrals': 'Рефералы',
        'support': 'Поддержка',
        'settings': '⚙️ Настройки',
        'currency': 'Валюта',
        'language': '🌐 Язык',
        'select_currency': 'Выберите валюту:',
        'select_language': 'Выберите язык:',
        'settings_saved': '✅ Настройки сохранены',
        'back': '🔙 Назад',
        'welcome': 'Добро пожаловать',
        'subscription_active': 'Активна',
        'subscription_inactive': 'Не активна',
        'expires': 'Истекает',
        'days_left': 'Осталось дней',
        'traffic': 'Трафик',
        'unlimited': 'Безлимитный',
        'used': 'Использовано',
        'login_data': 'Данные для входа',
        'email': 'Логин',
        'password': 'Пароль',
        'connect': 'Подключиться',
        'activate_trial': 'Активировать триал',
        'select_tariff': 'Выбрать тариф',
        'price': 'Цена',
        'duration': 'Длительность',
        'days': 'дней',
        'select_payment': 'Выберите способ оплаты',
        'payment_created': 'Платеж создан',
        'go_to_payment': 'Перейти к оплате',
        'pay_with_balance': 'Оплатить с баланса',
        'insufficient_balance': 'Недостаточно средств',
        'top_up_balance': '💰 Пополнить баланс',
        'enter_amount': 'Введите сумму пополнения',
        'invalid_amount': 'Неверная сумма',
        'select_topup_method': 'Выберите способ пополнения',
        'balance_topup_created': 'Создан платеж на пополнение баланса',
        'balance': 'Баланс',
        'amount': 'Сумма',
        'select_amount_hint': 'Выберите сумму или введите свою',
        'enter_custom_amount': '✏️ Ввести свою сумму',
        'send_amount': 'Отправьте сумму пополнения числом',
        'invalid_amount_format': 'Неверный формат суммы. Введите число (например: 1500)',
        'amount_too_small': 'Минимальная сумма пополнения: 1',
        'go_to_payment_button': '💳 Перейти к оплате',
        'go_to_payment_text': 'Перейдите по ссылке для оплаты',
        'after_payment': 'После оплаты баланс будет автоматически пополнен',
        'payment_successful': 'Платеж успешно обработан',
        'payment_processed': 'Ваш платеж обрабатывается',
        'subscription_updating': 'Подписка обновляется...',
        'referral_program': 'Реферальная программа',
        'your_referral_link': 'Ваша реферальная ссылка',
        'your_code': 'Ваш код',
        'copy_link': 'Копировать ссылку',
        'link_copied': 'Ссылка отправлена в чат',
        'support_tickets': 'Ваши тикеты',
        'create_ticket': 'Создать тикет',
        'ticket_created': 'Тикет создан',
        'ticket_number': 'Номер тикета',
        'subject': 'Тема',
        'reply': 'Ответить',
        'reply_sent': 'Ответ отправлен',
        'servers_list': 'Список серверов',
        'online': 'Онлайн',
        'offline': 'Офлайн',
        'not_registered': 'Вы еще не зарегистрированы',
        'register': 'Зарегистрироваться',
        'register_success': 'Регистрация успешна',
        'trial_activated': 'Триал активирован',
        'trial_days': 'Вы получили 3 дня премиум доступа',
        'error': 'Ошибка',
        'auth_error': 'Ошибка авторизации',
        'not_found': 'Не найдено',
        'loading': 'Загрузка...',
        'welcome_bot': f'Добро пожаловать в {SERVICE_NAME} VPN Bot!',
        'not_registered_text': 'Вы еще не зарегистрированы в системе.',
        'register_here': 'Вы можете зарегистрироваться прямо здесь в боте или на сайте.',
        'after_register': 'После регистрации вы получите логин и пароль для входа на сайте.',
        'welcome_user': 'Добро пожаловать',
        'stealthnet_bot': f'{SERVICE_NAME} VPN Bot',
        'subscription_status_title': 'Статус подписки',
        'active': 'Активна',
        'inactive': 'Не активна',
        'expires_at': 'Истекает',
        'days_remaining': 'Осталось дней',
        'traffic_title': 'Трафик',
        'unlimited_traffic': 'Безлимитный',
        'traffic_used': 'Использовано',
        'login_data_title': 'Данные для входа на сайте',
        'login_label': 'Логин',
        'password_label': 'Пароль',
        'password_set': 'Установлен (недоступен)',
        'password_not_set': 'Пароль не установлен',
        'data_not_found': 'Данные не найдены',
        'connect_button': '🚀 Подключиться к VPN',
        'activate_trial_button': '💡 Активировать триал',
        'select_tariff_button': '💎 Выбрать тариф',
        'main_menu_button': 'Главное меню',
        'status_button': '📊 Моя подписка',
        'tariffs_button': '💎 Тарифы',
        'options_button': '📦 Опции',
        'configs_button': '🧩 Подписки',
        'servers_button': '🌐 Серверы',
        'referrals_button': '🎁 Рефералка',
        'support_button': '💬 Поддержка',
        'contact_support_button': '💬 Связаться с поддержкой',
        'support_bot_button': '🤖 Бот Поддержки',
        'administration_button': '👮 Администрация',
        'settings_button': '⚙️ Настройки',
        'cabinet_button': '📱 Web Кабинет',
        'documents_button': 'Документы',
        'user_agreement_button': '📄 Соглашение',
        'offer_button': '📋 Оферта',
        'refund_policy_button': 'Политика возврата',
        'user_agreement_title': '📄 Пользовательское соглашение',
        'offer_title': '📋 Публичная оферта',
        'refund_policy_title': '💰 Политика возврата',
        'subscription_link': 'Ссылка подключения',
        'your_id': 'ID',
        'devices_available': 'доступно',
        'devices_unlimited': 'Безлимит',
        'copy_link': '📋 Копировать ссылку',
        'traffic_usage': 'Использование трафика',
        'unlimited_traffic_full': 'Безлимитный трафик',
        'use_login_password': 'Используйте этот логин и пароль для входа на сайте',
        'select_tariff_type': 'Выберите тип тарифа',
        # Эти значения используются как fallback, основное название берется из брендинга
        'basic_tier': 'Базовый',
        'pro_tier': 'Премиум',
        'elite_tier': 'Элитный',
        'from_price': 'От',
        'available_options': 'Доступно вариантов',
        'select_duration': 'Выберите длительность подписки',
        'per_day': 'день',
        'back_to_type': '🔙 К выбору типа',
        'servers_title': 'Серверы',
        'available_servers': 'Доступные серверы',
        'total_servers': 'Всего серверов',
        'and_more': 'и еще',
        'servers_not_found': 'Серверы не найдены',
        'subscription_not_active': 'Подписка не активна. Активируйте триал или выберите тариф',
        'referral_program_title': 'Реферальная программа',
        'invite_friends': 'Приглашайте друзей и получайте бонусы!',
        'your_referral_code': 'Ваш код',
        'referral_code_not_found': 'Реферальный код не найден',
        'support_title': 'Поддержка',
        'your_tickets': 'Ваши тикеты',
        'no_tickets': 'У вас пока нет тикетов.',
        'select_action': 'Выберите действие',
        'create_ticket_button': 'Создать тикет',
        'ticket': 'Тикет',
        'ticket_created_success': 'Тикет создан!',
        'ticket_number_label': 'Номер тикета',
        'we_will_reply': 'Мы ответим вам в ближайшее время.',
        'view_ticket_support': 'Вы можете просмотреть тикет в разделе поддержки.',
        'reply_sent_success': 'Ответ отправлен!',
        'your_reply_added': 'Ваш ответ был добавлен в тикет.',
        'tariff_selected': 'Выбран тариф',
        'price_label': 'Цена',
        'duration_label': 'Длительность',
        'payment_methods': 'Выберите способ оплаты',
        'no_payment_methods': 'Нет доступных способов оплаты. Обратитесь в поддержку.',
        'back_to_tariffs': '🔙 Назад к тарифам',
        'payment_created_title': 'Платеж создан',
        'go_to_payment_text': 'Перейдите по ссылке для оплаты:',
        'after_payment': 'После успешной оплаты подписка будет активирована автоматически.',
        'go_to_payment_button': '💳 Перейти к оплате',
        'trial_activated_title': 'Триал активирован!',
        'trial_days_received': 'Вы получили 3 дня премиум доступа.',
        'enjoy_vpn': 'Наслаждайтесь VPN без ограничений!',
        'registration_success': 'Регистрация успешна!',
        'your_login_data': 'Ваши данные для входа на сайте',
        'important_save': 'ВАЖНО: Сохраните эти данные! Пароль больше не будет показан.',
        'login_site': 'Войти на сайте',
        'now_use_bot': 'Теперь вы можете использовать все функции бота!',
        'already_registered': 'Вы уже зарегистрированы!',
        'registering': 'Регистрируем...',
        'registration_error': 'Ошибка регистрации',
        'registration_failed': 'Не удалось зарегистрироваться. Попробуйте позже или зарегистрируйтесь на сайте:',
        'ticket_view_title': 'Тикет',
        'try_again_button': '🔙 Попробовать снова',
        'copy_token_button': '📋 Скопировать токен',
        'my_configs_button': '🧩 Мои подписки',
        'new_subscription_button': '➕ Новая подписка',
        'extend_button': '💎 Продлить',
        'share_button': '📤 Поделиться',
        'status_label': 'Статус',
        'subject_label': 'Тема',
        'messages_label': 'Сообщения',
        'you': 'Вы',
        'support_label': 'Поддержка',
        'reply_button': '💬 Ответить',
        'back_to_support': '🔙 К поддержке',
        'creating_ticket': 'Создание тикета',
        'send_subject': 'Отправьте тему тикета в следующем сообщении:',
        'subject_saved': 'Тема сохранена. Теперь отправьте текст сообщения:',
        'reply_to_ticket': 'Ответ на тикет',
        'send_reply': 'Отправьте ваш ответ в следующем сообщении:',
        'currency_changed': 'Валюта изменена',
        'language_changed': 'Язык изменен',
        'currency_already_selected': 'Эта валюта уже выбрана',
        'language_already_selected': 'Этот язык уже выбран',
        'invalid_currency': 'Неверная валюта',
        'invalid_language': 'Неверный язык',
        'failed_to_load': 'Не удалось загрузить данные',
        'failed_to_load_user': 'Не удалось загрузить данные пользователя',
        'tariffs_not_found': 'Тарифы не найдены',
        'tariff_not_found': 'Тариф не найден',
        'invalid_tariff_id': 'Ошибка: неверный ID тарифа',
        'link_sent_to_chat': 'Ссылка отправлена в чат',
        'click_to_copy': 'Нажмите на ссылку выше, чтобы скопировать её.',
        'click_link_to_copy': 'Нажмите на ссылку выше, чтобы скопировать её.',
        'send_ticket_subject': 'Отправьте тему тикета в следующем сообщении',
        'send_your_reply': 'Отправьте ваш ответ в следующем сообщении',
        'invalid_ticket_id': 'Ошибка: неверный ID тикета',
        'ticket_not_found': 'Не удалось загрузить тикет',
        'ticket_not_exists': 'Возможно, тикет не существует или у вас нет доступа.',
        'loading_ticket': 'Загружаем тикет...',
        'unknown': 'Неизвестно',
        'error_loading': 'Ошибка',
        'on_site': 'на сайте',
        'or': 'или',
        'activating_trial': 'Активируем триал',
        'error_activating_trial': 'Ошибка активации триала',
        'failed_activate_trial': 'Не удалось активировать триал. Попробуйте позже.',
        'creating_payment': 'Создаем платеж',
        'error_creating_payment': 'Ошибка создания платежа',
    },
    'ua': {
        'main_menu': 'Головне меню',
        'subscription_status': 'Статус підписки',
        'tariffs': 'Тарифи',
        'servers': 'Сервери',
        'referrals': 'Реферали',
        'support': 'Підтримка',
        'settings': '⚙️ Налаштування',
        'currency': 'Валюта',
        'language': '🌐 Мова',
        'select_currency': 'Виберіть валюту:',
        'select_language': 'Виберіть мову:',
        'settings_saved': '✅ Налаштування збережено',
        'back': '🔙 Назад',
        'welcome': 'Ласкаво просимо',
        'subscription_active': 'Активна',
        'subscription_inactive': 'Не активна',
        'expires': 'Закінчується',
        'days_left': 'Залишилось днів',
        'traffic': 'Трафік',
        'unlimited': 'Безлімітний',
        'used': 'Використано',
        'login_data': 'Дані для входу',
        'email': 'Логін',
        'password': 'Пароль',
        'connect': 'Підключитися',
        'activate_trial': 'Активувати триал',
        'select_tariff': 'Вибрати тариф',
        'price': 'Ціна',
        'duration': 'Тривалість',
        'days': 'днів',
        'select_payment': 'Виберіть спосіб оплати',
        'payment_created': 'Платіж створено',
        'go_to_payment': 'Перейти до оплати',
        'pay_with_balance': 'Оплатити з балансу',
        'insufficient_balance': 'Недостатньо коштів',
        'top_up_balance': '💰 Поповнити баланс',
        'enter_amount': 'Введіть суму поповнення',
        'invalid_amount': 'Невірна сума',
        'select_topup_method': 'Виберіть спосіб поповнення',
        'balance_topup_created': 'Створено платіж на поповнення балансу',
        'balance': 'Баланс',
        'amount': 'Сума',
        'select_amount_hint': 'Виберіть суму або введіть свою',
        'enter_custom_amount': '✏️ Ввести свою суму',
        'send_amount': 'Відправте суму поповнення числом',
        'invalid_amount_format': 'Невірний формат суми. Введіть число (наприклад: 1500)',
        'amount_too_small': 'Мінімальна сума поповнення: 1',
        'go_to_payment_button': '💳 Перейти до оплати',
        'go_to_payment_text': 'Перейдіть за посиланням для оплати',
        'after_payment': 'Після оплати баланс буде автоматично поповнено',
        'payment_successful': 'Платіж успішно оброблено',
        'payment_processed': 'Ваш платіж обробляється',
        'subscription_updating': 'Підписка оновлюється...',
        'referral_program': 'Реферальна програма',
        'your_referral_link': 'Ваша реферальна посилання',
        'your_code': 'Ваш код',
        'copy_link': 'Скопіювати посилання',
        'link_copied': 'Посилання відправлено в чат',
        'support_tickets': 'Ваші тікети',
        'create_ticket': 'Створити тікет',
        'ticket_created': 'Тікет створено',
        'ticket_number': 'Номер тікета',
        'subject': 'Тема',
        'reply': 'Відповісти',
        'reply_sent': 'Відповідь відправлено',
        'servers_list': 'Список серверів',
        'online': 'Онлайн',
        'offline': 'Офлайн',
        'not_registered': 'Ви ще не зареєстровані',
        'register': 'Зареєструватися',
        'register_success': 'Реєстрація успішна',
        'trial_activated': 'Триал активовано',
        'trial_days': 'Ви отримали 3 дні преміум доступу',
        'error': 'Помилка',
        'auth_error': 'Помилка авторизації',
        'not_found': 'Не знайдено',
        'loading': 'Завантаження...',
        'welcome_bot': f'Ласкаво просимо в {SERVICE_NAME} VPN Bot!',
        'not_registered_text': 'Ви ще не зареєстровані в системі.',
        'register_here': 'Ви можете зареєструватися прямо тут в боті або на сайті.',
        'after_register': 'Після реєстрації ви отримаєте логін і пароль для входу на сайті.',
        'welcome_user': 'Ласкаво просимо',
        'stealthnet_bot': f'{SERVICE_NAME} VPN Bot',
        'subscription_status_title': 'Статус підписки',
        'active': 'Активна',
        'inactive': 'Не активна',
        'expires_at': 'Закінчується',
        'days_remaining': 'Залишилось днів',
        'traffic_title': 'Трафік',
        'unlimited_traffic': 'Безлімітний',
        'traffic_used': 'Використано',
        'login_data_title': 'Дані для входу на сайті',
        'login_label': 'Логін',
        'password_label': 'Пароль',
        'password_set': 'Встановлено (недоступно)',
        'password_not_set': 'Пароль не встановлено',
        'data_not_found': 'Дані не знайдено',
        'connect_button': '🚀 Підключитися до VPN',
        'activate_trial_button': '💡 Активувати тріал',
        'select_tariff_button': '💎 Вибрати тариф',
        'main_menu_button': 'Головне меню',
        'status_button': '📊 Моя підписка',
        'tariffs_button': '💎 Тарифи',
        'options_button': '📦 Опції',
        'configs_button': '🧩 Підписки',
        'servers_button': '🌐 Сервери',
        'referrals_button': '🎁 Рефералка',
        'support_button': '💬 Підтримка',
        'contact_support_button': '💬 Зв\'язатися з підтримкою',
        'support_bot_button': '🤖 Бот Підтримки',
        'administration_button': '👮 Адміністрація',
        'settings_button': '⚙️ Налаштування',
        'cabinet_button': '📱 Web Кабінет',
        'documents_button': 'Документи',
        'user_agreement_button': '📄 Угода',
        'offer_button': '📋 Оферта',
        'refund_policy_button': 'Політика повернення',
        'user_agreement_title': '📄 Користувацька угода',
        'offer_title': '📋 Публічна оферта',
        'refund_policy_title': '💰 Політика повернення',
        'subscription_link': 'Посилання підключення',
        'your_id': 'ID',
        'devices_available': 'доступно',
        'devices_unlimited': 'Безліміт',
        'copy_link': '📋 Копіювати посилання',
        'traffic_usage': 'Використання трафіку',
        'unlimited_traffic_full': 'Безлімітний трафік',
        'use_login_password': 'Використовуйте цей логін і пароль для входу на сайті',
        'select_tariff_type': 'Виберіть тип тарифу',
        'basic_tier': 'Базовий',
        'pro_tier': 'Преміум',
        'elite_tier': 'Елітний',
        'from_price': 'Від',
        'available_options': 'Доступно варіантів',
        'select_duration': 'Виберіть тривалість підписки',
        'per_day': 'день',
        'back_to_type': '🔙 До вибору типу',
        'servers_title': 'Сервери',
        'available_servers': 'Доступні сервери',
        'total_servers': 'Всього серверів',
        'and_more': 'і ще',
        'servers_not_found': 'Сервери не знайдено',
        'subscription_not_active': 'Підписка не активна. Активуйте триал або виберіть тариф',
        'referral_program_title': 'Реферальна програма',
        'invite_friends': 'Запрошуйте друзів і отримуйте бонуси!',
        'your_referral_code': 'Ваш код',
        'referral_code_not_found': 'Реферальний код не знайдено',
        'support_title': 'Підтримка',
        'your_tickets': 'Ваші тікети',
        'no_tickets': 'У вас поки немає тікетів.',
        'select_action': 'Виберіть дію',
        'create_ticket_button': 'Створити тікет',
        'ticket': 'Тікет',
        'ticket_created_success': 'Тікет створено!',
        'ticket_number_label': 'Номер тікета',
        'we_will_reply': 'Ми відповімо вам найближчим часом.',
        'view_ticket_support': 'Ви можете переглянути тікет в розділі підтримки.',
        'reply_sent_success': 'Відповідь відправлено!',
        'your_reply_added': 'Ваша відповідь була додана в тікет.',
        'tariff_selected': 'Вибрано тариф',
        'price_label': 'Ціна',
        'duration_label': 'Тривалість',
        'payment_methods': 'Виберіть спосіб оплати',
        'no_payment_methods': 'Немає доступних способів оплати. Зверніться в підтримку.',
        'back_to_tariffs': '🔙 Назад до тарифів',
        'payment_created_title': 'Платіж створено',
        'go_to_payment_text': 'Перейдіть за посиланням для оплати:',
        'after_payment': 'Після успішної оплати підписка буде активована автоматично.',
        'go_to_payment_button': '💳 Перейти до оплати',
        'trial_activated_title': 'Триал активовано!',
        'trial_days_received': 'Ви отримали 3 дні преміум доступу.',
        'enjoy_vpn': 'Насолоджуйтесь VPN без обмежень!',
        'registration_success': 'Реєстрація успішна!',
        'your_login_data': 'Ваші дані для входу на сайті',
        'important_save': 'ВАЖЛИВО: Збережіть ці дані! Пароль більше не буде показано.',
        'login_site': 'Увійти на сайті',
        'now_use_bot': 'Тепер ви можете використовувати всі функції бота!',
        'already_registered': 'Ви вже зареєстровані!',
        'registering': 'Реєструємо...',
        'registration_error': 'Помилка реєстрації',
        'registration_failed': 'Не вдалося зареєструватися. Спробуйте пізніше або зареєструйтеся на сайті:',
        'ticket_view_title': 'Тікет',
        'try_again_button': '🔙 Спробувати знову',
        'copy_token_button': '📋 Скопіювати токен',
        'my_configs_button': '🧩 Мої підписки',
        'new_subscription_button': '➕ Нова підписка',
        'extend_button': '💎 Продовжити',
        'share_button': '📤 Поділитися',
        'status_label': 'Статус',
        'subject_label': 'Тема',
        'messages_label': 'Повідомлення',
        'you': 'Ви',
        'support_label': 'Підтримка',
        'reply_button': '💬 Відповісти',
        'back_to_support': '🔙 До підтримки',
        'creating_ticket': 'Створення тікета',
        'send_subject': 'Відправте тему тікета в наступному повідомленні:',
        'subject_saved': 'Тема збережена. Тепер відправте текст повідомлення:',
        'reply_to_ticket': 'Відповідь на тікет',
        'send_reply': 'Відправте вашу відповідь в наступному повідомленні:',
        'currency_changed': 'Валюта змінена',
        'language_changed': 'Мова змінена',
        'currency_already_selected': 'Ця валюта вже вибрана',
        'language_already_selected': 'Ця мова вже вибрана',
        'invalid_currency': 'Невірна валюта',
        'invalid_language': 'Невірна мова',
        'failed_to_load': 'Не вдалося завантажити дані',
        'failed_to_load_user': 'Не вдалося завантажити дані користувача',
        'tariffs_not_found': 'Тарифи не знайдено',
        'tariff_not_found': 'Тариф не знайдено',
        'invalid_tariff_id': 'Помилка: невірний ID тарифу',
        'link_sent_to_chat': 'Посилання відправлено в чат',
        'click_to_copy': 'Натисніть на посилання вище, щоб скопіювати його.',
        'click_link_to_copy': 'Натисніть на посилання вище, щоб скопіювати його.',
        'send_ticket_subject': 'Відправте тему тікета в наступному повідомленні',
        'send_your_reply': 'Відправте вашу відповідь в наступному повідомленні',
        'invalid_ticket_id': 'Помилка: невірний ID тікета',
        'ticket_not_found': 'Не вдалося завантажити тікет',
        'ticket_not_exists': 'Можливо, тікет не існує або у вас немає доступу.',
        'loading_ticket': 'Завантажуємо тікет...',
        'unknown': 'Невідомо',
        'error_loading': 'Помилка',
        'on_site': 'на сайті',
        'or': 'або',
        'activating_trial': 'Активуємо триал',
        'error_activating_trial': 'Помилка активації триалу',
        'failed_activate_trial': 'Не вдалося активувати триал. Спробуйте пізніше.',
        'creating_payment': 'Створюємо платіж',
        'error_creating_payment': 'Помилка створення платежу',
    },
    'en': {
        'main_menu': 'Main Menu',
        'subscription_status': 'Subscription Status',
        'tariffs': 'Tariffs',
        'servers': 'Servers',
        'referrals': 'Referrals',
        'support': 'Support',
        'settings': '⚙️ Settings',
        'currency': 'Currency',
        'language': '🌐 Language',
        'select_currency': 'Select currency:',
        'select_language': 'Select language:',
        'settings_saved': '✅ Settings saved',
        'back': '🔙 Back',
        'welcome': 'Welcome',
        'subscription_active': 'Active',
        'subscription_inactive': 'Inactive',
        'expires': 'Expires',
        'days_left': 'Days left',
        'traffic': 'Traffic',
        'unlimited': 'Unlimited',
        'used': 'Used',
        'login_data': 'Login Data',
        'email': 'Email',
        'password': 'Password',
        'connect': 'Connect',
        'activate_trial': 'Activate Trial',
        'select_tariff': 'Select Tariff',
        'price': 'Price',
        'duration': 'Duration',
        'days': 'days',
        'select_payment': 'Select payment method',
        'payment_created': 'Payment created',
        'go_to_payment': 'Go to payment',
        'pay_with_balance': 'Pay with balance',
        'insufficient_balance': 'Insufficient funds',
        'top_up_balance': 'Top up balance',
        'enter_amount': 'Enter top-up amount',
        'invalid_amount': 'Invalid amount',
        'select_topup_method': 'Select top-up method',
        'balance_topup_created': 'Balance top-up payment created',
        'balance': 'Balance',
        'amount': 'Amount',
        'select_amount_hint': 'Select amount or enter your own',
        'enter_custom_amount': '✏️ Enter custom amount',
        'send_amount': 'Send the top-up amount as a number',
        'invalid_amount_format': 'Invalid amount format. Enter a number (e.g., 1500)',
        'amount_too_small': 'Minimum top-up amount: 1',
        'go_to_payment_button': '💳 Go to Payment',
        'go_to_payment_text': 'Go to the link to pay',
        'after_payment': 'After payment, the balance will be automatically topped up',
        'payment_successful': 'Payment successfully processed',
        'payment_processed': 'Your payment is being processed',
        'subscription_updating': 'Subscription updating...',
        'referral_program': 'Referral Program',
        'your_referral_link': 'Your referral link',
        'your_code': 'Your code',
        'copy_link': 'Copy link',
        'link_copied': 'Link sent to chat',
        'support_tickets': 'Your tickets',
        'create_ticket': 'Create ticket',
        'ticket_created': 'Ticket created',
        'ticket_number': 'Ticket number',
        'subject': 'Subject',
        'reply': 'Reply',
        'reply_sent': 'Reply sent',
        'servers_list': 'Servers list',
        'online': 'Online',
        'offline': 'Offline',
        'not_registered': 'You are not registered yet',
        'register': 'Register',
        'register_success': 'Registration successful',
        'trial_activated': 'Trial activated',
        'trial_days': 'You received 3 days of premium access',
        'error': 'Error',
        'auth_error': 'Authorization error',
        'not_found': 'Not found',
        'loading': 'Loading...',
        'welcome_bot': f'Welcome to {SERVICE_NAME} VPN Bot!',
        'not_registered_text': 'You are not registered in the system yet.',
        'register_here': 'You can register right here in the bot or on the website.',
        'after_register': 'After registration, you will receive login and password to access the website.',
        'welcome_user': 'Welcome',
        'stealthnet_bot': f'{SERVICE_NAME} VPN Bot',
        'subscription_status_title': 'Subscription Status',
        'active': 'Active',
        'inactive': 'Inactive',
        'expires_at': 'Expires',
        'days_remaining': 'Days remaining',
        'traffic_title': 'Traffic',
        'unlimited_traffic': 'Unlimited',
        'traffic_used': 'Used',
        'login_data_title': 'Login Data for Website',
        'login_label': 'Login',
        'password_label': 'Password',
        'password_set': 'Set (unavailable)',
        'password_not_set': 'Password not set',
        'data_not_found': 'Data not found',
        'connect_button': '🚀 Connect to VPN',
        'activate_trial_button': '💡 Activate Trial',
        'select_tariff_button': '💎 Select Tariff',
        'main_menu_button': 'Main Menu',
        'status_button': '📊 My Subscription',
        'tariffs_button': '💎 Tariffs',
        'options_button': '📦 Options',
        'configs_button': '🧩 Configs',
        'servers_button': '🌐 Servers',
        'referrals_button': '🎁 Referrals',
        'support_button': '💬 Support',
        'contact_support_button': '💬 Contact Support',
        'support_bot_button': '🤖 Support Bot',
        'administration_button': '👮 Administration',
        'settings_button': '⚙️ Settings',
        'cabinet_button': '📱 Web Cabinet',
        'documents_button': 'Documents',
        'user_agreement_button': '📄 Agreement',
        'offer_button': '📋 Offer',
        'refund_policy_button': 'Refund Policy',
        'user_agreement_title': '📄 User Agreement',
        'offer_title': '📋 Public Offer',
        'refund_policy_title': '💰 Refund Policy',
        'subscription_link': 'Connection Link',
        'your_id': 'ID',
        'devices_available': 'available',
        'devices_unlimited': 'Unlimited',
        'copy_link': '📋 Copy link',
        'traffic_usage': 'Traffic Usage',
        'unlimited_traffic_full': 'Unlimited Traffic',
        'use_login_password': 'Use this login and password to access the website',
        'select_tariff_type': 'Select Tariff Type',
        'basic_tier': 'Basic',
        'pro_tier': 'Premium',
        'elite_tier': 'Elite',
        'from_price': 'From',
        'available_options': 'Available options',
        'select_duration': 'Select subscription duration',
        'per_day': 'day',
        'back_to_type': '🔙 Back to Type Selection',
        'servers_title': 'Servers',
        'available_servers': 'Available Servers',
        'total_servers': 'Total Servers',
        'and_more': 'and more',
        'servers_not_found': 'Servers not found',
        'subscription_not_active': 'Subscription is not active. Activate trial or select a tariff',
        'referral_program_title': 'Referral Program',
        'invite_friends': 'Invite friends and get bonuses!',
        'your_referral_code': 'Your Code',
        'referral_code_not_found': 'Referral code not found',
        'support_title': 'Support',
        'your_tickets': 'Your Tickets',
        'no_tickets': 'You have no tickets yet.',
        'select_action': 'Select Action',
        'create_ticket_button': 'Create Ticket',
        'ticket': 'Ticket',
        'ticket_created_success': 'Ticket Created!',
        'ticket_number_label': 'Ticket Number',
        'we_will_reply': 'We will reply to you as soon as possible.',
        'view_ticket_support': 'You can view the ticket in the support section.',
        'reply_sent_success': 'Reply Sent!',
        'your_reply_added': 'Your reply has been added to the ticket.',
        'tariff_selected': 'Tariff Selected',
        'price_label': 'Price',
        'duration_label': 'Duration',
        'payment_methods': 'Select Payment Method',
        'no_payment_methods': 'No payment methods available. Contact support.',
        'back_to_tariffs': '🔙 Back to Tariffs',
        'payment_created_title': 'Payment Created',
        'go_to_payment_text': 'Go to the link to pay:',
        'after_payment': 'After successful payment, the subscription will be activated automatically.',
        'go_to_payment_button': '💳 Go to Payment',
        'trial_activated_title': 'Trial Activated!',
        'trial_days_received': 'You received 3 days of premium access.',
        'enjoy_vpn': 'Enjoy VPN without restrictions!',
        'registration_success': 'Registration Successful!',
        'your_login_data': 'Your Login Data for Website',
        'important_save': 'IMPORTANT: Save this data! The password will not be shown again.',
        'login_site': 'Login to Website',
        'now_use_bot': 'Now you can use all bot features!',
        'already_registered': 'You are already registered!',
        'registering': 'Registering...',
        'registration_error': 'Registration Error',
        'registration_failed': 'Failed to register. Try again later or register on the website:',
        'ticket_view_title': 'Ticket',
        'try_again_button': '🔙 Try Again',
        'copy_token_button': '📋 Copy Token',
        'my_configs_button': '🧩 My Subscriptions',
        'new_subscription_button': '➕ New Subscription',
        'extend_button': '💎 Extend',
        'share_button': '📤 Share',
        'status_label': 'Status',
        'subject_label': 'Subject',
        'messages_label': 'Messages',
        'you': 'You',
        'support_label': 'Support',
        'reply_button': '💬 Reply',
        'back_to_support': '🔙 Back to Support',
        'creating_ticket': 'Creating Ticket',
        'send_subject': 'Send the ticket subject in the next message:',
        'subject_saved': 'Subject saved. Now send the message text:',
        'reply_to_ticket': 'Reply to Ticket',
        'send_reply': 'Send your reply in the next message:',
        'currency_changed': 'Currency Changed',
        'language_changed': 'Language Changed',
        'currency_already_selected': 'This currency is already selected',
        'language_already_selected': 'This language is already selected',
        'invalid_currency': 'Invalid currency',
        'invalid_language': 'Invalid language',
        'failed_to_load': 'Failed to load data',
        'failed_to_load_user': 'Failed to load user data',
        'tariffs_not_found': 'Tariffs not found',
        'tariff_not_found': 'Tariff not found',
        'invalid_tariff_id': 'Error: Invalid tariff ID',
        'link_sent_to_chat': 'Link sent to chat',
        'click_to_copy': 'Click on the link above to copy it.',
        'click_link_to_copy': 'Click on the link above to copy it.',
        'send_ticket_subject': 'Send the ticket subject in the next message',
        'send_your_reply': 'Send your reply in the next message',
        'invalid_ticket_id': 'Error: Invalid ticket ID',
        'ticket_not_found': 'Failed to load ticket',
        'ticket_not_exists': 'The ticket may not exist or you do not have access.',
        'loading_ticket': 'Loading ticket...',
        'unknown': 'Unknown',
        'error_loading': 'Error',
        'on_site': 'on site',
        'or': 'or',
        'activating_trial': 'Activating trial',
        'error_activating_trial': 'Error activating trial',
        'failed_activate_trial': 'Failed to activate trial. Please try again later.',
        'creating_payment': 'Creating payment',
        'error_creating_payment': 'Error creating payment',
    },
    'cn': {
        'main_menu': '主菜单',
        'subscription_status': '订阅状态',
        'tariffs': '套餐',
        'servers': '服务器',
        'referrals': '推荐',
        'support': '支持',
        'settings': '⚙️ 设置',
        'currency': '货币',
        'language': '🌐 语言',
        'select_currency': '选择货币:',
        'select_language': '选择语言:',
        'settings_saved': '✅ 设置已保存',
        'back': '🔙 返回',
        'welcome': '欢迎',
        'subscription_active': '活跃',
        'subscription_inactive': '未活跃',
        'expires': '到期',
        'days_left': '剩余天数',
        'traffic': '流量',
        'unlimited': '无限',
        'used': '已使用',
        'login_data': '登录数据',
        'email': '邮箱',
        'password': '密码',
        'connect': '连接',
        'activate_trial': '激活试用',
        'select_tariff': '选择套餐',
        'price': '价格',
        'duration': '时长',
        'days': '天',
        'select_payment': '选择支付方式',
        'payment_created': '支付已创建',
        'go_to_payment': '前往支付',
        'pay_with_balance': '使用余额支付',
        'insufficient_balance': '余额不足',
        'top_up_balance': '💰 充值余额',
        'enter_amount': '输入充值金额',
        'invalid_amount': '无效金额',
        'select_topup_method': '选择充值方式',
        'balance_topup_created': '已创建余额充值支付',
        'balance': '余额',
        'amount': '金额',
        'select_amount_hint': '选择金额或输入自定义金额',
        'enter_custom_amount': '✏️ 输入自定义金额',
        'send_amount': '发送充值金额（数字）',
        'invalid_amount_format': '金额格式无效。请输入数字（例如：1500）',
        'amount_too_small': '最低充值金额：1',
        'go_to_payment_button': '💳 前往支付',
        'go_to_payment_text': '前往链接进行支付',
        'after_payment': '支付后余额将自动充值',
        'payment_successful': '支付成功处理',
        'payment_processed': '您的支付正在处理中',
        'subscription_updating': '订阅更新中...',
        'referral_program': '推荐计划',
        'your_referral_link': '您的推荐链接',
        'your_code': '您的代码',
        'copy_link': '复制链接',
        'link_copied': '链接已发送到聊天',
        'support_tickets': '您的工单',
        'create_ticket': '创建工单',
        'ticket_created': '工单已创建',
        'ticket_number': '工单号',
        'subject': '主题',
        'reply': '回复',
        'reply_sent': '回复已发送',
        'servers_list': '服务器列表',
        'online': '在线',
        'offline': '离线',
        'not_registered': '您尚未注册',
        'register': '注册',
        'register_success': '注册成功',
        'trial_activated': '试用已激活',
        'trial_days': '您获得了3天的高级访问权限',
        'error': '错误',
        'auth_error': '授权错误',
        'not_found': '未找到',
        'loading': '加载中...',
        'welcome_bot': f'欢迎使用 {SERVICE_NAME} VPN Bot！',
        'not_registered_text': '您尚未在系统中注册。',
        'register_here': '您可以在此处或网站上注册。',
        'after_register': '注册后，您将收到登录名和密码以访问网站。',
        'welcome_user': '欢迎',
        'stealthnet_bot': f'{SERVICE_NAME} VPN Bot',
        'subscription_status_title': '订阅状态',
        'active': '活跃',
        'inactive': '未活跃',
        'expires_at': '到期',
        'days_remaining': '剩余天数',
        'traffic_title': '流量',
        'unlimited_traffic': '无限',
        'traffic_used': '已使用',
        'login_data_title': '网站登录数据',
        'login_label': '登录',
        'password_label': '密码',
        'password_set': '已设置（不可用）',
        'password_not_set': '未设置密码',
        'data_not_found': '未找到数据',
        'connect_button': '🚀 连接VPN',
        'activate_trial_button': '💡 激活试用',
        'select_tariff_button': '💎 选择套餐',
        'main_menu_button': '主菜单',
        'status_button': '📊 我的订阅',
        'tariffs_button': '💎 套餐',
        'options_button': '📦 选项',
        'configs_button': '🧩 配置',
        'servers_button': '🌐 服务器',
        'referrals_button': '🎁 推荐',
        'support_button': '💬 支持',
        'contact_support_button': '💬 联系支持',
        'support_bot_button': '🤖 支持机器人',
        'administration_button': '👮 管理',
        'settings_button': '⚙️ 设置',
        'cabinet_button': '📱 Web кабинет',
        'documents_button': '文件',
        'user_agreement_button': '📄 协议',
        'offer_button': '📋 要约',
        'refund_policy_button': '退款政策',
        'user_agreement_title': '📄 用户协议',
        'offer_title': '📋 公开要约',
        'refund_policy_title': '💰 退款政策',
        'subscription_link': '连接链接',
        'your_id': 'ID',
        'devices_available': '可用',
        'devices_unlimited': '无限',
        'copy_link': '📋 复制链接',
        'traffic_usage': '流量使用',
        'unlimited_traffic_full': '无限流量',
        'use_login_password': '使用此登录名和密码访问网站',
        'select_tariff_type': '选择套餐类型',
        'basic_tier': '基础',
        'pro_tier': '高级',
        'elite_tier': '精英',
        'from_price': '从',
        'available_options': '可用选项',
        'select_duration': '选择订阅时长',
        'per_day': '天',
        'back_to_type': '🔙 返回类型选择',
        'servers_title': '服务器',
        'available_servers': '可用服务器',
        'total_servers': '总服务器数',
        'and_more': '还有',
        'servers_not_found': '未找到服务器',
        'subscription_not_active': '订阅未激活。激活试用或选择套餐',
        'referral_program_title': '推荐计划',
        'invite_friends': '邀请朋友并获得奖励！',
        'your_referral_code': '您的代码',
        'referral_code_not_found': '未找到推荐代码',
        'support_title': '支持',
        'your_tickets': '您的工单',
        'no_tickets': '您还没有工单。',
        'select_action': '选择操作',
        'create_ticket_button': '创建工单',
        'ticket': '工单',
        'ticket_created_success': '工单已创建！',
        'ticket_number_label': '工单号',
        'we_will_reply': '我们会尽快回复您。',
        'view_ticket_support': '您可以在支持部分查看工单。',
        'reply_sent_success': '回复已发送！',
        'your_reply_added': '您的回复已添加到工单。',
        'tariff_selected': '已选择套餐',
        'price_label': '价格',
        'duration_label': '时长',
        'payment_methods': '选择支付方式',
        'no_payment_methods': '没有可用的支付方式。请联系支持。',
        'back_to_tariffs': '🔙 返回套餐',
        'payment_created_title': '支付已创建',
        'go_to_payment_text': '转到链接进行支付：',
        'after_payment': '支付成功后，订阅将自动激活。',
        'go_to_payment_button': '💳 前往支付',
        'trial_activated_title': '试用已激活！',
        'trial_days_received': '您获得了3天的高级访问权限。',
        'enjoy_vpn': '享受无限制的VPN！',
        'registration_success': '注册成功！',
        'your_login_data': '您的网站登录数据',
        'important_save': '重要：保存这些数据！密码将不再显示。',
        'login_site': '登录网站',
        'now_use_bot': '现在您可以使用所有机器人功能！',
        'already_registered': '您已经注册！',
        'registering': '注册中...',
        'registration_error': '注册错误',
        'registration_failed': '注册失败。请稍后重试或在网站上注册：',
        'ticket_view_title': '工单',
        'try_again_button': '🔙 重试',
        'copy_token_button': '📋 复制令牌',
        'my_configs_button': '🧩 我的订阅',
        'new_subscription_button': '➕ 新订阅',
        'extend_button': '💎 续订',
        'share_button': '📤 分享',
        'status_label': '状态',
        'subject_label': '主题',
        'messages_label': '消息',
        'you': '您',
        'support_label': '支持',
        'reply_button': '💬 回复',
        'back_to_support': '🔙 返回支持',
        'creating_ticket': '创建工单',
        'send_subject': '在下一个消息中发送工单主题：',
        'subject_saved': '主题已保存。现在发送消息文本：',
        'reply_to_ticket': '回复工单',
        'send_reply': '在下一个消息中发送您的回复：',
        'currency_changed': '货币已更改',
        'language_changed': '语言已更改',
        'currency_already_selected': '此货币已选择',
        'language_already_selected': '此语言已选择',
        'invalid_currency': '无效货币',
        'invalid_language': '无效语言',
        'failed_to_load': '加载数据失败',
        'failed_to_load_user': '加载用户数据失败',
        'tariffs_not_found': '未找到套餐',
        'tariff_not_found': '未找到套餐',
        'invalid_tariff_id': '错误：无效的套餐ID',
        'link_sent_to_chat': '链接已发送到聊天',
        'click_to_copy': '点击上面的链接以复制它。',
        'click_link_to_copy': '点击上面的链接以复制它。',
        'send_ticket_subject': '在下一个消息中发送工单主题',
        'send_your_reply': '在下一个消息中发送您的回复',
        'invalid_ticket_id': '错误：无效的工单ID',
        'ticket_not_found': '加载工单失败',
        'ticket_not_exists': '工单可能不存在或您没有访问权限。',
        'loading_ticket': '加载工单中...',
        'unknown': '未知',
        'error_loading': '错误',
        'on_site': '在网站上',
        'or': '或',
        'activating_trial': '正在激活试用',
        'error_activating_trial': '激活试用时出错',
        'failed_activate_trial': '无法激活试用。请稍后再试。',
        'creating_payment': '正在创建支付',
        'error_creating_payment': '创建支付时出错',
    }
}

def get_text(key: str, lang: str = 'ru') -> str:
    """Получить переведенный текст (с приоритетом кастомных из админки).
    Названия кнопок (user_agreement_button, offer_button и др.) берутся из Конструктора бота /admin/bot-constructor."""
    # Сначала проверяем кастомные переводы из админки (Конструктор бота)
    custom = get_custom_translation(key, lang)
    if custom:
        # Заменяем {SERVICE_NAME} на актуальное название
        custom = custom.replace('{SERVICE_NAME}', get_service_name())

        return custom
    
    # Иначе используем встроенные переводы
    text = TRANSLATIONS.get(lang, TRANSLATIONS['ru']).get(key, key)
    # Заменяем {SERVICE_NAME} если есть
    if '{SERVICE_NAME}' in str(text):
        text = text.replace('{SERVICE_NAME}', get_service_name())
    return text

def get_user_lang(user_data: dict = None, context: ContextTypes.DEFAULT_TYPE = None, token: str = None) -> str:
    """Получить язык пользователя из данных, context или по токену"""
    # Сначала проверяем context.user_data (самый быстрый способ, если язык был недавно изменен)
    if context and hasattr(context, 'user_data') and 'user_lang' in context.user_data:
        lang = context.user_data['user_lang']
        if lang in ['ru', 'ua', 'en', 'cn']:
            return lang
    
    # Затем проверяем user_data
    if user_data:
        lang = user_data.get('preferred_lang') or user_data.get('preferredLang') or 'ru'
        if lang in ['ru', 'ua', 'en', 'cn']:
            # Сохраняем в context для следующего раза
            if context and hasattr(context, 'user_data'):
                context.user_data['user_lang'] = lang
            return lang
    
    # Если есть token, получаем данные из API
    if token:
        user_data = api.get_user_data(token)
        if user_data:
            lang = user_data.get('preferred_lang') or user_data.get('preferredLang') or 'ru'
            if lang in ['ru', 'ua', 'en', 'cn']:
                # Сохраняем в context для следующего раза
                if context and hasattr(context, 'user_data'):
                    context.user_data['user_lang'] = lang
                return lang
    
    # По умолчанию русский
    return 'ru'


def get_user_token(telegram_id: int) -> Optional[str]:
    """Получить или создать JWT токен для пользователя"""
    if telegram_id in user_tokens:
        cached = user_tokens.get(telegram_id)
        if isinstance(cached, str):
            # обратная совместимость со старым форматом кеша
            exp = _get_jwt_exp(cached)
            user_tokens[telegram_id] = {"token": cached, "exp": exp}
            cached = user_tokens.get(telegram_id)

        if isinstance(cached, dict) and cached.get("token"):
            exp = cached.get("exp")
            # refresh если exp неизвестен или скоро истечёт
            if exp and isinstance(exp, (int, float)):
                # обновляем заранее за 30 минут до истечения
                if exp - int(time.time()) > 30 * 60:
                    return cached["token"]
            else:
                # если exp не удалось прочитать — всё равно попробуем токен, но параллельно обновим его ниже
                return cached["token"]
    
    # Получаем токен через API
    token = api.get_user_by_telegram_id(telegram_id)
    if token and isinstance(token, str):
        user_tokens[telegram_id] = {"token": token, "exp": _get_jwt_exp(token)}
        return token
    # Иногда API возвращает dict (например, blocked)
    return token
    
    return None


def _get_jwt_exp(token: str) -> Optional[int]:
    """Достать exp из JWT без проверки подписи (нужно для авто-refresh кеша)"""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        # base64url padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload_raw = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        payload = json.loads(payload_raw.decode("utf-8"))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return int(exp)
        # pyjwt иногда сериализует datetime, но у нас backend отдаёт epoch
        return None
    except Exception:
        return None


def clear_user_token_cache(telegram_id: int):
    """Сбросить кеш токена, чтобы взять новый с API"""
    try:
        if telegram_id in user_tokens:
            del user_tokens[telegram_id]
    except Exception:
        pass


def get_system_defaults() -> tuple[str, str]:
    """Вернуть (default_language, default_currency) из системных настроек."""
    try:
        settings = api.get_system_settings() or {}
        lang = str(settings.get("default_language") or "ru").strip().lower() or "ru"
        currency = str(settings.get("default_currency") or "uah").strip().lower() or "uah"
        return lang, currency
    except Exception:
        return "ru", "uah"


def get_user_data_safe(telegram_id: int, token: Optional[str], force_refresh: bool = False):
    """
    Получить user_data. Если token протух/стал невалидным — автоматически обновит токен и повторит запрос.
    Возвращает: (token, user_data)
    """
    if not token or not isinstance(token, str):
        return token, None

    user_data = api.get_user_data(token, force_refresh=force_refresh)
    if user_data:
        return token, user_data

    # Наиболее частая причина: протухший JWT из кеша. Обновляем и пробуем ещё раз.
    clear_user_token_cache(telegram_id)
    new_token = get_user_token(telegram_id)
    if new_token and isinstance(new_token, str):
        user_data = api.get_user_data(new_token, force_refresh=force_refresh)
        if user_data:
            return new_token, user_data

    return token, None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    telegram_id = user.id
    chat_id = update.effective_chat.id
    
    # Удаляем старые сообщения перед отправкой нового
    await delete_recent_bot_messages(context, chat_id, context.user_data, max_messages=20)
    
    # Получаем токен для пользователя
    token = get_user_token(telegram_id)
    
    # Проверяем блокировку аккаунта
    if isinstance(token, dict) and token.get('blocked'):
        block_reason = token.get('block_reason', '') or "Ваш аккаунт заблокирован"
        text = f"🚫 **Ваш аккаунт заблокирован**\n\n"
        text += f"📝 **Причина:**\n{block_reason}\n\n"
        text += "━━━━━━━━━━━━━━━\n\n"
        text += "⚠️ Если вы считаете, что вас заблокировали ошибочно, свяжитесь с администрацией.\n\n"
        text += "💬 Для связи с поддержкой используйте кнопку ниже:"
        
        lang = get_user_lang(None, context, None)
        keyboard = [
            [InlineKeyboardButton(get_text('contact_support_button', lang), callback_data="support")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await reply_with_logo(update, text, reply_markup=reply_markup, parse_mode="Markdown", context=context)
        return
    
    if not token or not isinstance(token, str):
        # Авто-регистрация: язык/валюта берутся из системных настроек
        referral_code = None
        if context.args and len(context.args) > 0:
            referral_code = context.args[0]
            context.user_data['ref_code'] = referral_code

        # Проверяем подписку на канал если требуется (до регистрации)
        if is_channel_subscription_required():
            is_subscribed = await check_channel_subscription(telegram_id, context)
            if not is_subscribed:
                await show_channel_subscription_required(update, context)
                return

        default_lang, default_currency = get_system_defaults()
        telegram_username = user.username or ""
        result = api.register_user(
            telegram_id,
            telegram_username,
            ref_code=referral_code,
            preferred_lang=default_lang,
            preferred_currency=default_currency
        )

        # Если регистрация вернула token — используем его; иначе пробуем получить токен как обычно
        if isinstance(result, dict) and isinstance(result.get("token"), str):
            token = result.get("token")
            user_tokens[telegram_id] = {"token": token, "exp": _get_jwt_exp(token)}
        else:
            clear_user_token_cache(telegram_id)
            token = get_user_token(telegram_id)

        if not token or not isinstance(token, str):
            # Если что-то пошло не так — показываем сообщение об ошибке
            await reply_with_logo(update, f"❌ {get_text('auth_error', 'ru')}", context=context)
            return
    
    # Получаем данные пользователя (с авто-refresh токена)
    token, user_data = get_user_data_safe(telegram_id, token)
    
    if not user_data:
        lang = get_user_lang(None, context, token)
        await reply_with_logo(update, f"❌ {get_text('failed_to_load_user', lang)}", context=context)
        return
    
    # Получаем язык пользователя
    user_lang = get_user_lang(user_data, context, token)
    
    # Получаем данные для клавиатуры
    is_active = user_data.get("activeInternalSquads", [])
    expire_at = user_data.get("expireAt")
    subscription_url = user_data.get("subscriptionUrl", "")
    used_traffic = user_data.get("usedTrafficBytes", 0)
    traffic_limit = user_data.get("trafficLimitBytes", 0)
    
    # Проверяем, есть ли активная подписка (не истекшая)
    has_active_subscription = False
    expire_date = None
    days_left = 0
    
    if is_active and expire_at:
        expire_date = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
        now = datetime.now(expire_date.tzinfo)
        delta = expire_date - now
        seconds_left = delta.total_seconds()
        # Чтобы совпадало с miniapp: считаем оставшиеся дни через ceil
        days_left = int(math.ceil(seconds_left / (60 * 60 * 24))) if seconds_left > 0 else 0
        has_active_subscription = seconds_left > 0
    
    # ВАЖНО: /start всегда должен показывать главное меню (баланс/статус/трафик),
    # чтобы кастомные тексты (например, из рассылок) не подменяли основной экран.
    welcome_text = f"{get_emoji('HEADER')} **{get_text('stealthnet_bot', user_lang)}**\n"
    welcome_text += f"{get_text('main_menu_button', user_lang)}\n"
    welcome_text += f" {get_text('your_id', user_lang)}: {telegram_id}\n"
    welcome_text += "━━━━━━━━━━━━━━━\n"
    
    # Баланс
    balance = user_data.get("balance", 0)
    preferred_currency = user_data.get("preferred_currency", "uah")
    currency_symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(preferred_currency, "₴")
    welcome_text += f"{get_emoji('BALANCE')} **{get_text('balance', user_lang)}:** {balance:.2f} {currency_symbol}\n"

    # Статус подписки
    if has_active_subscription and expire_date:
        # Статус с индикатором - в одну строку
        status_icon = get_emoji("ACTIVE_GREEN") if days_left > 7 else get_emoji("ACTIVE_YELLOW") if days_left > 0 else get_emoji("INACTIVE")
        welcome_text += f"{get_emoji('STATUS')} **{get_text('subscription_status_title', user_lang)}** - {status_icon} {get_text('active', user_lang)}\n"
        
        # Дата с "до"
        ed = get_emoji("DATE")
        if user_lang == 'ru':
            welcome_text += f"{ed} до {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        elif user_lang == 'ua':
            welcome_text += f"{ed} до {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        elif user_lang == 'en':
            welcome_text += f"{ed} until {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        else:
            welcome_text += f"{ed} {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        
        # Дни с правильным склонением (days_left уже > 0 здесь)
        if user_lang == 'ru':
            if days_left == 1:
                days_text = f"{days_left} день"
            elif 2 <= days_left <= 4:
                days_text = f"{days_left} дня"
            else:
                days_text = f"{days_left} дней"
            welcome_text += f"{get_emoji('TIME')} осталось {days_text}\n"
        elif user_lang == 'ua':
            if days_left == 1:
                days_text = f"{days_left} день"
            elif 2 <= days_left <= 4:
                days_text = f"{days_left} дні"
            else:
                days_text = f"{days_left} днів"
            welcome_text += f"{get_emoji('TIME')} залишилось {days_text}\n"
        elif user_lang == 'en':
            days_text = f"{days_left} day{'s' if days_left != 1 else ''}"
            welcome_text += f"{get_emoji('TIME')} {days_text} left\n"
        else:
            days_text = get_days_text(days_left, user_lang)
            welcome_text += f"{get_emoji('TIME')} {days_text}\n"
        
        # Устройства (доступное количество из тарифа)
        hwid_limit = user_data.get("hwidDeviceLimit")
        if hwid_limit is not None:
            if hwid_limit == -1 or hwid_limit >= 100:
                welcome_text += f"{get_emoji('DEVICES')} **Устройств:** {get_text('devices_unlimited', user_lang)}\n"
            else:
                welcome_text += f"{get_emoji('DEVICES')} **Устройств:** {hwid_limit} {get_text('devices_available', user_lang)}\n"
        
        # Трафик - в одну строку
        if traffic_limit == 0:
            welcome_text += f"{get_emoji('TRAFFIC')} **{get_text('traffic_title', user_lang)}**  - ♾️ {get_text('unlimited_traffic', user_lang)}\n"
        else:
            used_gb = used_traffic / (1024 ** 3)
            limit_gb = traffic_limit / (1024 ** 3)
            percentage = (used_traffic / traffic_limit * 100) if traffic_limit > 0 else 0
            
            filled = int(percentage / (100 / 15))
            filled = min(filled, 15)
            progress_bar = "█" * filled + "░" * (15 - filled)
            progress_color = get_emoji("ACTIVE_GREEN") if percentage < 70 else get_emoji("ACTIVE_YELLOW") if percentage < 90 else get_emoji("INACTIVE")
            
            welcome_text += f"{get_emoji('TRAFFIC')} **{get_text('traffic_title', user_lang)}**  - {progress_color} {progress_bar} {percentage:.0f}% ({used_gb:.2f} / {limit_gb:.2f} GB)\n"
        
        # Ссылка подключения: в тексте — для копирования (не кликается), кнопка «Подключиться» — открывает
        if subscription_url:
            welcome_text += f"{get_emoji('LINK')} **{get_text('subscription_link', user_lang)}:**\n"
            welcome_text += f"{_subscription_url_for_copy(subscription_url)}\n"
        
        welcome_text += "━━━━━━━━━━━━━━━\n"
    else:
        welcome_text += f"{get_emoji('STATUS')} **{get_text('subscription_status_title', user_lang)}**\n"
        welcome_text += f"{get_emoji('INACTIVE')} {get_text('inactive', user_lang)}\n"
        _act_btn = get_text('activate_trial_button', user_lang)
        _act_plain = _act_btn.lstrip(get_emoji('TRIAL') + ' ').lstrip('🎁 ').strip() or _act_btn
        welcome_text += f"{get_emoji('TRIAL')} {_act_plain}\n"
        welcome_text += "━━━━━━━━━━━━━━━\n"
    
    # Кнопки главного меню - строим динамически из конфига
    # Используем has_active_subscription для правильного отображения кнопок
    trial_used = user_data.get('trial_used', False)  # Получаем информацию об использовании триала
    keyboard = build_main_menu_keyboard(user_lang, has_active_subscription, subscription_url, expire_at, trial_used)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Кастомные эмодзи по emoji-id показываем всем (parse_mode=HTML)
    if has_cards(welcome_text):
        welcome_text_clean = clean_markdown_for_cards(welcome_text)
        await reply_with_logo(
            update,
            welcome_text_clean,
            reply_markup=reply_markup,
            context=context
        )
    else:
        try:
            welcome_html = welcome_text_to_html_with_tg_emoji(welcome_text, user_lang)
            await reply_with_logo(
                update,
                welcome_html,
                reply_markup=reply_markup,
                parse_mode="HTML",
                context=context
            )
        except Exception as e:
            logger.warning(f"HTML (tg-emoji) parsing error, fallback to Markdown: {e}")
            try:
                await reply_with_logo(
                    update,
                    welcome_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown",
                    context=context
                )
            except Exception as e2:
                await reply_with_logo(
                    update,
                    clean_markdown_for_cards(welcome_text),
                    reply_markup=reply_markup,
                    context=context
                )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status"""
    await show_status(update, context)


async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статус подписки"""
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await update.callback_query.answer(f"❌ {get_text('auth_error', lang)}")
        return
    
    # Попробуем обработать "зависшие" оплаты (если webhook не дошел), затем обновим профиль
    try:
        api.session.post(
            f"{FLASK_API_URL}/api/client/payments/reconcile",
            headers={"Authorization": f"Bearer {token}"},
            json={},
            timeout=15
        )
    except Exception:
        pass

    token, user_data = get_user_data_safe(telegram_id, token, force_refresh=True)
    if not user_data:
        lang = get_user_lang(None, context, token)
        await update.callback_query.answer(f"❌ {get_text('failed_to_load', lang)}")
        return
    
    # Получаем язык пользователя
    user_lang = get_user_lang(user_data, context, token)
    
    # Формируем сообщение со статусом
    is_active = user_data.get("activeInternalSquads", [])
    expire_at = user_data.get("expireAt")
    used_traffic = user_data.get("usedTrafficBytes", 0)
    traffic_limit = user_data.get("trafficLimitBytes", 0)
    subscription_url = user_data.get("subscriptionUrl", "")
    balance = user_data.get("balance", 0)
    preferred_currency = user_data.get("preferred_currency", "uah")
    currency_symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(preferred_currency, "₴")
    
    status_text = f"📊 {get_text('subscription_status_title', user_lang)}\n"
    status_text += f" ID: {telegram_id}\n"
    status_text += "--------------------------------\n"
    
    # Баланс
    status_text += f"💰 {get_text('balance', user_lang)}: {balance:.2f} {currency_symbol}\n"
    
    # Проверяем, есть ли активная подписка (не истекшая)
    has_active_subscription = False
    expire_date = None
    days_left = 0
    
    if is_active and expire_at:
        expire_date = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
        now = datetime.now(expire_date.tzinfo)
        delta = expire_date - now
        seconds_left = delta.total_seconds()
        days_left = int(math.ceil(seconds_left / (60 * 60 * 24))) if seconds_left > 0 else 0
        
        # Подписка активна только если не истекла
        has_active_subscription = seconds_left > 0
    
    if has_active_subscription and expire_date:
        # Статус подписки
        status_icon = "🟢" if days_left > 7 else "🟡" if days_left > 0 else "🔴"
        status_text += f"📊 {get_text('subscription_status_title', user_lang)} - {status_icon} {get_text('active', user_lang)}\n"
        
        # Дата окончания
        if user_lang == 'ru':
            status_text += f"📅 До {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        elif user_lang == 'ua':
            status_text += f"📅 До {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        elif user_lang == 'en':
            status_text += f"📅 Until {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        else:
            status_text += f"📅 {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        
        # Осталось дней
        if user_lang == 'ru':
            days_text = f"{days_left} день" if days_left == 1 else f"{days_left} дня" if 2 <= days_left <= 4 else f"{days_left} дней"
            status_text += f"⏰ Осталось {days_text}\n"
        elif user_lang == 'ua':
            days_text = f"{days_left} день" if days_left == 1 else f"{days_left} дні" if 2 <= days_left <= 4 else f"{days_left} днів"
            status_text += f"⏰ Залишилось {days_text}\n"
        elif user_lang == 'en':
            days_text = f"{days_left} day{'s' if days_left != 1 else ''}"
            status_text += f"⏰ {days_text} left\n"
        else:
            days_text = get_days_text(days_left, user_lang)
            status_text += f"⏰ {days_text}\n"
        
        # Устройства (доступное количество из тарифа)
        hwid_limit = user_data.get("hwidDeviceLimit")
        if hwid_limit is not None:
            if hwid_limit == -1 or hwid_limit >= 100:
                status_text += f"📱 Устройств: {get_text('devices_unlimited', user_lang)}\n"
            else:
                status_text += f"📱 Устройств: {hwid_limit} {get_text('devices_available', user_lang)}\n"
        
        # Трафик — одна строка с прогресс-баром
        if traffic_limit == 0:
            status_text += f"📈 {get_text('traffic_title', user_lang)} - ♾️ {get_text('unlimited_traffic', user_lang)}\n"
        else:
            used_gb = used_traffic / (1024 ** 3)
            limit_gb = traffic_limit / (1024 ** 3)
            percentage = (used_traffic / traffic_limit * 100) if traffic_limit > 0 else 0
            filled = int(percentage / (100 / 15))
            filled = min(filled, 15)
            progress_bar = "█" * filled + "░" * (15 - filled)
            progress_color = "🟢" if percentage < 70 else "🟡" if percentage < 90 else "🔴"
            status_text += f"📈 {get_text('traffic_title', user_lang)} - {progress_color} {progress_bar} {percentage:.0f}% ({used_gb:.2f} / {limit_gb:.2f} GB)\n"
        
        # Ссылка подключения (в тексте — для копирования, не открывается по тапу)
        if subscription_url:
            status_text += f"🔗 {get_text('subscription_link', user_lang)}:\n"
            status_text += f"{_subscription_url_for_copy(subscription_url)}\n"
        
        status_text += "--------------------------------\n"
    else:
        status_text += f"📊 {get_text('subscription_status_title', user_lang)} - 🔴 {get_text('inactive', user_lang)}\n"
        status_text += f"💡 {get_text('subscription_not_active', user_lang)}\n"
        
        # Трафик (при неактивной подписке)
        if traffic_limit == 0:
            status_text += f"📈 {get_text('traffic_title', user_lang)} - ♾️ {get_text('unlimited_traffic', user_lang)}\n"
        else:
            used_gb = used_traffic / (1024 ** 3)
            limit_gb = traffic_limit / (1024 ** 3)
            percentage = (used_traffic / traffic_limit * 100) if traffic_limit > 0 else 0
            filled = int(percentage / (100 / 15))
            filled = min(filled, 15)
            progress_bar = "█" * filled + "░" * (15 - filled)
            progress_color = "🟢" if percentage < 70 else "🟡" if percentage < 90 else "🔴"
            status_text += f"📈 {get_text('traffic_title', user_lang)} - {progress_color} {progress_bar} {percentage:.0f}% ({used_gb:.2f} / {limit_gb:.2f} GB)\n"
        
        status_text += "--------------------------------\n"
    
    # Данные для входа
    status_text += f"\n🔐 {get_text('login_data_title', user_lang)}\n"
    
    credentials = api.get_credentials(telegram_id)
    if credentials and credentials.get("email"):
        status_text += f"📧 `{credentials['email']}`\n"
        if credentials.get("password"):
            status_text += f"🔑 `{credentials['password']}`\n\n"
            status_text += f"💡 {get_text('use_login_password', user_lang)}\n"
            status_text += f"🌐 {YOUR_SERVER_IP}\n"
        elif credentials.get("has_password"):
            status_text += f"🔑 {get_text('password_set', user_lang)}\n\n"
            status_text += f"💡 {get_text('use_login_password', user_lang)}\n"
            status_text += f"🌐 {YOUR_SERVER_IP}\n"
        else:
            status_text += f"⚠️ {get_text('password_not_set', user_lang)}\n"
    else:
        status_text += f"❌ {get_text('data_not_found', user_lang)}\n"
    
    # Кнопки действий
    keyboard = []
    
    # Кнопка подключения (открывает ссылку). Ссылка в тексте выше — нажатием на неё копируется (как в «поделиться подпиской»).
    if is_active and subscription_url:
        keyboard.append([
            InlineKeyboardButton(get_text('connect_button', user_lang), url=subscription_url)
        ])
    
    keyboard.append([
        InlineKeyboardButton(get_text('select_tariff_button', user_lang), callback_data="tariffs"),
        InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования/отправки
    if has_cards(status_text):
        status_text_clean = clean_markdown_for_cards(status_text)
        await safe_edit_or_send_with_logo(update, context, status_text_clean, reply_markup=reply_markup, logo_page="subscription_status")
    else:
        # Для текста без карточек используем Markdown
        try:
            await safe_edit_or_send_with_logo(update, context, status_text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="subscription_status")
        except Exception as e:
            logger.warning(f"Error in show_status, sending without formatting: {e}")
            status_text_clean = clean_markdown_for_cards(status_text)
            await safe_edit_or_send_with_logo(update, context, status_text_clean, reply_markup=reply_markup, logo_page="subscription_status")


async def show_subscription_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Моя подписка: статус + быстрые действия (подписки/сервера/пополнение)."""
    query = update.callback_query
    if not query:
        return

    telegram_id = update.effective_user.id
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}", show_alert=True)
        return

    # Обновим профиль, чтобы статус был актуален
    token, user_data = get_user_data_safe(telegram_id, token, force_refresh=True)
    if not user_data:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('failed_to_load', lang)}", show_alert=True)
        return

    user_lang = get_user_lang(user_data, context, token)

    is_active = user_data.get("activeInternalSquads", [])
    expire_at = user_data.get("expireAt")
    used_traffic = user_data.get("usedTrafficBytes", 0)
    traffic_limit = user_data.get("trafficLimitBytes", 0)
    subscription_url = user_data.get("subscriptionUrl", "")
    balance = user_data.get("balance", 0)
    preferred_currency = user_data.get("preferred_currency", "uah")
    currency_symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(preferred_currency, "₴")

    text = f"{get_emoji('STATUS')} {get_text('subscription_status_title', user_lang)}\n"
    text += f" ID: {telegram_id}\n"
    text += f"{SEPARATOR_LINE}\n"
    text += f"{get_emoji('BALANCE')} {get_text('balance', user_lang)}: {balance:.2f} {currency_symbol}\n"

    has_active_subscription = False
    expire_date = None
    days_left = 0
    if is_active and expire_at:
        try:
            expire_date = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
            now = datetime.now(expire_date.tzinfo)
            delta = expire_date - now
            seconds_left = delta.total_seconds()
            days_left = int(math.ceil(seconds_left / (60 * 60 * 24))) if seconds_left > 0 else 0
            has_active_subscription = seconds_left > 0
        except Exception:
            has_active_subscription = False

    if has_active_subscription and expire_date:
        status_icon = get_emoji("ACTIVE_GREEN") if days_left > 7 else get_emoji("ACTIVE_YELLOW") if days_left > 0 else get_emoji("INACTIVE")
        text += f"{get_emoji('STATUS')} {get_text('subscription_status_title', user_lang)} - {status_icon} {get_text('active', user_lang)}\n"
        ed = get_emoji("DATE")
        if user_lang == 'ru':
            text += f"{ed} До {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        elif user_lang == 'ua':
            text += f"{ed} До {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        elif user_lang == 'en':
            text += f"{ed} Until {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        else:
            text += f"{ed} {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
        et = get_emoji("TIME")
        if user_lang == 'ru':
            days_part = get_days_text(days_left, user_lang)
            text += f"{et} Осталось {days_part}\n"
        elif user_lang == 'ua':
            days_part = get_days_text(days_left, user_lang)
            text += f"{et} Залишилось {days_part}\n"
        elif user_lang == 'en':
            days_part = get_days_text(days_left, user_lang)
            text += f"{et} {days_part} left\n"
        else:
            text += f"{et} {get_days_text(days_left, user_lang)}\n"
        hwid_limit = user_data.get("hwidDeviceLimit")
        if hwid_limit is not None:
            if hwid_limit == -1 or hwid_limit >= 100:
                text += f"{get_emoji('DEVICES')} Устройств: {get_text('devices_unlimited', user_lang)}\n"
            else:
                text += f"{get_emoji('DEVICES')} Устройств: {hwid_limit} {get_text('devices_available', user_lang)}\n"
    else:
        text += f"{get_emoji('STATUS')} {get_text('subscription_status_title', user_lang)} - {get_emoji('INACTIVE')} {get_text('inactive', user_lang)}\n"

    if traffic_limit == 0:
        text += f"{get_emoji('TRAFFIC')} {get_text('traffic_title', user_lang)} - ♾️ {get_text('unlimited_traffic', user_lang)}\n"
    else:
        used_gb = used_traffic / (1024 ** 3)
        limit_gb = traffic_limit / (1024 ** 3)
        percentage = (used_traffic / traffic_limit * 100) if traffic_limit > 0 else 0
        filled = int(percentage / (100 / 15))
        filled = min(filled, 15)
        progress_bar = "█" * filled + "░" * (15 - filled)
        progress_color = get_emoji("ACTIVE_GREEN") if percentage < 70 else get_emoji("ACTIVE_YELLOW") if percentage < 90 else get_emoji("INACTIVE")
        text += f"{get_emoji('TRAFFIC')} {get_text('traffic_title', user_lang)} - {progress_color} {progress_bar} {percentage:.0f}% ({used_gb:.2f} / {limit_gb:.2f} GB)\n"

    if has_active_subscription and subscription_url:
        text += f"{get_emoji('LINK')} {get_text('subscription_link', user_lang)}:\n"
        text += f"{_subscription_url_for_copy(subscription_url)}\n"

    text += f"{SEPARATOR_LINE}\n"

    keyboard = []

    actions_row = [InlineKeyboardButton(get_text('configs_button', user_lang), callback_data="sub_configs")]
    if is_button_visible('servers'):
        actions_row.append(InlineKeyboardButton(get_text('servers_button', user_lang), callback_data="sub_servers"))
    keyboard.append(actions_row)

    if is_button_visible('topup'):
        keyboard.append([InlineKeyboardButton(get_text('top_up_balance', user_lang), callback_data="sub_topup")])

    keyboard.append([InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    temp_update = Update(update_id=0, callback_query=query)
    if has_cards(text):
        await safe_edit_or_send_with_logo(temp_update, context, clean_markdown_for_cards(text), reply_markup=reply_markup, logo_page="subscription_menu")
    else:
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="subscription_menu")
        except Exception:
            await safe_edit_or_send_with_logo(temp_update, context, clean_markdown_for_cards(text), reply_markup=reply_markup, logo_page="subscription_menu")


async def show_support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поддержка: тикеты + оферта + соглашение."""
    query = update.callback_query
    if not query:
        return

    telegram_id = update.effective_user.id
    token = get_user_token(telegram_id)
    user_lang = get_user_lang(None, context, token)

    text = f"💬 **{get_text('support', user_lang)}**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    text += f"**{get_text('select_action', user_lang)}**:"

    keyboard = []

    if is_button_visible('support'):
        keyboard.append([InlineKeyboardButton("🎫 Тикеты", callback_data="support_tickets")])

    # Доп. кнопки из админки: бот поддержки и администрация
    try:
        bot_cfg = get_bot_config() or {}
    except Exception:
        bot_cfg = {}

    support_bot_username = str(bot_cfg.get("support_bot_username") or "").strip()
    support_url = str(bot_cfg.get("support_url") or "").strip()

    def _normalize_tg_username(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        if value.startswith("@"):
            value = value[1:]
        m = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,})", value)
        if m:
            return m.group(1)
        # plain username
        if re.fullmatch(r"[A-Za-z0-9_]{5,}", value):
            return value
        return ""

    support_bot_url = ""
    support_bot_clean = _normalize_tg_username(support_bot_username)
    if support_bot_clean:
        support_bot_url = f"https://t.me/{support_bot_clean}"

    admin_url = ""
    if support_url:
        if support_url.startswith(("http://", "https://")):
            admin_url = support_url
        else:
            maybe_username = _normalize_tg_username(support_url)
            if maybe_username:
                admin_url = f"https://t.me/{maybe_username}"

    extra_links = []
    if support_bot_url:
        extra_links.append(InlineKeyboardButton(get_text('support_bot_button', user_lang), url=support_bot_url))
    if admin_url:
        extra_links.append(InlineKeyboardButton(get_text('administration_button', user_lang), url=admin_url))
    if extra_links:
        # 1-2 кнопки в ряд
        keyboard.append(extra_links)

    agreement_url = ''
    offer_url = ''
    try:
        branding = api.get_branding() or {}
        agreement_url = (branding.get('user_agreement_url') or '').strip()
        offer_url = (branding.get('offer_url') or '').strip()
    except Exception:
        pass

    def _extract_direct_url(value: str) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        # plain http(s)
        if s.startswith(("http://", "https://")):
            return s
        # t.me link without scheme
        m = re.match(r"^t\.me/([A-Za-z0-9_]{5,})/?$", s)
        if m:
            return f"https://t.me/{m.group(1)}"
        # @username or username
        u = _normalize_tg_username(s)
        if u:
            return f"https://t.me/{u}"
        return ""

    # Если в админке в "Документы" вместо текста указана ссылка — открываем её напрямую
    if not agreement_url:
        agreement_url = _extract_direct_url(get_custom_user_agreement(user_lang))
    if not offer_url:
        offer_url = _extract_direct_url(get_custom_offer_text(user_lang))

    if is_button_visible('agreement'):
        if agreement_url:
            keyboard.append([InlineKeyboardButton(get_text('user_agreement_button', user_lang), url=agreement_url)])
        else:
            keyboard.append([InlineKeyboardButton(get_text('user_agreement_button', user_lang), callback_data="support_agreement")])

    if is_button_visible('offer'):
        if offer_url:
            keyboard.append([InlineKeyboardButton(get_text('offer_button', user_lang), url=offer_url)])
        else:
            keyboard.append([InlineKeyboardButton(get_text('offer_button', user_lang), callback_data="support_offer")])

    keyboard.append([InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    temp_update = Update(update_id=0, callback_query=query)
    await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="support_menu")

async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать выбор типа тарифа (Basic/Pro/Elite)"""
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        await update.callback_query.answer("❌ Ошибка авторизации")
        return
    
    tariffs = api.get_tariffs()
    
    if not tariffs:
        await update.callback_query.answer("❌ Тарифы не найдены")
        return
    
    # Получаем валюту и язык пользователя
    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    currency = user_data.get("preferred_currency", "uah") if user_data else "uah"
    
    currency_map = {
        "uah": {"field": "price_uah", "symbol": "₴"},
        "rub": {"field": "price_rub", "symbol": "₽"},
        "usd": {"field": "price_usd", "symbol": "$"}
    }
    
    currency_config = currency_map.get(currency, currency_map["uah"])
    symbol = currency_config["symbol"]
    
    # Динамические уровни тарифов (как в V3)
    levels = api.get_tariff_levels()
    levels_sorted = sorted(
        (lvl for lvl in levels if isinstance(lvl, dict) and lvl.get("code")),
        key=lambda x: (x.get("display_order", 0), x.get("id", 0))
    )

    branding = api.get_branding()
    basic_name = branding.get("tariff_tier_basic_name", "Базовый") or "Базовый"
    pro_name = branding.get("tariff_tier_pro_name", "Премиум") or "Премиум"
    elite_name = branding.get("tariff_tier_elite_name", "Элитный") or "Элитный"

    tier_names = {lvl["code"]: (lvl.get("name") or lvl["code"]) for lvl in levels_sorted}
    tier_names.setdefault("basic", basic_name)
    tier_names.setdefault("pro", pro_name)
    tier_names.setdefault("elite", elite_name)

    ordered_codes = [lvl["code"] for lvl in levels_sorted]
    if not ordered_codes:
        ordered_codes = ["basic", "pro", "elite"]

    groups = {code: [] for code in ordered_codes}

    for tariff in tariffs:
        duration = tariff.get("duration_days", 0)
        tier = tariff.get("tier")

        if not tier:
            # Обратная совместимость для старых тарифов без tier
            if duration >= 180:
                tier = "elite"
            elif duration >= 90:
                tier = "pro"
            else:
                tier = "basic"

        tier = str(tier).lower()
        tariff["_tier"] = tier
        if tier not in groups:
            groups[tier] = []
            ordered_codes.append(tier)
        groups[tier].append(tariff)

    # Формируем сообщение с выбором уровня тарифа
    text = f"{get_emoji('TARIFFS')} **Тарифные планы**\n"
    text += "━━━━━━━━━━━━━━━\n"

    tier_icons = {
        "basic": get_emoji("PACKAGE"),
        "pro": get_emoji("STAR"),
        "elite": get_emoji("CROWN")
    }

    for code in ordered_codes:
        tier_tariffs = groups.get(code, [])
        if not tier_tariffs:
            continue
        min_price = min(t.get(currency_config["field"], 0) for t in tier_tariffs)
        icon = tier_icons.get(code, get_emoji("STAR"))
        text += f"{icon} {tier_names.get(code, code)} |{get_emoji('BALANCE')}От {min_price:.0f} {symbol}\n"

    text += "━━━━━━━━━━━━━━━\n"

    keyboard = []
    for code in ordered_codes:
        tier_tariffs = groups.get(code, [])
        if not tier_tariffs:
            continue
        icon = tier_icons.get(code, get_emoji("STAR"))
        label = tier_names.get(code, code)
        keyboard.append([InlineKeyboardButton(f"{icon} {label}", callback_data=f"tier_{code}")])
    
    keyboard.append([
        InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="tariffs")
    else:
        try:
            await safe_edit_or_send_with_logo(update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="tariffs")
        except Exception as e:
            logger.warning(f"Error in show_tariffs, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="tariffs")


async def show_tier_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    """Показать тарифы конкретного типа (Basic/Pro/Elite) с выбором длительности"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        await query.answer("❌ Ошибка авторизации")
        return
    
    tariffs = api.get_tariffs()
    
    if not tariffs:
        await query.answer("❌ Тарифы не найдены")
        return
    
    # Получаем валюту и язык пользователя
    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    currency = user_data.get("preferred_currency", "uah") if user_data else "uah"
    
    currency_map = {
        "uah": {"field": "price_uah", "symbol": "₴"},
        "rub": {"field": "price_rub", "symbol": "₽"},
        "usd": {"field": "price_usd", "symbol": "$"}
    }
    
    currency_config = currency_map.get(currency, currency_map["uah"])
    price_field = currency_config["field"]
    symbol = currency_config["symbol"]
    
    # Получаем названия уровней тарифов (TariffLevel), fallback на branding
    branding = api.get_branding()
    basic_name = branding.get("tariff_tier_basic_name", "Базовый") or "Базовый"
    pro_name = branding.get("tariff_tier_pro_name", "Премиум") or "Премиум"
    elite_name = branding.get("tariff_tier_elite_name", "Элитный") or "Элитный"

    levels = api.get_tariff_levels()
    tier_names_plain = {lvl.get("code"): (lvl.get("name") or lvl.get("code")) for lvl in levels if isinstance(lvl, dict) and lvl.get("code")}
    tier_names_plain.setdefault("basic", basic_name)
    tier_names_plain.setdefault("pro", pro_name)
    tier_names_plain.setdefault("elite", elite_name)

    # Фильтруем тарифы по tier
    tier_tariffs = []
    
    for tariff in tariffs:
        duration = tariff.get("duration_days", 0)
        tariff_tier = tariff.get("tier")
        
        if not tariff_tier:
            # Определяем tier по длительности
            if duration >= 180:
                tariff_tier = "elite"
            elif duration >= 90:
                tariff_tier = "pro"
            else:
                tariff_tier = "basic"
        
        if str(tariff_tier).lower() == str(tier).lower():
            tier_tariffs.append(tariff)
    
    if not tier_tariffs:
        await query.answer("❌ Тарифы этого типа не найдены")
        return
    
    # Сортируем по длительности
    tier_tariffs.sort(key=lambda x: x.get("duration_days", 0))
    
    # Получаем функции тарифа для этого tier
    tariff_features = api.get_tariff_features()
    features_list = tariff_features.get(tier, [])
    
    # Получаем названия функций из брендинга
    branding = api.get_branding()
    features_names = branding.get("tariff_features_names", {})
    
    # Подготавливаем функции для генерации изображения
    processed_features = []
    for feature in features_list[:5]:  # Берем первые 5 функций
        if isinstance(feature, dict):
            feature_key = feature.get("key") or feature.get("name")
            feature_name = feature.get("name") or feature.get("title")
            # Пробуем получить название из брендинга
            if feature_key and features_names and isinstance(features_names, dict):
                branded_name = features_names.get(feature_key)
                if branded_name:
                    feature_name = branded_name
            if not feature_name:
                feature_name = feature_key or "Функция"
            
            icon = feature.get("icon", "✓")
            processed_features.append({
                "name": feature_name,
                "icon": icon
            })
        elif isinstance(feature, str):
            processed_features.append({
                "name": feature,
                "icon": "✓"
            })
    
    # Определяем название тарифа и иконку (из .env для премиум)
    tier_icons = {"basic": get_emoji("PACKAGE"), "pro": get_emoji("STAR"), "elite": get_emoji("CROWN")}
    tier_info = {
        "name": tier_names_plain.get(str(tier), str(tier)),
        "icon": tier_icons.get(str(tier), get_emoji("STAR")),
    }
    
    # Генерируем изображение
    try:
        from modules.image_generator import generate_tariff_image
        from io import BytesIO
        
        # Получаем цвет из брендинга (если есть)
        primary_color_hex = branding.get("primary_color", "#3f69ff")
        # Конвертируем hex в RGB tuple
        try:
            hex_color = primary_color_hex.lstrip('#')
            if len(hex_color) == 3:
                hex_color = ''.join([c*2 for c in hex_color])
            primary_color = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        except:
            primary_color = (63, 105, 255)  # Синий по умолчанию
        
        image_bytes = generate_tariff_image(
            tier_name=tier_info["name"],
            tier_icon=tier_info["icon"],
            features=processed_features,
            tariffs=tier_tariffs,
            currency=currency,
            currency_symbol=symbol,
            primary_color=primary_color
        )
        
        # Кнопки выбора длительности
        keyboard = []
        row = []
        for tariff in tier_tariffs:
            duration = tariff.get("duration_days", 0)
            name = f"{duration} дн."
            if len(name) > 15:
                name = f"{duration}д"
            
            row.append(InlineKeyboardButton(
                name,
                callback_data=f"tariff_{tariff.get('id')}"
            ))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        keyboard.append([
            InlineKeyboardButton(get_text('back_to_type', user_lang), callback_data="tariffs")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отправляем изображение
        photo_file = BytesIO(image_bytes)
        photo_file.name = f"tariff_{tier}.png"
        
        # Пытаемся удалить старое сообщение
        try:
            await query.message.delete()
        except:
            pass
        
        # Отправляем новое сообщение с изображением
        sent_message = await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=photo_file,
            caption="Выберите длительность:",
            reply_markup=reply_markup
        )
        
        # Сохраняем message_id для последующего удаления
        if sent_message and sent_message.message_id:
            user_data = context.user_data if hasattr(context, 'user_data') else {}
            if 'bot_message_ids' not in user_data:
                user_data['bot_message_ids'] = []
            user_data['bot_message_ids'].append(sent_message.message_id)
            if len(user_data['bot_message_ids']) > 20:
                user_data['bot_message_ids'] = user_data['bot_message_ids'][-20:]
        
    except ImportError:
        # Если модуль не найден, используем текстовую версию (fallback)
        logger.warning("Image generator module not found, using text version")
        text = f"{tier_info['icon']} {tier_info['name']}\n"
        text += "━━━━━━━━━━━━━━━\n"
        
        if processed_features:
            text += "✨ **Включено в тариф:**\n"  # ✨ не в .env, оставляем как есть
            for feature in processed_features:
                text += f"{feature['icon']} {feature['name']}\n"
            if len(features_list) > 5:
                text += f"... и еще {len(features_list) - 5} функций\n"
            text += "\n"
        
        text += f"{get_emoji('DATE')} Выберите длительность:\n\n"
        
        for tariff in tier_tariffs:
            name = tariff.get("name", f"{tariff.get('duration_days', 0)} дней")
            price = tariff.get(price_field, 0)
            duration = tariff.get("duration_days", 0)
            per_day = price / duration if duration > 0 else price
            text += f"{get_emoji('PACKAGE')} {name} | {get_emoji('BALANCE')} {price:.0f} {symbol} | {get_emoji('STATUS')} {per_day:.2f} {symbol}/день | {get_emoji('DURATION')} {duration} дней\n"
        
        text += "━━━━━━━━━━━━━━━\n"
        
        keyboard = []
        row = []
        for tariff in tier_tariffs:
            duration = tariff.get("duration_days", 0)
            name = f"{duration} дн."
            if len(name) > 15:
                name = f"{duration}д"
            row.append(InlineKeyboardButton(name, callback_data=f"tariff_{tariff.get('id')}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton(get_text('back_to_type', user_lang), callback_data="tariffs")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        temp_update = Update(update_id=0, callback_query=query)
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="tariffs")
        except Exception as e:
            logger.warning(f"Error in show_tier_tariffs, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup, logo_page="tariffs")
    except Exception as e:
        logger.error(f"Error generating tariff image: {e}")
        import traceback
        logger.error(traceback.format_exc())
        # Fallback на текстовую версию при ошибке
        try:
            await query.answer("⚠️ Ошибка генерации изображения, используем текстовую версию")
        except:
            pass
        
        # Отправляем текстовую версию
        text = f"{tier_info['icon']} {tier_info['name']}\n"
        text += "━━━━━━━━━━━━━━━\n"
        
        if processed_features:
            text += "✨ **Включено в тариф:**\n"
            for feature in processed_features:
                text += f"{feature['icon']} {feature['name']}\n"
            if len(features_list) > 5:
                text += f"... и еще {len(features_list) - 5} функций\n"
            text += "\n"
        
        text += f"{get_emoji('DATE')} Выберите длительность:\n\n"
        
        for tariff in tier_tariffs:
            name = tariff.get("name", f"{tariff.get('duration_days', 0)} дней")
            price = tariff.get(price_field, 0)
            duration = tariff.get("duration_days", 0)
            per_day = price / duration if duration > 0 else price
            text += f"{get_emoji('PACKAGE')} {name} | {get_emoji('BALANCE')} {price:.0f} {symbol} | {get_emoji('STATUS')} {per_day:.2f} {symbol}/день | {get_emoji('DURATION')} {duration} дней\n"
        
        text += "━━━━━━━━━━━━━━━\n"
        
        keyboard = []
        row = []
        for tariff in tier_tariffs:
            duration = tariff.get("duration_days", 0)
            name = f"{duration} дн."
            if len(name) > 15:
                name = f"{duration}д"
            row.append(InlineKeyboardButton(name, callback_data=f"tariff_{tariff.get('id')}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton(get_text('back_to_type', user_lang), callback_data="tariffs")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        temp_update = Update(update_id=0, callback_query=query)
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="tariffs")
        except Exception as e2:
            logger.warning(f"Error in show_tier_tariffs fallback, sending without formatting: {e2}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup, logo_page="tariffs")


async def show_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать категории дополнительных опций"""
    query = update.callback_query
    telegram_id = query.from_user.id
    token = get_user_token(telegram_id)
    if not token:
        await query.answer("❌ Ошибка авторизации")
        return

    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    currency = (user_data.get("preferred_currency") if user_data else "uah") or "uah"
    symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(str(currency).lower(), "₴")

    options = api.get_purchase_options() or {}
    traffic = options.get("traffic", []) or []
    devices = options.get("devices", []) or []
    squad = options.get("squad", []) or []

    text = "📦 **Опции**\n\n"
    if not (traffic or devices or squad):
        text += "❌ Сейчас нет доступных опций для покупки."
        keyboard = [[InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]]
        await safe_edit_or_send_with_logo(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown", logo_page="options")
        return

    text += f"Валюта: {symbol}\n\nВыберите категорию:"

    keyboard = []
    if traffic:
        keyboard.append([InlineKeyboardButton(f"📊 Трафик ({len(traffic)})", callback_data="optcat_traffic")])
    if devices:
        keyboard.append([InlineKeyboardButton(f"📱 Устройства ({len(devices)})", callback_data="optcat_devices")])
    if squad:
        keyboard.append([InlineKeyboardButton(f"👥 Сквады ({len(squad)})", callback_data="optcat_squad")])
    keyboard.append([InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")])

    await safe_edit_or_send_with_logo(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown", logo_page="options")


async def show_options_category(update: Update, context: ContextTypes.DEFAULT_TYPE, option_type: str):
    """Показать список опций по типу"""
    query = update.callback_query
    telegram_id = query.from_user.id
    token = get_user_token(telegram_id)
    if not token:
        await query.answer("❌ Ошибка авторизации")
        return

    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    currency = (user_data.get("preferred_currency") if user_data else "uah") or "uah"
    symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(str(currency).lower(), "₴")

    options = api.get_purchase_options() or {}
    items = options.get(option_type, []) or []

    titles = {"traffic": "📊 Трафик", "devices": "📱 Устройства", "squad": "👥 Сквады"}
    title = titles.get(option_type, "📦 Опции")

    text = f"{title}\n\n"
    if not items:
        text += "❌ Нет доступных опций."
        keyboard = [
            [InlineKeyboardButton(get_text('back', user_lang), callback_data="options")],
            [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
        ]
        await safe_edit_or_send_with_logo(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown", logo_page="options")
        return

    text += f"Валюта: {symbol}\n\nВыберите опцию:"
    keyboard = []

    def _price_for(opt: dict) -> float:
        c = str(currency).lower()
        if c == "uah":
            return float(opt.get("price_uah") or 0)
        if c == "rub":
            return float(opt.get("price_rub") or 0)
        if c == "usd":
            return float(opt.get("price_usd") or 0)
        return float(opt.get("price_rub") or 0)

    for opt in items:
        opt_id = opt.get("id")
        if not opt_id:
            continue
        icon = opt.get("icon") or "📦"
        name = opt.get("name") or f"Option #{opt_id}"
        value = opt.get("value")
        unit = opt.get("unit") or ""
        price = _price_for(opt)
        label = f"{icon} {name} — {price:.2f} {symbol}"
        if value:
            label = f"{icon} {name} ({value}{(' ' + unit) if unit else ''}) — {price:.2f} {symbol}"
        if len(label) > 60:
            label = label[:57] + "..."
        keyboard.append([InlineKeyboardButton(label, callback_data=f"opt_{opt_id}")])

    keyboard.append([InlineKeyboardButton(get_text('back', user_lang), callback_data="options")])
    keyboard.append([InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")])

    await safe_edit_or_send_with_logo(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown", logo_page="options")


async def show_option_payment_methods(update: Update, context: ContextTypes.DEFAULT_TYPE, option_id: int):
    """Показать методы оплаты для опции"""
    query = update.callback_query
    telegram_id = query.from_user.id
    token = get_user_token(telegram_id)
    if not token:
        await query.answer("❌ Ошибка авторизации")
        return

    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    
    # Получаем валюту для отображения символа
    currency = user_data.get("preferred_currency", "rub") if user_data else "rub"
    currency_map = {
        "uah": {"field": "price_uah", "symbol": "₴"},
        "rub": {"field": "price_rub", "symbol": "₽"},
        "usd": {"field": "price_usd", "symbol": "$"}
    }
    currency_config = currency_map.get(currency, currency_map["rub"])

    available_methods = api.get_available_payment_methods()
    if not available_methods:
        await query.answer()
        text = "❌ Нет доступных способов оплаты. Настройте платежки в админке."
        keyboard = [[InlineKeyboardButton(get_text('back', user_lang), callback_data="options")]]
        await safe_edit_or_send_with_logo(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard), logo_page="options")
        return

    # Информация об опции и балансе — только через API (без доступа к БД из бота)
    options = api.get_purchase_options() or {}
    option = None
    for key in ("traffic", "devices", "squad"):
        for opt in (options.get(key) or []):
            if opt.get("id") == option_id:
                option = opt
                break
        if option:
            break

    text = "💳 **Способ оплаты**\n\nВыберите метод оплаты:"
    keyboard = []

    provider_names = {
        "crystalpay": "CrystalPay",
        "heleket": "Heleket",
        "yookassa": "YooKassa",
        "yoomoney": "YooMoney",
        "platega": "Platega",
        "platega_mir": "Platega (МИР)",
        "freekassa": "FreeKassa",
        "kassa_ai": "Kassa AI",
        "robokassa": "Robokassa",
        "cryptobot": "CryptoBot",
        "telegram_stars": "Telegram Stars",
        "monobank": "Monobank",
        "btcpayserver": "BTCPayServer",
        "mulenpay": "MulenPay",
        "urlpay": "URLPay",
        "tribute": "Tribute",
    }

    for provider in available_methods:
        # Исключаем баланс из списка внешних методов (добавим отдельно)
        if provider == "balance":
            continue
        name = provider_names.get(provider, provider)
        keyboard.append([InlineKeyboardButton(f"💳 {name}", callback_data=f"optpay_{option_id}_{provider}")])

    # Добавляем кнопку оплаты с баланса, если опция найдена
    if option:
        c = str(currency).lower()
        option_price = float(option.get("price_uah") if c == "uah" else option.get("price_rub") if c == "rub" else option.get("price_usd", 0) or option.get("price_rub", 0))
        currency_code = "UAH" if c == "uah" else "USD" if c == "usd" else "RUB"
        balance_usd = float(user_data.get("balance_usd") or user_data.get("balance") or 0) if user_data else 0.0
        # Простая конвертация в USD по курсу (как в API)
        rates = {"UAH": 41.0, "RUB": 95.0, "USD": 1.0}
        rate = rates.get(currency_code, 1.0)
        option_price_usd = option_price / rate if rate else option_price
        can_afford = balance_usd >= option_price_usd

        if option_price and option_price > 0:
            if can_afford:
                keyboard.append([
                    InlineKeyboardButton(
                        f"💰 {get_text('pay_with_balance', user_lang)} ({option_price:.0f} {currency_config['symbol']})",
                        callback_data=f"optpay_{option_id}_balance"
                    )
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(
                        f"💰 {get_text('pay_with_balance', user_lang)} ({get_text('insufficient_balance', user_lang)})",
                        callback_data=f"optpay_{option_id}_balance"
                    )
                ])

    keyboard.append([InlineKeyboardButton(get_text('back', user_lang), callback_data="options")])
    await query.answer()
    await safe_edit_or_send_with_logo(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown", logo_page="options")


async def show_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список серверов"""
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        await update.callback_query.answer("❌ Ошибка авторизации")
        return
    
    # Проверяем активность подписки
    token, user_data = get_user_data_safe(telegram_id, token)
    if not user_data:
        await update.callback_query.answer("❌ Не удалось загрузить данные")
        return
    
    user_lang = get_user_lang(user_data, context, token)
    is_active = user_data.get("activeInternalSquads", [])
    expire_at = user_data.get("expireAt")
    
    if not is_active or not expire_at:
        await update.callback_query.answer("❌ Подписка не активна. Активируйте триал или выберите тариф")
        return
    
    nodes = api.get_nodes(token)
    
    back_to = pop_back_callback(context, "main_menu")

    if not nodes:
        text = f"{get_emoji('SERVERS')} **Серверы**\n\n❌ Серверы не найдены"
        keyboard = [[InlineKeyboardButton(get_text('back', user_lang), callback_data=back_to)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if has_cards(text):
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="servers")
        else:
            try:
                await safe_edit_or_send_with_logo(update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="servers")
            except Exception as e:
                logger.warning(f"Error in show_servers, sending without formatting: {e}")
                text_clean = clean_markdown_for_cards(text)
                await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="servers")
        return
    
    # Формируем сообщение
    text = f"{get_emoji('SERVERS')} **Доступные серверы**\n\n"
    text += f"Всего серверов: {len(nodes)}\n\n"
    
    # Группируем по регионам
    regions = {}
    for node in nodes[:20]:  # Показываем первые 20
        region = node.get("regionName") or node.get("countryCode", "Unknown")
        if region not in regions:
            regions[region] = []
        regions[region].append(node)
    
    for region, region_nodes in list(regions.items())[:5]:  # Показываем первые 5 регионов
        text += f"{get_emoji('LOCATION')} **{region}** ({len(region_nodes)} серверов)\n"
        for node in region_nodes[:3]:  # По 3 сервера на регион
            name = node.get("nodeName", "Unknown")
            text += f"  • {name}\n"
        text += "\n"
    
    if len(nodes) > 20:
        text += f"\n... и еще {len(nodes) - 20} серверов"
    
    keyboard = [[InlineKeyboardButton(get_text('back', user_lang), callback_data=back_to)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="servers")
    else:
        try:
            await safe_edit_or_send_with_logo(update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="servers")
        except Exception as e:
            logger.warning(f"Error in show_servers, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="servers")


async def show_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать реферальную программу (с поддержкой новой процентной системы)"""
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        await update.callback_query.answer("❌ Ошибка авторизации")
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    if not user_data:
        await update.callback_query.answer("❌ Не удалось загрузить данные")
        return
    
    # Получаем язык пользователя
    user_lang = get_user_lang(user_data, context, token)
    
    # Получаем информацию о реферальной программе из API
    try:
        ref_resp = api.session.get(
            f"{FLASK_API_URL}/api/client/referrals/info",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5
        )
        if ref_resp.status_code == 200:
            ref_data = ref_resp.json()
            referral_code = ref_data.get("referral_code", "")
            referral_link_direct = ref_data.get("referral_link_direct", "")
            referral_link_telegram = ref_data.get("referral_link_telegram", "")
            referral_info = ref_data.get("referral_info", {})
            referrals_count = ref_data.get("referrals_count", 0)
        else:
            # Fallback на старую логику
            referral_code = user_data.get("referral_code", "")
            referral_link_direct = ""
            referral_link_telegram = ""
            referral_info = {}
            referrals_count = 0
    except Exception as e:
        logger.warning(f"Error fetching referral info: {e}")
        # Fallback на старую логику
        referral_code = user_data.get("referral_code", "")
        referral_link_direct = ""
        referral_link_telegram = ""
        referral_info = {}
        referrals_count = 0
    
    # Если нет данных из API, используем старую логику
    if not referral_code:
        referral_code = user_data.get("referral_code", "")
        if not referral_code:
            text = f"❌ {get_text('referral_code_not_found', user_lang)}\n"
            keyboard = [[InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await safe_edit_or_send_with_logo(update, context, text, reply_markup=reply_markup, logo_page="referrals")
            return
        
        # Получаем домен сервера из API
        try:
            domain_resp = api.session.get(f"{FLASK_API_URL}/api/public/server-domain", timeout=5)
            if domain_resp.status_code == 200:
                domain_data = domain_resp.json()
                server_domain = domain_data.get("full_url") or domain_data.get("domain") or YOUR_SERVER_IP
            else:
                server_domain = YOUR_SERVER_IP
        except:
            server_domain = YOUR_SERVER_IP
        
        if not server_domain.startswith("http"):
            server_domain = f"https://{server_domain}"
        referral_link_direct = f"{server_domain}/register?ref={referral_code}"
        
        # Для старого бота используем имя бота для реферальных ссылок
        # Приоритет: TELEGRAM_BOT_NAME_V2 -> TELEGRAM_BOT_NAME -> BOT_USERNAME -> CLIENT_BOT_USERNAME
        # Если нет TELEGRAM_BOT_NAME_V2, используем TELEGRAM_BOT_NAME
        bot_username = os.getenv("TELEGRAM_BOT_NAME_V2") or os.getenv("TELEGRAM_BOT_NAME") or os.getenv("BOT_USERNAME") or os.getenv("CLIENT_BOT_USERNAME", "stealthnet_vpn_bot")
        # Убираем @ если есть
        if bot_username.startswith('@'):
            bot_username = bot_username[1:]
        referral_link_telegram = f"https://t.me/{bot_username}?start={referral_code}"
    
    # Формируем текст сообщения
    text = f"🎁 **{get_text('referral_program', user_lang)}**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    
    # Информация о типе реферальной системы
    if referral_info:
        ref_type = referral_info.get("type", "DAYS")
        if ref_type == "PERCENT":
            # Процентная система
            text += f"💰 **{referral_info.get('title', 'Реферальная программа с процентами')}**\n\n"
            text += f"💡 {referral_info.get('description', 'Приглашайте друзей и получайте процент с их покупок!')}\n\n"
            text += f"📊 **Ваш процент:** {referral_info.get('your_percent', '10%')}\n"
            text += f"👥 **Приглашено:** {referrals_count} чел.\n\n"
            text += "**Как это работает:**\n"
            for step in referral_info.get("how_it_works", []):
                text += f"• {step}\n"
        else:
            # Система на дни
            text += f"📅 **{referral_info.get('title', 'Реферальная программа на дни')}**\n\n"
            text += f"💡 {referral_info.get('description', 'Приглашайте друзей и получайте бесплатные дни!')}\n\n"
            text += f"🎁 **Бонус приглашенному:** {referral_info.get('invitee_bonus', '3 дня')}\n"
            text += f"🎁 **Ваш бонус:** {referral_info.get('referrer_bonus', '3 дня за каждого')}\n"
            text += f"👥 **Приглашено:** {referrals_count} чел.\n\n"
            text += "**Как это работает:**\n"
            for step in referral_info.get("how_it_works", []):
                text += f"• {step}\n"
    else:
        text += f"💡 {get_text('invite_friends', user_lang)}\n\n"
    
    text += "\n━━━━━━━━━━━━━━━\n\n"
    
    # Реферальные ссылки
    if referral_code:
        text += f"🔗 **{get_text('your_referral_link', user_lang)}**\n"
        text += f"`{referral_link_direct}`\n\n"
        
        text += f"🤖 **Ссылка через бота:**\n"
        text += f"`{referral_link_telegram}`\n\n"
        
        text += f"📝 **{get_text('your_code', user_lang)}**\n"
        text += f"`{referral_code}`\n"
    
    keyboard = [
        [InlineKeyboardButton(get_text('copy_link', user_lang), callback_data=f"copy_ref_{referral_code}")],
        [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования/отправки
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="referrals")
    else:
        # Для текста без карточек используем Markdown
        try:
            await safe_edit_or_send_with_logo(update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="referrals")
        except Exception as e:
            logger.warning(f"Error in show_referrals, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="referrals")


async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать поддержку"""
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await update.callback_query.answer(f"❌ {get_text('auth_error', lang)}")
        return

    token, user_data = get_user_data_safe(telegram_id, token)
    if not user_data:
        lang = get_user_lang(None, context, token)
        await update.callback_query.answer(f"❌ {get_text('failed_to_load', lang)}")
        return

    user_lang = get_user_lang(user_data, context, token)

    tickets = api.get_support_tickets(token)
    
    text = f"💬 **{get_text('support_title', user_lang)}**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    
    if tickets:
        text += f"📋 **{get_text('your_tickets', user_lang)}:** ({len(tickets)})\n\n"
        for ticket in tickets[:5]:
            status_emoji = "✅" if ticket.get("status") == "CLOSED" else "🔄"
            ticket_id = ticket.get('id')
            subject = ticket.get('subject', get_text('no_subject', user_lang))
            text += f"{status_emoji} {get_text('ticket', user_lang)} #{ticket_id}: {subject}\n"
    else:
        text += f"{get_text('no_tickets', user_lang)}\n"
    
    text += f"\n**{get_text('select_action', user_lang)}**:"
    
    keyboard = [
        [InlineKeyboardButton(get_text('create_ticket_button', user_lang), callback_data="create_ticket")]
    ]
    
    # Добавляем кнопки для просмотра тикетов, если они есть
    if tickets:
        for ticket in tickets[:3]:  # Показываем первые 3 тикета
            ticket_id = ticket.get('id')
            subject = ticket.get('subject', get_text('no_subject', user_lang))
            # Обрезаем длинные темы
            if len(subject) > 30:
                subject = subject[:27] + "..."
            keyboard.append([
                InlineKeyboardButton(
                    f"📋 #{ticket_id}: {subject}",
                    callback_data=f"view_ticket_{ticket_id}"
                )
            ])
    
    back_to = pop_back_callback(context, "main_menu")
    keyboard.append([InlineKeyboardButton(get_text('back', user_lang), callback_data=back_to)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования/отправки
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="support_menu")
    else:
        # Для текста без карточек используем Markdown
        try:
            await safe_edit_or_send_with_logo(update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="support_menu")
        except Exception as e:
            logger.warning(f"Error in show_tariffs, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup, logo_page="support_menu")


async def show_user_agreement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать пользовательское соглашение"""
    telegram_id = update.effective_user.id
    token = get_user_token(telegram_id)
    user_lang = get_user_lang(None, context, token)
    
    # Текст пользовательского соглашения (может быть ссылкой)
    agreement_text = get_user_agreement_text(user_lang)
    agreement_url = ""
    if isinstance(agreement_text, str):
        s = agreement_text.strip()
        if s.startswith(("http://", "https://")):
            agreement_url = s
        else:
            m = re.match(r"^t\.me/([A-Za-z0-9_]{5,})/?$", s)
            if m:
                agreement_url = f"https://t.me/{m.group(1)}"
    
    back_to = pop_back_callback(context, "main_menu")
    keyboard = []
    if agreement_url:
        keyboard.append([InlineKeyboardButton(get_text('user_agreement_button', user_lang), url=agreement_url)])
    keyboard.append([InlineKeyboardButton(get_text('back', user_lang), callback_data=back_to)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования
    try:
        text_to_send = agreement_text if not agreement_url else f"📄 {get_text('user_agreement_title', user_lang)}\n\n{agreement_url}"
        await safe_edit_or_send_with_logo(update, context, text_to_send, reply_markup=reply_markup, parse_mode="Markdown", logo_page="agreement")
    except Exception as e:
        logger.warning(f"Error in show_user_agreement: {e}")
        await safe_edit_or_send_with_logo(update, context, clean_markdown_for_cards(agreement_text), reply_markup=reply_markup, logo_page="agreement")


async def show_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать публичную оферту"""
    telegram_id = update.effective_user.id
    token = get_user_token(telegram_id)
    user_lang = get_user_lang(None, context, token)
    
    # Текст публичной оферты (может быть ссылкой)
    offer_text = get_offer_text(user_lang)
    offer_url = ""
    if isinstance(offer_text, str):
        s = offer_text.strip()
        if s.startswith(("http://", "https://")):
            offer_url = s
        else:
            m = re.match(r"^t\.me/([A-Za-z0-9_]{5,})/?$", s)
            if m:
                offer_url = f"https://t.me/{m.group(1)}"
    
    back_to = pop_back_callback(context, "main_menu")
    keyboard = []
    if offer_url:
        keyboard.append([InlineKeyboardButton(get_text('offer_button', user_lang), url=offer_url)])
    keyboard.append([InlineKeyboardButton(get_text('back', user_lang), callback_data=back_to)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования
    try:
        text_to_send = offer_text if not offer_url else f"📋 {get_text('offer_title', user_lang)}\n\n{offer_url}"
        await safe_edit_or_send_with_logo(update, context, text_to_send, reply_markup=reply_markup, parse_mode="Markdown", logo_page="offer")
    except Exception as e:
        logger.warning(f"Error in show_offer: {e}")
        await safe_edit_or_send_with_logo(update, context, clean_markdown_for_cards(offer_text), reply_markup=reply_markup, logo_page="offer")


def get_user_agreement_text(lang: str = 'ru') -> str:
    """Получить текст пользовательского соглашения на указанном языке"""
    texts = {
        'ru': """📄 **Пользовательское соглашение**

━━━━━━━━━━━━━━━

**1. Общие положения**

1.1. Настоящее Пользовательское соглашение (далее — «Соглашение») определяет условия использования сервиса {SERVICE_NAME} VPN (далее — «Сервис»).

1.2. Используя Сервис, Пользователь соглашается с условиями настоящего Соглашения.

**2. Предмет соглашения**

2.1. Сервис предоставляет услуги по обеспечению доступа к сети Интернет через VPN-соединение.

2.2. Пользователь обязуется использовать Сервис в соответствии с законодательством и не нарушать права третьих лиц.

**3. Права и обязанности**

3.1. Пользователь имеет право использовать Сервис в соответствии с выбранным тарифным планом.

3.2. Пользователь обязуется не использовать Сервис для противоправных действий.

**4. Ответственность**

4.1. Сервис не несет ответственности за действия Пользователя при использовании VPN-соединения.

4.2. Пользователь несет полную ответственность за свои действия при использовании Сервиса.

**5. Заключительные положения**

5.1. Настоящее Соглашение вступает в силу с момента начала использования Сервиса.

5.2. Администрация Сервиса оставляет за собой право изменять условия Соглашения.""",
        'ua': """📄 **Користувацька угода**

━━━━━━━━━━━━━━━

**1. Загальні положення**

1.1. Ця Користувацька угода (далі — «Угода») визначає умови використання сервісу {SERVICE_NAME} VPN (далі — «Сервіс»).

1.2. Використовуючи Сервіс, Користувач погоджується з умовами цієї Угоди.

**2. Предмет угоди**

2.1. Сервіс надає послуги з забезпечення доступу до мережі Інтернет через VPN-з'єднання.

2.2. Користувач зобов'язується використовувати Сервіс відповідно до законодавства та не порушувати права третіх осіб.

**3. Права та обов'язки**

3.1. Користувач має право використовувати Сервіс відповідно до обраного тарифного плану.

3.2. Користувач зобов'язується не використовувати Сервіс для протиправних дій.

**4. Відповідальність**

4.1. Сервіс не несе відповідальності за дії Користувача при використанні VPN-з'єднання.

4.2. Користувач несе повну відповідальність за свої дії при використанні Сервісу.

**5. Заключні положення**

5.1. Ця Угода набуває чинності з моменту початку використання Сервісу.

5.2. Адміністрація Сервісу залишає за собою право змінювати умови Угоди.""",
        'en': """📄 **User Agreement**

━━━━━━━━━━━━━━━

**1. General Provisions**

1.1. This User Agreement (hereinafter — "Agreement") defines the terms of use of the {SERVICE_NAME} VPN service (hereinafter — "Service").

1.2. By using the Service, the User agrees to the terms of this Agreement.

**2. Subject of Agreement**

2.1. The Service provides services for Internet access through VPN connection.

2.2. The User undertakes to use the Service in accordance with the law and not to violate the rights of third parties.

**3. Rights and Obligations**

3.1. The User has the right to use the Service in accordance with the selected tariff plan.

3.2. The User undertakes not to use the Service for illegal activities.

**4. Liability**

4.1. The Service is not responsible for the User's actions when using VPN connection.

4.2. The User bears full responsibility for their actions when using the Service.

**5. Final Provisions**

5.1. This Agreement comes into force from the moment of starting to use the Service.

5.2. The Service Administration reserves the right to change the terms of the Agreement.""",
        'cn': """📄 **用户协议**

━━━━━━━━━━━━━━━

**1. 总则**

1.1. 本用户协议（以下简称"协议"）定义了使用 {SERVICE_NAME} VPN 服务（以下简称"服务"）的条款。

1.2. 使用服务即表示用户同意本协议的条款。

**2. 协议主题**

2.1. 服务提供通过 VPN 连接访问互联网的服务。

2.2. 用户承诺按照法律使用服务，不侵犯第三方权利。

**3. 权利和义务**

3.1. 用户有权根据所选资费计划使用服务。

3.2. 用户承诺不将服务用于非法活动。

**4. 责任**

4.1. 服务不对用户使用 VPN 连接时的行为负责。

4.2. 用户对其使用服务时的行为承担全部责任。

**5. 最终条款**

5.1. 本协议自开始使用服务时生效。

5.2. 服务管理方保留更改协议条款的权利。"""
    }
    # Сначала проверяем кастомный текст из админки
    custom = get_custom_user_agreement(lang)
    if custom and custom.strip():
        return custom.replace('{SERVICE_NAME}', get_service_name())
    
    # Иначе используем встроенный текст
    text = texts.get(lang, texts['ru'])
    # Форматируем текст, заменяя {SERVICE_NAME} на актуальное значение
    return text.format(SERVICE_NAME=get_service_name())


def get_offer_text(lang: str = 'ru') -> str:
    """Получить текст публичной оферты на указанном языке"""
    texts = {
        'ru': """📋 **Публичная оферта**

━━━━━━━━━━━━━━━

**Оферта на оказание услуг VPN**

Настоящий документ является публичной офертой (далее — «Оферта») в адрес физических и юридических лиц (далее — «Заказчик») о заключении договора на оказание услуг VPN (далее — «Договор»).

**1. Термины и определения**

1.1. **Исполнитель** — {SERVICE_NAME} VPN, предоставляющий услуги VPN.

1.2. **Заказчик** — физическое или юридическое лицо, принявшее условия настоящей Оферты.

1.3. **Услуги** — услуги по предоставлению доступа к сети Интернет через VPN-соединение.

1.4. **Тарифный план** — выбранный Заказчиком пакет услуг с определенными характеристиками и стоимостью.

**2. Предмет договора**

2.1. Исполнитель обязуется предоставить Заказчику услуги VPN в соответствии с выбранным Тарифным планом.

2.2. Заказчик обязуется оплатить услуги в размере и порядке, указанных в Тарифном плане.

**3. Порядок оказания услуг**

3.1. Услуги предоставляются после оплаты выбранного Тарифного плана.

3.2. Доступ к услугам предоставляется в течение 24 часов с момента оплаты.

**4. Стоимость услуг и порядок расчетов**

4.1. Стоимость услуг определяется в соответствии с выбранным Тарифным планом.

4.2. Оплата производится в порядке, указанном на сайте Сервиса.

**5. Права и обязанности сторон**

5.1. Исполнитель обязуется предоставить услуги в соответствии с условиями Договора.

5.2. Заказчик обязуется использовать услуги в соответствии с законодательством.

**6. Ответственность сторон**

6.1. Исполнитель не несет ответственности за действия Заказчика при использовании услуг.

6.2. Заказчик несет полную ответственность за свои действия.

**7. Заключительные положения**

7.1. Акцептом настоящей Оферты является оплата услуг Заказчиком.

7.2. Настоящая Оферта вступает в силу с момента публикации на сайте.""",
        'ua': """📋 **Публічна оферта**

━━━━━━━━━━━━━━━

**Оферта на надання послуг VPN**

Цей документ є публічною офертою (далі — «Оферта») на адресу фізичних та юридичних осіб (далі — «Замовник») про укладення договору на надання послуг VPN (далі — «Договір»).

**1. Терміни та визначення**

1.1. **Виконавець** — {SERVICE_NAME} VPN, що надає послуги VPN.

1.2. **Замовник** — фізична або юридична особа, яка прийняла умови цієї Оферти.

1.3. **Послуги** — послуги з надання доступу до мережі Інтернет через VPN-з'єднання.

1.4. **Тарифний план** — обраний Замовником пакет послуг з певними характеристиками та вартістю.

**2. Предмет договору**

2.1. Виконавець зобов'язується надати Замовнику послуги VPN відповідно до обраного Тарифного плану.

2.2. Замовник зобов'язується оплатити послуги в розмірі та порядку, зазначених у Тарифному плані.

**3. Порядок надання послуг**

3.1. Послуги надаються після оплати обраного Тарифного плану.

3.2. Доступ до послуг надається протягом 24 годин з моменту оплати.

**4. Вартість послуг та порядок розрахунків**

4.1. Вартість послуг визначається відповідно до обраного Тарифного плану.

4.2. Оплата здійснюється в порядку, зазначеному на сайті Сервісу.

**5. Права та обов'язки сторін**

5.1. Виконавець зобов'язується надати послуги відповідно до умов Договору.

5.2. Замовник зобов'язується використовувати послуги відповідно до законодавства.

**6. Відповідальність сторін**

6.1. Виконавець не несе відповідальності за дії Замовника при використанні послуг.

6.2. Замовник несе повну відповідальність за свої дії.

**7. Заключні положення**

7.1. Акцептом цієї Оферти є оплата послуг Замовником.

7.2. Ця Оферта набуває чинності з моменту публікації на сайті.""",
        'en': """📋 **Public Offer**

━━━━━━━━━━━━━━━

**Offer for VPN Services**

This document is a public offer (hereinafter — "Offer") addressed to individuals and legal entities (hereinafter — "Customer") for concluding a contract for VPN services (hereinafter — "Contract").

**1. Terms and Definitions**

1.1. **Contractor** — {SERVICE_NAME} VPN, providing VPN services.

1.2. **Customer** — an individual or legal entity that has accepted the terms of this Offer.

1.3. **Services** — services for providing Internet access through VPN connection.

1.4. **Tariff Plan** — a package of services selected by the Customer with certain characteristics and cost.

**2. Subject of Contract**

2.1. The Contractor undertakes to provide the Customer with VPN services in accordance with the selected Tariff Plan.

2.2. The Customer undertakes to pay for the services in the amount and manner specified in the Tariff Plan.

**3. Procedure for Providing Services**

3.1. Services are provided after payment of the selected Tariff Plan.

3.2. Access to services is provided within 24 hours from the moment of payment.

**4. Cost of Services and Payment Procedure**

4.1. The cost of services is determined in accordance with the selected Tariff Plan.

4.2. Payment is made in the manner specified on the Service website.

**5. Rights and Obligations of the Parties**

5.1. The Contractor undertakes to provide services in accordance with the terms of the Contract.

5.2. The Customer undertakes to use the services in accordance with the law.

**6. Liability of the Parties**

6.1. The Contractor is not responsible for the Customer's actions when using the services.

6.2. The Customer bears full responsibility for their actions.

**7. Final Provisions**

7.1. Acceptance of this Offer is the payment for services by the Customer.

7.2. This Offer comes into force from the moment of publication on the website.""",
        'cn': """📋 **公开要约**

━━━━━━━━━━━━━━━

**VPN 服务要约**

本文件是向个人和法律实体（以下简称"客户"）发出的关于签订 VPN 服务合同（以下简称"合同"）的公开要约（以下简称"要约"）。

**1. 术语和定义**

1.1. **承包商** — {SERVICE_NAME} VPN，提供 VPN 服务。

1.2. **客户** — 接受本要约条款的个人或法律实体。

1.3. **服务** — 通过 VPN 连接提供互联网访问的服务。

1.4. **资费计划** — 客户选择的服务包，具有特定特征和成本。

**2. 合同主题**

2.1. 承包商承诺根据所选资费计划向客户提供 VPN 服务。

2.2. 客户承诺按照资费计划中规定的金额和方式支付服务费用。

**3. 服务提供程序**

3.1. 服务在支付所选资费计划后提供。

3.2. 服务访问在付款后 24 小时内提供。

**4. 服务费用和付款程序**

4.1. 服务费用根据所选资费计划确定。

4.2. 付款按照服务网站上规定的方式进行。

**5. 双方的权利和义务**

5.1. 承包商承诺按照合同条款提供服务。

5.2. 客户承诺按照法律使用服务。

**6. 双方的责任**

6.1. 承包商不对客户使用服务时的行为负责。

6.2. 客户对其行为承担全部责任。

**7. 最终条款**

7.1. 接受本要约即客户支付服务费用。

7.2. 本要约自网站发布之日起生效。"""
    }
    # Сначала проверяем кастомный текст из админки
    custom = get_custom_offer_text(lang)
    if custom and custom.strip():
        return custom.replace('{SERVICE_NAME}', get_service_name())
    
    # Иначе используем встроенный текст
    text = texts.get(lang, texts['ru'])
    # Форматируем текст, заменяя {SERVICE_NAME} на актуальное значение
    return text.format(SERVICE_NAME=get_service_name())


def get_refund_policy_text(lang: str = 'ru') -> str:
    """Получить текст политики возврата на указанном языке"""
    texts = {
        'ru': """💰 **Политика возврата**

━━━━━━━━━━━━━━━

**Условия возврата средств**

1. **Общие положения**

1.1. Настоящая Политика возврата (далее — «Политика») определяет условия и порядок возврата денежных средств за услуги {SERVICE_NAME} VPN (далее — «Сервис»).

1.2. Возврат средств возможен только в случаях, предусмотренных настоящей Политикой.

**2. Условия возврата**

2.1. Возврат средств производится в следующих случаях:
   - Технические проблемы, не позволяющие использовать услугу более 48 часов
   - Ошибка при оплате (двойная оплата, неправильная сумма)
   - Отказ в предоставлении услуги по вине Сервиса

2.2. Возврат средств НЕ производится в следующих случаях:
   - Пользователь использовал услугу более 7 дней
   - Нарушение пользователем правил использования сервиса
   - Блокировка аккаунта за нарушение условий использования
   - Изменение решения пользователя после начала использования услуги

**3. Порядок возврата**

3.1. Запрос на возврат средств должен быть направлен в службу поддержки в течение 7 дней с момента оплаты.

3.2. Возврат средств производится на тот же способ оплаты, которым была произведена оплата.

3.3. Срок возврата средств составляет от 3 до 14 рабочих дней в зависимости от способа оплаты.

**4. Контакты**

4.1. Для оформления возврата средств обратитесь в службу поддержки через раздел "Поддержка" в боте или на сайте.""",
        'ua': """💰 **Політика повернення**

━━━━━━━━━━━━━━━

**Умови повернення коштів**

1. **Загальні положення**

1.1. Ця Політика повернення (далі — «Політика») визначає умови та порядок повернення коштів за послуги {SERVICE_NAME} VPN (далі — «Сервіс»).

1.2. Повернення коштів можливе лише у випадках, передбачених цією Політикою.

**2. Умови повернення**

2.1. Повернення коштів здійснюється у таких випадках:
   - Технічні проблеми, що не дозволяють використовувати послугу більше 48 годин
   - Помилка при оплаті (подвійна оплата, неправильна сума)
   - Відмова в наданні послуги з вини Сервісу

2.2. Повернення коштів НЕ здійснюється у таких випадках:
   - Користувач використав послугу більше 7 днів
   - Порушення користувачем правил використання сервісу
   - Блокування акаунта за порушення умов використання
   - Зміна рішення користувача після початку використання послуги

**3. Порядок повернення**

3.1. Запит на повернення коштів має бути направлений до служби підтримки протягом 7 днів з моменту оплати.

3.2. Повернення коштів здійснюється на той самий спосіб оплати, яким була здійснена оплата.

3.3. Термін повернення коштів становить від 3 до 14 робочих днів залежно від способу оплати.

**4. Контакти**

4.1. Для оформлення повернення коштів зверніться до служби підтримки через розділ "Підтримка" в боті або на сайті.""",
        'en': """💰 **Refund Policy**

━━━━━━━━━━━━━━━

**Refund Terms**

1. **General Provisions**

1.1. This Refund Policy (hereinafter — "Policy") defines the terms and procedure for refunding funds for {SERVICE_NAME} VPN services (hereinafter — "Service").

1.2. Refunds are possible only in cases provided for by this Policy.

**2. Refund Conditions**

2.1. Refunds are made in the following cases:
   - Technical problems that prevent the use of the service for more than 48 hours
   - Payment error (double payment, incorrect amount)
   - Refusal to provide service due to the fault of the Service

2.2. Refunds are NOT made in the following cases:
   - The user has used the service for more than 7 days
   - User's violation of the service usage rules
   - Account blocking for violation of terms of use
   - User's change of decision after starting to use the service

**3. Refund Procedure**

3.1. A refund request must be sent to the support service within 7 days from the date of payment.

3.2. Refunds are made to the same payment method used for payment.

3.3. The refund period is from 3 to 14 business days depending on the payment method.

**4. Contacts**

4.1. To request a refund, contact the support service through the "Support" section in the bot or on the website.""",
        'cn': """💰 **退款政策**

━━━━━━━━━━━━━━━

**退款条款**

1. **总则**

1.1. 本退款政策（以下简称"政策"）规定了{SERVICE_NAME} VPN服务（以下简称"服务"）的退款条件和程序。

1.2. 只有在符合本政策规定的情况下才能退款。

**2. 退款条件**

2.1. 在以下情况下可以退款：
   - 技术问题导致服务无法使用超过48小时
   - 支付错误（重复支付、金额错误）
   - 由于服务方原因拒绝提供服务

2.2. 在以下情况下不退款：
   - 用户使用服务超过7天
   - 用户违反服务使用规则
   - 因违反使用条款而被封禁账户
   - 用户在使用服务后改变决定

**3. 退款程序**

3.1. 退款请求必须在付款后7天内发送给支持服务。

3.2. 退款将退回到用于付款的同一支付方式。

3.3. 退款期限为3至14个工作日，具体取决于支付方式。

**4. 联系方式**

4.1. 要申请退款，请通过机器人或网站上的"支持"部分联系支持服务。"""
    }
    
    text = texts.get(lang, texts['ru'])
    # Форматируем текст, заменяя {SERVICE_NAME} на актуальное значение
    return text.format(SERVICE_NAME=get_service_name())


async def show_refund_policy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать политику возврата"""
    telegram_id = update.effective_user.id
    token = get_user_token(telegram_id)
    user_lang = get_user_lang(None, context, token)
    
    # Текст политики возврата
    policy_text = get_refund_policy_text(user_lang)
    
    keyboard = [
        [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования
    try:
        await safe_edit_or_send_with_logo(update, context, policy_text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="settings")
    except Exception as e:
        logger.warning(f"Error in show_refund_policy: {e}")
        await safe_edit_or_send_with_logo(update, context, clean_markdown_for_cards(policy_text), reply_markup=reply_markup, logo_page="settings")


async def delete_recent_bot_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_data: dict, max_messages: int = 10):
    """Удалить последние сообщения бота в чате"""
    try:
        # Получаем список сохраненных message_id из user_data
        bot_message_ids = user_data.get('bot_message_ids', [])
        if not bot_message_ids:
            return
        
        # Удаляем последние сообщения (не более max_messages)
        messages_to_delete = bot_message_ids[-max_messages:]
        
        for msg_id in messages_to_delete:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception as e:
                # Игнорируем ошибки удаления (сообщение уже удалено или недоступно)
                logger.debug(f"Could not delete message {msg_id}: {e}")
        
        # Очищаем список после удаления
        user_data['bot_message_ids'] = []
    except Exception as e:
        logger.debug(f"Error deleting recent messages: {e}")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на inline кнопки"""
    query = update.callback_query
    if not query:
        return
    
    data = query.data
    
    # Игнорируем платежные callback'и - они обрабатываются отдельным обработчиком
    if data and data.startswith("pay_"):
        return
    
    # Пытаемся ответить на callback query, но игнорируем ошибки если query слишком старый
    try:
        await query.answer()
    except Exception as e:
        # Игнорируем ошибки "Query is too old" - это нормально, если бот был перезапущен
        if "too old" not in str(e).lower() and "timeout" not in str(e).lower():
            logger.warning(f"Error answering callback query: {e}")
        # Продолжаем выполнение даже если не удалось ответить
    
    # Удаляем предыдущие сообщения бота только в специальных случаях
    # (например, при переходе в главное меню через кнопку рассылки или при очистке)
    # В остальных случаях редактируем текущее сообщение на месте
    if data == "clear_and_main_menu":
        user = update.effective_user
        chat_id = query.message.chat_id if query.message else user.id
        user_data = context.user_data
        await delete_recent_bot_messages(context, chat_id, user_data, max_messages=20)
    
    if data == "user_agreement":
        await show_user_agreement(update, context)
        return
    
    if data == "offer":
        await show_offer(update, context)
        return
    
    if data == "clear_and_main_menu":
        # Удаляем все сообщения и показываем главное меню (используется в рассылках)
        user = update.effective_user
        telegram_id = user.id
        chat_id = query.message.chat_id if query.message else telegram_id
        
        # Удаляем все сообщения бота
        user_data = context.user_data
        bot_message_ids = user_data.get('bot_message_ids', [])
        for msg_id in bot_message_ids:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
        user_data['bot_message_ids'] = []
        
        # Переходим к обработке main_menu
        data = "main_menu"
    
    if data == "main_menu":
        # Возвращаемся к главному меню с полной информацией
        user = update.effective_user
        telegram_id = user.id
        
        token = get_user_token(telegram_id)
        if token:
            token, user_data = get_user_data_safe(telegram_id, token)
            
            if user_data:
                # Получаем язык пользователя
                user_lang = get_user_lang(user_data, context, token)
                
                welcome_text = f"{get_emoji('HEADER')} **{get_text('stealthnet_bot', user_lang)}**\n"
                welcome_text += f"{get_text('main_menu_button', user_lang)}\n"
                welcome_text += f" {get_text('your_id', user_lang)}: {telegram_id}\n"
                welcome_text += "━━━━━━━━━━━━━━━\n"
                
                # Баланс
                balance = user_data.get("balance", 0)
                preferred_currency = user_data.get("preferred_currency", "uah")
                currency_symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(preferred_currency, "₴")
                welcome_text += f"{get_emoji('BALANCE')} **{get_text('balance', user_lang)}:** {balance:.2f} {currency_symbol}\n"
                
                # Статус подписки
                is_active = user_data.get("activeInternalSquads", [])
                expire_at = user_data.get("expireAt")
                subscription_url = user_data.get("subscriptionUrl", "")
                used_traffic = user_data.get("usedTrafficBytes", 0)
                traffic_limit = user_data.get("trafficLimitBytes", 0)
                
                # Проверяем, есть ли активная подписка (не истекшая)
                has_active_subscription = False
                expire_date = None
                days_left = 0
                
                if is_active and expire_at:
                    expire_date = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
                    now = datetime.now(expire_date.tzinfo)
                    delta = expire_date - now
                    seconds_left = delta.total_seconds()
                    days_left = int(math.ceil(seconds_left / (60 * 60 * 24))) if seconds_left > 0 else 0
                    
                    # Подписка активна только если не истекла
                    has_active_subscription = seconds_left > 0
                
                if has_active_subscription and expire_date:
                    # Статус с индикатором - в одну строку
                    status_icon = get_emoji("ACTIVE_GREEN") if days_left > 7 else get_emoji("ACTIVE_YELLOW") if days_left > 0 else get_emoji("INACTIVE")
                    welcome_text += f"{get_emoji('STATUS')} **{get_text('subscription_status_title', user_lang)}** - {status_icon} {get_text('active', user_lang)}\n"
                    
                    # Дата с "до"
                    ed = get_emoji("DATE")
                    if user_lang == 'ru':
                        welcome_text += f"{ed} до {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
                    elif user_lang == 'ua':
                        welcome_text += f"{ed} до {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
                    elif user_lang == 'en':
                        welcome_text += f"{ed} until {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
                    else:
                        welcome_text += f"{ed} {expire_date.strftime('%d.%m.%Y %H:%M')}\n"
                    
                    # Дни с правильным склонением (days_left уже > 0 здесь)
                    if user_lang == 'ru':
                        if days_left == 1:
                            days_text = f"{days_left} день"
                        elif 2 <= days_left <= 4:
                            days_text = f"{days_left} дня"
                        else:
                            days_text = f"{days_left} дней"
                        welcome_text += f"{get_emoji('TIME')} осталось {days_text}\n"
                    elif user_lang == 'ua':
                        if days_left == 1:
                            days_text = f"{days_left} день"
                        elif 2 <= days_left <= 4:
                            days_text = f"{days_left} дні"
                        else:
                            days_text = f"{days_left} днів"
                        welcome_text += f"{get_emoji('TIME')} залишилось {days_text}\n"
                    elif user_lang == 'en':
                        days_text = f"{days_left} day{'s' if days_left != 1 else ''}"
                        welcome_text += f"{get_emoji('TIME')} {days_text} left\n"
                    else:
                        days_text = get_days_text(days_left, user_lang)
                        welcome_text += f"{get_emoji('TIME')} {days_text}\n"
                    
                    # Устройства (доступное количество из тарифа)
                    hwid_limit = user_data.get("hwidDeviceLimit")
                    if hwid_limit is not None:
                        if hwid_limit == -1 or hwid_limit >= 100:
                            welcome_text += f"{get_emoji('DEVICES')} **Устройств:** {get_text('devices_unlimited', user_lang)}\n"
                        else:
                            welcome_text += f"{get_emoji('DEVICES')} **Устройств:** {hwid_limit} {get_text('devices_available', user_lang)}\n"
                    
                    # Трафик - в одну строку
                    if traffic_limit == 0:
                        welcome_text += f"{get_emoji('TRAFFIC')} **{get_text('traffic_title', user_lang)}**  - ♾️ {get_text('unlimited_traffic', user_lang)}\n"
                    else:
                        used_gb = used_traffic / (1024 ** 3)
                        limit_gb = traffic_limit / (1024 ** 3)
                        percentage = (used_traffic / traffic_limit * 100) if traffic_limit > 0 else 0
                        
                        filled = int(percentage / (100 / 15))
                        filled = min(filled, 15)
                        progress_bar = "█" * filled + "░" * (15 - filled)
                        progress_color = get_emoji("ACTIVE_GREEN") if percentage < 70 else get_emoji("ACTIVE_YELLOW") if percentage < 90 else get_emoji("INACTIVE")
                        
                        welcome_text += f"{get_emoji('TRAFFIC')} **{get_text('traffic_title', user_lang)}**  - {progress_color} {progress_bar} {percentage:.0f}% ({used_gb:.2f} / {limit_gb:.2f} GB)\n"
                    
                    # Ссылка подключения (в тексте — для копирования)
                    if subscription_url:
                        welcome_text += f"{get_emoji('LINK')} **{get_text('subscription_link', user_lang)}:**\n"
                        welcome_text += f"{_subscription_url_for_copy(subscription_url)}\n"
                    
                    welcome_text += "━━━━━━━━━━━━━━━\n"
                else:
                    welcome_text += f"{get_emoji('STATUS')} **{get_text('subscription_status_title', user_lang)}**\n"
                    welcome_text += f"{get_emoji('INACTIVE')} {get_text('inactive', user_lang)}\n"
                    welcome_text += "━━━━━━━━━━━━━━━\n"
                
                # Используем build_main_menu_keyboard для правильного порядка кнопок из админки
                # Используем has_active_subscription для правильного отображения кнопок
                trial_used = user_data.get('trial_used', False)  # Получаем информацию об использовании триала
                keyboard = build_main_menu_keyboard(user_lang, has_active_subscription, subscription_url, expire_at, trial_used)
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Используем безопасную функцию для редактирования/отправки
                temp_update = Update(update_id=0, callback_query=query)
                if has_cards(welcome_text):
                    welcome_text_clean = clean_markdown_for_cards(welcome_text)
                    await safe_edit_or_send_with_logo(temp_update, context, welcome_text_clean, reply_markup=reply_markup, logo_page="main_menu")
                else:
                    try:
                        await safe_edit_or_send_with_logo(temp_update, context, welcome_text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="main_menu")
                    except Exception as e:
                        logger.warning(f"Error in main_menu, sending without formatting: {e}")
                        welcome_text_clean = clean_markdown_for_cards(welcome_text)
                        await safe_edit_or_send_with_logo(temp_update, context, welcome_text_clean, reply_markup=reply_markup, logo_page="main_menu")
                return
        
        # Fallback если не удалось загрузить данные
        lang = get_user_lang(None, context, token) if token else 'ru'
        welcome_text = f"{get_text('main_menu_button', lang)}\n\n"
        welcome_text += f"{get_text('select_action', lang)}:"
        
        keyboard = [
            [InlineKeyboardButton(get_text('status_button', lang), callback_data="subscription_menu")],
            [
                InlineKeyboardButton(get_text('tariffs_button', lang), callback_data="tariffs"),
                InlineKeyboardButton(get_text('options_button', lang), callback_data="options"),
            ],
            [
                InlineKeyboardButton(get_text('referrals_button', lang), callback_data="referrals"),
                InlineKeyboardButton(get_text('support_button', lang), callback_data="support_menu"),
            ],
            [InlineKeyboardButton(get_text('settings_button', lang), callback_data="settings")],
        ]
        
        if MINIAPP_URL and MINIAPP_URL.startswith("https://"):
            keyboard.append([
                InlineKeyboardButton(get_text('cabinet_button', lang), web_app=WebAppInfo(url=MINIAPP_URL))
            ])
        reply_markup = InlineKeyboardMarkup(keyboard)
        # Используем безопасную функцию для редактирования/отправки
        temp_update = Update(update_id=0, callback_query=query)
        await safe_edit_or_send_with_logo(temp_update, context, welcome_text, reply_markup=reply_markup, logo_page="main_menu")
    
    elif data == "status":
        # Backward-compat: раньше это была кнопка "Статус подписки"
        await show_subscription_menu(update, context)

    elif data == "subscription_menu":
        await show_subscription_menu(update, context)
    
    elif data == "configs":
        await show_configs(update, context)
    
    elif data == "tariffs":
        await show_tariffs(update, context)

    elif data == "options":
        await show_options(update, context)

    elif data == "support_menu":
        await show_support_menu(update, context)

    elif data == "support_tickets":
        context.user_data["_back_to"] = "support_menu"
        await show_support(update, context)

    elif data == "support_agreement":
        context.user_data["_back_to"] = "support_menu"
        await show_user_agreement(update, context)

    elif data == "support_offer":
        context.user_data["_back_to"] = "support_menu"
        await show_offer(update, context)

    elif data == "sub_configs":
        context.user_data["_back_to"] = "subscription_menu"
        await show_configs(update, context)

    elif data == "sub_servers":
        context.user_data["_back_to"] = "subscription_menu"
        await show_servers(update, context)

    elif data == "sub_topup":
        context.user_data["_back_to"] = "subscription_menu"
        await show_topup_balance(update, context)

    elif data == "tariffs_newcfg":
        # Пользователь хочет оплатить создание нового конфига
        context.user_data["preferred_config_id"] = None
        context.user_data["preferred_create_new_config"] = True
        await show_tariffs(update, context)
    
    elif data.startswith("tariffs_cfg_"):
        try:
            cfg_id = int(data.replace("tariffs_cfg_", ""))
            context.user_data["preferred_config_id"] = cfg_id
            context.user_data["preferred_create_new_config"] = False
        except Exception:
            context.user_data["preferred_config_id"] = None
            context.user_data["preferred_create_new_config"] = False
        await show_tariffs(update, context)
    
    elif data.startswith("share_config_"):
        try:
            cfg_id = int(data.replace("share_config_", ""))
            await handle_share_config(update, context, cfg_id)
        except Exception as e:
            logger.error(f"Error handling share_config: {e}")
            await query.answer("❌ Ошибка при создании ссылки", show_alert=True)
    
    elif data.startswith("accept_config_"):
        try:
            share_token = data.replace("accept_config_", "")
            await handle_accept_shared_config(update, context, share_token)
        except Exception as e:
            logger.error(f"Error accepting config: {e}")
            await query.answer("❌ Ошибка при принятии подписки", show_alert=True)
    
    elif data.startswith("copy_share_token_"):
        share_token = data.replace("copy_share_token_", "")
        bot_username = os.getenv("CLIENT_BOT_USERNAME", "").replace("@", "")
        share_text = f"@{bot_username} {share_token}"
        await query.answer(f"✅ Токен скопирован: {share_text}", show_alert=False)
    
    elif data.startswith("tier_"):
        tier = data.replace("tier_", "")
        await show_tier_tariffs(update, context, tier)

    elif data.startswith("optcat_"):
        opt_type = data.replace("optcat_", "")
        await show_options_category(update, context, opt_type)

    elif data.startswith("optpay_"):
        # optpay_{optionId}_{provider}
        try:
            parts = data.split("_", 2)
            # parts: ["optpay", "{id}", "{provider}"]
            option_id = int(parts[1])
            provider = parts[2]
        except Exception:
            await update.callback_query.answer("❌ Неверные данные оплаты")
            return

        telegram_id = update.callback_query.from_user.id
        token = get_user_token(telegram_id)
        if not token:
            await update.callback_query.answer("❌ Ошибка авторизации")
            return

        # применяем к выбранной подписке (если пользователь ранее выбирал в меню подписок)
        cfg_id = None
        try:
            cfg_id = context.user_data.get("preferred_config_id")
        except Exception:
            cfg_id = None

        await update.callback_query.answer(get_text('creating_payment', get_user_lang(None, context, token)))
        result = api.create_option_payment(token, option_id, provider, config_id=cfg_id)
        user_data_api = api.get_user_data(token) or {}
        user_lang = get_user_lang(user_data_api, context, token)

        # Если оплата с баланса прошла успешно (payment_url == null и success == true)
        if result.get("success") or (result.get("payment_url") is None and provider == "balance"):
            text = "✅ **Опция успешно приобретена!**\n\n"
            text += f"💎 Опция активирована с баланса!\n"
            if result.get("balance") is not None:
                text += f"💰 Остаток баланса: {result.get('balance', 0):.2f}\n\n"
            text += f"🎉 Опция применена к вашей подписке!"
            keyboard = [
                [InlineKeyboardButton(get_text('back', user_lang), callback_data="options")],
                [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
            ]
            await safe_edit_or_send_with_logo(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown", logo_page="options")
        elif result.get("payment_url"):
            payment_url = result["payment_url"]
            text = "✅ Платеж создан.\n\n"
            text += f"Нажмите кнопку ниже, чтобы оплатить:"
            keyboard = [
                [InlineKeyboardButton(get_text('go_to_payment_button', user_lang), url=payment_url)],
                [InlineKeyboardButton(get_text('back', user_lang), callback_data="options")],
                [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
            ]
            await safe_edit_or_send_with_logo(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard), logo_page="options")
        else:
            msg = result.get("message") or result.get("error") or get_text('error_creating_payment', user_lang)
            keyboard = [
                [InlineKeyboardButton(get_text('back', user_lang), callback_data="options")],
                [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
            ]
            await safe_edit_or_send_with_logo(update, context, f"❌ {msg}", reply_markup=InlineKeyboardMarkup(keyboard), logo_page="options")

    elif data.startswith("opt_"):
        # opt_{optionId}
        try:
            option_id = int(data.replace("opt_", ""))
        except Exception:
            await update.callback_query.answer("❌ Неверная опция")
            return
        await show_option_payment_methods(update, context, option_id)
    
    elif data == "servers":
        await show_servers(update, context)
    
    elif data == "referrals":
        await show_referrals(update, context)
    
    elif data == "support":
        await show_support(update, context)
    
    elif data == "topup_balance":
        await show_topup_balance(update, context)
    
    elif data.startswith("topup_amount_"):
        try:
            amount = float(data.replace("topup_amount_", ""))
            await select_topup_method(update, context, amount)
        except (ValueError, IndexError):
            await query.answer("❌ Ошибка: неверная сумма")
    
    elif data == "topup_custom_amount":
        # Устанавливаем состояние ожидания ввода суммы
        user = update.effective_user
        telegram_id = user.id
        
        token = get_user_token(telegram_id)
        if not token:
            lang = get_user_lang(None, context, token)
            await query.answer(f"❌ {get_text('auth_error', lang)}")
            return
        
        token, user_data_api = get_user_data_safe(telegram_id, token)
        if not user_data_api:
            lang = get_user_lang(None, context, token)
            await query.answer(f"❌ {get_text('failed_to_load', lang)}")
            return
        
        user_lang = get_user_lang(user_data_api, context, token)
        preferred_currency = user_data_api.get("preferred_currency", "uah")
        currency_symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(preferred_currency, "₴")
        
        # Устанавливаем состояние в context.user_data
        context.user_data["waiting_for_topup_amount"] = True
        
        text = f"💰 **{get_text('top_up_balance', user_lang)}**\n"
        text += "━━━━━━━━━━━━━━━\n\n"
        text += f"📝 {get_text('enter_amount', user_lang)}\n\n"
        text += f"💡 Введите сумму в {currency_symbol} (например: 1500 или 1500.50)"
        
        keyboard = [
            [InlineKeyboardButton(get_text('back', user_lang), callback_data="topup_balance")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await safe_edit_or_send_with_logo(update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="topup")
    
    elif data.startswith("topup_pay_"):
        try:
            parts = data.replace("topup_pay_", "").split("_")
            amount = float(parts[0])
            provider = "_".join(parts[1:])
            await handle_topup_payment(update, context, amount, provider)
        except (ValueError, IndexError):
            await query.answer("❌ Ошибка: неверные данные")
    
    elif data == "activate_trial":
        await activate_trial(update, context)
    
    elif data.startswith("tariff_"):
        try:
            tariff_id = int(data.split("_")[1])
            await select_tariff(update, context, tariff_id)
        except (ValueError, IndexError):
            await query.answer("❌ Ошибка: неверный ID тарифа")
    
    elif data.startswith("copy_ref_"):
        referral_code = data.replace("copy_ref_", "")
        
        user = update.effective_user
        telegram_id = user.id
        token = get_user_token(telegram_id)
        token, user_data = get_user_data_safe(telegram_id, token) if token else (token, None)
        user_lang = get_user_lang(user_data, context, token)
        
        # Получаем домен сервера из API
        try:
            domain_resp = api.session.get(f"{FLASK_API_URL}/api/public/server-domain", timeout=5)
            if domain_resp.status_code == 200:
                domain_data = domain_resp.json()
                server_domain = domain_data.get("full_url") or domain_data.get("domain") or YOUR_SERVER_IP
            else:
                server_domain = YOUR_SERVER_IP
        except:
            server_domain = YOUR_SERVER_IP
        
        # Формируем ссылку
        if not server_domain.startswith("http"):
            server_domain = f"https://{server_domain}"
        referral_link = f"{server_domain}/register?ref={referral_code}"
        
        # Формируем ссылку через бота
        # Приоритет: TELEGRAM_BOT_NAME_V2 -> TELEGRAM_BOT_NAME -> BOT_USERNAME -> CLIENT_BOT_USERNAME
        # Если нет TELEGRAM_BOT_NAME_V2, используем TELEGRAM_BOT_NAME
        bot_username = os.getenv("TELEGRAM_BOT_NAME_V2") or os.getenv("TELEGRAM_BOT_NAME") or os.getenv("BOT_USERNAME") or os.getenv("CLIENT_BOT_USERNAME", "stealthnet_vpn_bot")
        if bot_username.startswith('@'):
            bot_username = bot_username[1:]
        referral_link_telegram = f"https://t.me/{bot_username}?start={referral_code}"
        
        # Отправляем ссылку отдельным сообщением для удобного копирования
        await query.answer(f"✅ {get_text('link_sent_to_chat', user_lang)}", show_alert=False)
        # Создаем Update объект для reply_with_logo
        temp_update = Update(update_id=0, message=query.message)
        await reply_with_logo(
            temp_update,
            f"🔗 **{get_text('your_referral_link', user_lang)}**\n\n"
            f"`{referral_link}`\n\n"
            f"🤖 **Ссылка через бота:**\n"
            f"`{referral_link_telegram}`\n\n"
            f"{get_text('click_link_to_copy', user_lang)}.",
            parse_mode="Markdown",
            context=context,
            logo_page="referrals"
        )
    
    elif data == "create_ticket":
        user = update.effective_user
        telegram_id = user.id
        token = get_user_token(telegram_id)
        token, user_data = get_user_data_safe(telegram_id, token) if token else (token, None)
        user_lang = get_user_lang(user_data, context, token)
        
        temp_update = Update(update_id=0, callback_query=query)
        await safe_edit_or_send_with_logo(
            temp_update,
            context,
            f"💬 **{get_text('creating_ticket', user_lang)}**\n\n"
            f"{get_text('send_ticket_subject', user_lang)}:",
            parse_mode="Markdown",
            logo_page="support_menu"
        )
        context.user_data["waiting_for_ticket_subject"] = True
    
    elif data.startswith("view_ticket_"):
        try:
            ticket_id = int(data.replace("view_ticket_", ""))
            await view_ticket(update, context, ticket_id)
        except (ValueError, IndexError):
            await query.answer("❌ Ошибка: неверный ID тикета")
    
    elif data.startswith("reply_ticket_"):
        try:
            ticket_id = int(data.replace("reply_ticket_", ""))
            user = update.effective_user
            telegram_id = user.id
            token = get_user_token(telegram_id)
            token, user_data = get_user_data_safe(telegram_id, token) if token else (token, None)
            user_lang = get_user_lang(user_data, context, token)
            
            temp_update = Update(update_id=0, callback_query=query)
            await safe_edit_or_send_with_logo(
                temp_update,
                context,
                f"💬 **{get_text('reply_to_ticket', user_lang)}**\n\n"
                f"{get_text('ticket', user_lang)} #{ticket_id}\n\n"
                f"{get_text('send_your_reply', user_lang)}:",
                parse_mode="Markdown",
                logo_page="support_menu"
            )
            context.user_data["waiting_for_ticket_reply"] = True
            context.user_data["reply_ticket_id"] = ticket_id
        except (ValueError, IndexError):
            user = update.effective_user
            telegram_id = user.id
            token = get_user_token(telegram_id)
            token, user_data = get_user_data_safe(telegram_id, token) if token else (token, None)
            user_lang = get_user_lang(user_data, context, token)
            await query.answer(f"❌ {get_text('invalid_ticket_id', user_lang)}")
    
    elif data == "register_user":
        await register_user(update, context)
    
    elif data == "check_subscription":
        # Проверяем подписку на канал и переходим к регистрации
        user = update.effective_user
        is_subscribed = await check_channel_subscription(user.id, context)
        if is_subscribed:
            await query.answer("✅ Подписка подтверждена!")
            await register_user(update, context)
        else:
            await query.answer("❌ Вы не подписаны на канал. Подпишитесь и попробуйте снова.", show_alert=True)
    
    elif data.startswith("reg_lang_"):
        # Регистрация теперь автоматическая (с системными настройками)
        await register_user(update, context)
    
    elif data.startswith("reg_currency_"):
        # Регистрация теперь автоматическая (с системными настройками)
        await register_user(update, context)
    
    elif data == "settings":
        await show_settings(update, context)
    
    elif data.startswith("set_currency_"):
        currency = data.replace("set_currency_", "")
        await set_currency(update, context, currency)
    
    elif data.startswith("set_lang_"):
        lang = data.replace("set_lang_", "")
        await set_language(update, context, lang)
    
    elif data == "select_language":
        await set_language(update, context)


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать настройки (валюта и язык)"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        await query.answer("❌ Ошибка авторизации")
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    if not user_data:
        await query.answer("❌ Не удалось загрузить данные")
        return
    
    # Получаем язык и валюту с правильными ключами
    user_lang = get_user_lang(user_data, context, token)
    current_currency = user_data.get("preferred_currency") or user_data.get("preferredCurrency") or "uah"
    
    logger.debug(f"Settings: lang={user_lang}, currency={current_currency}")
    
    text = f"⚙️ {get_text('settings', user_lang)}\n"
    text += "━━━━━━━━━━━━━━━\n"
    
    # Текущие настройки в современном стиле
    currency_names = {"uah": "₴ UAH", "rub": "₽ RUB", "usd": "$ USD"}
    currency_display = currency_names.get(current_currency, 'UAH')
    
    lang_names = {"ru": "🇷🇺 Русский", "ua": "🇺🇦 Українська", "en": "🇬🇧 English", "cn": "🇨🇳 中文"}
    lang_display = lang_names.get(user_lang, 'Русский')
    
    text += f"💱 {get_text('currency', user_lang)} - {currency_display}\n"
    text += f"🌐 {get_text('language', user_lang)} - {lang_display}\n"
    text += "━━━━━━━━━━━━━━━\n"
    text += f"📝 {get_text('select_currency', user_lang)}\n"
    
    # Получаем активные валюты из настроек
    system_settings = api.get_system_settings()
    active_currencies = system_settings.get("active_currencies", ["uah", "rub", "usd"])
    
    # Генерируем кнопки валют динамически
    currency_buttons = []
    currency_names = {"uah": "₴ UAH", "rub": "₽ RUB", "usd": "$ USD"}
    
    row = []
    for curr in ["uah", "rub", "usd"]:
        if curr in active_currencies:
            button_text = currency_names.get(curr, curr.upper()) + (" ✓" if current_currency == curr else "")
            row.append(InlineKeyboardButton(button_text, callback_data=f"set_currency_{curr}"))
            if len(row) == 2:  # По 2 кнопки в ряду
                currency_buttons.append(row)
                row = []
    
    if row:  # Добавляем оставшиеся кнопки
        currency_buttons.append(row)
    
    keyboard = currency_buttons + [
        [
            InlineKeyboardButton(get_text('language', user_lang), callback_data="select_language")
        ],
        [
            InlineKeyboardButton(get_text('back', user_lang), callback_data="main_menu")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования/отправки
    temp_update = Update(update_id=0, callback_query=query)
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup, logo_page="settings")
    else:
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="settings")
        except Exception as e:
            logger.warning(f"Error in show_settings, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup, logo_page="settings")


async def set_currency(update: Update, context: ContextTypes.DEFAULT_TYPE, currency: str):
    """Установить валюту"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        await query.answer("❌ Ошибка авторизации")
        return
    
    # Проверяем, что валюта активна
    system_settings = api.get_system_settings()
    active_currencies = system_settings.get("active_currencies", ["uah", "rub", "usd"])
    
    if currency not in active_currencies:
        await query.answer("❌ Эта валюта недоступна", show_alert=True)
        return
    
    # Проверяем текущую валюту
    token, user_data = get_user_data_safe(telegram_id, token)
    current_currency = user_data.get("preferred_currency", "uah") if user_data else "uah"
    
    if current_currency == currency:
        await query.answer("ℹ️ Эта валюта уже выбрана", show_alert=False)
        return
    
    # Сохраняем валюту
    result = api.save_settings(token, currency=currency)
    
    logger.info(f"Currency save result: {result}")
    
    if result.get("success"):
        await query.answer("✅ Валюта изменена", show_alert=False)
        # Возвращаемся к настройкам (данные обновятся автоматически из БД)
        try:
            await show_settings(update, context)
        except Exception as e:
            # Если ошибка "Message is not modified", просто игнорируем
            if "not modified" not in str(e).lower():
                logger.error(f"Error updating settings: {e}")
                await query.answer("✅ Валюта изменена", show_alert=False)
    else:
        error_msg = result.get("message", "Ошибка сохранения валюты")
        logger.error(f"Failed to save currency: {error_msg}")
        await query.answer(f"❌ {error_msg}", show_alert=True)


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str = None):
    """Показать меню выбора языка или установить язык"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        await query.answer("❌ Ошибка авторизации")
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    if not user_data:
        await query.answer("❌ Не удалось загрузить данные")
        return
    
    current_lang = get_user_lang(user_data, context, token)
    
    # Если язык не указан, показываем меню выбора
    if not lang:
        text = f"🌐 **{get_text('select_language', current_lang)}**\n\n"
        
        # Получаем активные языки из настроек
        system_settings = api.get_system_settings()
        active_languages = system_settings.get("active_languages", ["ru", "ua", "en", "cn"])
        
        # Генерируем кнопки языков динамически
        lang_buttons = []
        lang_names = {
            "ru": "🇷🇺 Русский",
            "ua": "🇺🇦 Українська",
            "en": "🇬🇧 English",
            "cn": "🇨🇳 中文"
        }
        
        row = []
        for lang_code in ["ru", "ua", "en", "cn"]:
            if lang_code in active_languages:
                button_text = lang_names.get(lang_code, lang_code) + (" ✓" if current_lang == lang_code else "")
                row.append(InlineKeyboardButton(button_text, callback_data=f"set_lang_{lang_code}"))
                if len(row) == 2:  # По 2 кнопки в ряду
                    lang_buttons.append(row)
                    row = []
        
        if row:  # Добавляем оставшиеся кнопки
            lang_buttons.append(row)
        
        keyboard = lang_buttons + [
            [
                InlineKeyboardButton(get_text('back', current_lang), callback_data="settings")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        temp_update = Update(update_id=0, callback_query=query)
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error in set_language: {e}")
        return
    
    # Проверяем, что язык активен
    system_settings = api.get_system_settings()
    active_languages = system_settings.get("active_languages", ["ru", "ua", "en", "cn"])
    
    if lang not in active_languages:
        await query.answer("❌ Этот язык недоступен", show_alert=True)
        return
    
    # Проверяем текущий язык
    if current_lang == lang:
        await query.answer("ℹ️ Этот язык уже выбран", show_alert=False)
        return
    
    # Сохраняем язык
    result = api.save_settings(token, lang=lang)
    
    logger.info(f"Language save result: {result}")
    
    if result.get("success"):
        await query.answer("✅ Язык изменен", show_alert=False)
        # Обновляем язык в context.user_data для немедленного применения
        context.user_data['user_lang'] = lang
        # Очищаем кэш user_data, чтобы при следующем запросе получить свежие данные
        if 'user_data' in context.user_data:
            del context.user_data['user_data']
        # Возвращаемся к настройкам (данные обновятся автоматически из БД)
        try:
            await show_settings(update, context)
        except Exception as e:
            # Если ошибка "Message is not modified", просто игнорируем
            if "not modified" not in str(e).lower():
                logger.error(f"Error updating settings: {e}")
                await query.answer("✅ Язык изменен", show_alert=False)
    else:
        error_msg = result.get("message", "Ошибка сохранения языка")
        logger.error(f"Failed to save language: {error_msg}")
        await query.answer(f"❌ {error_msg}", show_alert=True)


async def view_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int):
    """Просмотр тикета с сообщениями"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}")
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    
    await query.answer(f"⏳ {get_text('loading_ticket', user_lang)}...")
    
    ticket_data = api.get_ticket_messages(token, ticket_id)
    
    if not ticket_data or not ticket_data.get("messages"):
        temp_update = Update(update_id=0, callback_query=query)
        await safe_edit_or_send_with_logo(
            temp_update,
            context,
            f"❌ **{get_text('error_loading', user_lang)}**\n\n"
            f"{get_text('ticket_not_found', user_lang)} #{ticket_id}.\n"
            f"{get_text('ticket_not_exists', user_lang)}",
            parse_mode="Markdown"
        )
        return
    
    subject = ticket_data.get("subject", get_text('no_subject', user_lang))
    status = ticket_data.get("status", "OPEN")
    status_emoji = "✅" if status == "CLOSED" else "🔄"
    messages = ticket_data.get("messages", [])
    
    text = f"💬 **{get_text('ticket_view_title', user_lang)} #{ticket_id}**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    text += f"{status_emoji} **{get_text('status_label', user_lang)}:** {status}\n"
    text += f"📋 **{get_text('subject_label', user_lang)}:** {subject}\n\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    text += f"💬 **{get_text('messages_label', user_lang)}:**\n\n"
    
    # Показываем сообщения
    for msg in messages:
        sender_email = msg.get("sender_email", get_text('unknown', user_lang))
        sender_role = msg.get("sender_role", "USER")
        message_text = msg.get("message", "")
        created_at = msg.get("created_at", "")
        
        # Определяем, кто отправил
        if sender_role == "ADMIN":
            sender_label = f"👨‍💼 {get_text('support_label', user_lang)} ({sender_email})"
        else:
            sender_label = f"👤 {get_text('you', user_lang)}"
        
        # Форматируем дату
        try:
            if created_at:
                msg_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                date_str = msg_date.strftime('%d.%m.%Y %H:%M')
            else:
                date_str = get_text('unknown', user_lang)
        except:
            date_str = created_at
        
        text += f"**{sender_label}**\n"
        text += f"📅 {date_str}\n"
        text += f"{message_text}\n\n"
        text += "—\n\n" # Разделитель сообщений
    
    keyboard = [
        [InlineKeyboardButton(get_text('reply_button', user_lang), callback_data=f"reply_ticket_{ticket_id}")],
        [InlineKeyboardButton(get_text('back_to_support', user_lang), callback_data="support")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования/отправки
    temp_update = Update(update_id=0, callback_query=query)
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup, logo_page="support_menu")
    else:
        # Для текста без карточек используем Markdown
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="support_menu")
        except Exception as e:
            logger.warning(f"Error in view_ticket, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup, logo_page="support_menu")


async def show_channel_subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать сообщение о необходимости подписки на канал"""
    query = update.callback_query
    
    lang = 'ru'
    channel_url = get_channel_url()
    subscription_text = get_channel_subscription_text(lang)
    service_name = get_service_name()
    
    text = f"🛡️ **{service_name} VPN**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    text += f"📢 {subscription_text}\n\n"
    text += "👇 Нажмите кнопку ниже, чтобы подписаться, затем вернитесь и нажмите \"Проверить подписку\""
    
    keyboard = []
    if channel_url:
        keyboard.append([InlineKeyboardButton("📢 Подписаться на канал", url=channel_url)])
    keyboard.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_subscription")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Поддерживаем как callback_query, так и обычную команду /start (message)
    if query:
        temp_update = Update(update_id=0, callback_query=query)
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="trial")
        except Exception as e:
            logger.warning(f"Error in show_channel_subscription_required: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup, logo_page="trial")
    else:
        try:
            await reply_with_logo(update, text, reply_markup=reply_markup, parse_mode="Markdown", context=context, logo_page="trial")
        except Exception as e:
            logger.warning(f"Error in show_channel_subscription_required (message): {e}")
            await reply_with_logo(update, clean_markdown_for_cards(text), reply_markup=reply_markup, context=context, logo_page="trial")


async def register_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Регистрация (теперь автоматическая)"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    telegram_username = user.username or ""
    
    # Проверяем, не зарегистрирован ли уже
    token = get_user_token(telegram_id)
    if token:
        lang = get_user_lang(None, context, token) if token else 'ru'
        await query.answer(f"✅ {get_text('already_registered', lang)}", show_alert=True)
        await start(update, context)
        return
    
    # Проверяем подписку на канал если требуется
    if is_channel_subscription_required():
        logger.info(f"Channel subscription required, checking for user {telegram_id}")
        is_subscribed = await check_channel_subscription(telegram_id, context)
        if not is_subscribed:
            logger.info(f"User {telegram_id} is not subscribed, showing subscription requirement")
            await show_channel_subscription_required(update, context)
            return
        else:
            logger.info(f"User {telegram_id} is subscribed, proceeding with registration")

    # Авто-регистрация с системными настройками (язык/валюта)
    ref_code = context.user_data.get("ref_code")
    default_lang, default_currency = get_system_defaults()
    await query.answer("⏳", show_alert=False)
    result = api.register_user(
        telegram_id,
        telegram_username,
        ref_code=ref_code,
        preferred_lang=default_lang,
        preferred_currency=default_currency
    )
    if not (isinstance(result, dict) and (result.get("token") or result.get("message"))):
        await query.answer("❌ Ошибка", show_alert=True)
        return

    clear_user_token_cache(telegram_id)
    await start(update, context)


async def register_select_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str):
    """Выбор языка при регистрации - переход к выбору валюты"""
    query = update.callback_query
    if not query:
        return
    
    # Проверяем, что язык активен
    system_settings = api.get_system_settings()
    active_languages = system_settings.get("active_languages", ["ru", "ua", "en", "cn"])
    
    if lang not in active_languages:
        await query.answer("❌ Этот язык недоступен", show_alert=True)
        return
    
    # Сохраняем выбранный язык
    context.user_data["reg_lang"] = lang
    
    lang_names = {"ru": "Русский", "ua": "Українська", "en": "English", "cn": "中文"}
    lang_name = lang_names.get(lang, "Русский")
    
    await query.answer(f"✅ Язык: {lang_name}")
    
    text = f"🛡️ **{SERVICE_NAME} VPN**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    
    text += f"✅ **Язык выбран:** {lang_name}\n\n"
    text += "💱 **Выберите валюту**\n"
    text += "Для отображения цен в тарифах.\n\n"
    text += "💡 Вы сможете изменить её позже в настройках."
    
    # Получаем активные валюты из настроек
    system_settings = api.get_system_settings()
    active_currencies = system_settings.get("active_currencies", ["uah", "rub", "usd"])
    
    # Генерируем кнопки валют динамически на основе активных валют
    currency_names = {"uah": "₴ UAH", "rub": "₽ RUB", "usd": "$ USD"}
    
    keyboard = []
    row = []
    for curr in ["uah", "rub", "usd"]:
        if curr in active_currencies:
            row.append(InlineKeyboardButton(
                currency_names.get(curr, curr.upper()),
                callback_data=f"reg_currency_{curr}"
            ))
            if len(row) == 2:  # По 2 кнопки в ряду
                keyboard.append(row)
                row = []
    
    if row:  # Добавляем оставшиеся кнопки
        keyboard.append(row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования/отправки
    temp_update = Update(update_id=0, callback_query=query)
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup)
    else:
        # Для текста без карточек используем Markdown
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Error in register_select_language, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup)


async def register_select_currency(update: Update, context: ContextTypes.DEFAULT_TYPE, currency: str):
    """Выбор валюты при регистрации - завершение регистрации"""
    query = update.callback_query
    if not query:
        return
    
    # Проверяем, что валюта активна
    system_settings = api.get_system_settings()
    active_currencies = system_settings.get("active_currencies", ["uah", "rub", "usd"])
    
    if currency not in active_currencies:
        await query.answer("❌ Эта валюта недоступна", show_alert=True)
        return
    
    user = update.effective_user
    telegram_id = user.id
    telegram_username = user.username or ""
    
    # Получаем сохраненный язык
    lang = context.user_data.get("reg_lang", "ru")
    
    # Сохраняем выбранную валюту
    context.user_data["reg_currency"] = currency
    
    currency_names = {"uah": "₴ UAH", "rub": "₽ RUB", "usd": "$ USD"}
    currency_name = currency_names.get(currency, "₴ UAH")
    
    await query.answer("⏳ Регистрируем...")
    
    # Показываем выбранные настройки
    lang_names = {"ru": "Русский", "ua": "Українська", "en": "English", "cn": "中文"}
    lang_name = lang_names.get(lang, "Русский")
    
    text = f"🛡️ **{SERVICE_NAME} VPN**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    
    text += "✅ **Настройки**\n"
    text += f"🌐 {lang_name}\n"
    text += f"💱 {currency_name}\n\n"
    text += "⏳ Создаем ваш аккаунт..."
    
    # Используем безопасную функцию для редактирования/отправки
    temp_update = Update(update_id=0, callback_query=query)
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(temp_update, context, text_clean)
    else:
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Error in register_select_currency (loading), sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean)
    
    # Проверяем, есть ли реферальный код в контексте
    ref_code = context.user_data.get("ref_code")
    
    # Регистрируем пользователя с выбранными языком и валютой
    result = api.register_user(telegram_id, telegram_username, ref_code, preferred_lang=lang, preferred_currency=currency)
    
    if not result:
        text = "❌ **Ошибка регистрации**\n\n"
        text += "Не удалось зарегистрироваться. Попробуйте позже или зарегистрируйтесь на сайте:\n"
        text += f"{YOUR_SERVER_IP}/register"
        
        keyboard = [[InlineKeyboardButton(get_text('try_again_button', lang), callback_data="register_user")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        temp_update = Update(update_id=0, callback_query=query)
        await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
        return
    
    if result.get("message") == "User already registered":
        await query.answer("✅ Вы уже зарегистрированы!", show_alert=True)
        token = get_user_token(telegram_id)
        if token:
            await show_status(update, context)
        return
    
    # Регистрация успешна
    email = result.get("email", "")
    password = result.get("password", "")
    
    # Сохраняем язык в context для немедленного применения
    context.user_data['user_lang'] = lang
    
    # Формируем красивое сообщение об успешной регистрации
    text = "✨ **Регистрация завершена!**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    
    text += "✅ **Аккаунт создан!**\n"
    text += "Ваш аккаунт успешно создан и готов к использованию!\n\n"
    
    if email and password:
        text += "🔐 **Данные для входа**\n"
        text += f"📧 `{email}`\n"
        text += f"🔑 `{password}`\n\n"
        
        text += "⚠️ **Важно!**\n"
        text += "Сохраните эти данные! Пароль больше не будет показан.\n\n"
        
        text += "🌐 Войти на сайте:\n"
        text += f"{YOUR_SERVER_IP}\n\n"
    
    text += "🎉 Теперь вы можете использовать все функции бота!"
    
    keyboard = [
        [InlineKeyboardButton(get_text('status_button', lang), callback_data="subscription_menu")],
        [InlineKeyboardButton(get_text('tariffs_button', lang), callback_data="tariffs")],
        [InlineKeyboardButton(get_text('main_menu_button', lang), callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования/отправки
    temp_update = Update(update_id=0, callback_query=query)
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup)
    else:
        # Для текста без карточек используем Markdown
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Error in register_select_currency (success), sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup)
    
    # Сохраняем токен в кэш (если он есть)
    if result.get("token"):
        tok = result["token"]
        if isinstance(tok, str):
            user_tokens[telegram_id] = {"token": tok, "exp": _get_jwt_exp(tok)}
        else:
            user_tokens[telegram_id] = tok
    
    # Очищаем временные данные регистрации
    context.user_data.pop("reg_lang", None)
    context.user_data.pop("reg_currency", None)


async def activate_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Активировать триал"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}", show_alert=True)
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    
    await query.answer(f"⏳ {get_text('activating_trial', user_lang)}...")
    
    result = api.activate_trial(token)
    
    keyboard = [[InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Получаем настройки триала для сообщения об успехе
    trial_settings = get_trial_settings()
    
    # Проверяем результат активации
    if result and "message" in result:
        message_text = result.get("message", "").lower()
        # Проверяем на успех: "trial activated", "активирован", "успешно" и т.д.
        if ("trial" in message_text and "activated" in message_text) or \
           "активирован" in message_text or \
           "успешно" in message_text or \
           result.get("success", False):
            # Используем сообщение из настроек триала
            activation_message_key = f'activation_message_{user_lang}'
            activation_message = trial_settings.get(activation_message_key, '')
            if not activation_message:
                activation_message = trial_settings.get('activation_message_ru', '')
            if not activation_message:
                # Дефолтное сообщение
                activation_message = f"✅ Триал активирован! Вам добавлено {trial_settings.get('days', 3)} дней премиум-доступа."
            
            # Форматируем сообщение
            text = f"**{activation_message}**\n"
            text += "━━━━━━━━━━━━━━━\n\n"
            text += f"{get_text('enjoy_vpn', user_lang)}"
            
            temp_update = Update(update_id=0, callback_query=query)
            try:
                await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Error in activate_trial (success), sending without formatting: {e}")
                text_clean = clean_markdown_for_cards(text)
                await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup)
        else:
            # Если сообщение есть, но не об успехе - показываем его
            message = result.get("message", get_text('error_activating_trial', user_lang))
            temp_update = Update(update_id=0, callback_query=query)
            await safe_edit_or_send_with_logo(
                temp_update,
                context,
                f"❌ **{get_text('error', user_lang)}**\n\n{message}",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
    elif result and result.get("success", False):
        # Если есть поле success = True - используем сообщение из API
        message = result.get("message", "")
        if message:
            text = f"**{message}**\n"
        else:
            # Используем сообщение из настроек триала
            activation_message_key = f'activation_message_{user_lang}'
            activation_message = trial_settings.get(activation_message_key, '')
            if not activation_message:
                activation_message = trial_settings.get('activation_message_ru', '')
            if not activation_message:
                activation_message = f"✅ Триал активирован! Вам добавлено {trial_settings.get('days', 3)} дней премиум-доступа."
            text = f"**{activation_message}**\n"
        
        text += "━━━━━━━━━━━━━━━\n\n"
        text += f"{get_text('enjoy_vpn', user_lang)}"
        
        temp_update = Update(update_id=0, callback_query=query)
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Error in activate_trial (success 2), sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup)
    else:
        # Если result пустой или нет нужных полей
        error_message = result.get("message", get_text('failed_activate_trial', user_lang)) if result else get_text('failed_activate_trial', user_lang)
        temp_update = Update(update_id=0, callback_query=query)
        await safe_edit_or_send_with_logo(
            temp_update,
            context,
            f"❌ **{get_text('error', user_lang)}**\n\n{error_message}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )


async def select_tariff(update: Update, context: ContextTypes.DEFAULT_TYPE, tariff_id: Optional[int] = None):
    """Выбрать тариф и способ оплаты"""
    query = update.callback_query
    if not query:
        return
    
    if not tariff_id:
        # Получаем из callback_data
        if query.data:
            try:
                tariff_id = int(query.data.split("_")[1])
            except (ValueError, IndexError):
                await query.answer("❌ Ошибка: неверный ID тарифа", show_alert=True)
                return
        else:
            return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        await query.answer("❌ Ошибка авторизации", show_alert=True)
        return
    
    # Получаем информацию о тарифе
    tariffs = api.get_tariffs()
    tariff = next((t for t in tariffs if t.get("id") == tariff_id), None)
    
    if not tariff:
        await query.answer("❌ Тариф не найден", show_alert=True)
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    currency = user_data.get("preferred_currency", "uah") if user_data else "uah"
    user_lang = get_user_lang(user_data, context, token)
    
    currency_map = {
        "uah": {"field": "price_uah", "symbol": "₴"},
        "rub": {"field": "price_rub", "symbol": "₽"},
        "usd": {"field": "price_usd", "symbol": "$"}
    }
    currency_config = currency_map.get(currency, currency_map["uah"])
    price = tariff.get(currency_config["field"], 0)
    
    # Получаем баланс пользователя
    balance = user_data.get("balance", 0) if user_data else 0
    preferred_currency = user_data.get("preferred_currency", currency) if user_data else currency
    balance_currency_config = currency_map.get(preferred_currency, currency_map["uah"])
    balance_symbol = balance_currency_config["symbol"]
    
    # Определяем tier тарифа
    tariff_tier = tariff.get("tier")
    if not tariff_tier:
        duration = tariff.get("duration_days", 0)
        if duration >= 180:
            tariff_tier = "elite"
        elif duration >= 90:
            tariff_tier = "pro"
        else:
            tariff_tier = "basic"
    
    # Получаем функции тарифа
    tariff_features = api.get_tariff_features()
    features_list = tariff_features.get(tariff_tier, [])
    
    # Получаем названия функций из брендинга
    branding = api.get_branding()
    features_names = branding.get("tariff_features_names", {})
    
    text = f"💎 **{get_text('tariff_selected', user_lang)}:** {tariff.get('name', get_text('unknown', user_lang))}\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    text += f"💰 **{get_text('price_label', user_lang)}:** {price:.0f} {currency_config['symbol']}\n"
    text += f"📅 **{get_text('duration_label', user_lang)}:** {tariff.get('duration_days', 0)} {get_text('days', user_lang)}\n"
    
    # Добавляем информацию о трафике, если есть
    traffic_limit_gb = tariff.get("traffic_limit_gb")
    if traffic_limit_gb:
        if traffic_limit_gb == -1 or traffic_limit_gb >= 10000:
            text += f"📊 **Трафик:** Безлимитный\n"
        else:
            text += f"📊 **Трафик:** {traffic_limit_gb:.0f} GB\n"
    
    # Добавляем информацию об устройствах, если есть
    hwid_limit = tariff.get("hwid_device_limit")
    if hwid_limit:
        if hwid_limit == -1 or hwid_limit >= 100:
            text += f"📱 **Устройства:** Безлимит\n"
        else:
            text += f"📱 **Устройства:** {hwid_limit} шт.\n"
    
    # Добавляем функции тарифа
    if features_list:
        text += "\n✨ **Функции тарифа:**\n"
        for feature in features_list:
            if isinstance(feature, dict):
                feature_key = feature.get("key") or feature.get("name")
                feature_name = feature.get("name") or feature.get("title")
                # Пробуем получить название из брендинга
                if feature_key and features_names and isinstance(features_names, dict):
                    branded_name = features_names.get(feature_key)
                    if branded_name:
                        feature_name = branded_name
                if not feature_name:
                    feature_name = feature_key or "Функция"
                
                # Добавляем иконку и описание
                icon = feature.get("icon", "✓")
                description = feature.get("description") or feature.get("value")
                
                if description:
                    text += f"{icon} **{feature_name}** - {description}\n"
                else:
                    text += f"{icon} {feature_name}\n"
            elif isinstance(feature, str):
                # Если функция - просто строка
                text += f"✓ {feature}\n"
    
    text += f"\n💳 **Баланс:** {balance:.2f} {balance_symbol}\n\n"
    text += f"**{get_text('payment_methods', user_lang)}**:"
    
    # Получаем доступные способы оплаты из API
    available_methods = api.get_available_payment_methods()
    
    # Маппинг названий способов оплаты
    payment_names = {
        'crystalpay': '💳 CrystalPay',
        'heleket': '₿ Heleket',
        'yookassa': '💳 YooKassa',
        'platega': '💳 Platega',
        'platega_mir': '💳 Карты МИР',
        'mulenpay': '💳 Mulenpay',
        'urlpay': '💳 UrlPay',
        'telegram_stars': '⭐ Telegram Stars',
        'monobank': '💳 Monobank',
        'btcpayserver': '₿ BTCPayServer',
        'tribute': '💳 Tribute',
        'robokassa': '💳 Robokassa',
        'freekassa': '💳 Freekassa',
        'kassa_ai': '💳 Kassa AI'
    }
    
    keyboard = []
    row = []
    
    # Добавляем все способы оплаты, возвращённые API (кроме balance — он ниже)
    for method in available_methods:
        if method == "balance":
            continue
        label = payment_names.get(method, f"💳 {method}")
        row.append(InlineKeyboardButton(
            label,
            callback_data=f"pay_{tariff_id}_{method}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    # Добавляем кнопку оплаты с баланса, если баланс достаточен
    can_afford = balance >= price
    if can_afford:
        keyboard.append([
            InlineKeyboardButton(
                f"💰 {get_text('pay_with_balance', user_lang)} ({price:.0f} {currency_config['symbol']})",
                callback_data=f"pay_{tariff_id}_balance"
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                f"💰 {get_text('pay_with_balance', user_lang)} ({get_text('insufficient_balance', user_lang)})",
                callback_data=f"pay_{tariff_id}_balance"
            )
        ])
    
    # Если нет доступных способов оплаты
    if not keyboard or (len(keyboard) == 1 and not can_afford):
        text += f"\n\n❌ {get_text('no_payment_methods', user_lang)}"
    
    keyboard.append([
        InlineKeyboardButton(get_text('back_to_tariffs', user_lang), callback_data="tariffs")
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Используем безопасную функцию для редактирования/отправки
    if has_cards(text):
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup)
    else:
        # Для текста без карточек используем Markdown
        try:
            await safe_edit_or_send_with_logo(update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Error in show_tariffs, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(update, context, text_clean, reply_markup=reply_markup)


async def handle_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tariff_id: int,
    provider: str,
    config_id: Optional[int] = None,
    create_new_config: bool = False
):
    """Обработать создание платежа (с учетом выбора конфига)"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}")
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    
    # Если оплата с баланса, используем специальный endpoint
    if provider == 'balance':
        await query.answer(f"⏳ Обработка оплаты с баланса...")
        
        try:
            payload = {"tariff_id": tariff_id}
            if config_id:
                payload["config_id"] = int(config_id)
            if create_new_config:
                payload["create_new_config"] = True

            response = api.session.post(
                f"{FLASK_API_URL}/api/client/purchase-with-balance",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=30
            )
            result = response.json()
            
            if response.status_code == 200:
                text = f"✅ **Тариф активирован!**\n"
                text += "━━━━━━━━━━━━━━━\n\n"
                text += f"💎 Тариф успешно активирован с баланса!\n"
                text += f"💰 Остаток баланса: {result.get('balance', 0):.2f}\n\n"
                text += f"🎉 Подписка продлена!"
                
                keyboard = [
                    [InlineKeyboardButton(get_text('status_button', user_lang), callback_data="subscription_menu")],
                    [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                temp_update = Update(update_id=0, callback_query=query)
                await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
                return
            else:
                message = result.get("message", "Ошибка покупки тарифа")
                keyboard = [[InlineKeyboardButton(get_text('back_to_tariffs', user_lang), callback_data="tariffs")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                temp_update = Update(update_id=0, callback_query=query)
                await safe_edit_or_send_with_logo(
                    temp_update,
                    context,
                    f"❌ **Ошибка**\n\n{message}",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return
        except Exception as e:
            logger.error(f"Error in balance payment: {e}")
            keyboard = [[InlineKeyboardButton(get_text('back_to_tariffs', user_lang), callback_data="tariffs")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            temp_update = Update(update_id=0, callback_query=query)
            await safe_edit_or_send_with_logo(
                temp_update,
                context,
                f"❌ **Ошибка**\n\nОшибка при обработке платежа: {str(e)}",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return
    
    await query.answer(f"⏳ {get_text('creating_payment', user_lang)}...")
    
    result = api.create_payment(
        token,
        tariff_id,
        provider,
        config_id=config_id,
        create_new_config=create_new_config
    )
    
    if result.get("payment_url"):
        payment_url = result["payment_url"]
        
        # Показываем краткое сообщение с кнопкой оплаты
        # Сообщение будет автоматически заменено после успешной оплаты
        text = f"💳 {get_text('creating_payment', user_lang)}..."
        
        keyboard = [
            [InlineKeyboardButton(get_text('go_to_payment_button', user_lang), url=payment_url)],
            [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        temp_update = Update(update_id=0, callback_query=query)
        try:
            sent_message = await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
            
            # Сохраняем message_id в базе данных для последующего удаления после успешной оплаты
            if sent_message and hasattr(sent_message, 'message_id'):
                message_id = sent_message.message_id
            elif query.message:
                message_id = query.message.message_id
            else:
                message_id = None
            
            # Сохраняем message_id в payment, если можем получить order_id из результата
            if message_id and result.get("order_id"):
                try:
                    from modules.models.payment import Payment
                    from modules.core import get_db
                    db = get_db()
                    payment = Payment.query.filter_by(order_id=result["order_id"]).first()
                    if payment:
                        # Сохраняем message_id в базу данных
                        payment.telegram_message_id = message_id
                        db.session.commit()
                        logger.debug(f"Saved telegram_message_id={message_id} for payment order_id={result['order_id']}")
                except Exception as e:
                    logger.debug(f"Could not save message_id: {e}")
                    
        except Exception as e:
            logger.warning(f"Error in handle_payment, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup)
    else:
        message = result.get("message", get_text('error_creating_payment', user_lang))
        keyboard = [[InlineKeyboardButton(get_text('back_to_tariffs', user_lang), callback_data="tariffs")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        temp_update = Update(update_id=0, callback_query=query)
        await safe_edit_or_send_with_logo(
            temp_update,
            context,
            f"❌ **{get_text('error', user_lang)}**\n\n{message}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
            )


async def choose_config_for_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, tariff_id: int, provider: str):
    """Шаг выбора подписки перед оплатой/продлением"""
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    telegram_id = user.id

    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}", show_alert=True)
        return

    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)

    cfgs_resp = api.get_configs(token)
    cfgs = (cfgs_resp or {}).get('configs') or []

    text = "🧩 **Выберите подписку для оплаты**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    text += "Оплата/продление будет применено к выбранной подписке.\n"

    keyboard = []
    for cfg in cfgs:
        try:
            cfg_id = cfg.get('id')
            name = cfg.get('config_name') or f"Подписка {cfg_id}"
            is_primary = bool(cfg.get('is_primary'))
            prefix = "⭐" if is_primary else "🧩"
            keyboard.append([
                InlineKeyboardButton(f"{prefix} {name}", callback_data=f"pay_{tariff_id}_{provider}_cfg_{cfg_id}")
            ])
        except Exception:
            continue

    # Кнопка "новая подписка" — создаст новую подписку после успешной оплаты
    keyboard.append([
        InlineKeyboardButton(get_text('new_subscription_button', user_lang), callback_data=f"pay_{tariff_id}_{provider}_newcfg")
    ])

    keyboard.append([
        InlineKeyboardButton(get_text('back_to_tariffs', user_lang), callback_data="tariffs")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    temp_update = Update(update_id=0, callback_query=query)
    await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")


async def show_configs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список конфигов пользователя и их статус"""
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    telegram_id = user.id
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}", show_alert=True)
        return

    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)

    cfgs_resp = api.get_configs(token, force_refresh=True)
    cfgs = (cfgs_resp or {}).get('configs') or []

    text = f"{get_emoji('PUZZLE')} **{get_text('configs_button', user_lang)}**\n"
    text += f"{SEPARATOR_LINE}\n"
    if not cfgs:
        text += "Подписки не найдены.\n"
        text += f"{SEPARATOR_LINE}\n"
    else:
        for cfg in cfgs:
            name = cfg.get('config_name') or f"Подписка {cfg.get('id')}"
            is_primary = bool(cfg.get('is_primary'))
            status = f"{get_emoji('ACTIVE_GREEN')} активен" if cfg.get('is_active') else f"{get_emoji('INACTIVE')} неактивен"
            exp = cfg.get('expire_at')
            if exp and isinstance(exp, str):
                try:
                    exp_dt = datetime.fromisoformat(exp.replace('Z', '+00:00'))
                    exp_str = exp_dt.strftime('%d.%m.%Y %H:%M')
                except Exception:
                    exp_str = exp
            else:
                exp_str = "—"

            prefix = get_emoji('STAR') if is_primary else get_emoji('PUZZLE')
            text += f"{prefix} **{name}**\n"
            text += f"{status} • до {exp_str}\n"
            text += f"{SEPARATOR_LINE}\n"

    keyboard = []
    for cfg in cfgs:
        cfg_id = cfg.get('id')
        name = cfg.get('config_name') or f"Подписка {cfg_id}"
        sub_url = cfg.get('subscription_url')
        row = []
        if sub_url:
            row.append(InlineKeyboardButton(f"🚀 {name}", url=sub_url))
        row.append(InlineKeyboardButton(get_text('extend_button', user_lang), callback_data=f"tariffs_cfg_{cfg_id}"))
        row.append(InlineKeyboardButton(get_text('share_button', user_lang), callback_data=f"share_config_{cfg_id}"))
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton(get_text('new_subscription_button', user_lang), callback_data="tariffs_newcfg")])
    back_to = pop_back_callback(context, "main_menu")
    keyboard.append([InlineKeyboardButton(get_text('back', user_lang), callback_data=back_to)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    temp_update = Update(update_id=0, callback_query=query)
    await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="configs")


async def handle_share_config(update: Update, context: ContextTypes.DEFAULT_TYPE, config_id: int):
    """Обработка запроса на создание токена для обмена подпиской"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}", show_alert=True)
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    
    try:
        # Создаем токен через API
        response = requests.post(
            f"{FLASK_API_URL}/api/client/configs/{config_id}/share-token",
            headers={"Authorization": f"Bearer {token}"},
            json={"expires_hours": 168, "max_uses": 1},  # 7 дней, 1 использование
            timeout=10
        )
        
        if response.status_code != 200:
            await query.answer("❌ Ошибка при создании ссылки", show_alert=True)
            return
        
        share_data = response.json()
        share_token = share_data.get('token')
        
        if not share_token:
            await query.answer("❌ Ошибка при создании ссылки", show_alert=True)
            return
        
        # Получаем информацию о конфиге
        cfgs_resp = api.get_configs(token, force_refresh=True)
        cfgs = (cfgs_resp or {}).get('configs') or []
        config = next((c for c in cfgs if c.get('id') == config_id), None)
        
        config_name = config.get('config_name') if config else f"Подписка {config_id}"
        # Получаем username бота (без @)
        bot_username = (
            os.getenv("TELEGRAM_BOT_NAME_V2") or 
            os.getenv("TELEGRAM_BOT_NAME") or 
            os.getenv("BOT_USERNAME") or 
            os.getenv("CLIENT_BOT_USERNAME", "")
        ).replace("@", "")
        
        # Формируем текст с инструкцией
        text = f"📤 **Поделиться подпиской**\n"
        text += f"{SEPARATOR_LINE}\n\n"
        text += f"🧩 **{config_name}**\n\n"
        text += f"Чтобы поделиться этой подпиской:\n\n"
        text += f"📋 Скопируйте данное сообщение и перешлите тому, с кем хотите поделиться подпиской:\n\n"
        # Без обратных кавычек и с нулевым пробелом после @ — не ссылка, удобно копировать по нажатию
        text += f"@\u200b{bot_username} {share_token}\n\n"
        text += f"💡 Ссылка действительна 7 дней\n"
        text += f"📊 Можно использовать 1 раз\n"
        
        keyboard = [
            [InlineKeyboardButton(get_text('copy_token_button', user_lang), callback_data=f"copy_share_token_{share_token}")],
            [InlineKeyboardButton(get_text('back', user_lang), callback_data="sub_configs")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        temp_update = Update(update_id=0, callback_query=query)
        await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="configs")
        await query.answer("✅ Ссылка создана!")
        
    except Exception as e:
        logger.error(f"Error creating share token: {e}")
        await query.answer("❌ Ошибка при создании ссылки", show_alert=True)


async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline запросов для обмена подписками"""
    inline_query = update.inline_query
    if not inline_query:
        return
    
    raw = inline_query.query.strip()
    # Если вставили полную строку «@bot_username TOKEN» — извлекаем токен
    if raw and " " in raw and (raw.startswith("@") or "\u200b" in raw):
        parts = raw.replace("\u200b", "").strip().split()
        if len(parts) >= 2 and parts[0].startswith("@"):
            raw = parts[1]
    query_text = raw
    if not query_text:
        # Показываем инструкцию, если запрос пустой
        results = [
            InlineQueryResultArticle(
                id="help",
                title="📤 Поделиться подпиской",
                description="Введите токен подписки для получения доступа",
                input_message_content=InputTextMessageContent(
                    message_text="Введите токен подписки, который вам прислали"
                )
            )
        ]
        await inline_query.answer(results, cache_time=1)
        return
    
    # Проверяем, является ли запрос токеном
    try:
        # Получаем информацию о подписке по токену
        response = requests.get(
            f"{FLASK_API_URL}/api/public/config-share/{query_text}",
            timeout=10
        )
        
        if response.status_code != 200:
            # Токен не найден или невалиден
            results = [
                InlineQueryResultArticle(
                    id="invalid",
                    title="❌ Токен не найден",
                    description="Проверьте правильность токена",
                    input_message_content=InputTextMessageContent(
                        message_text="❌ Токен подписки не найден или истек"
                    )
                )
            ]
            await inline_query.answer(results, cache_time=1)
            return
        
        config_data = response.json()
        config_name = config_data.get('config_name', 'Подписка')
        owner_username = config_data.get('owner_username', 'пользователя')
        is_active = config_data.get('is_active', False)
        subscription_url = config_data.get('subscription_url')
        
        # Формируем результат
        status_text = "🟢 Активен" if is_active else "🔴 Неактивен"
        description = f"{config_name} от {owner_username} • {status_text}"
        
        # Формируем сообщение с информацией о подписке
        # Используем простой текст без Markdown, чтобы избежать проблем с парсингом
        message_text = f"🧩 {config_name}\n"
        message_text += f"От: {owner_username}\n"
        message_text += f"Статус: {status_text}\n\n"
        message_text += f"Нажмите кнопку ниже, чтобы получить доступ к этой подписке."
        
        # Создаем кнопку для принятия подписки
        accept_button = InlineKeyboardButton(
            "✅ Получить подписку",
            callback_data=f"accept_config_{query_text}"
        )
        keyboard = InlineKeyboardMarkup([[accept_button]])
        
        # ID должен быть уникальным, но не слишком длинным (максимум 64 символа)
        # Используем хеш токена для ID
        result_id = hashlib.md5(query_text.encode()).hexdigest()[:32]
        
        # Ограничиваем длину description (максимум 255 символов)
        if len(description) > 255:
            description = description[:252] + "..."
        
        # Ограничиваем длину title (максимум 64 символа)
        title = f"🧩 {config_name}"
        if len(title) > 64:
            title = title[:61] + "..."
        
        # Создаем результат с кнопкой прямо в сообщении
        # reply_markup добавляется к InlineQueryResultArticle и будет в отправленном сообщении
        results = [
            InlineQueryResultArticle(
                id=result_id,
                title=title,
                description=description,
                input_message_content=InputTextMessageContent(
                    message_text=message_text
                ),
                reply_markup=keyboard
            )
        ]
        
        await inline_query.answer(results, cache_time=1)
        
    except Exception as e:
        import traceback
        logger.error(f"Error handling inline query: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Показываем более информативное сообщение об ошибке
        error_msg = str(e)[:100]  # Ограничиваем длину
        results = [
            InlineQueryResultArticle(
                id="error",
                title="❌ Ошибка",
                description=f"Ошибка: {error_msg}",
                input_message_content=InputTextMessageContent(
                    message_text=f"❌ Произошла ошибка при обработке запроса: {error_msg}"
                )
            )
        ]
        try:
            await inline_query.answer(results, cache_time=1)
        except Exception as answer_error:
            logger.error(f"Error answering inline query: {answer_error}")


async def handle_accept_shared_config(update: Update, context: ContextTypes.DEFAULT_TYPE, share_token: str):
    """Обработка принятия подписки по токену"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}", show_alert=True)
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    
    try:
        # Принимаем подписку через API
        response = requests.post(
            f"{FLASK_API_URL}/api/client/configs/share/{share_token}/accept",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            config_id = result.get('config_id')
            
            text = f"✅ **Подписка успешно добавлена!**\n\n"
            text += f"🧩 Подписка добавлена в ваш список подписок.\n"
            text += f"Вы можете найти её в разделе «Подписки»."
            
            keyboard = [
                [InlineKeyboardButton(get_text('configs_button', user_lang), callback_data="sub_configs")],
                [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Проверяем, есть ли сообщение для редактирования
            if query.message:
                # Пытаемся отредактировать сообщение
                try:
                    await safe_edit_or_send_with_logo(update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
                except Exception as edit_error:
                    logger.debug(f"Could not edit message, sending new one: {edit_error}")
                    # Если не удалось отредактировать, отправляем новое сообщение
                    await reply_with_logo(update, text, reply_markup=reply_markup, parse_mode="Markdown", context=context)
            else:
                # Если сообщения нет (например, отправлено через inline режим в другой чат),
                # отправляем новое сообщение напрямую пользователю
                try:
                    chat_id = user.id
                    logo_path = _get_logo_path("default")
                    if os.path.exists(logo_path):
                        with open(logo_path, 'rb') as logo_file:
                            await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=logo_file,
                                caption=text,
                                reply_markup=reply_markup,
                                parse_mode="Markdown"
                            )
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode="Markdown"
                        )
                except Exception as send_error:
                    logger.error(f"Error sending message to user: {send_error}")
                    # Fallback - просто отвечаем на callback
                    await query.answer("✅ Подписка добавлена! Проверьте раздел «Подписки»", show_alert=True)
                    return
            
            await query.answer("✅ Подписка добавлена!")
            
        elif response.status_code == 400:
            result = response.json()
            message = result.get('message', 'Ошибка')
            await query.answer(f"❌ {message}", show_alert=True)
        else:
            await query.answer("❌ Ошибка при принятии подписки", show_alert=True)
            
    except Exception as e:
        logger.error(f"Error accepting shared config: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        await query.answer("❌ Ошибка при принятии подписки", show_alert=True)


async def show_topup_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать форму пополнения баланса"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}")
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    if not user_data:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('failed_to_load', lang)}")
        return
    
    user_lang = get_user_lang(user_data, context, token)
    balance = user_data.get("balance", 0)
    preferred_currency = user_data.get("preferred_currency", "uah")
    currency_symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(preferred_currency, "₴")
    
    text = f"{get_emoji('BALANCE')} **{get_text('top_up_balance', user_lang)}**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    text += f"{get_emoji('CARD')} **{get_text('balance', user_lang)}:** {balance:.2f} {currency_symbol}\n\n"
    text += f"{get_emoji('NOTE')} {get_text('enter_amount', user_lang)}:\n\n"
    text += f"{get_emoji('TRIAL')} {get_text('select_amount_hint', user_lang)}"
    
    # Предустановленные суммы
    amounts = [100, 500, 1000, 2000, 5000]
    keyboard = []
    row = []
    
    for amount in amounts:
        row.append(InlineKeyboardButton(
            f"{amount} {currency_symbol}",
            callback_data=f"topup_amount_{amount}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    # Кнопка для ввода своей суммы
    keyboard.append([
        InlineKeyboardButton(get_text('enter_custom_amount', user_lang), callback_data="topup_custom_amount")
    ])
    
    back_to = pop_back_callback(context, "main_menu")
    keyboard.append([
        InlineKeyboardButton(get_text('back', user_lang), callback_data=back_to)
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    temp_update = Update(update_id=0, callback_query=query)
    try:
        await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown", logo_page="topup")
    except Exception as e:
        logger.warning(f"Error in show_topup_balance, sending without formatting: {e}")
        text_clean = clean_markdown_for_cards(text)
        await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup, logo_page="topup")


async def select_topup_method(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float):
    """Выбрать способ пополнения баланса"""
    # Может быть вызвано как из callback, так и из текстового сообщения
    query = update.callback_query
    message = update.message
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        if query:
            await query.answer(f"❌ {get_text('auth_error', lang)}")
        elif message:
            temp_update = Update(update_id=0, message=message)
            await reply_with_logo(temp_update, f"❌ {get_text('auth_error', lang)}", context=context)
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    preferred_currency = user_data.get("preferred_currency", "uah") if user_data else "uah"
    currency_symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(preferred_currency, "₴")
    
    text = f"💰 **{get_text('top_up_balance', user_lang)}**\n"
    text += "━━━━━━━━━━━━━━━\n\n"
    text += f"💵 **{get_text('amount', user_lang)}:** {amount:.0f} {currency_symbol}\n\n"
    text += f"**{get_text('select_topup_method', user_lang)}**:"
    
    # Получаем доступные способы оплаты
    available_methods = api.get_available_payment_methods()
    
    payment_names = {
        'crystalpay': '💳 CrystalPay',
        'heleket': '₿ Heleket',
        'yookassa': '💳 YooKassa',
        'platega': '💳 Platega',
        'platega_mir': '💳 Карты МИР',
        'mulenpay': '💳 Mulenpay',
        'urlpay': '💳 UrlPay',
        'telegram_stars': '⭐ Telegram Stars',
        'monobank': '💳 Monobank',
        'btcpayserver': '₿ BTCPayServer',
        'tribute': '💳 Tribute',
        'robokassa': '💳 Robokassa',
        'freekassa': '💳 Freekassa',
        'kassa_ai': '💳 Kassa AI'
    }
    
    keyboard = []
    row = []
    
    for method in available_methods:
        if method == "balance":
            continue
        label = payment_names.get(method, f"💳 {method}")
        row.append(InlineKeyboardButton(
            label,
            callback_data=f"topup_pay_{amount}_{method}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton(get_text('back', user_lang), callback_data="topup_balance")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        temp_update = Update(update_id=0, callback_query=query)
        try:
            await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Error in select_topup_method, sending without formatting: {e}")
            text_clean = clean_markdown_for_cards(text)
            await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup)
    elif message:
        temp_update = Update(update_id=0, message=message)
        try:
            text = text_to_html_with_tg_emoji(text)
            pm = "HTML"
        except Exception:
            pm = "Markdown"
        try:
            await reply_with_logo(
                temp_update,
                text,
                reply_markup=reply_markup,
                parse_mode=pm
            )
        except Exception as e:
            logger.warning(f"Markdown parsing error in select_topup_method: {e}")
            await reply_with_logo(
                temp_update,
                clean_markdown_for_cards(text),
                reply_markup=reply_markup
            )


async def handle_topup_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float, provider: str):
    """Обработать создание платежа на пополнение баланса"""
    query = update.callback_query
    if not query:
        return
    
    user = update.effective_user
    telegram_id = user.id
    
    token = get_user_token(telegram_id)
    if not token:
        lang = get_user_lang(None, context, token)
        await query.answer(f"❌ {get_text('auth_error', lang)}")
        return
    
    token, user_data = get_user_data_safe(telegram_id, token)
    user_lang = get_user_lang(user_data, context, token)
    preferred_currency = user_data.get("preferred_currency", "uah") if user_data else "uah"
    currency_symbol = {"uah": "₴", "rub": "₽", "usd": "$"}.get(preferred_currency, "₴")
    
    await query.answer(f"⏳ {get_text('creating_payment', user_lang)}...")
    
    try:
        response = api.session.post(
            f"{FLASK_API_URL}/api/client/create-payment",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "type": "balance_topup",
                "amount": amount,
                "currency": preferred_currency,
                "payment_provider": provider,
                "source": "bot"
            },
            timeout=30
        )
        
        result = response.json()
        
        if response.status_code == 200 and result.get("payment_url"):
            payment_url = result["payment_url"]
            text = f"{get_emoji('CARD')} **{get_text('balance_topup_created', user_lang)}**\n"
            text += "━━━━━━━━━━━━━━━\n\n"
            text += f"{get_emoji('BALANCE')} **{get_text('amount', user_lang)}:** {amount:.0f} {currency_symbol}\n\n"
            text += f"{get_text('go_to_payment_text', user_lang)}:\n\n"
            text += f"`{payment_url}`\n\n"
            text += f"{get_text('after_payment', user_lang)}"
            
            keyboard = [
                [InlineKeyboardButton(get_text('go_to_payment_button', user_lang), url=payment_url)],
                [InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            temp_update = Update(update_id=0, callback_query=query)
            try:
                await safe_edit_or_send_with_logo(temp_update, context, text, reply_markup=reply_markup, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Error in handle_topup_payment, sending without formatting: {e}")
                text_clean = clean_markdown_for_cards(text)
                await safe_edit_or_send_with_logo(temp_update, context, text_clean, reply_markup=reply_markup)
        else:
            message = result.get("message", "Ошибка создания платежа")
            keyboard = [[InlineKeyboardButton(get_text('back', user_lang), callback_data="topup_balance")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            temp_update = Update(update_id=0, callback_query=query)
            await safe_edit_or_send_with_logo(
                temp_update,
                context,
                f"❌ **Ошибка**\n\n{message}",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Error in topup payment: {e}")
        keyboard = [[InlineKeyboardButton(get_text('back', user_lang), callback_data="topup_balance")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        temp_update = Update(update_id=0, callback_query=query)
        await safe_edit_or_send_with_logo(
            temp_update,
            context,
            f"❌ **Ошибка**\n\nОшибка при создании платежа: {str(e)}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )


def main():
    """Главная функция запуска бота"""
    # Создаем приложение
    application = Application.builder().token(CLIENT_BOT_TOKEN).build()
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status_command))
    
    # Обработчик платежей (должен быть ПЕРЕД общим button_callback, так как он более специфичный)
    async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query and query.data and query.data.startswith("pay_"):
            try:
                raw = query.data

                # Форматы:
                # 1) pay_{tariffId}_{provider}                    -> показать выбор подписки
                # 2) pay_{tariffId}_{provider}_cfg_{configId}     -> оплатить выбранную подписку
                # 3) pay_{tariffId}_{provider}_newcfg             -> создать новую подписку после оплаты

                parts = raw.split("_")
                if len(parts) < 3:
                    await query.answer("❌ Неверный формат данных платежа", show_alert=True)
                    return

                tariff_id = int(parts[1])

                # Выбрана конкретная подписка
                if "_cfg_" in raw:
                    left, cfg_id_str = raw.split("_cfg_", 1)
                    provider = left.split("_", 2)[2]  # pay, tariffId, provider(with underscores)
                    config_id = int(cfg_id_str)
                    await handle_payment(update, context, tariff_id, provider, config_id=config_id)
                    return

                # Новая подписка
                if raw.endswith("_newcfg"):
                    left = raw[:-len("_newcfg")]
                    provider = left.split("_", 2)[2]
                    await handle_payment(update, context, tariff_id, provider, create_new_config=True)
                    return

                # Иначе это первый шаг: pay_{tariffId}_{provider}
                provider = raw.split("_", 2)[2]
                preferred_cfg = context.user_data.get("preferred_config_id")
                preferred_new = bool(context.user_data.get("preferred_create_new_config"))

                # Если пользователь пришел из "Мои подписки" и выбрал конкретную подписку — пропускаем шаг выбора
                if preferred_new:
                    context.user_data["preferred_config_id"] = None
                    context.user_data["preferred_create_new_config"] = False
                    await handle_payment(update, context, tariff_id, provider, create_new_config=True)
                    return

                if preferred_cfg:
                    context.user_data["preferred_config_id"] = None
                    context.user_data["preferred_create_new_config"] = False
                    await handle_payment(update, context, tariff_id, provider, config_id=int(preferred_cfg))
                    return

                await choose_config_for_payment(update, context, tariff_id, provider)
                return  # Важно: возвращаемся, чтобы не обрабатывать дальше
            except (ValueError, IndexError) as e:
                logger.error(f"Payment callback error: {e}")
                await query.answer("❌ Ошибка обработки платежа", show_alert=True)
    
    # Регистрируем обработчик платежей ПЕРВЫМ (более специфичный паттерн)
    application.add_handler(CallbackQueryHandler(payment_callback, pattern="^pay_"))
    
    # Обработчик inline запросов (для обмена подписками)
    application.add_handler(InlineQueryHandler(handle_inline_query))
    
    # Регистрируем общий обработчик callback кнопок ПОСЛЕ специфичных
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Обработчик текстовых сообщений (для создания тикетов)
    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик текстовых сообщений"""
        if update.message and update.message.text:
            user_data = context.user_data
            
            if user_data.get("waiting_for_ticket_subject"):
                # Сохраняем тему и просим сообщение
                user_data["ticket_subject"] = update.message.text
                user_data["waiting_for_ticket_subject"] = False
                user_data["waiting_for_ticket_message"] = True
                
                await reply_with_logo(
                    update,
                    "💬 **Создание тикета**\n\n"
                    "Тема сохранена. Теперь отправьте текст сообщения:",
                    parse_mode="Markdown"
                )
            
            elif user_data.get("waiting_for_ticket_message"):
                # Создаем тикет
                subject = user_data.get("ticket_subject", "Без темы")
                message = update.message.text
                
                telegram_id = update.effective_user.id
                token = get_user_token(telegram_id)
                
                if token:
                    result = api.create_support_ticket(token, subject, message)
                    
                    # Получаем язык пользователя для кнопки
                    token, user_data_api = get_user_data_safe(telegram_id, token) if token else (token, None)
                    user_lang = get_user_lang(user_data_api, context, token)
                    
                    # API возвращает {"message": "Created", "ticket_id": nt.id} со статусом 201
                    # Проверяем оба варианта
                    ticket_id = result.get("ticket_id") if result else None
                    if not ticket_id and result and result.get("message") == "Created":
                        # Пытаемся получить ticket_id из другого поля
                        ticket_id = result.get("id")
                    
                    if ticket_id:
                        # Создаем кнопку "Вернуться в меню"
                        keyboard = [[InlineKeyboardButton(get_text('main_menu_button', user_lang), callback_data="main_menu")]]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        await reply_with_logo(
                            update,
                            f"✅ **Тикет создан!**\n\n"
                            f"Номер тикета: #{ticket_id}\n"
                            f"Тема: {subject}\n\n"
                            f"Мы ответим вам в ближайшее время.\n\n"
                            f"Вы можете просмотреть тикет в разделе поддержки.",
                            reply_markup=reply_markup,
                            parse_mode="Markdown"
                        )
                    else:
                        error_msg = result.get("message", "Ошибка создания тикета") if result else "Ошибка создания тикета"
                        await reply_with_logo(
                            update,
                            f"❌ **Ошибка**\n\n{error_msg}",
                            parse_mode="Markdown"
                        )
                else:
                    await reply_with_logo(
                        update,
                        "❌ Ошибка авторизации. Используйте /start для повторной авторизации."
                    )
                
                # Очищаем состояние
                user_data.pop("ticket_subject", None)
                user_data.pop("waiting_for_ticket_message", None)
            
            elif user_data.get("waiting_for_ticket_reply"):
                # Отвечаем на тикет
                ticket_id = user_data.get("reply_ticket_id")
                message = update.message.text
                
                telegram_id = update.effective_user.id
                token = get_user_token(telegram_id)
                
                if token and ticket_id:
                    # Получаем язык пользователя для кнопок
                    token, user_data_api = get_user_data_safe(telegram_id, token)
                    user_lang = get_user_lang(user_data_api, context, token)
                    
                    result = api.reply_to_ticket(token, ticket_id, message)
                    
                    if result.get("id") or result.get("success"):
                        # Создаем клавиатуру с кнопками "Просмотреть тикет" и "Назад"
                        keyboard = [
                            [InlineKeyboardButton(f"{get_text('ticket_view_title', user_lang)} #{ticket_id}", callback_data=f"view_ticket_{ticket_id}")],
                            [InlineKeyboardButton(get_text('back_to_support', user_lang), callback_data="support")]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        await reply_with_logo(
                            update,
                            f"✅ **Ответ отправлен!**\n\n"
                            f"Тикет #{ticket_id}\n\n"
                            f"Ваш ответ был добавлен в тикет.",
                            reply_markup=reply_markup,
                            parse_mode="Markdown"
                        )
                    else:
                        error_msg = result.get("message", "Ошибка отправки ответа") if result else "Ошибка отправки ответа"
                        await reply_with_logo(
                            update,
                            f"❌ **Ошибка**\n\n{error_msg}",
                            parse_mode="Markdown"
                        )
                else:
                    await reply_with_logo(
                        update,
                        "❌ Ошибка авторизации. Используйте /start для повторной авторизации."
                    )
                
                # Очищаем состояние
                user_data.pop("waiting_for_ticket_reply", None)
                user_data.pop("reply_ticket_id", None)
            
            elif user_data.get("waiting_for_topup_amount"):
                # Обрабатываем ввод суммы для пополнения баланса
                user = update.effective_user
                telegram_id = user.id
                
                token = get_user_token(telegram_id)
                token, user_data_api = get_user_data_safe(telegram_id, token) if token else (token, None)
                user_lang = get_user_lang(user_data_api, context, token)
                
                try:
                    amount_text = update.message.text.strip()
                    # Удаляем все нецифровые символы кроме точки и запятой
                    amount_text = amount_text.replace(",", ".").replace(" ", "")
                    amount = float(amount_text)
                    
                    if amount <= 0:
                        await reply_with_logo(
                            update,
                            f"❌ {get_text('amount_too_small', user_lang)}"
                        )
                        return
                    
                    # Очищаем состояние
                    user_data.pop("waiting_for_topup_amount", None)
                    
                    # Переходим к выбору способа оплаты
                    await select_topup_method(update, context, amount)
                    
                except ValueError:
                    await reply_with_logo(
                        update,
                        f"❌ {get_text('invalid_amount_format', user_lang)}"
                    )
            
            else:
                # Проверяем, не содержит ли сообщение токен подписки (отправленным через inline режим)
                message_text = update.message.text.strip()
                
                # Ищем токен в сообщении - он может быть:
                # 1. Чистым токеном (без пробелов, 20-100 символов)
                # 2. В тексте после "Токен: " или "токен: " или похожих паттернов
                share_token = None
                
                # Сначала проверяем, является ли всё сообщение токеном
                if (len(message_text) >= 20 and 
                    len(message_text) <= 100 and
                    not ' ' in message_text and
                    re.match(r'^[a-zA-Z0-9_-]+$', message_text)):
                    share_token = message_text
                else:
                    # Ищем токен в тексте сообщения (после "Токен:", "токен:", "Token:" и т.д.)
                    # Паттерн: слово "токен" (любой регистр) + двоеточие/пробел + токен (20-100 символов)
                    token_pattern = r'(?:токен|token)[:\s]+([a-zA-Z0-9_-]{20,100})'
                    match = re.search(token_pattern, message_text, re.IGNORECASE)
                    if match:
                        share_token = match.group(1)
                
                # Если нашли потенциальный токен, проверяем его через API
                if share_token:
                    try:
                        # Пробуем получить информацию о подписке по токену
                        response = requests.get(
                            f"{FLASK_API_URL}/api/public/config-share/{share_token}",
                            timeout=5
                        )
                        
                        if response.status_code == 200:
                            # Это валидный токен подписки, предлагаем принять
                            config_data = response.json()
                            config_name = config_data.get('config_name', 'Подписка')
                            owner_username = config_data.get('owner_username', 'пользователя')
                            
                            telegram_id = update.effective_user.id
                            user_token = get_user_token(telegram_id)
                            
                            if user_token:
                                token, user_data_api = get_user_data_safe(telegram_id, user_token)
                                user_lang = get_user_lang(user_data_api, context, user_token)
                                
                                text = f"🧩 **{config_name}**\n"
                                text += f"От: {owner_username}\n\n"
                                text += f"Хотите получить доступ к этой подписке?"
                                
                                keyboard = [
                                    [InlineKeyboardButton("✅ Получить подписку", callback_data=f"accept_config_{share_token}")],
                                    [InlineKeyboardButton("❌ Отмена", callback_data="main_menu")]
                                ]
                                reply_markup = InlineKeyboardMarkup(keyboard)
                                
                                await reply_with_logo(update, text, reply_markup=reply_markup, parse_mode="Markdown", context=context)
                                return  # Не обрабатываем дальше
                    except Exception as e:
                        # Игнорируем ошибки при проверке токена (это не токен или токен невалиден)
                        logger.debug(f"Token check failed (not a subscription token): {e}")
                        pass
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обработчик для Telegram Stars - PreCheckoutQuery (подтверждение платежа)
    async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик подтверждения платежа Telegram Stars"""
        query = update.pre_checkout_query
        if not query:
            return
        
        order_id = query.invoice_payload
        logger.info(f"PreCheckoutQuery received: order_id={order_id}, query_id={query.id}")
        
        # Подтверждаем все платежи - вебхук проверит статус при successful_payment
        # Это необходимо, чтобы Telegram не показывал ошибку ожидания
        try:
            await query.answer(ok=True)
            logger.info(f"PreCheckoutQuery confirmed for order_id={order_id}")
        except Exception as e:
            logger.error(f"Error answering PreCheckoutQuery: {e}")
            try:
                await query.answer(ok=False, error_message="Payment verification error")
            except:
                pass
    
    # Обработчик для Telegram Stars - SuccessfulPayment (успешный платеж)
    async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик успешного платежа Telegram Stars"""
        message = update.message
        if not message or not message.successful_payment:
            return
        
        successful_payment = message.successful_payment
        order_id = successful_payment.invoice_payload
        user = update.effective_user
        telegram_id = user.id
        
        logger.info(f"Successful payment received: order_id={order_id}, telegram_id={telegram_id}")
        
        # При polling бот получает обновления, вебхук Flask не вызывается — обрабатываем платеж через внутренний API
        try:
            import asyncio
            def _process_payment():
                r = requests.post(
                    f"{FLASK_API_URL}/api/internal/process-telegram-payment",
                    headers={"Content-Type": "application/json", "X-Internal-Key": "telegram-stars-internal"},
                    json={"order_id": order_id, "telegram_id": telegram_id},
                    timeout=15
                )
                return r
            resp = await asyncio.to_thread(_process_payment)
            if resp.status_code == 200:
                logger.info(f"Payment processed via internal API: order_id={order_id}")
            else:
                logger.warning(f"Internal API returned {resp.status_code} for order_id={order_id}: {resp.text[:200]}")
        except Exception as e:
            logger.exception(f"Failed to process Telegram Stars payment via internal API: {e}")
        
        token = get_user_token(telegram_id)
        if not token:
            await message.reply_text("❌ Ошибка авторизации")
            return
        
        token, user_data = get_user_data_safe(telegram_id, token)
        user_lang = get_user_lang(user_data, context, token)
        
        text = f"✅ **{get_text('payment_successful', user_lang)}**\n\n"
        text += f"💳 {get_text('payment_processed', user_lang)}\n\n"
        text += f"🔄 {get_text('subscription_updating', user_lang)}"
        
        await reply_with_logo(update, text, parse_mode="Markdown", context=context)
        
        import asyncio
        await asyncio.sleep(1)
        
        # Обновляем данные пользователя - создаем временный callback для показа главного меню
        # Вебхук уже обработал платеж, подписка обновлена
        from telegram import CallbackQuery
        # Создаем временный callback query для показа главного меню
        temp_query = CallbackQuery(
            id=0,
            from_user=user,
            chat_instance=0,
            message=message,
            data="main_menu"
        )
        temp_update = Update(update_id=update.update_id, callback_query=temp_query)
        await button_callback(temp_update, context)
    
    # Регистрируем обработчики Telegram Stars
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    
    # Добавляем обработчик ошибок ПЕРЕД запуском
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик ошибок"""
        error = context.error
        
        # Обрабатываем конфликт обновлений
        if isinstance(error, Conflict):
            logger.warning("Bot conflict detected: terminated by other getUpdates request")
            logger.warning("This usually means multiple bot instances are running.")
            logger.warning("Make sure only one instance of the bot is running.")
            logger.warning("If using systemd service, check if bot is already running: systemctl status client-bot")
            return  # Не логируем как критическую ошибку
        
        # Логируем другие ошибки
        logger.error(f"Exception while handling an update: {error}", exc_info=error)
    
    application.add_error_handler(error_handler)
    
    # Запускаем бота: webhook или polling
    logger.info("Бот запущен и готов к работе!")
    
    if BOT_USE_WEBHOOK and BOT_WEBHOOK_BASE_URL:
        # Режим webhook: Telegram шлёт обновления на наш URL
        webhook_url = f"{BOT_WEBHOOK_BASE_URL}/{BOT_WEBHOOK_PATH}"
        try:
            async def _set_webhook():
                await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
            asyncio.run(_set_webhook())
            logger.info(f"Webhook set: {webhook_url}")
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")
            raise
        try:
            logger.info(f"Starting bot with webhook on 0.0.0.0:{BOT_WEBHOOK_PORT}/{BOT_WEBHOOK_PATH}...")
            if not hasattr(application, "run_webhook"):
                logger.error(
                    "Application.run_webhook not found (your python-telegram-bot version may use custom webhook). "
                    "See docs/BOT_WEBHOOK.md for alternatives or use BOT_USE_WEBHOOK=false for polling."
                )
                raise RuntimeError("run_webhook not available")
            application.run_webhook(
                listen="0.0.0.0",
                port=BOT_WEBHOOK_PORT,
                url_path=BOT_WEBHOOK_PATH,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
        except Exception as e:
            logger.error(f"Error running webhook: {e}")
            raise
    else:
        # Режим polling: удаляем webhook и опрашиваем getUpdates
        try:
            logger.info("Checking for active webhook...")
            bot_token = CLIENT_BOT_TOKEN
            webhook_info_url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
            delete_webhook_url = f"https://api.telegram.org/bot{bot_token}/deleteWebhook"
            webhook_response = requests.get(webhook_info_url, timeout=5)
            if webhook_response.status_code == 200:
                webhook_data = webhook_response.json()
                if webhook_data.get('ok') and webhook_data.get('result', {}).get('url'):
                    logger.info(f"Found active webhook. Deleting it...")
                    delete_response = requests.post(
                        delete_webhook_url,
                        json={"drop_pending_updates": True},
                        timeout=5
                    )
                    if delete_response.status_code == 200 and delete_response.json().get('ok'):
                        logger.info("Webhook deleted successfully")
                    else:
                        logger.warning(f"Failed to delete webhook: {delete_response.text}")
                else:
                    logger.info("No active webhook found")
            else:
                logger.warning(f"Failed to check webhook status: {webhook_response.text}")
        except Exception as e:
            logger.warning(f"Error checking/deleting webhook: {e}. Continuing with polling...")
        try:
            logger.info("Starting bot with polling...")
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
        except Exception as e:
            logger.error(f"Error starting bot: {e}")
            raise


if __name__ == "__main__":
    main()

