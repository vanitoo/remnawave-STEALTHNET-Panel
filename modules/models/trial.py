"""
Модель настроек триала
"""
from datetime import datetime, timezone
from modules.core import get_db

db = get_db()

class TrialSettings(db.Model):
    """Настройки триального периода"""
    id = db.Column(db.Integer, primary_key=True)
    
    # Параметры триала
    days = db.Column(db.Integer, default=3, nullable=False)  # Количество дней триала
    devices = db.Column(db.Integer, default=3, nullable=False)  # Количество устройств
    traffic_limit_bytes = db.Column(db.BigInteger, default=0, nullable=False)  # Лимит трафика (0 = безлимит)
    
    # Заголовки (RU, UA, EN, CN)
    title_ru = db.Column(db.String(500), default='Получите {days} дней премиум', nullable=True)
    title_ua = db.Column(db.String(500), default='Отримайте {days} днів преміум', nullable=True)
    title_en = db.Column(db.String(500), default='Get {days} Days Premium', nullable=True)
    title_cn = db.Column(db.String(500), default='获得 {days} 天高级版', nullable=True)
    
    # Описания (RU, UA, EN, CN)
    description_ru = db.Column(db.Text, default='Дадим полный доступ без ограничений — протестируйте сеть перед оплатой.', nullable=True)
    description_ua = db.Column(db.Text, default='Дамо повний доступ без обмежень — протестуйте мережу перед оплатою.', nullable=True)
    description_en = db.Column(db.Text, default='We\'ll give you full access without restrictions — test the network before payment.', nullable=True)
    description_cn = db.Column(db.Text, default='我们将为您提供无限制的完全访问权限 — 在付款前测试网络。', nullable=True)
    
    # Текст кнопки (RU, UA, EN, CN)
    button_text_ru = db.Column(db.String(200), default='🎁 Попробовать бесплатно ({days} дня)', nullable=True)
    button_text_ua = db.Column(db.String(200), default='🎁 Спробувати безкоштовно ({days} дні)', nullable=True)
    button_text_en = db.Column(db.String(200), default='🎁 Try Free ({days} Days)', nullable=True)
    button_text_cn = db.Column(db.String(200), default='🎁 免费试用 ({days} 天)', nullable=True)
    
    # Текст сообщения при активации (RU, UA, EN, CN)
    activation_message_ru = db.Column(db.Text, default='✅ Триал активирован! Вам добавлено {days} дней премиум-доступа.', nullable=True)
    activation_message_ua = db.Column(db.Text, default='✅ Тріал активовано! Вам додано {days} днів преміум-доступу.', nullable=True)
    activation_message_en = db.Column(db.Text, default='✅ Trial activated! You have been added {days} days of premium access.', nullable=True)
    activation_message_cn = db.Column(db.Text, default='✅ 试用已激活！您已获得 {days} 天的高级访问权限。', nullable=True)
    
    # Включен/выключен
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))


def get_trial_settings():
    """Получить настройки триала"""
    settings = TrialSettings.query.first()
    if not settings:
        # Создаем настройки по умолчанию
        settings = TrialSettings()
        db.session.add(settings)
        db.session.commit()
    return settings
