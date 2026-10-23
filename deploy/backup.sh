#!/usr/bin/env bash
#
# Consistent backup of the SQLite database.
#
# Usage:
#   ./deploy/backup.sh
#
# Environment overrides:
#   APP_DIR      Project directory (default: parent of this script)
#   DB_FILE      SQLite database path (default: $APP_DIR/expenses.db)
#   BACKUP_DIR   Destination directory (default: $APP_DIR/backups)
#   KEEP_DAYS    Delete backups older than this many days (default: 30)
#
set -euo pipefail

umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
DB_FILE="${DB_FILE:-$APP_DIR/expenses.db}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"

if [[ ! -f "$DB_FILE" ]]; then
    echo "Database not found: $DB_FILE" >&2
    exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "sqlite3 CLI is required (apt install sqlite3)" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

timestamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/expenses-$timestamp.db"

# .backup is safe while the bot is running (unlike a plain file copy).
sqlite3 "$DB_FILE" ".backup '$target'"
gzip -f "$target"

# Prune old backups.
find "$BACKUP_DIR" -type f -name 'expenses-*.db.gz' -mtime +"$KEEP_DAYS" -delete

echo "Backup created: $target.gz"
