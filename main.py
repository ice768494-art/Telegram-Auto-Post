import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.environ.get("ADMIN_ID", "").strip()
DB_PATH = os.environ.get("DB_PATH", "data/channel_os.db")
TIMEZONE_NAME = os.environ.get("TZ", "Asia/Kolkata")
TIMEZONE = ZoneInfo(TIMEZONE_NAME)

SELECT_CHANNEL, GET_CONTENT, GET_BUTTONS, GET_REPEAT, GET_TIME = range(5)
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("channel-os")


def admin_id() -> int:
    try:
        return int(ADMIN_ID_RAW)
    except (TypeError, ValueError):
        raise RuntimeError("ADMIN_ID must be your numeric Telegram user ID.")


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == admin_id())


def db_connect():
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                username TEXT,
                added_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                channel_title TEXT NOT NULL,
                source_chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                buttons_json TEXT NOT NULL DEFAULT '[]',
                repeat_type TEXT NOT NULL DEFAULT 'once',
                next_run REAL NOT NULL,
                created_at TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_posts(enabled, next_run)")


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def fmt_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")


def get_channels():
    with db_connect() as conn:
        return conn.execute("SELECT * FROM channels ORDER BY title COLLATE NOCASE").fetchall()


def save_channel(chat_id: int, title: str, username: str | None):
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO channels(chat_id, title, username, added_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, username=excluded.username""",
            (chat_id, title, username, now_local().isoformat()),
        )


def channel_by_id(chat_id: int):
    with db_connect() as conn:
        return conn.execute("SELECT * FROM channels WHERE chat_id=?", (chat_id,)).fetchone()


async def verify_channel_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    me = await context.bot.get_me()
    member = await context.bot.get_chat_member(chat_id, me.id)
    status = getattr(member, "status", "")
    if status not in ("administrator", "creator"):
        raise ValueError("The bot is not an administrator in that channel.")
    can_post = getattr(member, "can_post_messages", None)
    if can_post is False:
        raise ValueError("The bot is an admin, but it does not have permission to post messages.")


def channel_label(row) -> str:
    username = f" (@{row['username']})" if row["username"] else ""
    return f"{row['title']}{username}"


def make_buttons(raw: str):
    raw = raw.strip()
    if not raw or raw.lower() in {"none", "no", "skip"}:
        return []
    buttons = []
    for line in raw.splitlines():
        if "|" not in line:
            raise ValueError("Each button must use: Button label | https://example.com")
        label, url = (part.strip() for part in line.split("|", 1))
        if not label or not url.startswith(("https://", "http://")):
            raise ValueError("Each button needs a label and a valid http:// or https:// URL.")
        buttons.append({"text": label[:64], "url": url})
    if len(buttons) > 8:
        raise ValueError("Maximum 8 buttons per post.")
    return buttons


def keyboard_from_json(buttons_json: str):
    buttons = json.loads(buttons_json or "[]")
    if not buttons:
        return None
    # One button per row for a simple, readable layout.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(item["text"], url=item["url"])]
        for item in buttons
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text("⛔ This bot is private. Only the configured admin can use it.")
        return
    text = (
        "⚡ <b>CHANNEL OS</b>\n\n"
        "Manage multiple Telegram channels from one bot.\n\n"
        "📢 /addchannel @username — register a channel\n"
        "📂 /channels — list registered channels\n"
        "🗑️ /removechannel @username — remove a channel\n"
        "🚀 /publish — publish a post now\n"
        "📅 /schedule — schedule a one-time/daily/weekly post\n"
        "📋 /scheduled — view scheduled posts\n"
        "❌ /cancel ID — cancel a scheduled post\n"
        "❓ /help — show this menu\n\n"
        f"🕒 Time zone: <code>{TIMEZONE_NAME}</code>"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /addchannel @channelusername\n\n"
            "First add this bot as a channel administrator with permission to post."
        )
        return
    target = context.args[0]
    try:
        chat = await context.bot.get_chat(target)
        if chat.type != ChatType.CHANNEL:
            await update.effective_message.reply_text("That does not look like a Telegram channel.")
            return
        await verify_channel_admin(context, chat.id)
        save_channel(chat.id, chat.title or target, chat.username)
        await update.effective_message.reply_text(
            f"✅ Channel added!\n📢 {chat.title}\n🆔 <code>{chat.id}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.info("Could not add channel %s: %s", target, exc)
        await update.effective_message.reply_text(
            "❌ Couldn't add that channel.\n\n"
            "Check that the username is correct and the bot is an administrator with posting permission.\n"
            "Details: " + str(exc)[:250]
        )


async def register_forwarded_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not update.message:
        return
    message = update.message
    origin = getattr(message, "forward_origin", None)
    source_chat = getattr(origin, "chat", None) if origin else None
    if source_chat is None and getattr(message, "forward_from_chat", None):
        source_chat = message.forward_from_chat
    if source_chat is None or getattr(source_chat, "type", None) != ChatType.CHANNEL:
        await message.reply_text(
            "I couldn't identify a source channel from that forwarded message.\n"
            "Try /addchannel @channelusername instead."
        )
        return
    try:
        await verify_channel_admin(context, source_chat.id)
        save_channel(source_chat.id, source_chat.title or "Telegram channel", source_chat.username)
        await message.reply_text(f"✅ Registered channel: {source_chat.title}")
    except Exception as exc:
        await message.reply_text(
            "❌ I found the channel, but couldn't verify that I can publish there.\n"
            "Add me as an administrator with permission to post, then try again.\n"
            f"Details: {str(exc)[:200]}"
        )


async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    rows = get_channels()
    if not rows:
        await update.effective_message.reply_text(
            "No channels registered yet.\nUse /addchannel @username or forward a channel post to me."
        )
        return
    lines = ["📂 <b>Registered Channels</b>\n"]
    for row in rows:
        lines.append(f"• {channel_label(row)}\n  <code>{row['chat_id']}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /removechannel @username")
        return
    target = context.args[0].lstrip("@")
    rows = get_channels()
    match = None
    for row in rows:
        if str(row["chat_id"]) == context.args[0] or (row["username"] or "").lower() == target.lower():
            match = row
            break
    if not match:
        await update.effective_message.reply_text("Channel not found. Use /channels to see registered channels.")
        return
    with db_connect() as conn:
        conn.execute("DELETE FROM channels WHERE chat_id=?", (match["chat_id"],))
    await update.effective_message.reply_text(f"🗑️ Removed {match['title']} from the channel list.")


def private_admin_only(update: Update) -> bool:
    return (
        is_admin(update)
        and update.effective_chat is not None
        and update.effective_chat.type == ChatType.PRIVATE
    )


async def begin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    if not is_admin(update):
        await update.effective_message.reply_text("⛔ Admin only.")
        return ConversationHandler.END
    if not private_admin_only(update):
        await update.effective_message.reply_text("Please use /publish and /schedule in the bot's private chat.")
        return ConversationHandler.END
    rows = get_channels()
    if not rows:
        await update.effective_message.reply_text("Add a channel first using /addchannel @username.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["mode"] = mode
    keyboard = [
        [InlineKeyboardButton(channel_label(row)[:60], callback_data=f"select:{row['chat_id']}")]
        for row in rows
    ]
    await update.effective_message.reply_text(
        "📢 Choose the destination channel:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECT_CHANNEL


async def begin_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await begin_flow(update, context, "publish")


async def begin_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await begin_flow(update, context, "schedule")


async def select_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        chat_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.edit_message_text("Invalid channel selection.")
        return ConversationHandler.END
    row = channel_by_id(chat_id)
    if not row:
        await query.edit_message_text("That channel is no longer registered. Try again.")
        return ConversationHandler.END
    context.user_data["channel_id"] = int(row["chat_id"])
    context.user_data["channel_title"] = row["title"]
    await query.edit_message_text(
        f"✅ Destination: {row['title']}\n\n"
        "Now send the post to publish or schedule.\n"
        "Supported: text, photo, video, or document (one message at a time)."
    )
    return GET_CONTENT


async def get_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not update.message:
        return ConversationHandler.END
    if update.message.text and update.message.text.startswith("/"):
        await update.message.reply_text("Please send the post content, not a command.")
        return GET_CONTENT
    context.user_data["source_chat_id"] = update.effective_chat.id
    context.user_data["source_message_id"] = update.message.message_id
    await update.message.reply_text(
        "🔗 Add inline URL buttons?\n\n"
        "Send one button per line in this format:\n"
        "<code>Join Channel | https://t.me/example</code>\n"
        "<code>More Updates | https://example.com</code>\n\n"
        "Send <code>none</code> to continue without buttons.",
        parse_mode="HTML",
    )
    return GET_BUTTONS


async def get_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not update.message or not update.message.text:
        await update.effective_message.reply_text("Please send button details as text, or send `none`.")
        return GET_BUTTONS
    try:
        buttons = make_buttons(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"❌ {exc}\nTry again, or send none.")
        return GET_BUTTONS
    context.user_data["buttons"] = buttons
    if context.user_data.get("mode") == "publish":
        try:
            await publish_saved_message(context, repeat_type="once", when=None)
            await update.message.reply_text("✅ Post published successfully.")
        except Exception as exc:
            logger.exception("Publish failed")
            await update.message.reply_text(f"❌ Publishing failed: {str(exc)[:300]}")
        context.user_data.clear()
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🕒 One time", callback_data="repeat:once")],
        [InlineKeyboardButton("🔁 Daily", callback_data="repeat:daily")],
        [InlineKeyboardButton("📆 Weekly", callback_data="repeat:weekly")],
    ])
    await update.message.reply_text("How often should this post repeat?", reply_markup=keyboard)
    return GET_REPEAT


async def choose_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    repeat_type = query.data.split(":", 1)[1]
    if repeat_type not in {"once", "daily", "weekly"}:
        await query.edit_message_text("Invalid schedule type.")
        return ConversationHandler.END
    context.user_data["repeat_type"] = repeat_type
    if repeat_type == "once":
        prompt = "Send the publish date and time in this format:\n<code>2026-10-20 20:30</code>"
    elif repeat_type == "daily":
        prompt = "Send the first publish date and time in this format:\n<code>2026-10-20 20:30</code>\nThe post will repeat every day at that time."
    else:
        prompt = "Send the first publish date and time in this format:\n<code>2026-10-20 20:30</code>\nThe post will repeat every 7 days."
    await query.edit_message_text(prompt, parse_mode="HTML")
    return GET_TIME


async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update) or not update.message or not update.message.text:
        await update.effective_message.reply_text("Send a date and time as text.")
        return GET_TIME
    try:
        due = datetime.strptime(update.message.text.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use YYYY-MM-DD HH:MM, for example 2026-10-20 20:30.")
        return GET_TIME
    if due <= now_local():
        await update.message.reply_text("❌ Choose a future date and time.")
        return GET_TIME
    try:
        await save_schedule(context, due.timestamp())
        repeat_type = context.user_data["repeat_type"]
        repeat_label = {"once": "one time", "daily": "daily", "weekly": "weekly"}[repeat_type]
        await update.message.reply_text(
            f"✅ <b>Post scheduled!</b>\n\n"
            f"📢 Channel: {context.user_data['channel_title']}\n"
            f"🗓️ First publish: {due.strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"🔁 Repeat: {repeat_label}\n\n"
            "Use /scheduled to view schedules or /cancel ID to cancel one.",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.exception("Could not schedule post")
        await update.message.reply_text(f"❌ Couldn't schedule this post: {str(exc)[:250]}")
    context.user_data.clear()
    return ConversationHandler.END


async def publish_saved_message(context: ContextTypes.DEFAULT_TYPE, repeat_type: str, when: float | None):
    data = context.user_data
    buttons_json = json.dumps(data.get("buttons", []), ensure_ascii=False)
    keyboard = keyboard_from_json(buttons_json)
    channel_id = int(data["channel_id"])
    source_chat_id = int(data["source_chat_id"])
    source_message_id = int(data["source_message_id"])
    if when is None:
        await context.bot.copy_message(
            chat_id=channel_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
            reply_markup=keyboard,
        )
        return
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO scheduled_posts
               (channel_id, channel_title, source_chat_id, source_message_id,
                buttons_json, repeat_type, next_run, created_at, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                channel_id,
                data["channel_title"],
                source_chat_id,
                source_message_id,
                buttons_json,
                repeat_type,
                when,
                now_local().isoformat(),
            ),
        )


async def save_schedule(context: ContextTypes.DEFAULT_TYPE, timestamp: float):
    await publish_saved_message(context, context.user_data["repeat_type"], timestamp)


async def scheduled_posts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE enabled=1 ORDER BY next_run LIMIT 30"
        ).fetchall()
    if not rows:
        await update.effective_message.reply_text("📭 No active scheduled posts.")
        return
    lines = ["📋 <b>Active Scheduled Posts</b>\n"]
    for row in rows:
        repeat = {"once": "One time", "daily": "Daily", "weekly": "Weekly"}.get(row["repeat_type"], row["repeat_type"])
        lines.append(
            f"🆔 <code>{row['id']}</code> · {row['channel_title']}\n"
            f"🕒 {fmt_time(row['next_run'])} · {repeat}\n"
            f"Cancel: <code>/cancel {row['id']}</code>\n"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def cancel_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /cancel ID\nUse /scheduled to find the ID.")
        return
    try:
        schedule_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("ID must be a number.")
        return
    with db_connect() as conn:
        cur = conn.execute(
            "UPDATE scheduled_posts SET enabled=0 WHERE id=? AND enabled=1",
            (schedule_id,),
        )
    if cur.rowcount:
        await update.effective_message.reply_text(f"✅ Schedule #{schedule_id} cancelled.")
    else:
        await update.effective_message.reply_text("No active schedule found with that ID.")


async def check_due_posts(context: ContextTypes.DEFAULT_TYPE):
    now_ts = datetime.now(TIMEZONE).timestamp()
    with db_connect() as conn:
        due_rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT 20",
            (now_ts,),
        ).fetchall()

    for row in due_rows:
        try:
            await context.bot.copy_message(
                chat_id=row["channel_id"],
                from_chat_id=row["source_chat_id"],
                message_id=row["source_message_id"],
                reply_markup=keyboard_from_json(row["buttons_json"]),
            )
            if row["repeat_type"] == "once":
                with db_connect() as conn:
                    conn.execute("UPDATE scheduled_posts SET enabled=0 WHERE id=?", (row["id"],))
            else:
                interval = 86400 if row["repeat_type"] == "daily" else 7 * 86400
                next_run = float(row["next_run"]) + interval
                # If the worker was offline, avoid rapidly sending every missed occurrence.
                while next_run <= now_ts:
                    next_run += interval
                with db_connect() as conn:
                    conn.execute(
                        "UPDATE scheduled_posts SET next_run=? WHERE id=? AND enabled=1",
                        (next_run, row["id"]),
                    )
            try:
                await context.bot.send_message(
                    chat_id=admin_id(),
                    text=f"✅ CHANNEL OS published scheduled post #{row['id']} to {row['channel_title']}."
                )
            except Exception:
                pass
        except Exception as exc:
            logger.exception("Scheduled post %s failed", row["id"])
            try:
                await context.bot.send_message(
                    chat_id=admin_id(),
                    text=f"⚠️ Scheduled post #{row['id']} failed for {row['channel_title']}.\n"
                         f"Reason: {str(exc)[:250]}\n"
                         "The schedule remains active; check channel permissions and /scheduled."
                )
            except Exception:
                pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception while processing update", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN environment variable.")
    admin_id()  # Validate early.
    init_db()
    application = Application.builder().token(BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("publish", begin_publish),
            CommandHandler("schedule", begin_schedule),
        ],
        states={
            SELECT_CHANNEL: [CallbackQueryHandler(select_channel, pattern=r"^select:-?\d+$")],
            GET_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, get_content)],
            GET_BUTTONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_buttons)],
            GET_REPEAT: [CallbackQueryHandler(choose_repeat, pattern=r"^repeat:(once|daily|weekly)$")],
            GET_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
        },
        fallbacks=[CommandHandler("cancel", cancel_schedule), CommandHandler("start", start)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("addchannel", addchannel))
    application.add_handler(CommandHandler("channels", channels_command))
    application.add_handler(CommandHandler("removechannel", removechannel))
    application.add_handler(CommandHandler("scheduled", scheduled_posts_command))
    application.add_handler(CommandHandler("cancel", cancel_schedule))
    application.add_handler(conversation)
    application.add_handler(MessageHandler(filters.FORWARDED, register_forwarded_channel), group=1)

    if application.job_queue is None:
        raise RuntimeError("JobQueue is missing. Install python-telegram-bot[job-queue].")
    application.job_queue.run_repeating(check_due_posts, interval=15, first=10, name="scheduled-post-check")
    application.add_error_handler(error_handler)

    logger.info("Starting CHANNEL OS in timezone %s", TIMEZONE_NAME)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
