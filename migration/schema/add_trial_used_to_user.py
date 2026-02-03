#!/usr/bin/env python3
"""
Миграция: Добавление поля trial_used в таблицу user
"""
import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.core import get_db

def migrate(app_instance=None):
    """Добавить поле trial_used в таблицу user"""
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
            columns = [col['name'] for col in inspector.get_columns('user')]
            
            if 'trial_used' in columns:
                print("ℹ️  Поле trial_used уже существует")
                return
            
            # Добавляем поле
            db.session.execute(text("ALTER TABLE \"user\" ADD COLUMN trial_used BOOLEAN DEFAULT FALSE NOT NULL"))
            db.session.commit()
            print("✅ Поле trial_used добавлено в таблицу user")
            
        except Exception as e:
            db.session.rollback()
            # Проверяем, может быть поле уже существует
            if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower() or 'существует' in str(e).lower():
                print("ℹ️  Поле trial_used уже существует")
            else:
                print(f"❌ Ошибка миграции: {e}")
                import traceback
                traceback.print_exc()
                raise

if __name__ == '__main__':
    migrate()
