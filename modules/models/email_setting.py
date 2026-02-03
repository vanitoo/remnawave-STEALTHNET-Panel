"""
Настройки почты: имя отправителя и шаблоны писем (редактируемые из админки).
"""
from modules.core import get_db

db = get_db()


class EmailSetting(db.Model):
    """Настройки почты: отправитель и шаблоны писем (верификация, рассылка)."""
    __tablename__ = "email_setting"

    id = db.Column(db.Integer, primary_key=True)

    # Имя отправителя в поле "From" (например: "StealthNET" или "Мой VPN")
    mail_sender_name = db.Column(db.String(100), nullable=True)

    # Письмо подтверждения email
    verification_subject = db.Column(db.String(200), nullable=True)  # тема письма
    verification_body_html = db.Column(db.Text, nullable=True)  # HTML; переменная: {{ verification_url }}

    # Шаблон рассылки (обёртка для broadcast): переменные {{ subject }}, {{ message }}
    broadcast_body_html = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "mail_sender_name": self.mail_sender_name or "",
            "verification_subject": self.verification_subject or "",
            "verification_body_html": self.verification_body_html or "",
            "broadcast_body_html": self.broadcast_body_html or "",
        }
