#!/usr/bin/env python3
"""
Скрипт автоматической миграции данных из SQLite в PostgreSQL
Выполняется автоматически при первом запуске с PostgreSQL
"""

import os
import sys
import sqlite3
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

# Загрузка переменных окружения
load_dotenv()

def get_sqlite_db_path():
    """Получить путь к SQLite базе данных"""
    # Проверяем несколько возможных путей
    possible_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'stealthnet.db'),  # instance/stealthnet.db (приоритет)
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stealthnet.db'),  # корень
    ]
    
    for db_path in possible_paths:
        if os.path.exists(db_path):
            return db_path
    
    return None

def get_postgresql_url():
    """Получить URL для PostgreSQL"""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    
    if os.getenv("DB_TYPE", "").lower() in ["postgresql", "postgres"]:
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "stealthnet")
        db_user = os.getenv("DB_USER", "stealthnet")
        db_password = os.getenv("DB_PASSWORD", "")
        
        if db_password:
            return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        else:
            return f"postgresql://{db_user}@{db_host}:{db_port}/{db_name}"
    
    return None

def check_migration_needed():
    """Проверить, нужна ли миграция"""
    sqlite_path = get_sqlite_db_path()
    postgresql_url = get_postgresql_url()
    
    if not sqlite_path:
        return False, "SQLite база данных не найдена"
    
    if not postgresql_url:
        return False, "PostgreSQL не настроен"
    
    # Проверяем, есть ли данные в SQLite
    try:
        conn = sqlite3.connect(sqlite_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        conn.close()
        
        if not tables:
            return False, "SQLite база данных пуста"
    except Exception as e:
        return False, f"Ошибка при проверке SQLite: {e}"
    
    # Проверяем, есть ли данные в PostgreSQL
    # ПРИМЕЧАНИЕ: Теперь разрешаем перемиграцию, если нужно
    # Можно добавить флаг FORCE_MIGRATION для принудительной миграции
    try:
        engine = create_engine(postgresql_url)
        inspector = inspect(engine)
        pg_tables = inspector.get_table_names()
        
        # Если в PostgreSQL уже есть таблицы с данными, проверяем количество
        if pg_tables:
            # Проверяем, есть ли данные в таблице user
            if 'user' in pg_tables:
                with engine.connect() as conn:
                    result = conn.execute(text('SELECT COUNT(*) FROM "user"')).scalar()
                    # Если данных меньше чем в SQLite, разрешаем миграцию
                    sqlite_conn = sqlite3.connect(sqlite_path)
                    sqlite_count = sqlite_conn.execute('SELECT COUNT(*) FROM user').fetchone()[0]
                    sqlite_conn.close()
                    
                    if result and result > 0:
                        if result < sqlite_count:
                            return True, f"В PostgreSQL меньше данных ({result} vs {sqlite_count} в SQLite)"
                        # Если данных больше или равно, миграция не нужна
                        return False, f"PostgreSQL уже содержит данные ({result} пользователей)"
    except Exception as e:
        # Если не можем подключиться, значит нужно создать таблицы
        pass
    
    return True, "Миграция необходима"

def migrate_data():
    """Выполнить миграцию данных из SQLite в PostgreSQL"""
    print("=" * 80)
    print("МИГРАЦИЯ ДАННЫХ: SQLite → PostgreSQL")
    print("=" * 80)
    print()
    
    # Проверка необходимости миграции
    needed, message = check_migration_needed()
    if not needed:
        print(f"ℹ️  {message}")
        return True
    
    sqlite_path = get_sqlite_db_path()
    postgresql_url = get_postgresql_url()
    
    if not sqlite_path or not postgresql_url:
        print("❌ Ошибка: SQLite или PostgreSQL не настроены")
        return False
    
    print(f"📖 SQLite база: {sqlite_path}")
    # Скрываем пароль в выводе
    display_url = postgresql_url
    if '@' in display_url:
        parts = display_url.split('@')
        if ':' in parts[0]:
            user_pass = parts[0].split('://')[1] if '://' in parts[0] else parts[0]
            if ':' in user_pass:
                user = user_pass.split(':')[0]
                display_url = postgresql_url.split('://')[0] + '://' + user + ':***@' + parts[1]
    print(f"📖 PostgreSQL: {display_url}")
    print()
    
    try:
        # Подключаемся к SQLite
        sqlite_conn = sqlite3.connect(sqlite_path)
        sqlite_conn.row_factory = sqlite3.Row
        
        # Подключаемся к PostgreSQL
        pg_engine = create_engine(postgresql_url)
        
        # Создаем таблицы в PostgreSQL через Flask приложение
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from flask import Flask
        from modules.core import init_app, get_db
        
        # Создаем Flask приложение для PostgreSQL ПЕРЕД импортом моделей
        pg_app = Flask(__name__)
        pg_app.config['SQLALCHEMY_DATABASE_URI'] = postgresql_url
        pg_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        # Инициализируем приложение
        init_app(pg_app)
        pg_db = get_db()
        
        # ТЕПЕРЬ импортируем модели (они будут использовать правильный db)
        with pg_app.app_context():
            # Импортируем модели после инициализации приложения
            from modules.models import (
                User, Payment, PaymentSetting, Tariff, PromoCode,
                Ticket, TicketMessage, SystemSetting, BrandingSetting,
                BotConfig, ReferralSetting, CurrencyRate, TariffFeatureSetting
            )
            
            # Создаем таблицы в PostgreSQL
            print("📋 Создание таблиц в PostgreSQL...")
            pg_db.create_all()
            print("✅ Таблицы созданы")
            print()
            
            # Список таблиц для миграции (в правильном порядке для внешних ключей)
            tables_order = [
                ('system_setting', SystemSetting),
                ('branding_setting', BrandingSetting),
                ('bot_config', BotConfig),
                ('referral_setting', ReferralSetting),
                ('currency_rate', CurrencyRate),
                ('tariff_feature_setting', TariffFeatureSetting),
                ('tariff', Tariff),
                ('promo_code', PromoCode),
                ('user', User),
                ('payment_setting', PaymentSetting),
                ('payment', Payment),
                ('ticket', Ticket),
                ('ticket_message', TicketMessage),
            ]
            
            total_migrated = 0
            existing_user_ids = set()  # Список существующих user_id для проверки внешних ключей
            
            for table_name, model in tables_order:
                try:
                    # Проверяем, существует ли таблица в SQLite
                    cursor = sqlite_conn.cursor()
                    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'")
                    if not cursor.fetchone():
                        print(f"   ⏭️  {table_name}: таблица не существует в SQLite")
                        continue
                    
                    # Получаем данные из SQLite
                    cursor.execute(f"SELECT * FROM {table_name}")
                    rows = cursor.fetchall()
                    
                    if not rows:
                        print(f"   ⏭️  {table_name}: нет данных")
                        continue
                    
                    print(f"   📦 {table_name}: {len(rows)} записей...")
                    
                    # Получаем названия колонок
                    columns = [description[0] for description in cursor.description]
                    
                    # Вставляем данные в PostgreSQL
                    migrated_count = 0
                    skipped_count = 0
                    for row in rows:
                        try:
                            # Создаем словарь из данных
                            data = {}
                            for i, col in enumerate(columns):
                                value = row[i]
                                # Обрабатываем None и специальные типы
                                if value is None:
                                    data[col] = None
                                elif isinstance(value, bytes):
                                    # Бинарные данные (например, зашифрованные ключи)
                                    data[col] = value
                                elif isinstance(value, str) and value == '':
                                    # Пустые строки
                                    data[col] = None if 'id' not in col.lower() else value
                                else:
                                    data[col] = value
                            
                            # Пропускаем поля, которых нет в модели (например, если структура изменилась)
                            model_columns = {c.name for c in model.__table__.columns}
                            data = {k: v for k, v in data.items() if k in model_columns}
                            
                            # Для платежей проверяем внешние ключи
                            if table_name == 'payment' and 'user_id' in data:
                                if data['user_id'] not in existing_user_ids:
                                    skipped_count += 1
                                    if skipped_count <= 3:
                                        print(f"      ⚠️  Пропущен платеж ID {data.get('id', '?')}: пользователь ID {data['user_id']} не существует")
                                    continue
                            
                            # Для тикетов проверяем внешние ключи
                            if table_name == 'ticket' and 'user_id' in data:
                                if data['user_id'] not in existing_user_ids:
                                    skipped_count += 1
                                    if skipped_count <= 3:
                                        print(f"      ⚠️  Пропущен тикет ID {data.get('id', '?')}: пользователь ID {data['user_id']} не существует")
                                    continue
                            
                            # Для сообщений тикетов проверяем внешние ключи
                            if table_name == 'ticket_message' and 'ticket_id' in data:
                                # Проверяем существование тикета (будет проверено после миграции тикетов)
                                pass
                            
                            # Создаем объект модели
                            instance = model(**data)
                            pg_db.session.add(instance)
                            migrated_count += 1
                        except Exception as e:
                            skipped_count += 1
                            if skipped_count <= 3:  # Показываем только первые 3 ошибки
                                print(f"      ⚠️  Ошибка при миграции записи: {str(e)[:100]}")
                            continue
                    
                    if skipped_count > 3:
                        print(f"      ⚠️  ... и еще {skipped_count - 3} ошибок")
                    
                    pg_db.session.commit()
                    print(f"      ✅ Мигрировано: {migrated_count} записей")
                    total_migrated += migrated_count
                    
                    # После миграции пользователей обновляем список user_id для проверки внешних ключей
                    if table_name == 'user':
                        existing_user_ids = {u.id for u in User.query.all()}
                        print(f"      ℹ️  Обновлен список user_id: {len(existing_user_ids)} пользователей")
                    
                except Exception as e:
                    print(f"   ⚠️  Ошибка при миграции {table_name}: {str(e)[:100]}")
                    pg_db.session.rollback()
                    import traceback
                    traceback.print_exc()
                    continue
            
            sqlite_conn.close()
            
            print()
            print("=" * 80)
            print(f"✅ МИГРАЦИЯ ЗАВЕРШЕНА")
            print(f"   Всего мигрировано записей: {total_migrated}")
            print("=" * 80)
            
            return True
                
    except Exception as e:
        print(f"❌ Ошибка миграции: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = migrate_data()
    sys.exit(0 if success else 1)
