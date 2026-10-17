import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# =========================
# CONFIGURATION
# =========================

def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Add it in Render > Environment."
        )
    return value


BOT_TOKEN = required_env("BOT_TOKEN")
ADMIN_ID = int(required_env("ADMIN_ID"))
MONGO_URI = required_env("MONGO_URI")
DEFAULT_MAIN_CHANNEL_ID = int(required_env("MAIN_CHANNEL_ID"))

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# =========================
# MONGODB
# =========================

mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000,
)

mongo_client.admin.command("ping")

db = mongo_client["anime_auto_poster"]

anime_collection = db["anime"]
settings_collection = db["settings"]
processed_collection = db["processed_posts"]

processed_collection.create_index(
    [("channel_id", 1), ("message_id", 1)],
    unique=True,
)

# =========================
# CONVERSATION STATES
# =========================

ANIME_NAME = 1
CHANNEL_ID = 2
JOIN_URL = 3
POSTER = 4


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


async def admin_only(update: Update) -> bool:
    if not is_admin(update):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ You are not authorized to use this command."
            )
        return False

    return True


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "🎬 Anime Auto Poster Bot is running."
        )
        return

    await update.effective_message.reply_text(
        "🎬 ANIME AUTO POSTER BOT\n\n"
        "⚡ Automatic episode posting is enabled.\n\n"
        "Commands:\n"
        "/addanime - Register an anime\n"
        "/listanime - List registered anime\n"
        "/removeanime - Remove an anime\n"
        "/setmain - Change the Main Channel\n"
        "/cancel - Cancel the current operation\n"
        "/help - Show commands"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "🎬 ANIME AUTO POSTER BOT\n\n"
        "/addanime - Register an anime\n"
        "/listanime - List registered anime\n"
        "/removeanime <name> - Remove an anime\n"
        "/setmain <channel_id> - Change Main Channel\n"
        "/cancel - Cancel an operation\n\n"
        "The bot automatically detects new channel posts.\n"
        "Each anime needs a source channel, Join Channel link, "
        "and custom poster."
    )


# =========================
# ADD ANIME WIZARD
# =========================

async def add_anime_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await admin_only(update):
        return ConversationHandler.END

    context.user_data["new_anime"] = {}

    await update.effective_message.reply_text(
        "🎬 Enter the anime name.\n\n"
        "Example: Black Clover"
    )

    return ANIME_NAME


async def receive_anime_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    name = update.effective_message.text.strip()

    if not name:
        await update.effective_message.reply_text(
            "Please enter a valid anime name."
        )
        return ANIME_NAME

    context.user_data["new_anime"]["name"] = name

    await update.effective_message.reply_text(
        "📢 Send the source channel ID.\n\n"
        "Example:\n"
        "-1001234567890\n\n"
        "The bot must be an administrator in this channel."
    )

    return CHANNEL_ID


async def receive_channel_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        channel_id = int(update.effective_message.text.strip())

        if channel_id >= 0:
            raise ValueError

    except (ValueError, AttributeError):
        await update.effective_message.reply_text(
            "❌ Invalid channel ID.\n"
            "Send a channel ID such as -1001234567890."
        )
        return CHANNEL_ID

    context.user_data["new_anime"]["channel_id"] = channel_id

    await update.effective_message.reply_text(
        "🔗 Send the Join Channel URL.\n\n"
        "For example, a public channel link or a valid "
        "private-channel invite link."
    )

    return JOIN_URL


def valid_telegram_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())

        return (
            parsed.scheme == "https"
            and parsed.netloc.lower() in {
                "t.me",
                "www.t.me",
                "telegram.me",
                "www.telegram.me",
            }
            and bool(parsed.path.strip("/"))
        )
    except Exception:
        return False


