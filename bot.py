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
WAITING_MEDIA, WAITING_TEXT, WAITING_TIME = range(3)

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
        "👋 <b>Welcome to the Channel Scheduler Bot!</b>\n\n"
        "I can schedule posts to your Telegram channel.\n\n"
        "<b>Commands:</b>\n"
        "/newpost — Schedule a new post\n"
        "/list — View all pending posts\n"
        "/cancel <code>&lt;id&gt;</code> — Cancel a scheduled post\n"
        "/help — Show this message\n\n"
        "✨ <b>Supports:</b> Bold, italic, links, premium emoji, photos, videos & text-only posts!",
        parse_mode="HTML",
    )


# ── /help command ────────────────────────────────────────────────────────
@admin_only
async def cmd_help(update: Update, context):
    tz = config.TIMEZONE
    await update.message.reply_text(
        "📖 <b>How to schedule a post:</b>\n\n"
        "1️⃣ Send /newpost\n"
        "2️⃣ Send a 📸 photo, 📹 video, or /skip for text-only\n"
        "3️⃣ Type the caption/text for the post\n"
        "   ✨ <i>Premium emoji, bold, italic, links — all preserved!</i>\n"
        "4️⃣ Enter date &amp; time:\n"
        f"   <code>YYYY-MM-DD HH:MM</code>  (Timezone: {tz})\n\n"
        "📋 /list — See all scheduled posts\n"
        "❌ /cancel <code>&lt;id&gt;</code> — Cancel a post by ID\n"
        "🚫 /done — Cancel current operation",
        parse_mode="HTML",
    )


# ── /newpost conversation ───────────────────────────────────────────────
@admin_only
async def cmd_newpost(update: Update, context):
    user_id = update.effective_user.id
    user_data_store[user_id] = {}
    await update.message.reply_text(
        "📸 <b>Send me a photo or video</b> for your post.\n\n"
        "Or send /skip for a <b>text-only</b> post.\n\n"
        "<i>(Send /done to cancel)</i>",
        parse_mode="HTML",
    )
    return WAITING_MEDIA


@admin_only
async def receive_photo(update: Update, context):
    user_id = update.effective_user.id
    # Get the largest photo size (best quality)
    photo = update.message.photo[-1]
    user_data_store[user_id]["media_file_id"] = photo.file_id
    user_data_store[user_id]["media_type"] = "photo"

    await update.message.reply_text(
        "✅ Photo received!\n\n"
        "✏️ Now <b>send me the caption/text</b> for this post.\n\n"
        "✨ <i>You can use formatted text, premium emoji, bold, italic, links — everything will be preserved!</i>\n\n"
        "<i>(Send /done to cancel)</i>",
        parse_mode="HTML",
    )
    return WAITING_TEXT


@admin_only
async def receive_video(update: Update, context):
    user_id = update.effective_user.id
    user_data_store[user_id]["media_file_id"] = update.message.video.file_id
    user_data_store[user_id]["media_type"] = "video"

    await update.message.reply_text(
        "✅ Video received!\n\n"
        "✏️ Now <b>send me the caption/text</b> for this post.\n\n"
        "✨ <i>You can use formatted text, premium emoji, bold, italic, links — everything will be preserved!</i>\n\n"
        "<i>(Send /done to cancel)</i>",
        parse_mode="HTML",
    )
    return WAITING_TEXT


@admin_only
async def skip_media(update: Update, context):
    """Skip media for a text-only post."""
    user_id = update.effective_user.id
    user_data_store[user_id]["media_file_id"] = None
    user_data_store[user_id]["media_type"] = "none"

    await update.message.reply_text(
        "📝 <b>Text-only post!</b>\n\n"
        "✏️ Now <b>send me the text</b> for your post.\n\n"
        "✨ <i>You can use formatted text, premium emoji, bold, italic, links — everything will be preserved!</i>\n\n"
        "<i>(Send /done to cancel)</i>",
        parse_mode="HTML",
    )
    return WAITING_TEXT


