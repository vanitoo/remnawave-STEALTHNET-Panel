#!/usr/bin/env python3
"""
Миграция: Добавление таблицы user_config для хранения нескольких конфигов пользователя
"""
import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.core import get_db
from modules.models.user_config import UserConfig

def migrate(app_instance=None):
    """Создать таблицу user_config"""
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
            if 'user_config' not in tables:
                db.create_all()
                print("✅ Таблица user_config создана")
            else:
                print("ℹ️  Таблица user_config уже существует")
            
            # Мигрируем существующих пользователей: создаем UserConfig для каждого пользователя с remnawave_uuid
            from modules.models.user import User
            
            users_with_uuid = User.query.filter(User.remnawave_uuid.isnot(None), User.remnawave_uuid != '').all()
            
            migrated_count = 0
            for user in users_with_uuid:
                # Проверяем, есть ли уже конфиг для этого пользователя
                existing_config = UserConfig.query.filter_by(user_id=user.id, remnawave_uuid=user.remnawave_uuid).first()
                if not existing_config:
                    # Создаем основной конфиг
                    primary_config = UserConfig(
                        user_id=user.id,
                        remnawave_uuid=user.remnawave_uuid,
                        config_name='Основной конфиг',
                        is_primary=True
                    )
                    db.session.add(primary_config)
                    migrated_count += 1
                    print(f"✅ Создан основной конфиг для пользователя {user.id} (telegram_id: {user.telegram_id})")
            
            if migrated_count > 0:
                db.session.commit()
                print(f"✅ Мигрировано {migrated_count} пользователей")
            else:
                print("ℹ️  Все пользователи уже имеют конфиги")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Ошибка миграции: {e}")
            import traceback
            traceback.print_exc()
            raise

if __name__ == '__main__':
    migrate()
