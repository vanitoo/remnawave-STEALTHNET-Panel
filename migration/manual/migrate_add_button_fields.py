#!/usr/bin/env python3
"""
Скрипт для добавления полей кнопок в таблицу auto_broadcast_message
Поля: button_text, button_url, button_action

Использование:
    python3 migrate_add_button_fields.py
    или
    docker exec stealthnet-api python3 migrate_add_button_fields.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.core import get_db, get_app

app = get_app()
db = get_db()

with app.app_context():
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        
        # Проверяем существование таблицы
        tables = inspector.get_table_names()
        if 'auto_broadcast_message' not in tables:
            print("❌ Ошибка: Таблица auto_broadcast_message не существует!")
            print("ℹ️  Сначала запустите скрипт add_auto_broadcast_table.py")
            sys.exit(1)
        
        columns = [col['name'] for col in inspector.get_columns('auto_broadcast_message')]
        
        added = []
        
        # Добавляем button_text
        if 'button_text' not in columns:
            try:
                db.session.execute(text("""
                    ALTER TABLE auto_broadcast_message 
                    ADD COLUMN button_text VARCHAR(100) NULL
                """))
                added.append('button_text')
                print("✅ Добавлено поле: button_text")
            except Exception as e:
                if 'already exists' not in str(e).lower() and 'существует' not in str(e).lower():
                    print(f"⚠️  Ошибка при добавлении button_text: {e}")
        else:
            print("ℹ️  Поле button_text уже существует")
        
        # Добавляем button_url
        if 'button_url' not in columns:
            try:
                db.session.execute(text("""
                    ALTER TABLE auto_broadcast_message 
                    ADD COLUMN button_url VARCHAR(255) NULL
                """))
                added.append('button_url')
                print("✅ Добавлено поле: button_url")
            except Exception as e:
                if 'already exists' not in str(e).lower() and 'существует' not in str(e).lower():
                    print(f"⚠️  Ошибка при добавлении button_url: {e}")
        else:
            print("ℹ️  Поле button_url уже существует")
        
        # Добавляем button_action
        if 'button_action' not in columns:
            try:
                db.session.execute(text("""
                    ALTER TABLE auto_broadcast_message 
                    ADD COLUMN button_action VARCHAR(50) NULL
                """))
                added.append('button_action')
                print("✅ Добавлено поле: button_action")
            except Exception as e:
                if 'already exists' not in str(e).lower() and 'существует' not in str(e).lower():
                    print(f"⚠️  Ошибка при добавлении button_action: {e}")
        else:
            print("ℹ️  Поле button_action уже существует")
        
        if added:
            db.session.commit()
            print(f"\n✅ Миграция завершена! Добавлено полей: {', '.join(added)}")
        else:
            print("\n✅ Все поля уже существуют в таблице auto_broadcast_message")
                
    except Exception as e:
        error_msg = str(e).lower()
        print(f"❌ Ошибка при выполнении миграции: {e}")
        db.session.rollback()
        sys.exit(1)

