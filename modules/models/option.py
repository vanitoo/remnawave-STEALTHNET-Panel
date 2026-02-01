"""
Модель дополнительных опций для покупки
- Докупить трафик
- Докупить устройств
- Докупить сквад
"""

from datetime import datetime

from modules.core import get_db

db = get_db()


class PurchaseOption(db.Model):
    """Дополнительные опции для покупки"""

    __tablename__ = 'purchase_options'

    id = db.Column(db.Integer, primary_key=True)

    # traffic | devices | squad
    option_type = db.Column(db.String(50), nullable=False)

    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)

    # traffic: GB, devices: count, squad: optional text
    value = db.Column(db.String(255), nullable=False)

    # only for squad option
    squad_uuid = db.Column(db.String(100), nullable=True)

    unit = db.Column(db.String(50), nullable=True)

    price_uah = db.Column(db.Float, default=0)
    price_rub = db.Column(db.Float, default=0)
    price_usd = db.Column(db.Float, default=0)

    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    icon = db.Column(db.String(50), default='📦')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'option_type': self.option_type,
            'name': self.name,
            'description': self.description,
            'value': self.value,
            'squad_uuid': self.squad_uuid,
            'unit': self.unit,
            'price_uah': self.price_uah,
            'price_rub': self.price_rub,
            'price_usd': self.price_usd,
            'is_active': self.is_active,
            'sort_order': self.sort_order,
            'icon': self.icon,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<PurchaseOption {self.id}: {self.name} ({self.option_type})>'

