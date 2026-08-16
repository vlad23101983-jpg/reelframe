"""
Kinomotor — cleanup.py
Фоновая задача: удаляет с диска видео старше 24 часов (expires_at),
чтобы не переполнять диск сервера. Запись в истории генераций остаётся —
просто video_path становится NULL, и фронтенд перестаёт показывать кнопку "Скачать".
"""

import os
import shutil
import asyncio

from db import get_db

CLEANUP_INTERVAL_SECONDS = 3600  # раз в час


async def _cleanup_frame_drafts():
    """
    Убирает просроченные черновики тарифа "Кадры".

    Картинки лежат на диске и, в отличие от роликов, ждут человека трое
    суток. Записи в базе оставляем — по ним видно историю; удаляем только
    файлы и строки с путями к ним.
    """
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT id FROM frame_drafts
            WHERE expires_at IS NOT NULL
              AND expires_at <= datetime('now')
              AND status != 'expired'
            """
        ).fetchall()

        for row in rows:
            draft_dir = os.path.join("media", "frames", str(row["id"]))
            try:
                shutil.rmtree(draft_dir, ignore_errors=True)
            except Exception as e:
                print(f"[cleanup] Не удалось удалить {draft_dir}: {e}", flush=True)

            db.execute("DELETE FROM frame_images WHERE draft_id = ?", (row["id"],))
            db.execute("UPDATE frame_drafts SET status = 'expired' WHERE id = ?", (row["id"],))

        if rows:
            db.commit()
            print(f"[cleanup] Удалено просроченных черновиков кадров: {len(rows)}", flush=True)
    finally:
        db.close()


async def _cleanup_once():
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT id, video_path FROM generations
            WHERE video_path IS NOT NULL
              AND expires_at IS NOT NULL
              AND expires_at <= datetime('now')
            """
        ).fetchall()

        for row in rows:
            video_path = row["video_path"]
            if video_path:
                disk_path = video_path.lstrip("/")  # "/media/x.mp4" -> "media/x.mp4"
                try:
                    if os.path.exists(disk_path):
                        os.remove(disk_path)
                except Exception as e:
                    print(f"[cleanup] Не удалось удалить {disk_path}: {e}", flush=True)

            db.execute("UPDATE generations SET video_path = NULL WHERE id = ?", (row["id"],))

        if rows:
            db.commit()
            print(f"[cleanup] Удалено просроченных видео: {len(rows)}", flush=True)
    finally:
        db.close()


async def run_cleanup_loop():
    """Бесконечный цикл — вызывается один раз при старте приложения (main.py)."""
    while True:
        try:
            await _cleanup_once()
        except Exception as e:
            print(f"[cleanup] Ошибка в цикле очистки: {e}", flush=True)

        try:
            await _cleanup_frame_drafts()
        except Exception as e:
            print(f"[cleanup] Ошибка в очистке черновиков кадров: {e}", flush=True)

        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
