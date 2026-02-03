#!/usr/bin/env python3
"""
Миграция: Добавление таблицы email_setting для настроек почты (имя отправителя и шаблоны писем).
"""
import sys
import os

root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, root)

from modules.core import get_db
from modules.models.email_setting import EmailSetting


def migrate(app_instance=None):
    """Создать таблицу email_setting."""
    if app_instance is None:
        from app import app as app_instance

    with app_instance.app_context():
        db = app_instance.extensions.get('sqlalchemy') or get_db()
        try:
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            if 'email_setting' not in tables:
                db.create_all()
                print("✅ Таблица email_setting создана")
            else:
                print("ℹ️  Таблица email_setting уже существует")
        except Exception as e:
            print(f"❌ Ошибка миграции: {e}")
            raise


if __name__ == '__main__':
    migrate()
