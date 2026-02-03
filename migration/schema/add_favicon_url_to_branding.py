#!/usr/bin/env python3
"""
Скрипт для добавления поля favicon_url в таблицу branding_setting
"""
from flask import Flask
from modules.core import init_app, get_db
from sqlalchemy import text, inspect

app = Flask(__name__)
init_app(app)

with app.app_context():
    db = get_db()
    
    try:
        # Проверяем, существует ли поле favicon_url
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('branding_setting')]
        
        if 'favicon_url' in columns:
            print("✅ Поле favicon_url уже существует в таблице branding_setting")
        else:
            print("📝 Добавляем поле favicon_url в таблицу branding_setting...")
            
            # Добавляем поле favicon_url
            db.session.execute(text("""
                ALTER TABLE branding_setting 
                ADD COLUMN favicon_url VARCHAR(500) NULL
            """))
            
            db.session.commit()
            print("✅ Поле favicon_url успешно добавлено")
            
    except Exception as e:
        db.session.rollback()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

