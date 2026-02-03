#!/usr/bin/env python3
"""
Скрипт для создания таблицы автоматических рассылок
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask
from modules.core import init_app, get_db

app = Flask(__name__)
init_app(app)

# Импортируем модель после инициализации
from modules.models.auto_broadcast import AutoBroadcastMessage

with app.app_context():
    db = get_db()
    
    # Создаем таблицу
    db.create_all()
    
    # Создаем дефолтные сообщения если их нет
    subscription_msg = AutoBroadcastMessage.query.filter_by(
        message_type='subscription_expiring_3days'
    ).first()
    
    if not subscription_msg:
        subscription_msg = AutoBroadcastMessage(
            message_type='subscription_expiring_3days',
            message_text='Подписка заканчивается через 3 дня, не забудьте продлить',
            enabled=True,
            bot_type='both'
        )
        db.session.add(subscription_msg)
        print("✅ Создано сообщение: subscription_expiring_3days")
    
    trial_msg = AutoBroadcastMessage.query.filter_by(
        message_type='trial_expiring'
    ).first()
    
    if not trial_msg:
        trial_msg = AutoBroadcastMessage(
            message_type='trial_expiring',
            message_text='Тестовый период заканчивается, не желаете купить подписку?',
            enabled=True,
            bot_type='both'
        )
        db.session.add(trial_msg)
        print("✅ Создано сообщение: trial_expiring")
    
    db.session.commit()
    print("✅ Таблица auto_broadcast_message создана и заполнена")

