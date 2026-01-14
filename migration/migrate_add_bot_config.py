#!/usr/bin/env python3
"""
Скрипт миграции для создания таблицы bot_config (конструктор бота).

Использование:
    python3 migration/migrate_add_bot_config.py
"""

import sqlite3
import os
import sys
from pathlib import Path

def find_database():
    """Находит путь к базе данных"""
    possible_paths = [
        Path('instance/stealthnet.db'),
        Path('stealthnet.db'),
        Path('/var/www/stealthnet-api/instance/stealthnet.db'),
        Path('/var/www/stealthnet-api/stealthnet.db'),
    ]
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
        db_uri = os.getenv('SQLALCHEMY_DATABASE_URI', '')
        if db_uri and db_uri.startswith('sqlite:///'):
            db_path = Path(db_uri.replace('sqlite:///', ''))
            if db_path.exists():
                return db_path
    except:
        pass
    
    for db_path in possible_paths:
        if db_path.exists():
            return db_path
    
    return None

# SQL для создания таблицы
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bot_config (
    id INTEGER PRIMARY KEY,
    
    -- Общие настройки
    service_name VARCHAR(100) DEFAULT 'StealthNET' NOT NULL,
    bot_username VARCHAR(100),
    support_url VARCHAR(500),
    support_bot_username VARCHAR(100),
    
    -- Настройки видимости кнопок
    show_webapp_button BOOLEAN DEFAULT 1 NOT NULL,
    show_trial_button BOOLEAN DEFAULT 1 NOT NULL,
    show_referral_button BOOLEAN DEFAULT 1 NOT NULL,
    show_support_button BOOLEAN DEFAULT 1 NOT NULL,
    show_servers_button BOOLEAN DEFAULT 1 NOT NULL,
    show_agreement_button BOOLEAN DEFAULT 1 NOT NULL,
    show_offer_button BOOLEAN DEFAULT 1 NOT NULL,
    show_topup_button BOOLEAN DEFAULT 1 NOT NULL,
    
    -- Настройки триала
    trial_days INTEGER DEFAULT 3 NOT NULL,
    
    -- Тексты переводов (JSON)
    translations_ru TEXT,
    translations_ua TEXT,
    translations_en TEXT,
    translations_cn TEXT,
    
    -- Кастомные сообщения
    welcome_message_ru TEXT,
    welcome_message_ua TEXT,
    welcome_message_en TEXT,
    welcome_message_cn TEXT,
    
    -- Документы
    user_agreement_ru TEXT,
    user_agreement_ua TEXT,
    user_agreement_en TEXT,
    user_agreement_cn TEXT,
    
    offer_text_ru TEXT,
    offer_text_ua TEXT,
    offer_text_en TEXT,
    offer_text_cn TEXT,
    
    -- Структура меню
    menu_structure TEXT,
    
    -- Проверка подписки на канал
    require_channel_subscription BOOLEAN DEFAULT 0 NOT NULL,
    channel_id VARCHAR(100),
    channel_url VARCHAR(500),
    channel_subscription_text_ru TEXT,
    channel_subscription_text_ua TEXT,
    channel_subscription_text_en TEXT,
    channel_subscription_text_cn TEXT,
    
    -- Ссылка на бота для Mini App
    bot_link_for_miniapp VARCHAR(500),
    
    -- Порядок кнопок
    buttons_order TEXT,
    
    -- Дата обновления
    updated_at DATETIME
);
"""

# Дополнительные колонки для существующей таблицы
NEW_COLUMNS = [
    ("require_channel_subscription", "BOOLEAN DEFAULT 0 NOT NULL"),
    ("channel_id", "VARCHAR(100)"),
    ("channel_url", "VARCHAR(500)"),
    ("channel_subscription_text_ru", "TEXT"),
    ("channel_subscription_text_ua", "TEXT"),
    ("channel_subscription_text_en", "TEXT"),
    ("channel_subscription_text_cn", "TEXT"),
    ("bot_link_for_miniapp", "VARCHAR(500)"),
    ("buttons_order", "TEXT"),
]

db_path = find_database()
if not db_path:
    print("❌ База данных не найдена.")
    sys.exit(1)

print(f"📦 Найдена база данных: {db_path.absolute()}")

conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

try:
    # Проверяем, существует ли таблица
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bot_config'")
    table_exists = cursor.fetchone() is not None
    
    if table_exists:
        print("✅ Таблица bot_config уже существует")
        
        # Проверяем колонки на случай, если таблица неполная
        cursor.execute("PRAGMA table_info(bot_config)")
        columns = [row[1] for row in cursor.fetchall()]
        print(f"📋 Существующие колонки: {', '.join(columns)}")
        
        # Добавляем недостающие колонки
        for col_name, col_type in NEW_COLUMNS:
            if col_name not in columns:
                print(f"➕ Добавляем колонку {col_name}...")
                try:
                    # SQLite не поддерживает DEFAULT в ALTER TABLE для NOT NULL
                    if "NOT NULL" in col_type:
                        # Сначала добавляем без NOT NULL и DEFAULT
                        clean_type = col_type.replace("NOT NULL", "").replace("DEFAULT 0", "").strip()
                        cursor.execute(f"ALTER TABLE bot_config ADD COLUMN {col_name} {clean_type}")
                        # Потом обновляем значение
                        cursor.execute(f"UPDATE bot_config SET {col_name} = 0 WHERE {col_name} IS NULL")
                    else:
                        cursor.execute(f"ALTER TABLE bot_config ADD COLUMN {col_name} {col_type}")
                    conn.commit()
                    print(f"✅ Колонка {col_name} добавлена")
                except sqlite3.OperationalError as e:
                    if "duplicate column name" in str(e).lower():
                        print(f"⏭ Колонка {col_name} уже существует")
                    else:
                        raise e
    else:
        print("➕ Создаём таблицу bot_config...")
        cursor.execute(CREATE_TABLE_SQL)
        conn.commit()
        print("✅ Таблица bot_config создана!")
        
        # Создаём запись по умолчанию
        cursor.execute("""
            INSERT INTO bot_config (id, service_name, trial_days) 
            VALUES (1, 'StealthNET', 3)
        """)
        conn.commit()
        print("✅ Создана запись по умолчанию")
    
    print()
    print("✅ Миграция успешно завершена!")
    
except sqlite3.Error as e:
    print(f"❌ Ошибка при выполнении миграции: {e}")
    conn.rollback()
    sys.exit(1)
finally:
    conn.close()

