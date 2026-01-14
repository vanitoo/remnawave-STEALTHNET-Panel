#!/usr/bin/env python3
"""
Скрипт миграции для добавления полей цветовой темы в таблицу system_setting.
Добавляет все колонки для полной кастомизации светлой и тёмной темы.

Использование:
    python3 migration/migrate_add_theme_colors.py
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

# Определяем колонки для добавления
THEME_COLUMNS = [
    # Светлая тема
    ('theme_primary_color', '#3f69ff'),
    ('theme_bg_primary', '#f8fafc'),
    ('theme_bg_secondary', '#eef2ff'),
    ('theme_text_primary', '#0f172a'),
    ('theme_text_secondary', '#64748b'),
    # Тёмная тема
    ('theme_primary_color_dark', '#6c7bff'),
    ('theme_bg_primary_dark', '#050816'),
    ('theme_bg_secondary_dark', '#0f172a'),
    ('theme_text_primary_dark', '#e2e8f0'),
    ('theme_text_secondary_dark', '#94a3b8'),
]

db_path = find_database()
if not db_path:
    print("❌ База данных не найдена.")
    sys.exit(1)

print(f"📦 Найдена база данных: {db_path.absolute()}")

conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

try:
    cursor.execute("PRAGMA table_info(system_setting)")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"📋 Существующие колонки в system_setting: {', '.join(columns)}")
    print()
    
    changes_made = False
    
    for col_name, default_value in THEME_COLUMNS:
        if col_name not in columns:
            print(f"➕ Добавляем колонку {col_name}...")
            cursor.execute(f"ALTER TABLE system_setting ADD COLUMN {col_name} VARCHAR(20) DEFAULT '{default_value}'")
            cursor.execute(f"UPDATE system_setting SET {col_name} = '{default_value}' WHERE {col_name} IS NULL")
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
    cursor.execute("PRAGMA table_info(system_setting)")
    final_columns = [row[1] for row in cursor.fetchall()]
    print(f"📋 Финальные колонки в system_setting: {', '.join(final_columns)}")
    
except sqlite3.Error as e:
    print(f"❌ Ошибка при выполнении миграции: {e}")
    conn.rollback()
    sys.exit(1)
finally:
    conn.close()
