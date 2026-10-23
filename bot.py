"""
Telegram Expense Tracker Bot -Bot Core Logic & Telegram Handlers.

- Authentication: only the Telegram user id(s) in TELEGRAM_USER_ID (env) may interact.
- Commands: /start, /summary, /last [n], /delete [id]
- Message handler: parses natural text like "Coffee 3.50" and stores the expense.
"""

import logging
import os
import re
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_IDS = {int(uid) for uid in os.getenv("TELEGRAM_USER_ID", "").split(",") if uid.strip()}
DEFAULT_CATEGORY = "General"
# An amount is an integer or decimal with up to 2 places: "3", "3.50", "12,50".
# Two patterns are used so multi-number descriptions keep the right amount:
#   "Milk 2 liters 3.50" -> item="Milk 2 liters", amount=3.50
#   "3.50 Coffee"        -> item="Coffee", amount=3.50
_AMOUNT = r"[0-9]+(?:[.,][0-9]{1,2})?"
_AMOUNT_AT_END = re.compile(rf"^(?P<item>.+?)\s+(?P<amount>{_AMOUNT})$")
_AMOUNT_AT_START = re.compile(rf"^(?P<amount>{_AMOUNT})\s+(?P<item>.+)$")


def authorized(update: Update) -> bool:
    """Check that the message comes from an allowed Telegram user id."""
    user_id = update.effective_user.id if update.effective_user else None
    return user_id in ALLOWED_USER_IDS


def parse_expense(text: str) -> tuple[str, float] | None:
    """
    Parse a message like "Coffee 3.50", "3.50 Coffee" or "Milk 2 liters 3.50"
    into (item, amount). Returns None if no valid amount is found.
    """
    text = text.strip()
    match = _AMOUNT_AT_END.match(text) or _AMOUNT_AT_START.match(text)
    if not match:
        return None

    try:
        amount = float(match.group("amount").replace(",", "."))
    except ValueError:
        return None

    item = match.group("item").strip()
    if not item:
        return None

    return item, amount


def _format_timestamp(timestamp: str) -> str:
    """Render a stored UTC ISO timestamp in the local timezone."""
    dt = datetime.fromisoformat(timestamp).astimezone(db.LOCAL_TZ)
    return dt.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start - welcome message and instructions."""
    if not authorized(update):
        return
    await update.message.reply_text(
        "👋 Welcome to your Expense Tracker!\n\n"
        "Log an expense by simply typing:\n"
        "    <item> <amount>   e.g.  Coffee 3.50\n\n"
        "Available commands:\n"
        "/summary - this month's total & category breakdown\n"
        "/last [n] - view the last n transactions (default 5)\n"
        "/delete [id] - remove an entry by its database id"
    )


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/summary - this month's total spending and per-category breakdown."""
    if not authorized(update):
        return
    user_id = update.effective_user.id
    now = datetime.now(db.LOCAL_TZ)
    total, breakdown = db.monthly_summary(user_id, now.year, now.month)

    lines = [f"📊 *Spending summary for {now.strftime('%B %Y')}*", f"Total: *{total:.2f}*"]
    if breakdown:
        lines.append("\nBy category:")
        for row in breakdown:
            lines.append(f"  • {row['category']}: {row['total']:.2f}")
    else:
        lines.append("No expenses recorded this month yet.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/last [n] - view the last n recorded transactions."""
    if not authorized(update):
        return
    user_id = update.effective_user.id

    n = 5
    if context.args:
        try:
            n = max(1, int(context.args[0]))
        except ValueError:
            await update.message.reply_text("Usage: /last [n]  (n must be a number)")
            return
    n = min(n, 50)

    rows = db.get_last_expenses(n, user_id)
    if not rows:
        await update.message.reply_text("No expenses recorded yet.")
        return

    lines = [f"🕐 Last {len(rows)} transactions:"]
    for row in rows:
        lines.append(
            f"  #{row['id']} | {_format_timestamp(row['timestamp'])} | "
            f"{row['item']} - {row['amount']:.2f} [{row['category']}]"
        )
    await update.message.reply_text("\n".join(lines))


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delete [id] - remove an erroneous entry by its database id."""
    if not authorized(update):
        return
    user_id = update.effective_user.id

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /delete [id]  (see /last for ids)")
        return

    expense_id = int(context.args[0])
    if db.delete_expense(expense_id, user_id):
        await update.message.reply_text(f"🗑️ Deleted entry #{expense_id}.")
    else:
        await update.message.reply_text(
            f"⚠️ Entry #{expense_id} not found (or it isn't yours)."
        )


# ---------------------------------------------------------------------------
# Expense message handler
# ---------------------------------------------------------------------------

async def handle_expense(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parse plain-text messages like 'Coffee 3.50' and store the expense."""
    if not authorized(update):
        return
    user_id = update.effective_user.id

    parsed = parse_expense(update.message.text)
    if parsed is None:
        await update.message.reply_text(
            "❌ Couldn't parse that. Use the format:  <item> <amount>  e.g.  Coffee 3.50"
        )
        return

    item, amount = parsed
    expense_id = db.add_expense(item, amount, user_id, category=DEFAULT_CATEGORY)

    now = datetime.now(db.LOCAL_TZ)
    day_total = db.daily_total(user_id)
    month_total, _ = db.monthly_summary(user_id, now.year, now.month)

    await update.message.reply_text(
        f"✅ Added: {item} - {amount:.2f} [{DEFAULT_CATEGORY}] (#{expense_id})\n"
        f"Today: {day_total:.2f} | This month: {month_total:.2f}"
    )


async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently ignore (or politely decline) messages from strangers."""
    logger.warning(
        "Unauthorized access attempt from user_id=%s", update.effective_user.id
    )
    await update.message.reply_text("⛔ You are not authorized to use this bot.")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and configure it.")
    if not ALLOWED_USER_IDS:
        raise SystemExit("TELEGRAM_USER_ID is not set. Copy .env.example to .env and configure it.")
    db.init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    auth_filter = filters.User(user_id=list(ALLOWED_USER_IDS))
    app.add_handler(CommandHandler("start", start, filters=auth_filter))
    app.add_handler(CommandHandler("summary", summary, filters=auth_filter))
    app.add_handler(CommandHandler("last", last, filters=auth_filter))
    app.add_handler(CommandHandler("delete", delete, filters=auth_filter))
    # Plain text messages from authorized users -> expense parsing
    app.add_handler(MessageHandler(auth_filter & filters.TEXT & ~filters.COMMAND, handle_expense))
    # Anything else -> unauthorized notice
    app.add_handler(MessageHandler(~auth_filter, unauthorized))
    logger.info("Bot started. Polling for updates...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
