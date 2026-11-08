"""Backup fayllari `backup` konteyneri (pg_dump, postgres bilan bir xil versiya) tomonidan yoziladi; ilova faqat o'qiydi."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

TELEGRAM_UPLOAD_LIMIT = 50 * 1024 * 1024


@dataclass
class BackupFile:
    path: Path
    size: int
    created_at: datetime


def latest_backup(directory: str) -> BackupFile | None:
    folder = Path(directory)
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    stat = files[0].stat()
    return BackupFile(files[0], stat.st_size, datetime.fromtimestamp(stat.st_mtime, tz=UTC))
