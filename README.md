# Telegram Expense Tracker Bot

A lightweight, self-hosted Telegram bot for logging expenses with quick natural
text messages. Data is stored locally in SQLite — no external cloud services.

- **Stack:** Python 3.10+, [`python-telegram-bot`](https://python-telegram-bot.org/) v22, SQLite
- **Target host:** small VPS (1 vCPU / 1 GB RAM), Debian/Ubuntu, managed by systemd

## Features

- Log an expense by sending a plain message: `Coffee 3.50`
- Per-user data: every expense is tied to the sender's Telegram user id
- Commands: `/start`, `/summary`, `/last [n]`, `/delete [id]`
- Day/month totals computed in the `Europe/Rome` timezone
- Database and schema are created automatically on first start
- Optional weekly/daily backups with automatic pruning

## Project layout

```
.
├── bot.py                  # Telegram handlers and bot entrypoint
├── database.py             # SQLite schema and queries
├── requirements.txt        # Pinned dependencies
├── .env.example            # Configuration template
└── deploy/
    ├── telegrambot.service # systemd unit
    └── backup.sh           # Consistent SQLite backup script
```

`expenses.db` is created in the **current working directory** on first run. It is
git-ignored and should never be committed.

## Configuration

Copy the template and fill in the values:

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Description |
| --- | --- |
| `BOT_TOKEN` | Token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_USER_ID` | Numeric Telegram user id(s) allowed to use the bot. Separate multiple ids with commas: `111111111,222222222` |

To find your numeric user id, message [@userinfobot](https://t.me/userinfobot).
Alternatively, message your own bot and check its logs: unauthorized senders are
logged with their id.

> `TELEGRAM_USER_ID` is **not** your username or the bot token.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit it
python bot.py
```

The database is initialized automatically. To create/check it manually:

```bash
python database.py
```

## Usage

| Input | Result |
| --- | --- |
| `Coffee 3.50` | Logs an expense |
| `Milk 2 liters 3.50` | Logs `Milk 2 liters` for `3.50` |
| `3.50 Coffee` | Amount-first form is also accepted |
| `Coffee 12,50` | Comma decimals are accepted |
| `/summary` | Current month total and per-category breakdown |
| `/last 10` | Last 10 transactions |
| `/delete 42` | Delete entry `#42` |

Multi-number descriptions are supported: the amount is taken from the start or
the end of the message. Anything else in the middle is kept as the item name.

### Sharing the bot

Add each friend's numeric id to `TELEGRAM_USER_ID`, comma-separated, then
restart the bot. All users share the same bot but only see and delete their own
rows, because every query is filtered by the sender's Telegram id.

This is logical separation, not encryption: whoever can read `expenses.db` on
the server can read all data.

## Deployment on a VPS

The following assumes Debian/Ubuntu and installs the app in
`/opt/expense-tg-tracker`, matching `deploy/telegrambot.service`.

### 1. System packages and service user

```bash
sudo apt update
sudo apt install -y python3 python3-venv sqlite3 tzdata git

# Dedicated system user (default home: /home/telegrambot)
sudo useradd --system --shell /usr/sbin/nologin telegrambot

# App directory, separate from the user's home
sudo mkdir -p /opt/expense-tg-tracker
sudo chown telegrambot:telegrambot /opt/expense-tg-tracker
```

### 2. Install the application

```bash
# Create a read-only deploy key for the telegrambot user
sudo -u telegrambot ssh-keygen -t ed25519 -N "" -f /home/telegrambot/.ssh/id_ed25519
sudo -u telegrambot ssh-keyscan github.com >> /home/telegrambot/.ssh/known_hosts
sudo cat /home/telegrambot/.ssh/id_ed25519.pub
```

Add the printed public key on GitHub: repository → **Settings → Deploy keys →
Add deploy key** (read-only access is enough).

```bash
sudo -u telegrambot git clone git@github.com:rosagiulietto/expense-tg-tracker.git /opt/expense-tg-tracker
cd /opt/expense-tg-tracker

sudo -u telegrambot python3 -m venv .venv
sudo -u telegrambot .venv/bin/pip install -r requirements.txt

sudo -u telegrambot cp .env.example .env
sudo -u telegrambot chmod 600 .env
sudo -u telegrambot nano .env   # BOT_TOKEN + TELEGRAM_USER_ID
```

### 3. Install the systemd service

```bash
sudo cp deploy/telegrambot.service /etc/systemd/system/telegrambot.service
sudo systemctl daemon-reload
sudo systemctl enable --now telegrambot
```

Check status and logs:

```bash
sudo systemctl status telegrambot
journalctl -u telegrambot -f
```

Useful commands:

```bash
sudo systemctl restart telegrambot   # after changing .env
sudo systemctl stop telegrambot
```

If you use a different path or username, edit `User`, `WorkingDirectory` and
`ExecStart` in the unit file before installing it.

### 4. Backups

`deploy/backup.sh` uses SQLite's `.backup` command, so it is safe while the bot
is running. It writes gzipped copies to `backups/` and deletes files older than
30 days.

```bash
chmod +x deploy/backup.sh
./deploy/backup.sh
```

Schedule it with cron (as the service user):

```bash
sudo crontab -u telegrambot -e
# Daily at 03:00
0 3 * * * /opt/expense-tg-tracker/deploy/backup.sh
```

Overridable variables: `APP_DIR`, `DB_FILE`, `BACKUP_DIR`, `KEEP_DAYS`.

Restore a backup (stop the bot first):

```bash
sudo systemctl stop telegrambot
gunzip -c backups/expenses-YYYYMMDD-HHMMSS.db.gz > expenses.db
sudo systemctl start telegrambot
```

Consider copying backups off the VPS as well; a backup on the same disk does
not protect against disk loss.

## Timezone

Timestamps are stored in UTC. Daily and monthly totals are computed using
`Europe/Rome` (`LOCAL_TZ` in `database.py`). To use another timezone, change that
constant. On minimal systems, make sure the timezone database is installed:
`sudo apt install tzdata`.

## Security notes

- Never commit `.env` or `expenses.db`; both are in `.gitignore`.
- Keep `.env` readable only by the service user (`chmod 600`).
- The bot only accepts ids listed in `TELEGRAM_USER_ID`; everyone else is rejected.
- The systemd unit sets `UMask=0077` so the database is not world-readable.
- If the token leaks, revoke it via [@BotFather](https://t.me/BotFather) and update `.env`.

## Troubleshooting

**Bot starts but ignores me** — your numeric id is missing from
`TELEGRAM_USER_ID`. Check `journalctl -u telegrambot` for the logged id, add it,
and restart the service.

**`BOT_TOKEN is not set`** — `.env` is missing or not in the working directory.
The unit's `WorkingDirectory` must be the project root.

**Data “disappears” after restart** — the bot was started from a different
directory, creating a second `expenses.db`. Always start it from the project
root (the systemd unit handles this via `WorkingDirectory`).

**`No time zone found with key Europe/Rome`** — install the timezone database:
`sudo apt install tzdata`.
