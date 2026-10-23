"""
Telegram Expense Tracker Bot -Bot Core Logic & Telegram Handlers.

- Authentication: only the Telegram user id(s) in TELEGRAM_USER_ID (env) may interact.
- Commands: /start, /summary, /today, /week, /last [n], /undo, /delete [id], /export, /report
- Message handler: parses natural text like "Coffee 3.50" and stores the expense.
- Phase 4: keyword auto-categorization, CSV export, monthly chart + scheduled report.
"""

import csv
import io
import logging
import os
import re
import unicodedata
from calendar import month_name
from datetime import datetime, time as dt_time, timedelta

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
import reports

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_IDS = {int(uid) for uid in os.getenv("TELEGRAM_USER_ID", "").split(",") if uid.strip()}
DEFAULT_CATEGORY = "General"

# Phase 4: keyword-based auto-tagging. Keywords are matched as whole words on a
# lowercased, accent-free version of the item (so "Caffè" matches "caffe").
CATEGORY_KEYWORDS = {
    "Groceries": (
        "supermarket", "supermercato", "esselunga", "coop", "conad", "lidl",
        "aldi", "carrefour", "pasta", "pane", "latte", "frutta", "verdura",
        "spesa", "macelleria", "pescheria",
    ),
    "Alcohol": (
        "birra", "vino", "drink",
    ),
    "Food": (
        "caffe", "coffee", "pizza", "ristorante", "restaurant", "bar",
        "pranzo", "cena", "colazione", "gelato", "pasticceria", "delivery",
        "panino", "hamburger", "sushi",
    ),
    "Transport": (
        "uber", "taxi", "bus", "treno", "train", "metro", "benzina", "fuel",
        "parcheggio", "parking", "biglietto", "traghetto", "aereo", "volo",
        "autostrada", "pedaggio",
    ),
    "Bills": (
        "bolletta", "luce", "gas", "acqua", "internet", "wifi", "telefono",
        "ricarica", "abbonamento", "affitto", "rent", "condominio",
    ),
    "Health": (
        "farmacia", "pharmacy", "medico", "doctor", "dentista", "visita",
        "ospedale", "palestra", "gym", "analisi",
    ),
    "Shopping": (
        "amazon", "vestiti", "shopping", "scarpe", "libri", "libro", "regalo",
        "gift", "elettronica", "ikea",
    ),
    "Entertainment": (
        "cinema", "netflix", "spotify", "concerto", "teatro", "videogioco",
        "game", "mostra", "museo",
    ),
}

# An amount is an integer or decimal with up to 2 places: "3", "3.50", "12,50".
# Two patterns are used so multi-number descriptions keep the right amount:
#   "Milk 2 liters 3.50" -> item="Milk 2 liters", amount=3.50
#   "3.50 Coffee"        -> item="Coffee", amount=3.50
_AMOUNT = r"[0-9]+(?:[.,][0-9]{1,2})?"
_AMOUNT_AT_END = re.compile(rf"^(?P<item>.+?)\s+(?P<amount>{_AMOUNT})$")
_AMOUNT_AT_START = re.compile(rf"^(?P<amount>{_AMOUNT})\s+(?P<item>.+)$")


def authorized(update: Update) -> bool:
    """
    Check that the message comes from an allowed Telegram user id.
    """
    user_id = update.effective_user.id if update.effective_user else None
    return user_id in ALLOWED_USER_IDS


def _format_timestamp(timestamp: str) -> str:
    """
    Render a stored UTC ISO timestamp in the local timezone.
    """
    dt = datetime.fromisoformat(timestamp).astimezone(db.LOCAL_TZ)
    return dt.strftime("%Y-%m-%d %H:%M")


