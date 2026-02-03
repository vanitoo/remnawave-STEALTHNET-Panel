"""
Schema migration: add bot_page_logos to bot_config (JSON, логотипы по страницам бота).
"""

from __future__ import annotations

from sqlalchemy import inspect, text


def migrate(app_instance=None):
    from modules.core import get_db
    from modules.models.bot_config import BotConfig

    db = get_db()
    engine = db.engine
    dialect = (getattr(engine.dialect, "name", "") or "").lower()

    try:
        BotConfig.__table__.create(bind=engine, checkfirst=True)
    except Exception:
        pass

    insp = inspect(engine)
    if "bot_config" not in insp.get_table_names():
        return

    existing_cols = {c.get("name") for c in insp.get_columns("bot_config")}
    if "bot_page_logos" in existing_cols:
        return

    ddl = "ALTER TABLE bot_config ADD COLUMN bot_page_logos TEXT"
    try:
        db.session.execute(text(ddl))
        db.session.commit()
    except Exception:
        db.session.rollback()
