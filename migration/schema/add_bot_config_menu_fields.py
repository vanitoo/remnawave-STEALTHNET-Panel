"""
Schema migration: ensure new BotConfig menu fields exist.

- Adds missing columns to bot_config for menu visibility flags
- Works on both SQLite and PostgreSQL (best-effort)
- Normalizes some old default translations in DB (optional)
"""

from __future__ import annotations

import json
from sqlalchemy import inspect, text


def migrate(app_instance=None):
    from modules.core import get_db
    from modules.models.bot_config import BotConfig

    db = get_db()
    engine = db.engine
    dialect = (getattr(engine.dialect, "name", "") or "").lower()

    # Ensure table exists (create if missing)
    try:
        BotConfig.__table__.create(bind=engine, checkfirst=True)
    except Exception:
        pass

    insp = inspect(engine)
    if "bot_config" not in insp.get_table_names():
        return

    existing_cols = {c.get("name") for c in insp.get_columns("bot_config")}

    desired = [
        ("show_connect_button", True),
        ("show_status_button", True),
        ("show_tariffs_button", True),
        ("show_options_button", True),
        ("show_settings_button", True),
    ]

    def _bool_sql(value: bool) -> str:
        if dialect == "postgresql":
            return "TRUE" if value else "FALSE"
        return "1" if value else "0"

    for col_name, default_value in desired:
        if col_name in existing_cols:
            continue

        ddl_strict = f"ALTER TABLE bot_config ADD COLUMN {col_name} BOOLEAN NOT NULL DEFAULT {_bool_sql(default_value)}"
        ddl_loose = f"ALTER TABLE bot_config ADD COLUMN {col_name} BOOLEAN"

        try:
            db.session.execute(text(ddl_strict))
            db.session.commit()
        except Exception:
            db.session.rollback()
            try:
                db.session.execute(text(ddl_loose))
                db.session.commit()
            except Exception:
                db.session.rollback()
                continue

        # Backfill NULLs (safety)
        try:
            db.session.execute(
                text(f"UPDATE bot_config SET {col_name} = {_bool_sql(default_value)} WHERE {col_name} IS NULL")
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

    # Normalize old default translations if they were saved in DB
    try:
        cfg = BotConfig.query.first()
        if not cfg:
            return

        replacements = {
            "ru": {
                "cabinet_button": ({"Кабинет", "Личный Кабинет"}, "Web Кабинет"),
                "referrals_button": ({"Рефералы", "Пригласить друга", "Пригласи друга"}, "Рефералка"),
            },
            "ua": {
                "cabinet_button": ({"Кабінет", "Особистий Кабінет"}, "Web Кабінет"),
                "referrals_button": ({"Реферали", "Запросити друга"}, "Рефералка"),
            },
            "en": {
                "cabinet_button": ({"Cabinet"}, "Web Cabinet"),
            },
            "cn": {
                "cabinet_button": ({"控制面板"}, "Web кабинет"),
            },
        }

        def _normalize(translations_raw: str | None, lang: str) -> str | None:
            if not translations_raw:
                return translations_raw
            try:
                obj = json.loads(translations_raw) if isinstance(translations_raw, str) else {}
            except Exception:
                return translations_raw
            if not isinstance(obj, dict):
                return translations_raw

            changed = False
            for key, (old_set, new_val) in replacements.get(lang, {}).items():
                cur = obj.get(key)
                if isinstance(cur, str) and cur.strip() in old_set:
                    obj[key] = new_val
                    changed = True

            return json.dumps(obj, ensure_ascii=False) if changed else translations_raw

        cfg.translations_ru = _normalize(getattr(cfg, "translations_ru", None), "ru")
        cfg.translations_ua = _normalize(getattr(cfg, "translations_ua", None), "ua")
        cfg.translations_en = _normalize(getattr(cfg, "translations_en", None), "en")
        cfg.translations_cn = _normalize(getattr(cfg, "translations_cn", None), "cn")
        db.session.commit()
    except Exception:
        db.session.rollback()

