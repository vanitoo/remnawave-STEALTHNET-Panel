#!/usr/bin/env python3
"""
Скрипт миграции для добавления полей active_languages и active_currencies в таблицу system_setting.
Добавляет колонки для управления активными языками и валютами.

Использование:
    python3 migration/migrate_add_active_languages_currencies.py
"""

import sqlite3
import os
import sys
import json
from pathlib import Path
from datetime import datetime

def find_database():
    """Находит путь к базе данных"""
    # Сначала пробуем найти через переменные окружения или стандартные пути
    possible_paths = [
        Path('instance/stealthnet.db'),
        Path('stealthnet.db'),
        Path('/var/www/stealthnet-api/instance/stealthnet.db'),
        Path('/var/www/stealthnet-api/stealthnet.db'),
    ]
    
    # Если есть .env, пробуем прочитать путь из него
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
    
    # Ищем в стандартных путях
    for db_path in possible_paths:
        if db_path.exists():
            return db_path
    
    return None

# Находим базу данных
db_path = find_database()
if not db_path:
    print("❌ База данных не найдена. Проверьте следующие пути:")
    for p in [Path('instance/stealthnet.db'), Path('stealthnet.db')]:
        print(f"   - {p.absolute()}")
    sys.exit(1)

print(f"📦 Найдена база данных: {db_path.absolute()}")

# Подключаемся к базе данных
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

try:
    # Проверяем существующие колонки
    cursor.execute("PRAGMA table_info(system_setting)")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"📋 Существующие колонки в system_setting: {', '.join(columns)}")
    print()
    
    changes_made = False
    
    # Значения по умолчанию
    default_languages = json.dumps(["ru", "ua", "en", "cn"])
    default_currencies = json.dumps(["uah", "rub", "usd"])
    
    # Колонка active_languages
    if 'active_languages' not in columns:
        print(f"➕ Добавляем колонку active_languages...")
        # В SQLite нельзя использовать параметризованные запросы в ALTER TABLE с DEFAULT
        # Добавляем колонку без DEFAULT, затем обновим значения
        cursor.execute("ALTER TABLE system_setting ADD COLUMN active_languages TEXT")
        print(f"✓ Колонка active_languages добавлена")
        changes_made = True
        
        # Обновляем все существующие записи
        cursor.execute("UPDATE system_setting SET active_languages = ?", (default_languages,))
        print(f"✓ Существующие записи обновлены (установлены все языки по умолчанию)")
    else:
        print(f"✓ Колонка active_languages уже существует")
        # Проверяем, что значение не пустое
        cursor.execute("SELECT active_languages FROM system_setting LIMIT 1")
        result = cursor.fetchone()
        if result and (not result[0] or result[0].strip() == ''):
            cursor.execute("UPDATE system_setting SET active_languages = ?", (default_languages,))
            conn.commit()
            print(f"✓ Обновлено пустое значение active_languages")
    
    # Колонка active_currencies
    if 'active_currencies' not in columns:
        print(f"➕ Добавляем колонку active_currencies...")
        # В SQLite нельзя использовать параметризованные запросы в ALTER TABLE с DEFAULT
        # Добавляем колонку без DEFAULT, затем обновим значения
        cursor.execute("ALTER TABLE system_setting ADD COLUMN active_currencies TEXT")
        print(f"✓ Колонка active_currencies добавлена")
        changes_made = True
        
        # Обновляем все существующие записи
        cursor.execute("UPDATE system_setting SET active_currencies = ?", (default_currencies,))
        print(f"✓ Существующие записи обновлены (установлены все валюты по умолчанию)")
    else:
        print(f"✓ Колонка active_currencies уже существует")
        # Проверяем, что значение не пустое
        cursor.execute("SELECT active_currencies FROM system_setting LIMIT 1")
        result = cursor.fetchone()
        if result and (not result[0] or result[0].strip() == ''):
            cursor.execute("UPDATE system_setting SET active_currencies = ?", (default_currencies,))
            conn.commit()
            print(f"✓ Обновлено пустое значение active_currencies")
    
    # Сохраняем изменения
    if changes_made:
        conn.commit()
        print()
        print("✅ Миграция успешно завершена!")
        
        # Создаем резервную копию после успешной миграции
        backup_path = f"{db_path}.backup_{int(datetime.now().timestamp())}"
        import shutil
        shutil.copy2(db_path, backup_path)
        print(f"📝 Резервная копия сохранена: {backup_path}")
    else:
        print()
        print("✅ Все необходимые колонки уже существуют. Миграция не требуется.")
    
    # Показываем финальную структуру таблицы
    print()
    cursor.execute("PRAGMA table_info(system_setting)")
    final_columns = [row[1] for row in cursor.fetchall()]
    print(f"📋 Финальные колонки в system_setting: {', '.join(final_columns)}")
    
    # Показываем текущие значения
    cursor.execute("SELECT active_languages, active_currencies FROM system_setting LIMIT 1")
    result = cursor.fetchone()
    if result:
        print(f"📋 Текущие значения:")
        print(f"   active_languages: {result[0]}")
        print(f"   active_currencies: {result[1]}")
    
except sqlite3.Error as e:
    print(f"❌ Ошибка при выполнении миграции: {e}")
    conn.rollback()
    sys.exit(1)
finally:
    conn.close()
