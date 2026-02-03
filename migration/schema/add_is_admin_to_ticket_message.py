#!/usr/bin/env python3
"""
Скрипт для добавления поля is_admin в таблицу ticket_message
"""
from flask import Flask
from modules.core import init_app, get_db
from sqlalchemy import text

app = Flask(__name__)
init_app(app)

with app.app_context():
    db = get_db()
    
    try:
        # Проверяем, существует ли поле is_admin
        inspector = db.inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('ticket_message')]
        
        if 'is_admin' in columns:
            print("✅ Поле is_admin уже существует в таблице ticket_message")
        else:
            print("📝 Добавляем поле is_admin в таблицу ticket_message...")
            
            # Добавляем поле is_admin
            db.session.execute(text("""
                ALTER TABLE ticket_message 
                ADD COLUMN is_admin BOOLEAN DEFAULT FALSE
            """))
            
            # Обновляем существующие записи: если sender.role = 'ADMIN', то is_admin = TRUE
            db.session.execute(text("""
                UPDATE ticket_message 
                SET is_admin = TRUE 
                WHERE sender_id IN (
                    SELECT id FROM "user" WHERE role = 'ADMIN'
                )
            """))
            
            db.session.commit()
            print("✅ Поле is_admin успешно добавлено")
            
    except Exception as e:
        db.session.rollback()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

