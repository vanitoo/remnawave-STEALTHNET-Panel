#!/usr/bin/env python3
"""
Скрипт для добавления поля telegram_message_id в таблицу payment
Это поле используется для хранения message_id сообщения о создании платежа в Telegram боте
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.core import get_db, get_app

app = get_app()
db = get_db()

with app.app_context():
    try:
        # Проверяем, существует ли уже поле
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('payment')]
        
        if 'telegram_message_id' in columns:
            print("ℹ️  Поле telegram_message_id уже существует в таблице payment")
        else:
            # Добавляем поле
            db.session.execute(text("""
                ALTER TABLE payment 
                ADD COLUMN telegram_message_id INTEGER NULL
            """))
            db.session.commit()
            print("✅ Поле telegram_message_id добавлено в таблицу payment")
    except Exception as e:
        error_msg = str(e).lower()
        if 'already exists' in error_msg or 'существует' in error_msg or 'duplicate' in error_msg:
            print("ℹ️  Поле telegram_message_id уже существует в таблице payment")
        else:
            print(f"❌ Ошибка при добавлении поля: {e}")
            db.session.rollback()
            raise

