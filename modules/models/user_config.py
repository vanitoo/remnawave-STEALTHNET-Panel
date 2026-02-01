"""
Модель для хранения нескольких конфигов пользователя
Каждый конфиг связан с отдельным аккаунтом в Remna
"""
from datetime import datetime, timezone
from modules.core import get_db

db = get_db()

class UserConfig(db.Model):
    """Конфиг пользователя - связь с аккаунтом в Remna"""
    __tablename__ = 'user_config'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    remnawave_uuid = db.Column(db.String(100), nullable=False)  # UUID аккаунта в Remna
    config_name = db.Column(db.String(100), nullable=True)  # Имя конфига для отображения (например, "Конфиг 1", "Конфиг 2")
    is_primary = db.Column(db.Boolean, default=False, nullable=False)  # Основной конфиг (первый созданный)
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now(timezone.utc), onupdate=datetime.now(timezone.utc))
    
    # Связь с пользователем
    user = db.relationship('User', backref='configs')
    
    def to_dict(self):
        """Преобразовать в словарь для API"""
        return {
            'id': self.id,
            'config_name': self.config_name or f'Конфиг {self.id}',
            'is_primary': self.is_primary,
            'remnawave_uuid': self.remnawave_uuid,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
