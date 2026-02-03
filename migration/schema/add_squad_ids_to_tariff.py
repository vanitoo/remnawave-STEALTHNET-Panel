#!/usr/bin/env python3
"""
Скрипт для добавления поля squad_ids в таблицу tariff
"""
from flask import Flask
from modules.core import init_app, get_db
from sqlalchemy import text

app = Flask(__name__)
init_app(app)

with app.app_context():
    db = get_db()
    
    try:
        # Проверяем, существует ли поле squad_ids
        inspector = db.inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('tariff')]
        
        if 'squad_ids' in columns:
            print("✅ Поле squad_ids уже существует в таблице tariff")
        else:
            print("📝 Добавляем поле squad_ids в таблицу tariff...")
            
            # Добавляем поле squad_ids
            db.session.execute(text("""
                ALTER TABLE tariff 
                ADD COLUMN squad_ids TEXT NULL
            """))
            
            # Мигрируем данные из squad_id в squad_ids (для обратной совместимости)
            # Используем SQLite-совместимый синтаксис
            import json
            from modules.models.tariff import Tariff
            
            # Получаем все тарифы с squad_id
            tariffs = Tariff.query.filter(Tariff.squad_id.isnot(None)).filter(Tariff.squad_ids.is_(None)).all()
            for tariff in tariffs:
                if tariff.squad_id:
                    # Создаем JSON массив из squad_id
                    tariff.squad_ids = json.dumps([tariff.squad_id])
            
            db.session.commit()
            print("✅ Поле squad_ids успешно добавлено и данные мигрированы")
            
    except Exception as e:
        db.session.rollback()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

