#!/usr/bin/env python3
"""
Миграция только платежей из SQLite в PostgreSQL
"""

import os
import sys
import sqlite3
from dotenv import load_dotenv
from flask import Flask
from modules.core import init_app, get_db

load_dotenv()

def migrate_payments():
    sqlite_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'stealthnet.db')
    postgresql_url = os.getenv("DATABASE_URL")
    
    if not os.path.exists(sqlite_path):
        print(f"❌ SQLite база не найдена: {sqlite_path}")
        return False
    
    if not postgresql_url:
        print("❌ DATABASE_URL не настроен")
        return False
    
    print("=" * 80)
    print("МИГРАЦИЯ ПЛАТЕЖЕЙ")
    print("=" * 80)
    print()
    
    # Подключаемся к SQLite
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    
    # Создаем Flask приложение для PostgreSQL
    pg_app = Flask(__name__)
    pg_app.config['SQLALCHEMY_DATABASE_URI'] = postgresql_url
    pg_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    init_app(pg_app)
    pg_db = get_db()
    
    with pg_app.app_context():
        # Импортируем модели после инициализации
        from modules.models.user import User
        from modules.models.payment import Payment
        
        # Получаем список существующих user_id
        existing_user_ids = {u.id for u in User.query.all()}
        print(f"✅ Найдено пользователей в PostgreSQL: {len(existing_user_ids)}")
        print(f"   User IDs: {sorted(existing_user_ids)}")
        print()
        
        # Получаем платежи из SQLite
        cursor = sqlite_conn.cursor()
        cursor.execute("SELECT * FROM payment")
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        
        print(f"📦 Платежей в SQLite: {len(rows)}")
        print()
        
        migrated_count = 0
        skipped_count = 0
        
        for row in rows:
            try:
                data = {}
                for i, col in enumerate(columns):
                    value = row[i]
                    if value is None:
                        data[col] = None
                    elif isinstance(value, bytes):
                        data[col] = value
                    else:
                        data[col] = value
                
                # Проверяем user_id
                if 'user_id' in data and data['user_id'] not in existing_user_ids:
                    skipped_count += 1
                    if skipped_count <= 5:
                        print(f"   ⚠️  Пропущен платеж ID {data.get('id', '?')}: user_id {data['user_id']} не существует")
                    continue
                
                # Пропускаем поля, которых нет в модели
                model_columns = {c.name for c in Payment.__table__.columns}
                data = {k: v for k, v in data.items() if k in model_columns}
                
                # Создаем платеж
                payment = Payment(**data)
                pg_db.session.add(payment)
                migrated_count += 1
                
            except Exception as e:
                skipped_count += 1
                if skipped_count <= 5:
                    print(f"   ⚠️  Ошибка: {str(e)[:100]}")
                continue
        
        if skipped_count > 5:
            print(f"   ⚠️  ... и еще {skipped_count - 5} пропущено")
        
        pg_db.session.commit()
        sqlite_conn.close()
        
        print()
        print("=" * 80)
        print(f"✅ МИГРАЦИЯ ПЛАТЕЖЕЙ ЗАВЕРШЕНА")
        print(f"   Мигрировано: {migrated_count}")
        print(f"   Пропущено: {skipped_count}")
        print("=" * 80)
        
        return True

if __name__ == '__main__':
    success = migrate_payments()
    sys.exit(0 if success else 1)

