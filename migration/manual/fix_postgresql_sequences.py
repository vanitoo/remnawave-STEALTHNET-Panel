#!/usr/bin/env python3
"""
Скрипт для исправления последовательностей (sequences) в PostgreSQL
после миграции данных из SQLite
"""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

def fix_sequences(database_url=None):
    """
    Исправить все последовательности в PostgreSQL
    
    Args:
        database_url: URL базы данных (опционально, если None - берется из .env)
    
    Returns:
        bool: True если успешно
    """
    if not database_url:
        database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ DATABASE_URL не настроен в .env")
        return False
    
    # Проверяем, что это PostgreSQL
    if not database_url.startswith('postgresql://') and not database_url.startswith('postgresql+psycopg2://'):
        print("ℹ️  База данных не PostgreSQL, исправление sequences не требуется")
        return True
    
    try:
        engine = create_engine(database_url)
        
        # Список таблиц с автоинкрементом
        tables = [
            ('user', 'id'),
            ('payment', 'id'),
            ('tariff', 'id'),
            ('promo_code', 'id'),
            ('ticket', 'id'),
            ('ticket_message', 'id'),
            ('system_setting', 'id'),
            ('branding_setting', 'id'),
            ('bot_config', 'id'),
            ('referral_setting', 'id'),
            ('currency_rate', 'id'),
            ('tariff_feature_setting', 'id'),
            ('payment_setting', 'id'),
        ]
        
        print("=" * 80)
        print("ИСПРАВЛЕНИЕ ПОСЛЕДОВАТЕЛЬНОСТЕЙ В POSTGRESQL")
        print("=" * 80)
        print()
        
        with engine.begin() as conn:  # Используем begin() для автоматического rollback
            for table_name, column_name in tables:
                try:
                    # Получаем максимальное значение ID
                    result = conn.execute(text(f'SELECT COALESCE(MAX({column_name}), 0) FROM "{table_name}"'))
                    max_id = result.scalar()
                    
                    # Если max_id = 0, устанавливаем на 1 (минимум для последовательности)
                    if max_id == 0:
                        max_id = 1
                    
                    # Обновляем последовательность
                    sequence_name = f'"{table_name}_{column_name}_seq"'
                    conn.execute(text(f"SELECT setval('{sequence_name}', {max_id}, true)"))
                    
                    print(f"✅ {table_name}.{column_name}: установлено на {max_id}")
                except Exception as e:
                    print(f"⚠️  {table_name}.{column_name}: ошибка - {e}")
                    # Rollback для этой транзакции и продолжаем
                    conn.rollback()
                    continue
        
        print()
        print("=" * 80)
        print("✅ ВСЕ ПОСЛЕДОВАТЕЛЬНОСТИ ОБНОВЛЕНЫ")
        print("=" * 80)
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

if __name__ == '__main__':
    success = fix_sequences()
    sys.exit(0 if success else 1)

