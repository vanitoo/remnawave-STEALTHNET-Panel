#!/usr/bin/env python3
"""
Миграция только тикетов из SQLite в PostgreSQL
"""

import os
import sys
import sqlite3
from dotenv import load_dotenv
from flask import Flask
from modules.core import init_app, get_db

load_dotenv()

def migrate_tickets():
    sqlite_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'stealthnet.db')
    postgresql_url = os.getenv("DATABASE_URL")
    
    if not os.path.exists(sqlite_path):
        print(f"❌ SQLite база не найдена: {sqlite_path}")
        return False
    
    if not postgresql_url:
        print("❌ DATABASE_URL не настроен")
        return False
    
    print("=" * 80)
    print("МИГРАЦИЯ ТИКЕТОВ")
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
        from modules.models.ticket import Ticket, TicketMessage
        
        # Получаем список существующих user_id и ticket_id
        existing_user_ids = {u.id for u in User.query.all()}
        existing_ticket_ids = {t.id for t in Ticket.query.all()}
        
        print(f"✅ Найдено пользователей: {len(existing_user_ids)}")
        print(f"✅ Найдено тикетов: {len(existing_ticket_ids)}")
        print()
        
        # Мигрируем тикеты
        cursor = sqlite_conn.cursor()
        cursor.execute("SELECT * FROM ticket")
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description] if cursor.description else []
        
        print(f"📦 Тикетов в SQLite: {len(rows)}")
        
        migrated_tickets = 0
        skipped_tickets = 0
        
        for row in rows:
            try:
                data = {columns[i]: row[i] for i in range(len(columns))}
                
                # Проверяем user_id
                if 'user_id' in data and data['user_id'] not in existing_user_ids:
                    skipped_tickets += 1
                    continue
                
                model_columns = {c.name for c in Ticket.__table__.columns}
                data = {k: v for k, v in data.items() if k in model_columns}
                
                ticket = Ticket(**data)
                pg_db.session.add(ticket)
                migrated_tickets += 1
            except Exception as e:
                skipped_tickets += 1
                continue
        
        pg_db.session.commit()
        
        # Обновляем список ticket_id
        existing_ticket_ids = {t.id for t in Ticket.query.all()}
        
        # Мигрируем сообщения тикетов
        cursor.execute("SELECT * FROM ticket_message")
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description] if cursor.description else []
        
        print(f"📦 Сообщений в SQLite: {len(rows)}")
        
        migrated_messages = 0
        skipped_messages = 0
        
        for row in rows:
            try:
                data = {columns[i]: row[i] for i in range(len(columns))}
                
                # Проверяем ticket_id
                if 'ticket_id' in data and data['ticket_id'] not in existing_ticket_ids:
                    skipped_messages += 1
                    continue
                
                model_columns = {c.name for c in TicketMessage.__table__.columns}
                data = {k: v for k, v in data.items() if k in model_columns}
                
                message = TicketMessage(**data)
                pg_db.session.add(message)
                migrated_messages += 1
            except Exception as e:
                skipped_messages += 1
                continue
        
        pg_db.session.commit()
        sqlite_conn.close()
        
        print()
        print("=" * 80)
        print(f"✅ МИГРАЦИЯ ТИКЕТОВ ЗАВЕРШЕНА")
        print(f"   Тикетов мигрировано: {migrated_tickets}, пропущено: {skipped_tickets}")
        print(f"   Сообщений мигрировано: {migrated_messages}, пропущено: {skipped_messages}")
        print("=" * 80)
        
        return True

if __name__ == '__main__':
    success = migrate_tickets()
    sys.exit(0 if success else 1)

