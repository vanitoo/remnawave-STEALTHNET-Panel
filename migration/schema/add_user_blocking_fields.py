#!/usr/bin/env python3
"""
Миграция: Добавление полей блокировки аккаунта в таблицу user
"""
import sys
import os
from sqlalchemy import inspect, text
from flask import Flask
from modules.core import init_app, get_db

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def add_user_blocking_fields():
    print("Запуск миграции: add_user_blocking_fields.py")
    app = Flask(__name__)
    init_app(app)

    with app.app_context():
        db = get_db()
        inspector = inspect(db.engine)
        
        try:
            columns = [col['name'] for col in inspector.get_columns('user')]
            
            # Добавляем is_blocked
            if 'is_blocked' not in columns:
                print("Добавляем поле is_blocked в таблицу user...")
                with db.engine.connect() as conn:
                    if db.engine.name == 'postgresql':
                        conn.execute(text('ALTER TABLE "user" ADD COLUMN is_blocked BOOLEAN DEFAULT FALSE NOT NULL'))
                    else:  # sqlite
                        conn.execute(text('ALTER TABLE user ADD COLUMN is_blocked BOOLEAN DEFAULT 0 NOT NULL'))
                    conn.commit()
                print("✅ Поле is_blocked добавлено в таблицу user")
            else:
                print("✅ Поле is_blocked уже существует в таблице user")
            
            # Добавляем block_reason
            if 'block_reason' not in columns:
                print("Добавляем поле block_reason в таблицу user...")
                with db.engine.connect() as conn:
                    if db.engine.name == 'postgresql':
                        conn.execute(text('ALTER TABLE "user" ADD COLUMN block_reason TEXT'))
                    else:  # sqlite
                        conn.execute(text('ALTER TABLE user ADD COLUMN block_reason TEXT'))
                    conn.commit()
                print("✅ Поле block_reason добавлено в таблицу user")
            else:
                print("✅ Поле block_reason уже существует в таблице user")
            
            # Добавляем blocked_at
            if 'blocked_at' not in columns:
                print("Добавляем поле blocked_at в таблицу user...")
                with db.engine.connect() as conn:
                    if db.engine.name == 'postgresql':
                        conn.execute(text('ALTER TABLE "user" ADD COLUMN blocked_at TIMESTAMP'))
                    else:  # sqlite
                        conn.execute(text('ALTER TABLE user ADD COLUMN blocked_at TIMESTAMP'))
                    conn.commit()
                print("✅ Поле blocked_at добавлено в таблицу user")
            else:
                print("✅ Поле blocked_at уже существует в таблице user")
                
        except Exception as e:
            print(f"❌ Ошибка при добавлении полей блокировки: {e}")
            import traceback
            traceback.print_exc()
            return False
        return True

if __name__ == '__main__':
    success = add_user_blocking_fields()
    sys.exit(0 if success else 1)

