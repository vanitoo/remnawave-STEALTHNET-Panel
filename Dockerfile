FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создание рабочей директории
WORKDIR /app

# Копирование requirements.txt и установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование приложения
COPY app.py .

# Создание директорий для данных
RUN mkdir -p /app/data /app/instance/cache /app/templates

# Копирование опциональных директорий (если они существуют)
# Используем условное копирование через временную директорию
COPY . /tmp/build/
RUN set -e; \
    if [ -d "/tmp/build/instance" ] && [ "$(ls -A /tmp/build/instance 2>/dev/null)" ]; then \
        cp -r /tmp/build/instance/* /app/instance/ 2>/dev/null || true; \
    fi; \
    if [ -d "/tmp/build/templates" ] && [ "$(ls -A /tmp/build/templates 2>/dev/null)" ]; then \
        cp -r /tmp/build/templates/* /app/templates/ 2>/dev/null || true; \
    fi; \
    rm -rf /tmp/build

# Переменные окружения по умолчанию
ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1

# Открытие порта
EXPOSE 5000

# Команда запуска
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "app:app"]