def _normalize(text: str) -> str:
    """
    Lowercase and strip accents for keyword matching.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def guess_category(item: str) -> str:
    """
    Return the first matching category for an item, or the default one.
    """
    text = _normalize(item)
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                return category
    return DEFAULT_CATEGORY


_KNOWN_CATEGORY_ALIASES = {
    _normalize(name): name for name in (DEFAULT_CATEGORY, *CATEGORY_KEYWORDS)
}
_HASHTAG_AT_END = re.compile(r"\s+#(?P<category>\S+)\s*$")
_LAST_WORD = re.compile(r"\s+(?P<word>\S+)\s*$")


def _extract_manual_category(text: str) -> tuple[str, str | None]:
    """
    Extract an optional category at the end of the message:
    "Coffee 3.50 #food", "Coffee 3.50 food" or "Coffee 3.50 #vacanze".
    """
    match = _HASHTAG_AT_END.search(text)
    if match:
        category = match.group("category").strip()[:40]
        if category:
            category = _KNOWN_CATEGORY_ALIASES.get(_normalize(category), category)
            return text[: match.start()].strip(), category

    match = _LAST_WORD.search(text)
    if match:
        category = _KNOWN_CATEGORY_ALIASES.get(_normalize(match.group("word")))
        if category:
            return text[: match.start()].strip(), category

    return text, None


def parse_expense_with_category(
    text: str,
) -> tuple[str, float, str | None] | None:
    """
    Parse a message into (item, amount, manual_category).
    The category is None when it wasn't specified explicitly.
    """
    text, manual_category = _extract_manual_category(text.strip())
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

    return item, amount, manual_category


def parse_expense(text: str) -> tuple[str, float] | None:
    """Backward-compatible parser returning only (item, amount)."""
    parsed = parse_expense_with_category(text)
    if parsed is None:
        return None
    item, amount, _ = parsed
    return item, amount


def _parse_month_arg(args: list[str]) -> tuple[int, int] | None:
    """
    Parse an optional YYYY-MM argument; default to the current local month.
    """
    now = datetime.now(db.LOCAL_TZ)
    if not args:
        return now.year, now.month

    match = re.fullmatch(r"(\d{4})-(\d{1,2})", args[0])
    if not match:
        return None

    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return year, month


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start - welcome message and instructions.
    """
    if not authorized(update):
        return
    await update.message.reply_text(
        "👋 <b>Welcome to your Expense Tracker!</b>\n\n"
        "Add an expense by sending a message like:\n"
        "    <code>Coffee 3.50</code>\n"
        "    <code>Milk 2 liters 3.50</code>\n"
        "Override the category with <code>#food</code>:\n"
        "    <code>Coffee 3.50 #food</code>\n\n"
        "<b>Commands</b>\n"
        "<code>/summary</code> — this month's total &amp; category breakdown\n"
        "<code>/today</code> — today's total\n"
        "<code>/week</code> — this week's total\n"
        "<code>/last [n]</code> — view the last n transactions (default 5)\n"
        "<code>/undo</code> — remove the last transaction\n"
        "<code>/delete [id]</code> — remove an entry by its database id\n"
        "<code>/export</code> — download all expenses as CSV\n"
        "<code>/report [YYYY-MM]</code> — spending chart for a month",
        parse_mode="HTML",
    )


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /summary - this month's total spending and per-category breakdown.
    """
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


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/today - total spending for today."""
    if not authorized(update):
        return
    user_id = update.effective_user.id

    now = datetime.now(db.LOCAL_TZ)
    day = now.date()
    total, count = db.period_summary(
        user_id, day.isoformat(), (day + timedelta(days=1)).isoformat()
    )
    plural = "transaction" if count == 1 else "transactions"
    await update.message.reply_text(
        f"📅 <b>Today</b> ({day:%d %b %Y})\n"
        f"Total: <b>{total:.2f}</b>\n"
        f"{count} {plural}",
        parse_mode="HTML",
    )


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/week - total spending for the current week (Monday to Sunday)."""
    if not authorized(update):
        return
    user_id = update.effective_user.id

    now = datetime.now(db.LOCAL_TZ)
    start = now.date() - timedelta(days=now.weekday())
    end = start + timedelta(days=7)
    total, count = db.period_summary(user_id, start.isoformat(), end.isoformat())
    last_day = end - timedelta(days=1)
    plural = "transaction" if count == 1 else "transactions"
    await update.message.reply_text(
        f"🗓️ <b>This week</b> ({start:%d %b} – {last_day:%d %b %Y})\n"
        f"Total: <b>{total:.2f}</b>\n"
        f"{count} {plural}",
        parse_mode="HTML",
    )


async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /last [n] - view the last n recorded transactions.
    """
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


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/undo - remove the most recent transaction."""
    if not authorized(update):
        return
    user_id = update.effective_user.id

    rows = db.get_last_expenses(1, user_id)
    if not rows:
        await update.message.reply_text("No expenses recorded yet.")
        return

    row = rows[0]
    if db.delete_expense(row["id"], user_id):
        await update.message.reply_text(
            f"↩️ Removed #{row['id']}: {row['item']} - "
            f"{row['amount']:.2f} [{row['category']}]"
        )
    else:
        await update.message.reply_text("⚠️ Could not remove the last transaction.")


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /delete [id] - remove an erroneous entry by its database id.
    """
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


async def export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /export - send all expenses as a CSV file.
    """
    if not authorized(update):
        return
    user_id = update.effective_user.id

    rows = db.get_all_expenses(user_id)
    if not rows:
        await update.message.reply_text("No expenses recorded yet.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "timestamp_local", "timestamp_utc", "item", "amount", "category"])
    for row in rows:
        writer.writerow([
            row["id"],
            _format_timestamp(row["timestamp"]),
            row["timestamp"],
            row["item"],
            f"{row['amount']:.2f}",
            row["category"],
        ])

    # utf-8-sig adds a BOM so Excel opens the file correctly.
    data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    filename = f"expenses-{datetime.now(db.LOCAL_TZ):%Y%m%d}.csv"
    await update.message.reply_document(
        document=data,
        filename=filename,
        caption=f"📄 {len(rows)} expenses",
    )


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /report [YYYY-MM] - chart of spending by category for a month.
    """
    if not authorized(update):
        return
    user_id = update.effective_user.id

    parsed = _parse_month_arg(context.args)
    if parsed is None:
        await update.message.reply_text("Usage: /report [YYYY-MM]  e.g. /report 2026-10")
        return
    year, month = parsed

    try:
        image = reports.build_monthly_report(user_id, year, month)
    except Exception:
        logger.exception("Failed to build report for user_id=%s", user_id)
        await update.message.reply_text("⚠️ Could not generate the report.")
        return

    await update.message.reply_photo(
        photo=image,
        caption=f"📊 {month_name[month]} {year}",
    )


