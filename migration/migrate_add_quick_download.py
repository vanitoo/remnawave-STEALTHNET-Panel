#!/usr/bin/env python3
"""
Скрипт миграции для добавления полей быстрого скачивания в таблицу branding_setting.

Использование:
    python3 migration/migrate_add_quick_download.py
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

# Колонки для добавления
QUICK_DOWNLOAD_COLUMNS = [
    ('quick_download_enabled', 'BOOLEAN', '1'),  # TRUE по умолчанию
    ('quick_download_windows_url', 'VARCHAR(500)', None),
    ('quick_download_android_url', 'VARCHAR(500)', None),
    ('quick_download_macos_url', 'VARCHAR(500)', None),
    ('quick_download_ios_url', 'VARCHAR(500)', None),
    ('quick_download_profile_deeplink', 'VARCHAR(200)', "'stealthnet://install-config?url='"),  # Deeplink схема
]

db_path = find_database()
if not db_path:
    print("❌ База данных не найдена.")
    sys.exit(1)

print(f"📦 Найдена база данных: {db_path.absolute()}")

conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

try:
    cursor.execute("PRAGMA table_info(branding_setting)")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"📋 Существующие колонки в branding_setting: {', '.join(columns)}")
    print()
    
    changes_made = False
    
    for col_name, col_type, default_value in QUICK_DOWNLOAD_COLUMNS:
        if col_name not in columns:
            print(f"➕ Добавляем колонку {col_name}...")
            if default_value is not None:
                cursor.execute(f"ALTER TABLE branding_setting ADD COLUMN {col_name} {col_type} DEFAULT {default_value}")
            else:
                cursor.execute(f"ALTER TABLE branding_setting ADD COLUMN {col_name} {col_type}")
            print(f"✓ Колонка {col_name} добавлена")
            changes_made = True
        else:
            print(f"✓ Колонка {col_name} уже существует")
    
    if changes_made:
        conn.commit()
        print()
        print("✅ Миграция успешно завершена!")
    else:
        print()
        print("✅ Все необходимые колонки уже существуют.")
    
    print()
    cursor.execute("PRAGMA table_info(branding_setting)")
    final_columns = [row[1] for row in cursor.fetchall()]
    print(f"📋 Финальные колонки в branding_setting: {', '.join(final_columns)}")
    
except sqlite3.Error as e:
    print(f"❌ Ошибка при выполнении миграции: {e}")
    conn.rollback()
    sys.exit(1)
finally:
    conn.close()
