"""
Telegram notifications.

send_job_alert() — called by main.py, sends alert + PDFs synchronously.
Running `python -m src.notify` starts the polling bot for button callbacks.

Bot setup:
1. Create bot via @BotFather, get TOKEN
2. Start a chat with the bot, then visit:
   https://api.telegram.org/bot<TOKEN>/getUpdates
3. Put TOKEN + chat_id in .env
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

log = logging.getLogger("jobhunt.notify")


# ------------------------------------------------------------------ #
# Sending (called from main.py — runs synchronously via asyncio.run)

def send_job_alert(job, cv_path: str | None, cl_path: str | None, config) -> bool:
    try:
        asyncio.run(_send_alert_async(job, cv_path, cl_path, config))
        return True
    except Exception as exc:
        log.error(f"Telegram alert failed for '{job.title}': {exc}")
        return False


async def _send_alert_async(job, cv_path, cl_path, config):
    bot = Bot(token=config.telegram_bot_token)

    posted = ""
    if job.posted_date:
        age_h = int((datetime.now(timezone.utc) - job.posted_date).total_seconds() / 3600)
        posted = f"\n⏰ Posted: {age_h}h ago"

    text = (
        f"🎯 *{_md(job.title)}* at *{_md(job.company)}*\n"
        f"📍 {_md(job.location or 'Location not specified')}\n"
        f"⭐ Fit Score: {job.fit_score}/10\n"
        f"💬 \"{_md(job.fit_reason)}\"\n"
        f"🔗 [View Job]({job.url})"
        f"{posted}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Applied", callback_data=f"applied:{job.db_id}"),
            InlineKeyboardButton("⏭ Skip", callback_data=f"skipped:{job.db_id}"),
            InlineKeyboardButton("🚫 Not Relevant", callback_data=f"irrelevant:{job.db_id}"),
        ]
    ])

    await bot.send_message(
        chat_id=config.telegram_chat_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )

    for path, label in [(cv_path, "CV"), (cl_path, "Cover Letter")]:
        if path and Path(path).exists():
            with open(path, "rb") as f:
                await bot.send_document(
                    chat_id=config.telegram_chat_id,
                    document=f,
                    filename=f"{label}_{job.company}.pdf",
                )


def send_daily_summary(config):
    try:
        asyncio.run(_send_summary_async(config))
    except Exception as exc:
        log.error(f"Daily summary failed: {exc}")


async def _send_summary_async(config):
    from src.db import get_stats
    stats = get_stats(config.db_path)
    by_status = stats.get("by_status", {})
    total = sum(by_status.values())
    lines = [
        "📊 *JobHunt Stats*",
        f"Total tracked: {total}",
    ]
    for status, cnt in sorted(by_status.items()):
        lines.append(f"  • {status}: {cnt}")
    bot = Bot(token=config.telegram_bot_token)
    await bot.send_message(
        chat_id=config.telegram_chat_id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


# ------------------------------------------------------------------ #
# Polling bot (runs as a separate long-lived process)

def run_bot(config):
    app = Application.builder().token(config.telegram_bot_token).build()
    app.bot_data["config"] = config

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("stats", _cmd_stats))
    app.add_handler(CommandHandler("pause", _cmd_pause))
    app.add_handler(CommandHandler("resume", _cmd_resume))
    app.add_handler(CallbackQueryHandler(_callback_handler))

    log.info("Telegram bot polling started")
    app.run_polling()


async def _cmd_start(update, context):
    await update.message.reply_text(
        "👋 JobHunt Bot active.\n"
        "Commands: /stats — application summary\n"
        "/pause — stop notifications\n/resume — resume notifications"
    )


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
    """Escape Telegram Markdown special chars."""
    for ch in ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", ".", "!"]:
        text = text.replace(ch, f"\\{ch}")
    return text


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
        send_job_alert(test_job, None, None, cfg)
        print("Test alert sent.")
    else:
        run_bot(cfg)