async def receive_join_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    join_url = update.effective_message.text.strip()

    if not valid_telegram_url(join_url):
        await update.effective_message.reply_text(
            "❌ Please send a valid Telegram channel URL."
        )
        return JOIN_URL

    context.user_data["new_anime"]["join_url"] = join_url

    await update.effective_message.reply_text(
        "🖼️ Now upload the custom poster as a PHOTO.\n\n"
        "The poster will be used for every new episode "
        "from this anime."
    )

    return POSTER


async def receive_poster(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message.photo:
        await message.reply_text(
            "❌ Please upload the poster as a Telegram photo."
        )
        return POSTER

    data = context.user_data.get("new_anime", {})

    if not data:
        await message.reply_text(
            "Your registration session expired. Use /addanime again."
        )
        return ConversationHandler.END

    data["poster_file_id"] = message.photo[-1].file_id

    anime_key = data["name"].casefold()

    anime_collection.update_one(
        {"name_key": anime_key},
        {
            "$set": {
                "name": data["name"],
                "name_key": anime_key,
                "channel_id": data["channel_id"],
                "join_url": data["join_url"],
                "poster_file_id": data["poster_file_id"],
                "enabled": True,
            }
        },
        upsert=True,
    )

    context.user_data.pop("new_anime", None)

    await message.reply_text(
        "✅ ANIME REGISTERED SUCCESSFULLY!\n\n"
        f"🎬 Anime: {data['name']}\n"
        f"📢 Source Channel: {data['channel_id']}\n\n"
        "⚡ New episodes will now be processed automatically."
    )

    return ConversationHandler.END


async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("new_anime", None)

    await update.effective_message.reply_text(
        "❌ Operation cancelled."
    )

    return ConversationHandler.END


# =========================
# LIST ANIME
# =========================

async def list_anime(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await admin_only(update):
        return

    anime_list = list(
        anime_collection.find(
            {},
            {"name": 1, "channel_id": 1, "enabled": 1},
        ).sort("name", 1)
    )

    if not anime_list:
        await update.effective_message.reply_text(
            "No anime registered yet.\nUse /addanime."
        )
        return

    lines = ["🎬 REGISTERED ANIME\n"]

    for anime in anime_list:
        status = "✅" if anime.get("enabled", True) else "⏸️"

        lines.append(
            f"{status} {anime['name']}\n"
            f"Channel ID: {anime['channel_id']}\n"
        )

    await update.effective_message.reply_text(
        "\n".join(lines)
    )


# =========================
# REMOVE ANIME
# =========================

async def remove_anime(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await admin_only(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage:\n/removeanime Black Clover"
        )
        return

    name = " ".join(context.args).strip()

    result = anime_collection.delete_one(
        {"name_key": name.casefold()}
    )

    if result.deleted_count:
        await update.effective_message.reply_text(
            f"✅ Removed anime: {name}"
        )
    else:
        await update.effective_message.reply_text(
            "❌ Anime not found."
        )


# =========================
# SET MAIN CHANNEL
# =========================

async def set_main(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await admin_only(update):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "Usage:\n/setmain -1001234567890"
        )
        return

    try:
        channel_id = int(context.args[0])

        if channel_id >= 0:
            raise ValueError

    except ValueError:
        await update.effective_message.reply_text(
            "❌ Please provide a valid channel ID."
        )
        return

    settings_collection.update_one(
        {"key": "main_channel_id"},
        {"$set": {"value": channel_id}},
        upsert=True,
    )

    await update.effective_message.reply_text(
        f"✅ Main Channel updated to {channel_id}"
    )


def get_main_channel_id() -> int:
    setting = settings_collection.find_one(
        {"key": "main_channel_id"}
    )

    if setting:
        return int(setting["value"])

    return DEFAULT_MAIN_CHANNEL_ID


# =========================
# SOURCE POST LINK
# =========================

def make_episode_url(chat, message_id: int) -> str:
    # Public channel
    if chat.username:
        return (
            f"https://t.me/{chat.username}/{message_id}"
        )

    # Private channel: the user must already have access
    chat_id = str(chat.id)

    if chat_id.startswith("-100"):
        internal_id = chat_id[4:]
    else:
        raise ValueError(
            "Unsupported private channel ID format."
        )

    return (
        f"https://t.me/c/{internal_id}/{message_id}"
    )


# =========================
# AUTOMATIC CHANNEL POSTS
# =========================

async def handle_channel_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.channel_post

    if not message:
        return

    anime = anime_collection.find_one(
        {
            "channel_id": message.chat.id,
            "enabled": True,
        }
    )

    if not anime:
        return

    # Process supported episode media only.
    if not (
        message.photo
        or message.video
        or message.document
    ):
        return

    if not anime.get("poster_file_id"):
        logger.warning(
            "No poster configured for %s",
            anime.get("name"),
        )
        return

    try:
        episode_url = make_episode_url(
            message.chat,
            message.message_id,
        )
    except ValueError:
        logger.exception("Could not generate episode URL.")
        return

    # Prevent duplicate announcements.
    post_key = {
        "channel_id": message.chat.id,
        "message_id": message.message_id,
    }

    if processed_collection.find_one(post_key):
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📺 WATCH EPISODE",
                url=episode_url,
            )
        ],
        [
            InlineKeyboardButton(
                "📢 JOIN CHANNEL",
                url=anime["join_url"],
            )
        ],
    ])

    main_channel_id = get_main_channel_id()

    try:
        source_caption = (message.caption or "").strip()
        caption = f"🎬 {anime['name']}"
        if source_caption:
            caption += f"\n\n{source_caption}"
        # Telegram photo captions are limited to 1024 characters.
        caption = caption[:1000]

        await context.bot.send_photo(
            chat_id=main_channel_id,
            photo=anime["poster_file_id"],
            caption=caption,
            reply_markup=keyboard,
        )

        processed_collection.insert_one(post_key)

        logger.info(
            "Posted %s episode message %s",
            anime["name"],
            message.message_id,
        )

    except DuplicateKeyError:
        logger.info("Duplicate episode was already processed.")

    except Exception:
        logger.exception(
            "Failed to publish episode from %s",
            anime["name"],
        )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Unhandled bot error",
        exc_info=context.error,
    )


