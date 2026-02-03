#!/usr/bin/env python3
"""
Скрипт для добавления поля yookassa_receipt_required в таблицу payment_setting
"""
from flask import Flask
from modules.core import init_app, get_db
from sqlalchemy import text, inspect

app = Flask(__name__)
init_app(app)

with app.app_context():
    db = get_db()
    
    try:
        # Проверяем, существует ли поле
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('payment_setting')]
        
        if 'yookassa_receipt_required' in columns:
            print("✅ Поле yookassa_receipt_required уже существует")
        else:
            # Добавляем поле
            print("Добавление поля yookassa_receipt_required...")
            db.session.execute(text("""
                ALTER TABLE payment_setting 
                ADD COLUMN yookassa_receipt_required BOOLEAN DEFAULT FALSE NOT NULL
            """))
            db.session.commit()
            print("✅ Поле yookassa_receipt_required успешно добавлено")
            
    except Exception as e:
        db.session.rollback()
        print(f"❌ Ошибка при добавлении поля: {e}")
        import traceback
        traceback.print_exc()
