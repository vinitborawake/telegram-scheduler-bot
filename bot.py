import logging
from datetime import datetime

import pytz
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import database
import scheduler as sched_module

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation states ─────────────────────────────────────────────────
WAITING_IMAGE, WAITING_TEXT, WAITING_TIME = range(3)

# Temporary storage for in-progress posts (per user)
user_data_store: dict = {}


# ── Access control ──────────────────────────────────────────────────────
def admin_only(func):
    """Decorator to restrict commands to the admin user."""
    async def wrapper(update: Update, context):
        if update.effective_user.id != config.ADMIN_USER_ID:
            await update.message.reply_text("⛔ You are not authorized to use this bot.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


# ── /start command ───────────────────────────────────────────────────────
@admin_only
async def cmd_start(update: Update, context):
    await update.message.reply_text(
        "👋 *Welcome to the Channel Scheduler Bot!*\n\n"
        "I can schedule posts to your Telegram channel.\n\n"
        "*Commands:*\n"
        "/newpost — Schedule a new post\n"
        "/list — View all pending posts\n"
        "/cancel `<id>` — Cancel a scheduled post\n"
        "/help — Show this message",
        parse_mode="Markdown",
    )


# ── /help command ────────────────────────────────────────────────────────
@admin_only
async def cmd_help(update: Update, context):
    tz = config.TIMEZONE
    await update.message.reply_text(
        "📖 *How to schedule a post:*\n\n"
        "1️⃣ Send /newpost\n"
        "2️⃣ Send a photo (the image for your post)\n"
        "3️⃣ Type the caption/text for the post\n"
        "4️⃣ Enter date & time in format:\n"
        "   `YYYY-MM-DD HH:MM`\n"
        f"   _(Timezone: {tz})_\n\n"
        "📋 /list — See all scheduled posts\n"
        "❌ /cancel `<id>` — Cancel a post by ID\n"
        "🚫 /done — Cancel current operation",
        parse_mode="Markdown",
    )


# ── /newpost conversation ───────────────────────────────────────────────
@admin_only
async def cmd_newpost(update: Update, context):
    user_id = update.effective_user.id
    user_data_store[user_id] = {}
    await update.message.reply_text(
        "📸 *Send me the image* for your post.\n\n"
        "_(Send /done to cancel)_",
        parse_mode="Markdown",
    )
    return WAITING_IMAGE


@admin_only
async def receive_image(update: Update, context):
    user_id = update.effective_user.id
    # Get the largest photo size (best quality)
    photo = update.message.photo[-1]
    user_data_store[user_id]["image_file_id"] = photo.file_id

    await update.message.reply_text(
        "✅ Image received!\n\n"
        "✏️ Now *send me the caption/text* for this post.\n\n"
        "_(You can use HTML formatting: <b>bold</b>, <i>italic</i>, <a href=\"url\">links</a>)_\n"
        "_(Send /done to cancel)_",
        parse_mode="Markdown",
    )
    return WAITING_TEXT


@admin_only
async def receive_text(update: Update, context):
    user_id = update.effective_user.id
    user_data_store[user_id]["caption"] = update.message.text

    await update.message.reply_text(
        "✅ Caption saved!\n\n"
        "🕐 Now *send the date and time* to publish.\n"
        "Format: `YYYY-MM-DD HH:MM`\n"
        f"Example: `2026-09-15 14:30`\n"
        f"_(Timezone: {config.TIMEZONE})_\n\n"
        "_(Send /done to cancel)_",
        parse_mode="Markdown",
    )
    return WAITING_TIME


@admin_only
async def receive_time(update: Update, context):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Parse the datetime
    tz = pytz.timezone(config.TIMEZONE)
    try:
        scheduled_time = datetime.strptime(text, "%Y-%m-%d %H:%M")
        scheduled_time = tz.localize(scheduled_time)
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format! Please use: `YYYY-MM-DD HH:MM`\n"
            "Example: `2026-09-15 14:30`",
            parse_mode="Markdown",
        )
        return WAITING_TIME

    # Check if time is in the future
    now = datetime.now(tz)
    if scheduled_time <= now:
        await update.message.reply_text(
            "❌ That time is in the past! Please send a *future* date and time.",
            parse_mode="Markdown",
        )
        return WAITING_TIME

    # Save to database
    data = user_data_store[user_id]
    post_id = database.add_post(
        image_file_id=data["image_file_id"],
        caption=data["caption"],
        scheduled_time=scheduled_time,
    )

    # Schedule the job
    sched_module.schedule_post(post_id, scheduled_time)

    # Clean up temp data
    del user_data_store[user_id]

    # Format time nicely
    formatted_time = scheduled_time.strftime("%b %d, %Y at %I:%M %p")
    await update.message.reply_text(
        f"✅ *Post #{post_id} scheduled!*\n\n"
        f"📅 {formatted_time}\n"
        f"📢 Channel: `{config.CHANNEL_ID}`\n\n"
        "Send /newpost to schedule another post.\n"
        "Send /list to see all pending posts.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cmd_done(update: Update, context):
    """Cancel the current conversation."""
    user_id = update.effective_user.id
    if user_id in user_data_store:
        del user_data_store[user_id]
    await update.message.reply_text("🚫 Cancelled. Send /newpost to start again.")
    return ConversationHandler.END


# ── /list command ────────────────────────────────────────────────────────
@admin_only
async def cmd_list(update: Update, context):
    posts = database.get_pending_posts()
    if not posts:
        await update.message.reply_text("📭 No pending posts scheduled.")
        return

    tz = pytz.timezone(config.TIMEZONE)
    lines = ["📋 *Pending Scheduled Posts:*\n"]
    for post in posts:
        scheduled_time = datetime.fromisoformat(post["scheduled_time"])
        if scheduled_time.tzinfo is None:
            scheduled_time = tz.localize(scheduled_time)
        formatted = scheduled_time.strftime("%b %d, %Y at %I:%M %p")
        # Truncate caption for display
        caption_preview = post["caption"][:50]
        if len(post["caption"]) > 50:
            caption_preview += "..."
        lines.append(f"*#{post['id']}* — {formatted}\n   _{caption_preview}_\n")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /cancel command ──────────────────────────────────────────────────────
@admin_only
async def cmd_cancel(update: Update, context):
    if not context.args:
        await update.message.reply_text(
            "Usage: `/cancel <id>`\nExample: `/cancel 3`",
            parse_mode="Markdown",
        )
        return

    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Use a number.")
        return

    success = database.cancel_post(post_id)
    if success:
        sched_module.remove_scheduled_post(post_id)
        await update.message.reply_text(f"✅ Post #{post_id} has been cancelled.")
    else:
        await update.message.reply_text(
            f"❌ Post #{post_id} not found or already posted/cancelled."
        )


# ── Post-init callback (scheduler setup) ────────────────────────────────
async def post_init(application):
    """Called after the bot application is initialized."""
    sched_module.init_scheduler(application.bot)


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    # Initialize database
    database.init_db()

    # Build the bot application
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()

    # Conversation handler for /newpost
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newpost", cmd_newpost)],
        states={
            WAITING_IMAGE: [
                MessageHandler(filters.PHOTO, receive_image),
                CommandHandler("done", cmd_done),
            ],
            WAITING_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text),
                CommandHandler("done", cmd_done),
            ],
            WAITING_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_time),
                CommandHandler("done", cmd_done),
            ],
        },
        fallbacks=[CommandHandler("done", cmd_done)],
    )

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Start the bot
    logger.info("🤖 Bot is starting...")
    logger.info("Channel: %s", config.CHANNEL_ID)
    logger.info("Admin user ID: %s", config.ADMIN_USER_ID)
    logger.info("Timezone: %s", config.TIMEZONE)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
