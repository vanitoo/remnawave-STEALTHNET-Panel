#!/usr/bin/env python3
"""
Общий скрипт для запуска всех миграций схемы базы данных
Выполняется автоматически перед запуском app.py
"""

import os
import sys
import importlib.util
from pathlib import Path

def run_all_schema_migrations(app=None):
    """
    Запустить все миграции схемы базы данных
    
    Args:
        app: Flask приложение (опционально, если None - создаст временное)
    
    Returns:
        bool: True если все миграции выполнены успешно
    """
    print("=" * 80)
    print("🔧 ЗАПУСК МИГРАЦИЙ СХЕМЫ БАЗЫ ДАННЫХ")
    print("=" * 80)
    print()
    
    # Получаем путь к директории со скриптами
    base_dir = Path(__file__).parent.absolute()
    
    # Список скриптов миграции в порядке выполнения
    # Важно: порядок имеет значение для зависимостей между таблицами
    migration_scripts = [
        ('add_referral_fields.py', 'add_referral_fields'),
        ('add_user_blocking_fields.py', 'add_user_blocking_fields'),  # Раньше, чтобы is_blocked был доступен
        ('add_referral_percent_to_user.py', 'add_referral_percent_to_user'),
        ('add_branding_fields.py', 'add_branding_fields'),
        ('add_favicon_url_to_branding.py', 'add_favicon_url_to_branding'),  # После add_branding_fields, на случай если favicon_url не был добавлен
        ('add_yookassa_receipt_field.py', 'add_yookassa_receipt_field'),
        ('add_squad_ids_to_tariff.py', 'add_squad_ids_to_tariff'),
        ('add_squad_id_to_promo_code.py', 'add_squad_id_to_promo_code'),
        ('add_is_admin_to_ticket_message.py', 'add_is_admin_to_ticket_message'),
        ('add_telegram_message_id_to_payment.py', 'add_telegram_message_id_to_payment'),
        ('add_button_fields_to_auto_broadcast.py', 'add_button_fields_to_auto_broadcast'),  # Поля кнопок для авторассылки
        ('add_casino_tables.py', 'add_casino_tables'),  # Таблицы казино
        ('migration/migrate_add_trial_settings.py', 'migrate_add_trial_settings'),  # Настройки триала
    ]
    
    success_count = 0
    skipped_count = 0
    error_count = 0
    
    # Используем переданное приложение или создаем временное
    use_temp_app = app is None
    
    if use_temp_app:
        from flask import Flask
        from modules.core import init_app
        app = Flask(__name__)
        init_app(app)
    
    with app.app_context():
        for script_file, script_name in migration_scripts:
            script_path = base_dir / script_file
            
            if not script_path.exists():
                print(f"   ⏭️  {script_file}: файл не найден")
                skipped_count += 1
                continue
            
            print(f"   📦 {script_file}...", end=' ', flush=True)
            
            try:
                # Загружаем и выполняем скрипт миграции
                spec = importlib.util.spec_from_file_location(script_name, str(script_path))
                if spec is None or spec.loader is None:
                    print("⚠️  Не удалось загрузить модуль")
                    error_count += 1
                    continue
                
                # Выполняем скрипт в текущем контексте приложения
                module = importlib.util.module_from_spec(spec)
                
                # Для скриптов, которые используют app из app.py
                # Это нужно для add_yookassa_receipt_field.py и миграций из папки migration/
                needs_app_substitution = (
                    script_file == 'add_yookassa_receipt_field.py' or 
                    'migration/' in script_file
                )
                
                if needs_app_substitution:
                    # Этот скрипт импортирует app из app.py, нужно подменить
                    try:
                        import app as app_module
                        original_app = app_module.app if hasattr(app_module, 'app') else None
                        if hasattr(app_module, 'app'):
                            app_module.app = app
                    except ImportError:
                        # Если модуль app не найден, пропускаем
                        pass
                
                spec.loader.exec_module(module)
                
                # Восстанавливаем оригинальный app
                if needs_app_substitution:
                    try:
                        import app as app_module
                        if hasattr(app_module, 'app') and 'original_app' in locals():
                            app_module.app = original_app
                    except ImportError:
                        pass
                
                # Большинство скриптов выполняются при импорте (в with app.app_context())
                # Проверяем, есть ли функция для явного вызова
                # Пробуем несколько вариантов имени функции
                script_base_name = script_name.replace('.py', '')
                possible_func_names = [
                    'migrate',  # Стандартное имя функции для миграций в папке migration/
                    script_base_name,  # add_branding_fields
                    script_base_name.replace('_', ''),  # addbrandingfields
                    script_base_name.replace('_', '_'),  # add_branding_fields (то же самое)
                ]
                
                func = None
                for func_name in possible_func_names:
                    if hasattr(module, func_name):
                        func = getattr(module, func_name)
                        if callable(func):
                            break
                
                if func:
                    # Передаем app в функцию, если она принимает параметр
                    import inspect
                    sig = inspect.signature(func)
                    if 'app_instance' in sig.parameters or 'app' in sig.parameters:
                        func(app)
                    else:
                        func()
                
                success_count += 1
                print("✅")
            
            except Exception as e:
                error_msg = str(e)
                # Некоторые ошибки ожидаемы (например, поле уже существует)
                if any(keyword in error_msg.lower() for keyword in [
                    'already exists', 'существует', 'duplicate', 'уже'
                ]):
                    print("ℹ️  (уже выполнено)")
                    skipped_count += 1
                    success_count += 1  # Это не ошибка
                else:
                    print(f"❌ {error_msg[:100]}")
                    error_count += 1
                    # Не прерываем выполнение, продолжаем со следующим скриптом
                    import traceback
                    traceback.print_exc()
    
    print()
    print("=" * 80)
    print(f"✅ МИГРАЦИИ СХЕМЫ ЗАВЕРШЕНЫ")
    print(f"   Успешно: {success_count}")
    if skipped_count > 0:
        print(f"   Пропущено: {skipped_count}")
    if error_count > 0:
        print(f"   ⚠️  Ошибок: {error_count}")
    print("=" * 80)
    print()
    
    return error_count == 0

if __name__ == '__main__':
    success = run_all_schema_migrations()
    sys.exit(0 if success else 1)