@admin_only
async def receive_text(update: Update, context):
    user_id = update.effective_user.id

    # Use text_html to preserve ALL formatting: bold, italic, links, premium/custom emoji
    if update.message.text_html:
        caption = update.message.text_html
    else:
        caption = update.message.text

    user_data_store[user_id]["caption"] = caption

    # Show preview
    media_type = user_data_store[user_id].get("media_type", "none")
    media_label = {"photo": "📸 Photo", "video": "📹 Video", "none": "📝 Text-only"}.get(media_type, "📝 Text-only")

    await update.message.reply_text(
        "✅ Caption saved!\n\n"
        f"👁️ <b>Preview:</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{caption}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📎 Media: {media_label}\n\n"
        "🕐 Now <b>send the date and time</b> to publish.\n"
        f"Format: <code>YYYY-MM-DD HH:MM</code>\n"
        f"Example: <code>2026-09-15 14:30</code>\n"
        f"<i>(Timezone: {config.TIMEZONE})</i>\n\n"
        "<i>(Send /done to cancel)</i>",
        parse_mode="HTML",
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
            "❌ Invalid format! Please use: <code>YYYY-MM-DD HH:MM</code>\n"
            "Example: <code>2026-09-15 14:30</code>",
            parse_mode="HTML",
        )
        return WAITING_TIME

    # Check if time is in the future
    now = datetime.now(tz)
    if scheduled_time <= now:
        await update.message.reply_text(
            "❌ That time is in the past! Please send a <b>future</b> date and time.",
            parse_mode="HTML",
        )
        return WAITING_TIME

    # Save to database
    data = user_data_store[user_id]
    post_id = database.add_post(
        caption=data["caption"],
        scheduled_time=scheduled_time,
        media_file_id=data.get("media_file_id"),
        media_type=data.get("media_type", "none"),
    )

    # Schedule the job
    sched_module.schedule_post(post_id, scheduled_time)

    # Clean up temp data
    del user_data_store[user_id]

    # Format time nicely
    formatted_time = scheduled_time.strftime("%b %d, %Y at %I:%M %p")
    media_type = data.get("media_type", "none")
    media_label = {"photo": "📸 Photo", "video": "📹 Video", "none": "📝 Text-only"}.get(media_type, "📝 Text-only")

    await update.message.reply_text(
        f"✅ <b>Post #{post_id} scheduled!</b>\n\n"
        f"📅 {formatted_time}\n"
        f"📎 {media_label}\n"
        f"📢 Channel: <code>{config.CHANNEL_ID}</code>\n\n"
        "Send /newpost to schedule another post.\n"
        "Send /list to see all pending posts.",
        parse_mode="HTML",
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
    lines = ["📋 <b>Pending Scheduled Posts:</b>\n"]
    for post in posts:
        scheduled_time = datetime.fromisoformat(post["scheduled_time"])
        if scheduled_time.tzinfo is None:
            scheduled_time = tz.localize(scheduled_time)
        formatted = scheduled_time.strftime("%b %d, %Y at %I:%M %p")
        # Truncate caption for display
        # Strip HTML tags for preview
        import re
        raw_caption = re.sub(r'<[^>]+>', '', post["caption"])
        caption_preview = raw_caption[:50]
        if len(raw_caption) > 50:
            caption_preview += "..."
        media_type = post.get("media_type", "photo")
        media_icon = {"photo": "📸", "video": "📹", "none": "📝"}.get(media_type, "📝")
        lines.append(f"<b>#{post['id']}</b> {media_icon} — {formatted}\n   <i>{caption_preview}</i>\n")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /cancel command ──────────────────────────────────────────────────────
@admin_only
async def cmd_cancel(update: Update, context):
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/cancel &lt;id&gt;</code>\nExample: <code>/cancel 3</code>",
            parse_mode="HTML",
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
            WAITING_MEDIA: [
                MessageHandler(filters.PHOTO, receive_photo),
                MessageHandler(filters.VIDEO, receive_video),
                CommandHandler("skip", skip_media),
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
