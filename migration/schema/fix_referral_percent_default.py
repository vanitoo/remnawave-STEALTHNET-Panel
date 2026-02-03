#!/usr/bin/env python3
"""
Миграция: делает referral_percent "динамическим"

Исторически referral_percent добавлялся с DEFAULT 10.0 (server default),
из-за чего новые пользователи получали фиксированное значение и не следовали
глобальному default_referral_percent из referral_setting.

Правильная логика:
- user.referral_percent = NULL  -> использовать referral_setting.default_referral_percent (динамически)
- user.referral_percent = число -> индивидуальный процент
"""

import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.core import get_db


def migrate(app_instance=None):
    if app_instance is None:
        from app import app as app_instance

    with app_instance.app_context():
        db = app_instance.extensions.get('sqlalchemy')
        if db is None:
            db = get_db()

        try:
            from sqlalchemy import inspect, text

            inspector = inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('user')]
            if 'referral_percent' not in columns:
                print("ℹ️  Поле referral_percent отсутствует в таблице user (пропуск)")
                return

            # PostgreSQL: DROP DEFAULT
            # (в SQLite ALTER COLUMN может не поддерживаться, поэтому просто логируем и пропускаем)
            try:
                db.session.execute(text('ALTER TABLE "user" ALTER COLUMN referral_percent DROP DEFAULT'))
                db.session.commit()
                print("✅ referral_percent: DEFAULT удалён (будет NULL по умолчанию)")
            except Exception as e:
                db.session.rollback()
                # Некоторые СУБД/схемы могут выдавать ошибки "нет default" или несовместимый синтаксис
                msg = str(e).lower()
                if 'does not exist' in msg or 'no default' in msg or 'syntax' in msg or 'near' in msg:
                    print(f"ℹ️  referral_percent: не удалось DROP DEFAULT (возможно уже удалён/не поддерживается): {str(e)[:120]}")
                else:
                    print(f"⚠️  referral_percent: ошибка DROP DEFAULT: {e}")
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            print(f"❌ Ошибка миграции fix_referral_percent_default: {e}")
            import traceback
            traceback.print_exc()
            raise


if __name__ == '__main__':
    migrate()

