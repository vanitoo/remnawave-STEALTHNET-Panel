"""
Модель уровней тарифов
"""

from modules.core import get_db

db = get_db()


class TariffLevel(db.Model):
    """Уровни тарифов (названия)"""

    __tablename__ = 'tariff_level'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)  # basic, pro, elite, custom1, ...
    name = db.Column(db.String(100), nullable=False)  # Отображаемое название
    display_order = db.Column(db.Integer, default=0)  # Порядок отображения
    is_default = db.Column(db.Boolean, default=False)  # Уровень по умолчанию
    is_active = db.Column(db.Boolean, default=True)  # Активен ли уровень

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'display_order': self.display_order,
            'is_default': self.is_default,
            'is_active': self.is_active,
        }

