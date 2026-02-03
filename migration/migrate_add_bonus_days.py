#!/usr/bin/env python3
"""
Скрипт миграции для добавления поля bonus_days в таблицу tariff.
Добавляет колонку bonus_days для хранения бонусных дней тарифа.

Использование:
    python3 migration/migrate_add_bonus_days.py
"""

import sqlite3
import os
import sys
from pathlib import Path
from datetime import datetime

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

db_path = find_database()
if not db_path:
    print("❌ База данных не найдена.")
    sys.exit(1)

print(f"📦 Найдена база данных: {db_path.absolute()}")

conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

try:
    cursor.execute("PRAGMA table_info(tariff)")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"📋 Существующие колонки в tariff: {', '.join(columns)}")
    print()
    
    changes_made = False
    
    if 'bonus_days' not in columns:
        print(f"➕ Добавляем колонку bonus_days...")
        cursor.execute("ALTER TABLE tariff ADD COLUMN bonus_days INTEGER DEFAULT 0")
        print(f"✓ Колонка bonus_days добавлена")
        changes_made = True
    else:
        print(f"✓ Колонка bonus_days уже существует")
    
    if changes_made:
        conn.commit()
        print()
        print("✅ Миграция успешно завершена!")
        
        backup_path = f"{db_path}.backup_{int(datetime.now().timestamp())}"
        import shutil
        shutil.copy2(db_path, backup_path)
        print(f"📝 Резервная копия сохранена: {backup_path}")
    else:
        print()
        print("✅ Все необходимые колонки уже существуют.")
    
    print()
    cursor.execute("PRAGMA table_info(tariff)")
    final_columns = [row[1] for row in cursor.fetchall()]
    print(f"📋 Финальные колонки в tariff: {', '.join(final_columns)}")
    
except sqlite3.Error as e:
    print(f"❌ Ошибка при выполнении миграции: {e}")
    conn.rollback()
    sys.exit(1)
finally:
    conn.close()

