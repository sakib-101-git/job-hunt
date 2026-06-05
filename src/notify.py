"""
Telegram notifications.

send_job_alert() — called by main.py, sends alert to all subscribers.
Running `python -m src.notify` starts the polling bot for button callbacks.

Bot setup:
1. Create bot via @BotFather, get TOKEN
2. Share the bot link with friends — they send /start to subscribe
3. Put TOKEN in .env; TELEGRAM_CHAT_ID is optional (owner default subscriber)
"""
import asyncio
import logging
from datetime import datetime, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

log = logging.getLogger("jobhunt.notify")


# ------------------------------------------------------------------ #
# Sending (called from main.py — runs synchronously via asyncio.run)

def send_job_alert(job, config) -> bool:
    try:
        asyncio.run(_send_alert_async(job, config))
        return True
    except Exception as exc:
        log.error(f"Telegram alert failed for '{job.title}': {exc}")
        return False


async def _send_alert_async(job, config):
    from src.db import get_subscribers
    subscribers = get_subscribers(config.db_path)

    # Fall back to the owner chat_id if nobody has /start-ed yet
    if not subscribers and config.telegram_chat_id:
        subscribers = [config.telegram_chat_id]

    if not subscribers:
        log.warning("No Telegram subscribers — nobody will receive this alert.")
        return

    bot = Bot(token=config.telegram_bot_token)

    posted = ""
    if job.posted_date:
        age_h = int((datetime.now(timezone.utc) - job.posted_date).total_seconds() / 3600)
        posted = f"\n⏰ Posted: {age_h}h ago"

    salary = f"\n💰 {_md(job.salary_range)}" if getattr(job, "salary_range", None) else ""

    text = (
        f"🎯 *{_md(job.title)}* at *{_md(job.company)}*\n"
        f"📍 {_md(job.location or 'Location not specified')}\n"
        f"⭐ Fit Score: {job.fit_score}/10\n"
        f"💬 \"{_md(job.fit_reason or '')}\""
        f"{salary}\n"
        f"🔗 [View Job]({_escape_url(job.url)})"
        f"{posted}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Applied", callback_data=f"applied:{job.db_id}"),
            InlineKeyboardButton("⏭ Skip", callback_data=f"skipped:{job.db_id}"),
            InlineKeyboardButton("🚫 Not Relevant", callback_data=f"irrelevant:{job.db_id}"),
        ]
    ])

    for chat_id in subscribers:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            log.warning(f"Failed to send to chat_id {chat_id}: {exc}")


def send_daily_summary(config):
    try:
        asyncio.run(_send_summary_async(config))
    except Exception as exc:
        log.error(f"Daily summary failed: {exc}")


async def _send_summary_async(config):
    from src.db import get_stats, get_subscribers
    stats = get_stats(config.db_path)
    by_status = stats.get("by_status", {})
    total = sum(by_status.values())
    lines = [
        "📊 *JobHunt Stats*",
        f"Total tracked: {total}",
    ]
    for status, cnt in sorted(by_status.items()):
        lines.append(f"  • {status}: {cnt}")
    text = "\n".join(lines)

    subscribers = get_subscribers(config.db_path)
    if not subscribers and config.telegram_chat_id:
        subscribers = [config.telegram_chat_id]

    bot = Bot(token=config.telegram_bot_token)
    for chat_id in subscribers:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception as exc:
            log.warning(f"Summary failed for chat_id {chat_id}: {exc}")


# ------------------------------------------------------------------ #
# Polling bot (runs as a separate long-lived process)

def run_bot(config):
    app = Application.builder().token(config.telegram_bot_token).build()
    app.bot_data["config"] = config

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("stop", _cmd_stop))
    app.add_handler(CommandHandler("stats", _cmd_stats))
    app.add_handler(CommandHandler("pause", _cmd_pause))
    app.add_handler(CommandHandler("resume", _cmd_resume))
    app.add_handler(CallbackQueryHandler(_callback_handler))

    log.info("Telegram bot polling started")
    app.run_polling()


async def _cmd_start(update, context):
    from src.db import add_subscriber
    config = context.bot_data["config"]
    chat_id = str(update.effective_chat.id)
    username = update.effective_user.username if update.effective_user else None
    add_subscriber(config.db_path, chat_id, username)
    await update.message.reply_text(
        "👋 Subscribed! You'll now receive job alerts.\n"
        "Commands: /stats — application summary\n"
        "/stop — unsubscribe from alerts\n"
        "/pause — pause notifications\n/resume — resume notifications\n\n"
        f"Your chat ID: `{chat_id}`"
    )


async def _cmd_stop(update, context):
    from src.db import remove_subscriber
    config = context.bot_data["config"]
    chat_id = str(update.effective_chat.id)
    remove_subscriber(config.db_path, chat_id)
    await update.message.reply_text("🔕 Unsubscribed. Send /start to re-subscribe.")


async def _cmd_stats(update, context):
    config = context.bot_data["config"]
    await _send_summary_async(config)


async def _cmd_pause(update, context):
    context.bot_data["paused"] = True
    await update.message.reply_text("⏸ Notifications paused. Use /resume to restart.")


async def _cmd_resume(update, context):
    context.bot_data["paused"] = False
    await update.message.reply_text("▶️ Notifications resumed.")


async def _callback_handler(update, context):
    from src.db import update_status
    query = update.callback_query
    await query.answer()

    try:
        action, job_id_str = query.data.split(":", 1)
        job_id = int(job_id_str)
        config = context.bot_data["config"]
        update_status(config.db_path, job_id, action)
        labels = {
            "applied": "Marked as applied ✅",
            "skipped": "Skipped ⏭",
            "irrelevant": "Marked irrelevant 🚫",
        }
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(labels.get(action, f"Status: {action}"))
    except Exception as exc:
        log.error(f"Callback handler error: {exc}")
        await query.message.reply_text("⚠️ Error updating status.")


def _md(text: str) -> str:
    """Escape Telegram MarkdownV2 special chars."""
    for ch in ["\\", "_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]:
        text = text.replace(ch, f"\\{ch}")
    return text


def _escape_url(url: str) -> str:
    """Escape only ) and \ inside a MarkdownV2 link URL."""
    return url.replace("\\", "\\\\").replace(")", "\\)")


if __name__ == "__main__":
    from src.config import load_config
    from src.utils import setup_logging
    setup_logging("INFO")
    cfg = load_config()

    import sys
    if "--test" in sys.argv:
        from src.models import ScoredJob
        from datetime import datetime, timezone
        test_job = ScoredJob(
            source="test", source_job_id="999", title="Python Developer",
            company="Test Company", location="Dhaka, Bangladesh",
            url="https://example.com/job/999", jd_text="Test JD",
            fit_score=8, fit_reason="Strong Python match", db_id=999,
            posted_date=datetime.now(timezone.utc),
        )
        send_job_alert(test_job, cfg)
        print("Test alert sent.")
    else:
        run_bot(cfg)
