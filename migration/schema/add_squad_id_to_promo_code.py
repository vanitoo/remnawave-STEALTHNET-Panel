#!/usr/bin/env python3
"""
Скрипт для добавления поля squad_id в таблицу promo_code
"""
from flask import Flask
from modules.core import init_app, get_db
from sqlalchemy import text

app = Flask(__name__)
init_app(app)

with app.app_context():
    db = get_db()
    
    try:
        # Проверяем, существует ли поле squad_id
        inspector = db.inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('promo_code')]
        
        if 'squad_id' in columns:
            print("✅ Поле squad_id уже существует в таблице promo_code")
        else:
            print("📝 Добавляем поле squad_id в таблицу promo_code...")
            
            # Добавляем поле squad_id
            db.session.execute(text("""
                ALTER TABLE promo_code 
                ADD COLUMN squad_id VARCHAR(100) NULL
            """))
            
            db.session.commit()
            print("✅ Поле squad_id успешно добавлено")
            
    except Exception as e:
        db.session.rollback()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

