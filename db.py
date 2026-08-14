"""
Kinomotor — db.py
Подключение к SQLite для FastAPI.
Схема создаётся один раз при старте (см. main.py, event "startup").
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "kinomotor.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_db():
    """
    Новое соединение с БД на каждый вызов.
    Использование в роуте:
        db = get_db()
        try:
            ...
        finally:
            db.close()
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # доступ к колонкам по имени: row["email"]
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # снижает блокировки при параллельных запросах
    return conn


def init_db():
    """Вызывается один раз при старте приложения — создаёт таблицы, если их нет."""
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()