"""
Хелперы для отправки писем: имя отправителя из настроек, HTML из шаблонов (БД или файлы).
"""
import os
from flask import render_template, render_template_string

from modules.models.email_setting import EmailSetting
from modules.models.branding import BrandingSetting


def get_mail_sender():
    """
    Имя и email отправителя для писем.
    Returns: (sender_name, sender_email) для Message(sender=(name, email)).
    """
    try:
        from flask import current_app
        default_sender = current_app.config.get('MAIL_DEFAULT_SENDER')
        if isinstance(default_sender, (list, tuple)):
            default_name, default_email = default_sender[0], default_sender[1]
        elif isinstance(default_sender, str):
            default_name, default_email = default_sender, os.getenv("MAIL_USERNAME", "noreply@example.com")
        else:
            default_name = os.getenv("MAIL_SENDER_NAME", "Panel")
            default_email = os.getenv("MAIL_USERNAME", "noreply@example.com")

        es = EmailSetting.query.first()
        if es and es.mail_sender_name and (es.mail_sender_name or "").strip():
            return ((es.mail_sender_name or "").strip(), default_email)
        b = BrandingSetting.query.first()
        if b and b.site_name and (b.site_name or "").strip():
            return ((b.site_name or "").strip(), default_email)
        return (default_name or "Panel", default_email)
    except Exception:
        return (os.getenv("MAIL_SENDER_NAME", "Panel"), os.getenv("MAIL_USERNAME", "noreply@example.com"))


def get_verification_html(verification_url, service_name=None):
    """
    HTML письма подтверждения email.
    Если в EmailSetting задан verification_body_html — рендерим его (Jinja: verification_url, service_name).
    Иначе — render_template('email_verification.html', ...).
    """
    es = EmailSetting.query.first()
    if es and es.verification_body_html and es.verification_body_html.strip():
        return render_template_string(
            es.verification_body_html,
            verification_url=verification_url,
            service_name=service_name or ""
        )
    return render_template(
        'email_verification.html',
        verification_url=verification_url,
        branding=BrandingSetting.query.first(),
        service_name=service_name or ""
    )


def get_verification_subject():
    """Тема письма подтверждения (из настроек или по умолчанию)."""
    es = EmailSetting.query.first()
    if es and es.verification_subject and es.verification_subject.strip():
        return es.verification_subject.strip()
    return "Подтвердите email"


def get_broadcast_html(subject, message):
    """
    HTML письма рассылки.
    Если в EmailSetting задан broadcast_body_html — рендерим его (Jinja: subject, message).
    Иначе — render_template('email_broadcast.html', subject=subject, message=message).
    """
    es = EmailSetting.query.first()
    if es and es.broadcast_body_html and es.broadcast_body_html.strip():
        return render_template_string(
            es.broadcast_body_html,
            subject=subject or "",
            message=message or ""
        )
    return render_template('email_broadcast.html', subject=subject or "", message=message or "")
