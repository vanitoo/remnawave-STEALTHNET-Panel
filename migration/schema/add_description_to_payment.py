#!/usr/bin/env python3
"""
Миграция: добавить поле description в payment.

Используется для служебной информации о платеже (например OPTION:{option_id}).
"""

from flask import Flask
from modules.core import init_app, get_db


def add_description_to_payment(app=None):
    """Добавить колонку description в payment (если ещё нет)"""
    if app is None:
        app = Flask(__name__)
    # См. комментарий в add_purchase_options_table.py: расширения могут быть переинициализированы другими миграциями.
    init_app(app)

    with app.app_context():
        db = get_db()
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('payment')]

            if 'description' in columns:
                print("ℹ️  Поле description уже существует в таблице payment")
                return True

            db.session.execute(text("""
                ALTER TABLE payment
                ADD COLUMN description TEXT NULL
            """))
            db.session.commit()
            print("✅ Поле description добавлено в таблицу payment")
            return True
        except Exception as e:
            error_msg = str(e).lower()
            if 'already exists' in error_msg or 'существует' in error_msg or 'duplicate' in error_msg:
                print("ℹ️  Поле description уже существует в таблице payment")
                return True
            print(f"❌ Ошибка при добавлении поля description: {e}")
            db.session.rollback()
            return False


if __name__ == "__main__":
    add_description_to_payment()

