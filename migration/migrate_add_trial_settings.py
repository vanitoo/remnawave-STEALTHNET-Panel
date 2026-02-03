#!/usr/bin/env python3
"""
Миграция: Добавление таблицы TrialSettings для настройки триального периода
"""
import sys
import os

# Добавляем корневую директорию проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.core import get_db
from modules.models.trial import TrialSettings

def migrate(app_instance=None):
    """Создать таблицу TrialSettings и добавить настройки по умолчанию"""
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
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            
            # Если таблица не существует, создаем её через create_all
            if 'trial_settings' not in tables:
                db.create_all()
                print("✅ Таблица TrialSettings создана")
            else:
                print("ℹ️  Таблица TrialSettings уже существует")
            
            # Проверяем, есть ли уже настройки
            existing = TrialSettings.query.first()
            if existing:
                print("✅ Настройки триала уже существуют")
                return
            
            # Создаём настройки по умолчанию
            default_settings = TrialSettings(
                days=3,
                devices=3,
                traffic_limit_bytes=0,
                enabled=True,
                title_ru='Получите {days} дней премиум',
                title_ua='Отримайте {days} днів преміум',
                title_en='Get {days} Days Premium',
                title_cn='获得 {days} 天高级版',
                description_ru='Дадим полный доступ без ограничений — протестируйте сеть перед оплатой.',
                description_ua='Дамо повний доступ без обмежень — протестуйте мережу перед оплатою.',
                description_en='We\'ll give you full access without restrictions — test the network before payment.',
                description_cn='我们将为您提供无限制的完全访问权限 — 在付款前测试网络。',
                button_text_ru='🎁 Попробовать бесплатно ({days} дня)',
                button_text_ua='🎁 Спробувати безкоштовно ({days} дні)',
                button_text_en='🎁 Try Free ({days} Days)',
                button_text_cn='🎁 免费试用 ({days} 天)',
                activation_message_ru='✅ Триал активирован! Вам добавлено {days} дней премиум-доступа.',
                activation_message_ua='✅ Тріал активовано! Вам додано {days} днів преміум-доступу.',
                activation_message_en='✅ Trial activated! You have been added {days} days of premium access.',
                activation_message_cn='✅ 试用已激活！您已获得 {days} 天的高级访问权限。'
            )
            
            db.session.add(default_settings)
            db.session.commit()
            print("✅ Настройки триала по умолчанию созданы")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Ошибка миграции: {e}")
            import traceback
            traceback.print_exc()
            raise

if __name__ == '__main__':
    migrate()