async def send_monthly_reports(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Job run on the 1st of each month: send the previous month's chart.
    """
    now = datetime.now(db.LOCAL_TZ)
    previous = now.replace(day=1) - timedelta(days=1)

    for user_id in ALLOWED_USER_IDS:
        try:
            image = reports.build_monthly_report(user_id, previous.year, previous.month)
            await context.bot.send_photo(
                chat_id=user_id,
                photo=image,
                caption=f"📊 {month_name[previous.month]} {previous.year} monthly report",
            )
        except Exception:
            logger.exception("Failed to send monthly report to user_id=%s", user_id)


# ---------------------------------------------------------------------------
# Expense message handler
# ---------------------------------------------------------------------------

async def handle_expense(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Parse plain-text messages like 'Coffee 3.50' and store the expense.
    """
    if not authorized(update):
        return
    user_id = update.effective_user.id

    parsed = parse_expense_with_category(update.message.text)
    if parsed is None:
        await update.message.reply_text(
            "❌ Couldn't parse that. Use the format:  <item> <amount>  e.g.  Coffee 3.50"
        )
        return

    item, amount, manual_category = parsed
    category = manual_category or guess_category(item)
    expense_id = db.add_expense(item, amount, user_id, category=category)

    now = datetime.now(db.LOCAL_TZ)
    day_total = db.daily_total(user_id)
    month_total, _ = db.monthly_summary(user_id, now.year, now.month)

    await update.message.reply_text(
        f"✅ Added: {item} - {amount:.2f} [{category}] (#{expense_id})\n"
        f"Today: {day_total:.2f} | This month: {month_total:.2f}"
    )


async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Silently ignore (or politely decline) messages from strangers.
    """
    logger.warning(
        "Unauthorized access attempt from user_id=%s", update.effective_user.id
    )
    await update.message.reply_text("⛔ You are not authorized to use this bot.")


BOT_COMMANDS = [
    BotCommand("start", "Welcome & instructions"),
    BotCommand("summary", "This month's total & categories"),
    BotCommand("today", "Today's total"),
    BotCommand("week", "This week's total"),
    BotCommand("last", "Show recent transactions"),
    BotCommand("undo", "Remove the last transaction"),
    BotCommand("delete", "Delete an entry by id"),
    BotCommand("export", "Download all expenses as CSV"),
    BotCommand("report", "Monthly spending chart"),
]


async def post_init(application: Application) -> None:
    """Register the command menu shown in Telegram's '/' button."""
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception:
        logger.exception("Could not register the bot command menu")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set. Copy .env.example to .env and configure it.")
    if not ALLOWED_USER_IDS:
        raise SystemExit("TELEGRAM_USER_ID is not set. Copy .env.example to .env and configure it.")
    db.init_db()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    auth_filter = filters.User(user_id=list(ALLOWED_USER_IDS))
    app.add_handler(CommandHandler("start", start, filters=auth_filter))
    app.add_handler(CommandHandler("summary", summary, filters=auth_filter))
    app.add_handler(CommandHandler("today", today, filters=auth_filter))
    app.add_handler(CommandHandler("week", week, filters=auth_filter))
    app.add_handler(CommandHandler("last", last, filters=auth_filter))
    app.add_handler(CommandHandler("undo", undo, filters=auth_filter))
    app.add_handler(CommandHandler("delete", delete, filters=auth_filter))
    app.add_handler(CommandHandler("export", export, filters=auth_filter))
    app.add_handler(CommandHandler("report", report, filters=auth_filter))
    # Plain text messages from authorized users -> expense parsing
    app.add_handler(MessageHandler(auth_filter & filters.TEXT & ~filters.COMMAND, handle_expense))
    # Anything else -> unauthorized notice
    app.add_handler(MessageHandler(~auth_filter, unauthorized))

    # Phase 4: previous month's report on the 1st at 09:00 local time.
    if app.job_queue is not None:
        app.job_queue.run_monthly(
            send_monthly_reports,
            when=dt_time(9, 0, tzinfo=db.LOCAL_TZ),
            day=1,
            name="monthly_report",
        )
    else:
        logger.warning(
            "JobQueue unavailable: install python-telegram-bot[job-queue] "
            "to enable scheduled monthly reports."
        )

    logger.info("Bot started. Polling for updates...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
