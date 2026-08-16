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


def _ensure_columns(conn) -> None:
    """
    Досыпает колонки, появившиеся после того, как таблица уже создана.

    schema.sql весь построен на CREATE TABLE IF NOT EXISTS: на новой машине
    он создаёт всё сразу, но на работающем сервере таблица уже есть, и новые
    колонки в неё сами не добавятся. Здесь перечислены такие добавления —
    каждое выполняется один раз и молча пропускается, если колонка уже на месте.
    """
    additions = [
        # Громкости дорожек, выбранные человеком при сведении ролика.
        ("frame_videos", "mix_json", "TEXT"),
    ]

    for table, column, coltype in additions:
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except Exception:
            continue  # таблицы ещё нет — её создаст schema.sql
        if not existing or column in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        print(f"[db] Добавлена колонка {table}.{column}", flush=True)


def init_db():
    """Вызывается один раз при старте приложения — создаёт таблицы, если их нет."""
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    _ensure_columns(conn)
    conn.commit()
    conn.close()