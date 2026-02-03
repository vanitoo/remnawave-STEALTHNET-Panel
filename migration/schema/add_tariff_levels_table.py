"""
Миграция: Добавление таблицы tariff_level для управления уровнями тарифов
"""

import sys
import os

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask
from modules.core import init_app, get_db
from modules.models.tariff_level import TariffLevel


def add_tariff_levels_table(app=None):
    """Создание таблицы tariff_level и инициализация базовых уровней"""
    if app is None:
        app = Flask(__name__)
        init_app(app)
    db = get_db()

    with app.app_context():
        try:
            db.create_all()
            print("✅ Таблица tariff_level создана")

            # Базовые уровни
            defaults = [
                dict(code='basic', name='Базовый', display_order=1),
                dict(code='pro', name='Премиум', display_order=2),
                dict(code='elite', name='Элитный', display_order=3),
            ]

            for item in defaults:
                existing = TariffLevel.query.filter_by(code=item['code']).first()
                if existing:
                    continue
                level = TariffLevel(
                    code=item['code'],
                    name=item['name'],
                    display_order=item['display_order'],
                    is_default=True,
                    is_active=True
                )
                db.session.add(level)
                print(f"✅ Создан уровень '{item['code']}'")

            db.session.commit()
            print("✅ Миграция tariff_level завершена успешно")
            return True

        except Exception as e:
            db.session.rollback()
            print(f"❌ Ошибка миграции tariff_level: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == '__main__':
    success = add_tariff_levels_table()
    sys.exit(0 if success else 1)

