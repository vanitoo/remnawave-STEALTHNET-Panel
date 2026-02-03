#!/usr/bin/env python3
"""
Миграция: Добавление поля user_config_id в таблицу payment
"""
import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.core import get_db

def migrate(app_instance=None):
    """Добавить поле user_config_id в таблицу payment"""
    # Используем переданное приложение или импортируем из app
    if app_instance is None:
        from app import app as app_instance
    
    with app_instance.app_context():
        # Используем db из расширений приложения
        db = app_instance.extensions.get('sqlalchemy')
        if db is None:
            # Если db не найден в расширениях, используем get_db()
            db = get_db()
        
        try:
            # Проверяем, существует ли уже поле
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('payment')]
            
            if 'user_config_id' in columns:
                print("ℹ️  Поле user_config_id уже существует")
                return
            
            # Добавляем поле
            db.session.execute(text("ALTER TABLE payment ADD COLUMN user_config_id INTEGER"))
            db.session.commit()
            
            # Создаем индекс (проверяем существование для PostgreSQL)
            try:
                # Для PostgreSQL проверяем существование индекса
                from sqlalchemy import text as sql_text
                index_exists = False
                try:
                    result = db.session.execute(sql_text("""
                        SELECT 1 FROM pg_indexes 
                        WHERE indexname = 'idx_payment_user_config_id'
                    """))
                    index_exists = result.fetchone() is not None
                except:
                    # Если не PostgreSQL или ошибка, пробуем создать индекс
                    pass
                
                if not index_exists:
                    db.session.execute(sql_text("CREATE INDEX idx_payment_user_config_id ON payment(user_config_id)"))
                    db.session.commit()
                    print("✅ Индекс idx_payment_user_config_id создан")
            except Exception as idx_error:
                # Если индекс уже существует или ошибка, просто пропускаем
                if 'already exists' not in str(idx_error).lower() and 'duplicate' not in str(idx_error).lower():
                    print(f"⚠️  Предупреждение при создании индекса: {idx_error}")
            
            print("✅ Поле user_config_id добавлено в таблицу payment")
            
        except Exception as e:
            db.session.rollback()
            # Проверяем, может быть поле уже существует
            if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower() or 'существует' in str(e).lower():
                print("ℹ️  Поле user_config_id уже существует")
            else:
                print(f"❌ Ошибка миграции: {e}")
                import traceback
                traceback.print_exc()
                raise

if __name__ == '__main__':
    migrate()
