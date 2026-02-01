#!/usr/bin/env python3
"""
Скрипт миграции данных из бекапа бота "Бедолага" в STEALTHNET-Panel
Создает SQLite базу данных в папке instance/stealthnet.db

Использование:
    python migration/migrate_from_bedolaga.py /path/to/backup_20260126_000000
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask
from modules.core import init_app, get_db

# Модели импортируем после init_app(), иначе при загрузке моделей вызывается get_db() и падает "Database not initialized"


def parse_args():
    """Парсинг аргументов командной строки"""
    parser = argparse.ArgumentParser(
        description='Миграция данных из бекапа Бедолага в STEALTHNET-Panel'
    )
    parser.add_argument(
        'backup_path',
        type=str,
        help='Путь к папке с бекапом (например, backup_20260126_000000)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Перезаписать существующую базу данных'
    )
    return parser.parse_args()

def load_bedolaga_backup(backup_path):
    """Загрузка данных из бекапа Бедолага"""
    backup_path = Path(backup_path)
    
    # Проверяем, что путь существует
    if not backup_path.exists():
        raise FileNotFoundError(f"Путь к бекапу не существует: {backup_path}")
    
    if not backup_path.is_dir():
        raise FileNotFoundError(f"Путь к бекапу должен быть директорией: {backup_path}")
    
    database_json = backup_path / 'database.json'
    
    if not database_json.exists():
        raise FileNotFoundError(
            f"Файл database.json не найден в {backup_path}\n"
            f"Убедитесь, что указан правильный путь к папке с бекапом."
        )
    
    print(f"📂 Загрузка данных из {database_json}...")
    
    try:
        with open(database_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Ошибка при парсинге JSON файла: {e}")
    except Exception as e:
        raise IOError(f"Ошибка при чтении файла database.json: {e}")
    
    if 'data' not in data:
        raise ValueError("В файле database.json отсутствует секция 'data'")
    
    return data.get('data', {})

def create_app_for_migration():
    """Создание Flask приложения для миграции"""
    app = Flask(__name__)
    
    # Устанавливаем путь к instance папке
    # Проверяем переменную окружения INSTANCE_PATH (для Docker) или используем стандартный путь
    instance_path = os.getenv('INSTANCE_PATH') or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
        'instance'
    )
    os.makedirs(instance_path, exist_ok=True)
    app.instance_path = instance_path
    
    # Настраиваем SQLite базу данных в instance/
    db_path = os.path.join(instance_path, 'stealthnet.db')
    
    # Если в Docker и есть PostgreSQL, можно использовать его, но для миграции используем SQLite
    # чтобы не зависеть от состояния PostgreSQL
    use_sqlite = os.getenv('MIGRATION_USE_SQLITE', 'true').lower() == 'true'
    
    if use_sqlite:
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    else:
        # Используем PostgreSQL из переменных окружения
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            db_host = os.getenv('DB_HOST', 'localhost')
            db_port = os.getenv('DB_PORT', '5432')
            db_name = os.getenv('DB_NAME', 'stealthnet')
            db_user = os.getenv('DB_USER', 'stealthnet')
            db_password = os.getenv('DB_PASSWORD', '')
            
            if db_password:
                database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
            else:
                database_url = f"postgresql://{db_user}@{db_host}:{db_port}/{db_name}"
        
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
        print(f"📊 Используется PostgreSQL: {db_host}:{db_port}/{db_name}")
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'migration-temp-key')
    
    # Инициализируем приложение
    init_app(app)
    
    return app, db_path

def migrate_users(bedolaga_data, db):
    """Миграция пользователей"""
    print("\n👥 Миграция пользователей...")
    bedolaga_users = bedolaga_data.get('users', [])
    
    if not bedolaga_users:
        print("  ℹ️  Пользователи не найдены в бекапе")
        return {}
    
    user_id_mapping = {}  # Старый ID -> Новый ID
    migrated_count = 0
    skipped_count = 0
    
    for bed_user in bedolaga_users:
        try:
            if not bed_user.get('telegram_id'):
                print(f"  ⚠️  Пользователь {bed_user.get('id')} без telegram_id, пропускаем")
                skipped_count += 1
                continue
            
            # Проверяем, существует ли пользователь с таким telegram_id
            existing_user = User.query.filter_by(telegram_id=str(bed_user['telegram_id'])).first()
            
            if existing_user:
                print(f"  ⚠️  Пользователь с telegram_id {bed_user['telegram_id']} уже существует, пропускаем")
                user_id_mapping[bed_user['id']] = existing_user.id
                skipped_count += 1
                continue
            
            # Создаем нового пользователя
            user = User(
                telegram_id=str(bed_user['telegram_id']),
                telegram_username=bed_user.get('username'),
                remnawave_uuid=bed_user.get('remnawave_uuid'),
                referral_code=bed_user.get('referral_code'),
                balance=bed_user.get('balance_kopeks', 0) / 100.0,  # Конвертируем копейки в рубли
                preferred_lang=bed_user.get('language', 'ru'),
                trial_used=bed_user.get('has_had_paid_subscription', False),
                created_at=datetime.fromisoformat(bed_user['created_at'].replace('Z', '+00:00')) if bed_user.get('created_at') else datetime.now(timezone.utc)
            )
            
            db.session.add(user)
            db.session.flush()  # Получаем ID
            
            user_id_mapping[bed_user['id']] = user.id
            migrated_count += 1
            user_name = bed_user.get('username') or bed_user.get('first_name') or f"User_{bed_user['id']}"
            print(f"  ✅ Пользователь {user_name} (ID: {bed_user['id']} -> {user.id})")
            
        except Exception as e:
            print(f"  ❌ Ошибка при миграции пользователя {bed_user.get('id')}: {e}")
            continue
    
    # Обновляем referrer_id после создания всех пользователей
    print("\n🔗 Обновление реферальных связей...")
    for bed_user in bedolaga_users:
        if bed_user.get('referred_by_id') and bed_user['referred_by_id'] in user_id_mapping:
            new_user_id = user_id_mapping.get(bed_user['id'])
            new_referrer_id = user_id_mapping.get(bed_user['referred_by_id'])
            
            if new_user_id and new_referrer_id:
                user = User.query.get(new_user_id)
                if user:
                    user.referrer_id = new_referrer_id
                    print(f"  ✅ Установлен реферер для пользователя {new_user_id}")
    
    db.session.commit()
    print(f"\n✅ Мигрировано пользователей: {migrated_count}")
    if skipped_count > 0:
        print(f"⚠️  Пропущено пользователей: {skipped_count}")
    return user_id_mapping

def migrate_user_configs(bedolaga_data, user_id_mapping, db):
    """Миграция конфигов пользователей (из подписок и пользователей)"""
    print("\n⚙️  Миграция конфигов пользователей...")
    bedolaga_subscriptions = bedolaga_data.get('subscriptions', [])
    bedolaga_users = bedolaga_data.get('users', [])
    
    migrated_count = 0
    processed_uuids = set()
    
    # Сначала обрабатываем подписки
    for sub in bedolaga_subscriptions:
        user_id = user_id_mapping.get(sub.get('user_id'))
        remnawave_uuid = sub.get('remnawave_short_uuid') or sub.get('remnawave_uuid')
        
        if not user_id or not remnawave_uuid or remnawave_uuid in processed_uuids:
            continue
        
        # Проверяем, существует ли уже конфиг с таким UUID
        existing_config = UserConfig.query.filter_by(remnawave_uuid=remnawave_uuid).first()
        if existing_config:
            processed_uuids.add(remnawave_uuid)
            continue
        
        # Создаем конфиг
        config = UserConfig(
            user_id=user_id,
            remnawave_uuid=remnawave_uuid,
            config_name=f"Конфиг из миграции",
            is_primary=False,
            created_at=datetime.fromisoformat(sub['created_at'].replace('Z', '+00:00')) if sub.get('created_at') else datetime.now(timezone.utc)
        )
        
        db.session.add(config)
        processed_uuids.add(remnawave_uuid)
        migrated_count += 1
    
    # Затем обрабатываем пользователей с remnawave_uuid
    for bed_user in bedolaga_users:
        user_id = user_id_mapping.get(bed_user.get('id'))
        remnawave_uuid = bed_user.get('remnawave_uuid')
        
        if not user_id or not remnawave_uuid or remnawave_uuid in processed_uuids:
            continue
        
        # Проверяем, существует ли уже конфиг с таким UUID
        existing_config = UserConfig.query.filter_by(remnawave_uuid=remnawave_uuid).first()
        if existing_config:
            processed_uuids.add(remnawave_uuid)
            continue
        
        # Создаем конфиг
        config = UserConfig(
            user_id=user_id,
            remnawave_uuid=remnawave_uuid,
            config_name=f"Основной конфиг",
            is_primary=True,
            created_at=datetime.fromisoformat(bed_user['created_at'].replace('Z', '+00:00')) if bed_user.get('created_at') else datetime.now(timezone.utc)
        )
        
        db.session.add(config)
        processed_uuids.add(remnawave_uuid)
        migrated_count += 1
    
    db.session.commit()
    print(f"✅ Мигрировано конфигов: {migrated_count}")

def migrate_payments(bedolaga_data, user_id_mapping, db):
    """Миграция транзакций в платежи"""
    print("\n💳 Миграция платежей...")
    bedolaga_transactions = bedolaga_data.get('transactions', [])
    
    migrated_count = 0
    
    for trans in bedolaga_transactions:
        user_id = user_id_mapping.get(trans.get('user_id'))
        if not user_id:
            continue
        
        # Определяем статус
        status = 'COMPLETED' if trans.get('is_completed', False) else 'PENDING'
        
        # Определяем провайдера
        payment_method = trans.get('payment_method')
        provider = 'platega' if payment_method == 'platega' else 'telegram_stars' if payment_method == 'telegram_stars' else 'manual'
        
        # Определяем тип транзакции
        trans_type = trans.get('type', 'deposit')
        if trans_type == 'subscription_payment':
            # Это оплата подписки
            amount = trans.get('amount_kopeks', 0) / 100.0
            currency = 'rub'  # Бедолага использует рубли
        elif trans_type == 'deposit':
            # Это пополнение баланса
            amount = trans.get('amount_kopeks', 0) / 100.0
            currency = 'rub'
        else:
            continue
        
        # Создаем уникальный order_id
        external_id = trans.get('external_id')
        if external_id:
            order_id = f"bedolaga_{trans['id']}_{external_id[:20]}"
        else:
            order_id = f"bedolaga_{trans['id']}_{int(datetime.now().timestamp())}"
        
        # Проверяем, существует ли уже такой платеж
        existing_payment = Payment.query.filter_by(order_id=order_id).first()
        if existing_payment:
            continue
        
        # payment_system_id в модели ограничен 100 символами — обрезаем при необходимости
        payment_system_id_val = (external_id[:100] if external_id and len(external_id) > 100 else external_id) if external_id else None
        payment = Payment(
            order_id=order_id,
            user_id=user_id,
            status=status,
            amount=amount,
            currency=currency,
            payment_provider=provider,
            payment_system_id=payment_system_id_val,
            description=trans.get('description', ''),
            created_at=datetime.fromisoformat(trans['created_at'].replace('Z', '+00:00')) if trans.get('created_at') else datetime.now(timezone.utc)
        )
        
        db.session.add(payment)
        migrated_count += 1
    
    db.session.commit()
    print(f"✅ Мигрировано платежей: {migrated_count}")

def migrate_tickets(bedolaga_data, user_id_mapping, db):
    """Миграция тикетов поддержки"""
    print("\n🎫 Миграция тикетов...")
    bedolaga_tickets = bedolaga_data.get('tickets', [])
    bedolaga_messages = bedolaga_data.get('ticket_messages', [])
    
    ticket_id_mapping = {}  # Старый ID -> Новый ID
    migrated_tickets = 0
    migrated_messages = 0
    
    for bed_ticket in bedolaga_tickets:
        user_id = user_id_mapping.get(bed_ticket.get('user_id'))
        if not user_id:
            continue
        
        # Маппинг статусов
        status_map = {
            'open': 'OPEN',
            'answered': 'IN_PROGRESS',
            'closed': 'CLOSED',
            'resolved': 'RESOLVED'
        }
        status = status_map.get(bed_ticket.get('status', 'open').lower(), 'OPEN')
        
        ticket = Ticket(
            user_id=user_id,
            subject=bed_ticket.get('title', 'Без темы'),
            status=status,
            created_at=datetime.fromisoformat(bed_ticket['created_at'].replace('Z', '+00:00')) if bed_ticket.get('created_at') else datetime.now(timezone.utc)
        )
        
        db.session.add(ticket)
        db.session.flush()
        
        ticket_id_mapping[bed_ticket['id']] = ticket.id
        migrated_tickets += 1
    
    # Мигрируем сообщения
    for bed_message in bedolaga_messages:
        ticket_id = ticket_id_mapping.get(bed_message.get('ticket_id'))
        user_id = user_id_mapping.get(bed_message.get('user_id'))
        
        if not ticket_id or not user_id:
            continue
        
        message = TicketMessage(
            ticket_id=ticket_id,
            sender_id=user_id,
            message=bed_message.get('message_text', ''),
            is_admin=bed_message.get('is_from_admin', False),
            created_at=datetime.fromisoformat(bed_message['created_at'].replace('Z', '+00:00')) if bed_message.get('created_at') else datetime.now(timezone.utc)
        )
        
        db.session.add(message)
        migrated_messages += 1
    
    db.session.commit()
    print(f"✅ Мигрировано тикетов: {migrated_tickets}, сообщений: {migrated_messages}")

def migrate_system_settings(bedolaga_data, db):
    """Миграция системных настроек (пропускаем, т.к. структура отличается)"""
    print("\n⚙️  Системные настройки...")
    print("  ℹ️  Структура системных настроек в STEALTHNET-Panel отличается от Бедолага")
    print("  ℹ️  Настройки нужно будет настроить вручную через админ-панель")
    
    # SystemSetting в STEALTHNET-Panel имеет фиксированную структуру,
    # а не key-value хранилище, поэтому не мигрируем автоматически

def main():
    """Основная функция миграции"""
    args = parse_args()
    
    print("=" * 60)
    print("🔄 Миграция данных из бекапа Бедолага в STEALTHNET-Panel")
    print("=" * 60)
    
    # Загружаем данные из бекапа
    try:
        bedolaga_data = load_bedolaga_backup(args.backup_path)
    except Exception as e:
        print(f"❌ Ошибка при загрузке бекапа: {e}")
        sys.exit(1)
    
    # Создаем Flask приложение (init_app вызывается внутри)
    app, db_path = create_app_for_migration()

    # Импорт моделей только после init_app(), иначе get_db() падает при загрузке модулей
    from modules.models import (
        User, Payment, Tariff, PromoCode, Ticket, TicketMessage,
        UserConfig,
    )
    # Чтобы migrate_users/migrate_payments/... видели модели, записываем в глобальное пространство модуля
    globals().update({
        'User': User, 'Payment': Payment, 'Tariff': Tariff, 'PromoCode': PromoCode,
        'Ticket': Ticket, 'TicketMessage': TicketMessage, 'UserConfig': UserConfig,
    })

    # Проверяем существование базы данных
    if os.path.exists(db_path) and not args.force:
        response = input(f"\n⚠️  База данных {db_path} уже существует. Перезаписать? (y/N): ")
        if response.lower() != 'y':
            print("❌ Миграция отменена")
            sys.exit(0)
        os.remove(db_path)
        print(f"🗑️  Удалена существующая база данных")
    
    # Создаем все таблицы
    print(f"\n📦 Создание базы данных в {db_path}...")
    with app.app_context():
        db = get_db()
        db.create_all()
        print("✅ Таблицы созданы")
        
        # Выполняем миграцию
        user_id_mapping = migrate_users(bedolaga_data, db)
        if user_id_mapping:
            migrate_user_configs(bedolaga_data, user_id_mapping, db)
            migrate_payments(bedolaga_data, user_id_mapping, db)
            migrate_tickets(bedolaga_data, user_id_mapping, db)
        migrate_system_settings(bedolaga_data, db)
    
    print("\n" + "=" * 60)
    print("✅ Миграция завершена успешно!")
    print(f"📁 База данных создана: {db_path}")
    
    # Показываем статистику
    with app.app_context():
        db = get_db()
        try:
            users_count = User.query.count()
            payments_count = Payment.query.count()
            tickets_count = Ticket.query.count()
            configs_count = UserConfig.query.count()
            
            print(f"\n📊 Статистика миграции:")
            print(f"  👥 Пользователей: {users_count}")
            print(f"  💳 Платежей: {payments_count}")
            print(f"  🎫 Тикетов: {tickets_count}")
            print(f"  ⚙️  Конфигов: {configs_count}")
        except Exception as e:
            print(f"  ⚠️  Не удалось получить статистику: {e}")
    
    print("\n📝 Следующие шаги:")
    print("  1. Проверьте базу данных через админ-панель")
    print("  2. Настройте системные настройки вручную")
    print("  3. При необходимости создайте тарифы для пользователей")
    print("  4. Настройте платежные системы")
    print("=" * 60)

if __name__ == '__main__':
    main()
