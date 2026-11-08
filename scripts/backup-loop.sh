#!/bin/sh
# Har BACKUP_INTERVAL_SECONDS da pg_dump (custom format), BACKUP_KEEP_DAYS kundan eskilarini o'chiradi.
# Avval .tmp ga yoziladi — ilova yarim yozilgan faylni "eng yangi backup" deb olmasin.
set -eu
mkdir -p /backups
while true; do
  file="/backups/samsara_bot_$(date -u +%Y%m%d_%H%M%S).dump"
  if pg_dump --format=custom --file="${file}.tmp"; then
    mv "${file}.tmp" "${file}"
    echo "backup ok: ${file} ($(du -h "${file}" | cut -f1))"
  else
    rm -f "${file}.tmp"
    echo "backup FAILED" >&2
  fi
  find /backups -name 'samsara_bot_*.dump' -mtime +"${BACKUP_KEEP_DAYS:-14}" -delete
  sleep "${BACKUP_INTERVAL_SECONDS:-86400}"
done
