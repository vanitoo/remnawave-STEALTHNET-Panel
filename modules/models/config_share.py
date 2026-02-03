"""
Модель для обмена конфигами через inline режим Telegram
"""
import secrets
from datetime import datetime, timezone, timedelta
from modules.core import get_db

db = get_db()

class ConfigShareToken(db.Model):
    """Токен для обмена конфигом через inline режим"""
    __tablename__ = 'config_share_token'
    
    id = db.Column(db.Integer, primary_key=True)
    config_id = db.Column(db.Integer, db.ForeignKey('user_config.id'), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # Владелец конфига
    token = db.Column(db.String(32), unique=True, nullable=False, index=True)  # Уникальный токен
    expires_at = db.Column(db.DateTime, nullable=True)  # Время истечения (None = бессрочный)
    max_uses = db.Column(db.Integer, default=1, nullable=False)  # Максимальное количество использований
    current_uses = db.Column(db.Integer, default=0, nullable=False)  # Текущее количество использований
    is_active = db.Column(db.Boolean, default=True, nullable=False)  # Активен ли токен
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    
    # Связи
    config = db.relationship('UserConfig', backref='share_tokens')
    owner = db.relationship('User', foreign_keys=[owner_id], backref='shared_configs')
    
    @staticmethod
    def generate_token() -> str:
        """Генерирует уникальный токен"""
        return secrets.token_urlsafe(24)[:32]  # 32 символа
    
    def is_valid(self) -> bool:
        """Проверяет, валиден ли токен"""
        if not self.is_active:
            return False
        
        if self.expires_at:
            # Приводим expires_at к timezone-aware, если он timezone-naive
            expires_at = self.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            
            # Сравниваем с текущим временем
            now = datetime.now(timezone.utc)
            if expires_at < now:
                return False
        
        if self.current_uses >= self.max_uses:
            return False
        return True
    
    def use(self):
        """Использовать токен (увеличить счетчик)"""
        self.current_uses += 1
        db.session.commit()
    
    def to_dict(self):
        """Преобразовать в словарь"""
        return {
            'id': self.id,
            'token': self.token,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'max_uses': self.max_uses,
            'current_uses': self.current_uses,
            'is_active': self.is_active,
            'is_valid': self.is_valid()
        }
