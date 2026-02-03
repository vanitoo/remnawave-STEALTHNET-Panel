#!/usr/bin/env python3
"""
Миграция: Добавление поля platega_mir_enabled в таблицу payment_setting

Нужно для включения отдельного метода оплаты "Карты МИР" (Platega paymentMethod=11).
"""

import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.core import get_db


def migrate(app_instance=None):
    """Добавить поле platega_mir_enabled в таблицу payment_setting"""
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

            if 'platega_mir_enabled' in columns:
                print("ℹ️  Поле platega_mir_enabled уже существует")
                return

            db.session.execute(text("""
                ALTER TABLE payment_setting
                ADD COLUMN platega_mir_enabled BOOLEAN DEFAULT FALSE NOT NULL
            """))
            db.session.commit()
            print("✅ Поле platega_mir_enabled добавлено в таблицу payment_setting")

        except Exception as e:
            db.session.rollback()
            if 'already exists' in str(e).lower() or 'duplicate' in str(e).lower() or 'существует' in str(e).lower():
                print("ℹ️  Поле platega_mir_enabled уже существует")
            else:
                print(f"❌ Ошибка миграции: {e}")
                import traceback
                traceback.print_exc()
                raise


if __name__ == '__main__':
    migrate()

