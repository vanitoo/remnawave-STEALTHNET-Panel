#!/bin/bash
# Скрипт для запуска миграции из бекапа Бедолага в Docker контейнере

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}🔄 Запуск миграции в Docker${NC}"
echo -e "${GREEN}========================================${NC}"

# Проверяем аргументы
if [ -z "$1" ]; then
    echo -e "${RED}❌ Ошибка: Не указан путь к бекапу${NC}"
    echo ""
    echo "Использование:"
    echo "  ./migration/run_migration_docker.sh /path/to/backup_20260126_000000 [--force]"
    echo ""
    echo "Или через docker-compose:"
    echo "  docker-compose run --rm api python migration/migrate_from_bedolaga.py /backup/backup_20260126_000000 [--force]"
    exit 1
fi

BACKUP_PATH="$1"
FORCE_FLAG="${2:-}"

# Проверяем, что контейнер запущен
if ! docker-compose ps api | grep -q "Up"; then
    echo -e "${YELLOW}⚠️  Контейнер api не запущен. Запускаем...${NC}"
    docker-compose up -d api
    echo -e "${GREEN}✅ Контейнер запущен${NC}"
fi

# Проверяем, что бекап существует
if [ ! -d "$BACKUP_PATH" ]; then
    echo -e "${RED}❌ Ошибка: Папка с бекапом не найдена: $BACKUP_PATH${NC}"
    exit 1
fi

# Проверяем наличие database.json
if [ ! -f "$BACKUP_PATH/database.json" ]; then
    echo -e "${RED}❌ Ошибка: Файл database.json не найден в $BACKUP_PATH${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Бекап найден: $BACKUP_PATH${NC}"

# Получаем абсолютный путь к бекапу
BACKUP_ABS_PATH=$(cd "$BACKUP_PATH" && pwd)
BACKUP_NAME=$(basename "$BACKUP_ABS_PATH")

echo -e "${YELLOW}📦 Запуск миграции в контейнере...${NC}"
echo ""

# Запускаем миграцию в контейнере
# Монтируем бекап как volume и запускаем скрипт миграции
if [ -n "$FORCE_FLAG" ]; then
    docker-compose run --rm \
        -v "$BACKUP_ABS_PATH:/backup/$BACKUP_NAME:ro" \
        api \
        python migration/migrate_from_bedolaga.py "/backup/$BACKUP_NAME" --force
else
    docker-compose run --rm \
        -v "$BACKUP_ABS_PATH:/backup/$BACKUP_NAME:ro" \
        api \
        python migration/migrate_from_bedolaga.py "/backup/$BACKUP_NAME"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ Миграция завершена!${NC}"
echo -e "${GREEN}========================================${NC}"
