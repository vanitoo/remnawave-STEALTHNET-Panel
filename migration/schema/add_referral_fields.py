#!/usr/bin/env python3
"""
Скрипт для добавления новых полей реферальной системы:
- referral_percent в таблицу user
- referral_type и default_referral_percent в таблицу referral_setting
"""

import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.core import init_app, get_db

def add_referral_fields():
    """Добавляет новые поля для реферальной системы"""
    from flask import Flask
    app = Flask(__name__)
    init_app(app)
    
    with app.app_context():
        db = get_db()
        from modules.models.user import User
        from modules.models.referral import ReferralSetting
        
        try:
            # Проверяем и добавляем referral_percent в user
            try:
                from sqlalchemy import inspect, text
                inspector = inspect(db.engine)
                user_columns = [col['name'] for col in inspector.get_columns('user')]
                
                if 'referral_percent' not in user_columns:
                    print("Добавляем поле referral_percent в таблицу user...")
                    with db.engine.connect() as conn:
                        conn.execute(text('ALTER TABLE "user" ADD COLUMN referral_percent FLOAT DEFAULT 10.0'))
                        conn.commit()
                    print("✅ Поле referral_percent добавлено в таблицу user")
                else:
                    print("✅ Поле referral_percent уже существует в таблице user")
            except Exception as e:
                print(f"⚠️ Ошибка при добавлении referral_percent: {e}")
            
            # Проверяем и добавляем referral_type в referral_setting
            try:
                from sqlalchemy import text
                referral_columns = [col['name'] for col in inspector.get_columns('referral_setting')]
                
                if 'referral_type' not in referral_columns:
                    print("Добавляем поле referral_type в таблицу referral_setting...")
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE referral_setting ADD COLUMN referral_type VARCHAR(20) DEFAULT 'DAYS'"))
                        conn.commit()
                    print("✅ Поле referral_type добавлено в таблицу referral_setting")
                else:
                    print("✅ Поле referral_type уже существует в таблице referral_setting")
            except Exception as e:
                print(f"⚠️ Ошибка при добавлении referral_type: {e}")
            
            # Проверяем и добавляем default_referral_percent в referral_setting
            try:
                from sqlalchemy import text
                if 'default_referral_percent' not in referral_columns:
                    print("Добавляем поле default_referral_percent в таблицу referral_setting...")
                    with db.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE referral_setting ADD COLUMN default_referral_percent FLOAT DEFAULT 10.0"))
                        conn.commit()
                    print("✅ Поле default_referral_percent добавлено в таблицу referral_setting")
                else:
                    print("✅ Поле default_referral_percent уже существует в таблице referral_setting")
            except Exception as e:
                print(f"⚠️ Ошибка при добавлении default_referral_percent: {e}")
            
            # Обновляем существующих пользователей, у которых нет referral_percent
            try:
                # Проверяем, что поле referral_percent существует перед запросом
                if 'referral_percent' in user_columns:
                    users_without_percent = db.session.query(User).filter(
                        (User.referral_percent == None) | (User.referral_percent == 0)
                    ).all()
                    
                    if users_without_percent:
                        # Получаем дефолтный процент из настроек
                        referral_settings = db.session.query(ReferralSetting).first()
                        default_percent = 10.0
                        if referral_settings and hasattr(referral_settings, 'default_referral_percent'):
                            default_percent = referral_settings.default_referral_percent or 10.0
                        
                        for user in users_without_percent:
                            user.referral_percent = default_percent
                        
                        db.session.commit()
                        print(f"✅ Обновлено {len(users_without_percent)} пользователей с дефолтным процентом {default_percent}%")
                    else:
                        print("✅ Все пользователи уже имеют referral_percent")
                else:
                    print("⚠️ Поле referral_percent еще не добавлено, пропускаем обновление пользователей")
            except Exception as e:
                print(f"⚠️ Ошибка при обновлении пользователей: {e}")
                import traceback
                traceback.print_exc()
                db.session.rollback()
            
            print("\n✅ Миграция завершена успешно!")
            
        except Exception as e:
            print(f"❌ Ошибка миграции: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback()
            return False
        
        return True

if __name__ == '__main__':
    success = add_referral_fields()
    sys.exit(0 if success else 1)

