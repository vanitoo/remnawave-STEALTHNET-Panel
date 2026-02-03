# Миграция из бекапа Бедолага в Docker

## Быстрый старт

### Вариант 1: Использование скрипта-обертки (рекомендуется)

```bash
# Базовая миграция
./migration/run_migration_docker.sh /path/to/backup_20260126_000000

# С перезаписью существующей базы
./migration/run_migration_docker.sh /path/to/backup_20260126_000000 --force
```

### Вариант 2: Прямой запуск через docker-compose

```bash
# Монтируем бекап и запускаем миграцию
docker-compose run --rm \
  -v /absolute/path/to/backup_20260126_000000:/backup/backup_20260126_000000:ro \
  api \
  python migration/migrate_from_bedolaga.py /backup/backup_20260126_000000

# С перезаписью
docker-compose run --rm \
  -v /absolute/path/to/backup_20260126_000000:/backup/backup_20260126_000000:ro \
  api \
  python migration/migrate_from_bedolaga.py /backup/backup_20260126_000000 --force
```

### Вариант 3: Запуск в уже работающем контейнере

```bash
# Если контейнер уже запущен
docker-compose exec api python migration/migrate_from_bedolaga.py /backup/backup_20260126_000000

# Но сначала нужно скопировать бекап в контейнер:
docker cp /path/to/backup_20260126_000000 stealthnet-api:/backup/
```

## Подробная инструкция

### 1. Подготовка бекапа

Убедитесь, что у вас есть папка с бекапом, содержащая файл `database.json`:

```
backup_20260126_000000/
├── database.json
├── metadata.json
├── data/
└── files/
```

### 2. Проверка структуры

```bash
# Проверьте наличие database.json
ls -la /path/to/backup_20260126_000000/database.json
```

### 3. Запуск миграции

#### Использование скрипта-обертки (самый простой способ)

```bash
# Перейдите в директорию проекта
cd /opt/remnawave-STEALTHNET-Panel

# Запустите миграцию
./migration/run_migration_docker.sh /path/to/backup_20260126_000000
```

Скрипт автоматически:
- Проверит наличие бекапа
- Проверит, что контейнер запущен
- Смонтирует бекап в контейнер
- Запустит миграцию
- Сохранит результат в `instance/stealthnet.db`

#### Прямой запуск через docker-compose

```bash
docker-compose run --rm \
  -v "$(pwd)/backup_20260126_000000:/backup/backup_20260126_000000:ro" \
  api \
  python migration/migrate_from_bedolaga.py /backup/backup_20260126_000000
```

**Важно:** Используйте абсолютный путь к бекапу или путь относительно текущей директории.

### 4. Результат

После успешной миграции база данных будет создана в:
```
instance/stealthnet.db
```

Этот файл будет доступен как на хосте, так и в контейнере благодаря volume mount в `docker-compose.yml`.

## Переменные окружения

Скрипт миграции использует следующие переменные окружения (если они установлены):

- `INSTANCE_PATH` - путь к папке instance (по умолчанию `/app/instance` в Docker)
- `MIGRATION_USE_SQLITE` - использовать SQLite вместо PostgreSQL (по умолчанию `true`)
- `JWT_SECRET_KEY` - секретный ключ для JWT (используется из .env)

## Использование PostgreSQL вместо SQLite

По умолчанию миграция создает SQLite базу данных. Если вы хотите использовать PostgreSQL:

```bash
# Установите переменную окружения
export MIGRATION_USE_SQLITE=false

# Запустите миграцию
docker-compose run --rm \
  -v /path/to/backup:/backup/backup_20260126_000000:ro \
  -e MIGRATION_USE_SQLITE=false \
  api \
  python migration/migrate_from_bedolaga.py /backup/backup_20260126_000000
```

**Важно:** При использовании PostgreSQL убедитесь, что:
- PostgreSQL контейнер запущен и здоров
- Переменные окружения `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` настроены правильно

## Устранение проблем

### Ошибка: "Контейнер api не запущен"

```bash
# Запустите контейнеры
docker-compose up -d
```

### Ошибка: "Файл database.json не найден"

Убедитесь, что:
1. Путь к бекапу указан правильно
2. В папке бекапа есть файл `database.json`
3. Используется абсолютный путь или путь относительно текущей директории

### Ошибка: "Permission denied"

```bash
# Убедитесь, что скрипт имеет права на выполнение
chmod +x migration/run_migration_docker.sh
chmod +x migration/migrate_from_bedolaga.py
```

### Ошибка: "ModuleNotFoundError"

Все зависимости уже установлены в Docker образе. Если ошибка все же возникает:

```bash
# Пересоберите образ
docker-compose build api
```

### База данных не создается

Проверьте:
1. Права на запись в папку `instance/`
2. Что volume mount настроен правильно в `docker-compose.yml`
3. Логи контейнера: `docker-compose logs api`

## Примеры использования

### Пример 1: Миграция из локальной папки

```bash
# Бекап находится в /home/user/backups/backup_20260126_000000
./migration/run_migration_docker.sh /home/user/backups/backup_20260126_000000
```

### Пример 2: Миграция с перезаписью

```bash
./migration/run_migration_docker.sh /path/to/backup --force
```

### Пример 3: Миграция в PostgreSQL

```bash
docker-compose run --rm \
  -v /path/to/backup:/backup/backup_20260126_000000:ro \
  -e MIGRATION_USE_SQLITE=false \
  api \
  python migration/migrate_from_bedolaga.py /backup/backup_20260126_000000
```

## Проверка результата

После миграции проверьте базу данных:

```bash
# Проверьте наличие файла
ls -lh instance/stealthnet.db

# Или подключитесь к контейнеру и проверьте
docker-compose exec api python -c "
from modules.core import get_db
from modules.models import User
from flask import Flask
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///instance/stealthnet.db'
from modules.core import init_app
init_app(app)
with app.app_context():
    db = get_db()
    users = User.query.all()
    print(f'Пользователей в базе: {len(users)}')
"
```

## Следующие шаги

После успешной миграции:

1. Проверьте базу данных через админ-панель
2. Настройте системные настройки вручную
3. Создайте тарифы для пользователей (если нужно)
4. Настройте платежные системы
5. Проверьте связи пользователей с RemnaWave