# =========================
# START BOT
# =========================

class HealthHandler(BaseHTTPRequestHandler):
    """Small HTTP endpoint so Render Web Service health checks succeed."""

    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"Anime Auto Poster Bot is running."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"Not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        logger.info("HTTP: " + format, *args)


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health server listening on port %s", port)


def main():
    start_health_server()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("addanime", add_anime_start)
        ],
        states={
            ANIME_NAME: [
                MessageHandler(
                    filters.ChatType.PRIVATE
                    & filters.TEXT
                    & ~filters.COMMAND,
                    receive_anime_name,
                )
            ],
            CHANNEL_ID: [
                MessageHandler(
                    filters.ChatType.PRIVATE
                    & filters.TEXT
                    & ~filters.COMMAND,
                    receive_channel_id,
                )
            ],
            JOIN_URL: [
                MessageHandler(
                    filters.ChatType.PRIVATE
                    & filters.TEXT
                    & ~filters.COMMAND,
                    receive_join_url,
                )
            ],
            POSTER: [
                MessageHandler(
                    filters.ChatType.PRIVATE
                    & filters.PHOTO,
                    receive_poster,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(conversation)
    app.add_handler(CommandHandler("listanime", list_anime))
    app.add_handler(CommandHandler("removeanime", remove_anime))
    app.add_handler(CommandHandler("setmain", set_main))

    # Channel posts are different from ordinary messages.
    app.add_handler(
        MessageHandler(
            filters.UpdateType.CHANNEL_POST,
            handle_channel_post,
        )
    )

    app.add_error_handler(error_handler)

    logger.info("Anime Auto Poster Bot started.")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
