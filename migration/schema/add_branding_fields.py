#!/usr/bin/env python3
"""
Скрипт для добавления новых полей в таблицу branding_setting
"""
from flask import Flask
from modules.core import init_app, get_db
from sqlalchemy import text, inspect

app = Flask(__name__)
init_app(app)

with app.app_context():
    db = get_db()
    
    try:
        # Список новых полей для добавления
        new_fields = [
            ('dashboard_referrals_title', 'VARCHAR(200)'),
            ('dashboard_referrals_description', 'VARCHAR(300)'),
            ('dashboard_support_title', 'VARCHAR(200)'),
            ('dashboard_support_description', 'VARCHAR(300)'),
            ('tariff_tier_basic_name', 'VARCHAR(100)'),
            ('tariff_tier_pro_name', 'VARCHAR(100)'),
            ('tariff_tier_elite_name', 'VARCHAR(100)'),
            ('tariff_features_names', 'TEXT'),
            ('button_subscribe_text', 'VARCHAR(50)'),
            ('button_buy_text', 'VARCHAR(50)'),
            ('button_connect_text', 'VARCHAR(50)'),
            ('button_share_text', 'VARCHAR(50)'),
            ('button_copy_text', 'VARCHAR(50)'),
            ('meta_title', 'VARCHAR(200)'),
            ('meta_description', 'VARCHAR(500)'),
            ('meta_keywords', 'VARCHAR(300)'),
            ('subscription_active_text', 'VARCHAR(200)'),
            ('subscription_expired_text', 'VARCHAR(200)'),
            ('subscription_trial_text', 'VARCHAR(200)'),
            ('balance_label_text', 'VARCHAR(50)'),
            ('referral_code_label_text', 'VARCHAR(50)'),
        ]
        
        # Проверяем тип базы данных
        database_url = app.config.get('SQLALCHEMY_DATABASE_URI', '')
        is_postgresql = 'postgresql' in database_url.lower()
        
        print(f"База данных: {'PostgreSQL' if is_postgresql else 'SQLite'}")
        print("=" * 60)
        
        # Получаем список существующих колонок
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('branding_setting')]
        existing_columns = set(columns)
        
        print(f"Существующие колонки: {len(existing_columns)}")
        
        # Добавляем новые поля
        added_count = 0
        for field_name, field_type in new_fields:
            if field_name in existing_columns:
                print(f"  ⏭️  {field_name} - уже существует")
                continue
            
            try:
                print(f"  🔧 Добавляем {field_name}...")
                db.session.execute(text(f'ALTER TABLE branding_setting ADD COLUMN {field_name} {field_type}'))
                print(f"  ✅ {field_name} - добавлено")
                added_count += 1
            except Exception as e:
                print(f"  ❌ {field_name} - ошибка: {str(e)[:100]}")
        
        db.session.commit()
        
        print("=" * 60)
        print(f"✅ Добавлено новых полей: {added_count}")
        print(f"⏭️  Пропущено (уже существуют): {len(new_fields) - added_count}")
        print("=" * 60)
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
