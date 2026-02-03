#!/usr/bin/env python3
"""
Миграция: Добавление полей YooMoney в таблицу payment_setting

- yoomoney_receiver (TEXT)
- yoomoney_notification_secret (TEXT)
"""

import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.core import get_db


def migrate(app_instance=None):
    """Добавить поля YooMoney в таблицу payment_setting"""
    if app_instance is None:
        from app import app as app_instance

    with app_instance.app_context():
        db = app_instance.extensions.get('sqlalchemy')
        if db is None:
            db = get_db()

        try:
            from sqlalchemy import inspect, text

            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('payment_setting')]

            if 'yoomoney_receiver' not in columns:
                db.session.execute(text("ALTER TABLE payment_setting ADD COLUMN yoomoney_receiver TEXT"))
                db.session.commit()
                print("✅ Поле yoomoney_receiver добавлено в таблицу payment_setting")
            else:
                print("ℹ️  Поле yoomoney_receiver уже существует")

            if 'yoomoney_notification_secret' not in columns:
                db.session.execute(text("ALTER TABLE payment_setting ADD COLUMN yoomoney_notification_secret TEXT"))
                db.session.commit()
                print("✅ Поле yoomoney_notification_secret добавлено в таблицу payment_setting")
            else:
                print("ℹ️  Поле yoomoney_notification_secret уже существует")

        except Exception as e:
            db.session.rollback()
            if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower() or 'существует' in str(e).lower():
                print("ℹ️  Поля YooMoney уже существуют")
            else:
                print(f"❌ Ошибка миграции: {e}")
                import traceback
                traceback.print_exc()
                raise


if __name__ == '__main__':
    migrate()

