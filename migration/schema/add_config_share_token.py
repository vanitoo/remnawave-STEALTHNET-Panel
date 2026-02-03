#!/usr/bin/env python3
"""
Миграция: Добавление таблицы config_share_token для обмена конфигами
"""

import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.core import get_db
from modules.models.config_share import ConfigShareToken

def migrate(app_instance=None):
    """Создать таблицу config_share_token"""
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
            # Проверяем, существует ли таблица
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            
            # Если таблица не существует, создаем её через create_all
            if 'config_share_token' not in tables:
                db.create_all()
                print("✅ Таблица config_share_token создана")
            else:
                print("ℹ️  Таблица config_share_token уже существует")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Ошибка миграции: {e}")
            import traceback
            traceback.print_exc()
            raise

if __name__ == '__main__':
    migrate()
