#!/usr/bin/env python3
"""
Скрипт для добавления поля referral_percent в таблицу user
"""
from flask import Flask
from modules.core import init_app, get_db
from sqlalchemy import text, inspect

app = Flask(__name__)
init_app(app)

with app.app_context():
    db = get_db()
    
    try:
        # Проверяем, существует ли поле referral_percent
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('user')]
        
        if 'referral_percent' in columns:
            print("✅ Поле referral_percent уже существует в таблице user")
        else:
            print("📝 Добавляем поле referral_percent в таблицу user...")
            
            # Добавляем поле referral_percent
            db.session.execute(text("""
                ALTER TABLE user 
                ADD COLUMN referral_percent REAL DEFAULT 10.0
            """))
            
            db.session.commit()
            print("✅ Поле referral_percent успешно добавлено")
            
    except Exception as e:
        db.session.rollback()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

