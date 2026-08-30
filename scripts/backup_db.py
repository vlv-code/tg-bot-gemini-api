#!/usr/bin/env python3
"""Скрипт для создания горячего онлайн-бэкапа SQLite базы данных бота.

Использует команду SQLite VACUUM INTO, которая гарантирует консистентность
и целостность бэкапа даже при активной параллельной записи в WAL-режиме.
"""

import argparse
import datetime
import os
import sqlite3
import sys


def backup_database(
    db_path: str = "data/bot.db", backup_dir: str = "data/backups", keep_days: int = 30
) -> str:
    if not os.path.exists(db_path):
        print(f"Ошибка: файл базы данных не найден: {db_path}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target_filename = f"bot_backup_{timestamp}.db"
    target_path = os.path.join(backup_dir, target_filename)

    print(f"Создание бэкапа из {db_path} в {target_path}...")

    # Открываем базу и выполняем VACUUM INTO
    conn = sqlite3.connect(db_path)
    try:
        safe_target = target_path.replace("'", "''")
        conn.execute(f"VACUUM INTO '{safe_target}'")
        print(
            f"Бэкап успешно создан: {target_path} ({os.path.getsize(target_path):,} байт)"
        )
    finally:
        conn.close()

    # Очистка старых бэкапов старше keep_days
    if keep_days > 0:
        cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
        for fname in os.listdir(backup_dir):
            if fname.startswith("bot_backup_") and fname.endswith(".db"):
                fpath = os.path.join(backup_dir, fname)
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
                if mtime < cutoff:
                    try:
                        os.remove(fpath)
                        print(f"Удалён старый бэкап: {fname}")
                    except Exception as e:
                        print(
                            f"Предупреждение: не удалось удалить {fname}: {e}",
                            file=sys.stderr,
                        )

    return target_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Онлайн-бэкап базы данных бота (VACUUM INTO)"
    )
    parser.add_argument(
        "--db",
        default=os.getenv("DB_PATH", "data/bot.db"),
        help="Путь к SQLite базе данных",
    )
    parser.add_argument(
        "--backup-dir",
        default=os.getenv("BACKUP_DIR", "data/backups"),
        help="Директория для сохранения бэкапов",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=30,
        help="Хранить бэкапы за последние N дней (0 - без удаления)",
    )
    args = parser.parse_args()

    backup_database(
        db_path=args.db, backup_dir=args.backup_dir, keep_days=args.keep_days
    )


if __name__ == "__main__":
    main()
