#!/usr/bin/env python3
"""
Миграция: создание таблицы purchase_options.

Используется модуль "опции" (traffic/devices/squad).
"""

from flask import Flask
from modules.core import init_app, get_db


def add_purchase_options_table(app=None):
    """Создать таблицу purchase_options (если ещё нет)"""
    if app is None:
        app = Flask(__name__)
    # Важно: в проекте некоторые миграции создают свой app и вызывают init_app(),
    # что меняет глобальные инстансы расширений. Поэтому здесь явно инициализируем app.
    init_app(app)

    # важно: импорт модели внутри функции (после init_app/app_context)
    with app.app_context():
        db = get_db()
        from modules.models.option import PurchaseOption  # noqa: F401
        try:
            db.create_all()
            print("✅ Таблица purchase_options создана")
            return True
        except Exception as e:
            db.session.rollback()
            print(f"❌ Ошибка создания таблицы purchase_options: {e}")
            return False


if __name__ == "__main__":
    add_purchase_options_table()

