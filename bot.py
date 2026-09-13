import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
        user = update.effective_user
        if not user or user.id != config.ADMIN_USER_ID:
            msg = update.message or update.callback_query.message
            if update.message:
                await update.message.reply_text("⛔ You are not authorized to use this bot.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


def get_tz():
    return pytz.timezone(config.TIMEZONE)


def get_now():
    return datetime.now(get_tz())


# ── Date/Time picker keyboards ──────────────────────────────────────────
def build_date_keyboard():
    """Build inline keyboard with date options."""
    tz = get_tz()
    now = datetime.now(tz)
    buttons = []

    # Row 1: Today & Tomorrow
    today_label = f"📅 Today ({now.strftime('%b %d')})"
    tomorrow = now + timedelta(days=1)
    tomorrow_label = f"📅 Tomorrow ({tomorrow.strftime('%b %d')})"
    buttons.append([
        InlineKeyboardButton(today_label, callback_data=f"date_{now.strftime('%Y-%m-%d')}"),
        InlineKeyboardButton(tomorrow_label, callback_data=f"date_{tomorrow.strftime('%Y-%m-%d')}"),
    ])

    # Row 2-3: Next 5 days
    row = []
    for i in range(2, 7):
        day = now + timedelta(days=i)
        label = day.strftime("%a %b %d")
        row.append(InlineKeyboardButton(label, callback_data=f"date_{day.strftime('%Y-%m-%d')}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    return InlineKeyboardMarkup(buttons)


def build_hour_keyboard():
    """Build inline keyboard with hour options."""
    buttons = []
    row = []
    for h in range(0, 24):
        if h == 0:
            label = "12 AM"
        elif h < 12:
            label = f"{h} AM"
        elif h == 12:
            label = "12 PM"
        else:
            label = f"{h - 12} PM"
        row.append(InlineKeyboardButton(label, callback_data=f"hour_{h}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def build_minute_keyboard():
    """Build inline keyboard with minute options."""
    buttons = []
    minutes = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
    row = []
    for m in minutes:
        label = f":{m:02d}"
        row.append(InlineKeyboardButton(label, callback_data=f"min_{m}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


# ── Duration & Rate Limit helpers ────────────────────────────────────────
def format_duration(seconds: int) -> str:
    """Format seconds into a human-friendly string (e.g. 90 -> '1 min 30 sec')."""
    if seconds < 60:
        return f"{seconds} sec"
    minutes = seconds // 60
    rem = seconds % 60
    if rem == 0:
        return f"{minutes} min" if minutes == 1 else f"{minutes} mins"
    return f"{minutes} min {rem} sec"


def parse_duration(text: str) -> Optional[int]:
    """Parse text into seconds. Supports: '90', '90s', '1m 30s', '1m30s', '1 min 30 sec', '2m', etc."""
    text = text.strip().lower()
    if text.isdigit():
        return int(text)

    # e.g., "90s", "90 sec", "90 seconds"
    m = re.match(r"^(\d+)\s*(?:s|sec|secs|second|seconds)$", text)
    if m:
        return int(m.group(1))

    # e.g., "1m 30s", "1m30s", "1 min 30 sec", "1 minute 30 seconds"
    m = re.match(r"^(\d+)\s*(?:m|min|mins|minute|minutes)\s*(?:and\s*)?(\d+)?\s*(?:s|sec|secs|second|seconds)?$", text)
    if m:
        mins = int(m.group(1))
        secs = int(m.group(2)) if m.group(2) else 0
        return mins * 60 + secs

    # e.g., "1.5m", "1.5 min"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)$", text)
    if m:
        return int(float(m.group(1)) * 60)

    return None


def ensure_rate_limit_slot(target_time: datetime, rate_limit: int, exclude_post_id: int = None) -> tuple[datetime, bool, Optional[dict]]:
    """
    Ensure target_time has at least `rate_limit` seconds distance from all other pending posts.
    If there is a conflict, push forward until a clear slot is found.
    Returns (scheduled_time, was_adjusted, conflicting_post).
    """
    tz = get_tz()
    pending = database.get_pending_posts()

    scheduled_posts = []
    for p in pending:
        if exclude_post_id and p["id"] == exclude_post_id:
            continue
        p_dt = datetime.fromisoformat(p["scheduled_time"])
        if p_dt.tzinfo is None:
            p_dt = tz.localize(p_dt)
        scheduled_posts.append((p_dt, p))
    scheduled_posts.sort(key=lambda x: x[0])

    adjusted = False
    first_conflict = None
    curr_time = target_time

    max_loops = 100
    loop = 0
    while loop < max_loops:
        conflict_found = False
        for p_dt, p in scheduled_posts:
            diff = (curr_time - p_dt).total_seconds()
            if abs(diff) < rate_limit:
                conflict_found = True
                adjusted = True
                if first_conflict is None:
                    first_conflict = p
                curr_time = p_dt + timedelta(seconds=rate_limit)
                break
        if not conflict_found:
            break
        loop += 1

    return curr_time, adjusted, first_conflict


def build_ratelimit_keyboard():
    """Build preset rate limit options."""
    buttons = [
        [
            InlineKeyboardButton("⚡ 30 sec", callback_data="rl_30"),
            InlineKeyboardButton("⏱️ 1 min", callback_data="rl_60"),
        ],
        [
            InlineKeyboardButton("🛡️ 1m 30s", callback_data="rl_90"),
            InlineKeyboardButton("⏳ 2 min", callback_data="rl_120"),
        ],
        [
            InlineKeyboardButton("🕒 5 min", callback_data="rl_300"),
            InlineKeyboardButton("🛑 10 min", callback_data="rl_600"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)



# ── Natural language time parser ─────────────────────────────────────────
def parse_natural_time(text: str) -> datetime | None:
    """Parse natural language time like 'today 3pm', 'tomorrow 10:30am', 'in 2h'."""
    text = text.strip().lower()
    tz = get_tz()
    now = datetime.now(tz)

    # "in Xh" or "in Xm" or "in X hours" or "in X min"
    match = re.match(r"in\s+(\d+)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)", text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("h"):
            return now + timedelta(hours=amount)
        else:
            return now + timedelta(minutes=amount)

    # "today 3pm", "today 15:30", "tomorrow 10:30am"
    match = re.match(r"(today|tomorrow)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if match:
        day_word = match.group(1)
        hour = int(match.group(2))
        minute = int(match.group(3)) if match.group(3) else 0
        ampm = match.group(4)

        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        target = now if day_word == "today" else now + timedelta(days=1)
        try:
            return target.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            return None

    # Standard format "YYYY-MM-DD HH:MM"
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
        return tz.localize(dt)
    except ValueError:
        pass

    return None


# ── /start command ───────────────────────────────────────────────────────
@admin_only
async def cmd_start(update: Update, context):
    rate_limit = database.get_rate_limit()
    await update.message.reply_text(
        "👋 <b>Welcome to the Channel Scheduler Bot!</b>\n\n"
        "I can schedule posts to your Telegram channel safely.\n\n"
        "<b>Commands:</b>\n"
        "/newpost — Schedule a new post\n"
        "/list — View all pending posts\n"
        "/cancel <code>&lt;id&gt;</code> — Cancel a scheduled post\n"
        f"/ratelimit — Set minimum delay between posts (current: {format_duration(rate_limit)})\n"
        "/help — Show this message\n\n"
        "✨ <b>Supports:</b> Bold, italic, links, premium emoji, photos, videos & text-only posts!",
        parse_mode="HTML",
    )


# ── /help command ────────────────────────────────────────────────────────
@admin_only
async def cmd_help(update: Update, context):
    tz = config.TIMEZONE
    rate_limit = database.get_rate_limit()
    await update.message.reply_text(
        "📖 <b>How to schedule a post:</b>\n\n"
        "1️⃣ Send /newpost\n"
        "2️⃣ Send a 📸 photo, 📹 video, or /skip for text-only\n"
        "3️⃣ Type the caption/text for the post\n"
        "   ✨ <i>Premium emoji, bold, italic, links — all preserved!</i>\n"
        "4️⃣ Pick date &amp; time using <b>buttons</b> or type:\n"
        "   • <code>today 3pm</code>\n"
        "   • <code>tomorrow 10:30am</code>\n"
        "   • <code>in 2h</code> or <code>in 30m</code>\n"
        f"   • <code>2026-09-15 14:30</code>\n\n"
        f"⏰ Timezone: {tz}\n"
        f"🛡️ Rate limit: {format_duration(rate_limit)} (change with /ratelimit)\n\n"
        "📋 /list — See all scheduled posts\n"
        "⏱️ /ratelimit — Adjust delay between posts (e.g. <code>/ratelimit 1m 30s</code>)\n"
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

    # Store RAW text + entities (not HTML) to perfectly preserve premium emoji
    caption = update.message.text
    entities = update.message.entities

    user_data_store[user_id]["caption"] = caption

    # Serialize entities to JSON for database storage
    if entities:
        entities_json = json.dumps([e.to_dict() for e in entities])
        user_data_store[user_id]["caption_entities"] = entities_json
    else:
        user_data_store[user_id]["caption_entities"] = None

    # Use text_html for preview display only
    preview = update.message.text_html if update.message.text_html else caption

    media_type = user_data_store[user_id].get("media_type", "none")
    media_label = {"photo": "📸 Photo", "video": "📹 Video", "none": "📝 Text-only"}.get(media_type, "📝 Text-only")

    await update.message.reply_text(
        "✅ Caption saved!\n\n"
        f"👁️ <b>Preview:</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{preview}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📎 Media: {media_label}\n\n"
        "🕐 <b>Pick the date to publish:</b>",
        parse_mode="HTML",
        reply_markup=build_date_keyboard(),
    )
    return WAITING_TIME


async def handle_date_pick(update: Update, context):
    """Handle date button tap."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id != config.ADMIN_USER_ID:
        return

    date_str = query.data.replace("date_", "")
    user_data_store[user_id]["selected_date"] = date_str

    # Parse date for display
    selected = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = selected.strftime("%b %d, %Y")

    await query.edit_message_text(
        f"📅 Date: <b>{date_display}</b>\n\n"
        "🕐 <b>Now pick the hour:</b>",
        parse_mode="HTML",
        reply_markup=build_hour_keyboard(),
    )
    return WAITING_TIME


async def handle_hour_pick(update: Update, context):
    """Handle hour button tap."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id != config.ADMIN_USER_ID:
        return

    hour = int(query.data.replace("hour_", ""))
    user_data_store[user_id]["selected_hour"] = hour

    # Format hour for display
    if hour == 0:
        hour_display = "12 AM"
    elif hour < 12:
        hour_display = f"{hour} AM"
    elif hour == 12:
        hour_display = "12 PM"
    else:
        hour_display = f"{hour - 12} PM"

    date_str = user_data_store[user_id]["selected_date"]
    selected = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = selected.strftime("%b %d, %Y")

    await query.edit_message_text(
        f"📅 Date: <b>{date_display}</b>\n"
        f"🕐 Hour: <b>{hour_display}</b>\n\n"
        "⏱️ <b>Now pick the minutes:</b>",
        parse_mode="HTML",
        reply_markup=build_minute_keyboard(),
    )
    return WAITING_TIME


async def handle_minute_pick(update: Update, context):
    """Handle minute button tap — finalize scheduling."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id != config.ADMIN_USER_ID:
        return ConversationHandler.END

    minute = int(query.data.replace("min_", ""))
    data = user_data_store[user_id]
    date_str = data["selected_date"]
    hour = data["selected_hour"]

    # Build the final datetime
    tz = get_tz()
    scheduled_time = datetime.strptime(f"{date_str} {hour}:{minute}", "%Y-%m-%d %H:%M")
    scheduled_time = tz.localize(scheduled_time)

    # Check if time is in the future
    now = datetime.now(tz)
    if scheduled_time <= now:
        await query.edit_message_text(
            "❌ That time is in the past! Let's try again.\n\n"
            "🕐 <b>Pick the date to publish:</b>",
            parse_mode="HTML",
            reply_markup=build_date_keyboard(),
        )
        return WAITING_TIME

    # Ensure slot obeys rate limit
    rate_limit = database.get_rate_limit()
    final_time, was_adjusted, conflict = ensure_rate_limit_slot(scheduled_time, rate_limit)

    # Save to database
    post_id = database.add_post(
        caption=data["caption"],
        scheduled_time=final_time,
        media_file_id=data.get("media_file_id"),
        media_type=data.get("media_type", "none"),
        caption_entities=data.get("caption_entities"),
    )

    # Schedule the job
    sched_module.schedule_post(post_id, final_time)

    # Clean up
    del user_data_store[user_id]

    formatted_time = final_time.strftime("%b %d, %Y at %I:%M %p")
    media_type = data.get("media_type", "none")
    media_label = {"photo": "📸 Photo", "video": "📹 Video", "none": "📝 Text-only"}.get(media_type, "📝 Text-only")

    rate_note = ""
    if was_adjusted and conflict:
        rate_note = f"\n\n🛡️ <i>Auto-spaced by {format_duration(rate_limit)} rate limit (spaced out to avoid overlapping Post #{conflict['id']}).</i>"

    await query.edit_message_text(
        f"✅ <b>Post #{post_id} scheduled!</b>\n\n"
        f"📅 {formatted_time}\n"
        f"📎 {media_label}\n"
        f"📢 Channel: <code>{config.CHANNEL_ID}</code>"
        f"{rate_note}\n\n"
        "Send /newpost to schedule another post.\n"
        "Send /list to see all pending posts.",
        parse_mode="HTML",
    )
    return ConversationHandler.END


@admin_only
async def receive_time_text(update: Update, context):
    """Handle text input for time (natural language or YYYY-MM-DD HH:MM)."""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    scheduled_time = parse_natural_time(text)
    if not scheduled_time:
        await update.message.reply_text(
            "❌ Couldn't understand that time.\n\n"
            "<b>Try:</b>\n"
            "• <code>today 3pm</code>\n"
            "• <code>tomorrow 10:30am</code>\n"
            "• <code>in 2h</code> or <code>in 30m</code>\n"
            "• <code>2026-09-15 14:30</code>\n\n"
            "Or use the <b>buttons</b> above ☝️",
            parse_mode="HTML",
            reply_markup=build_date_keyboard(),
        )
        return WAITING_TIME

    tz = get_tz()
    if scheduled_time.tzinfo is None:
        scheduled_time = tz.localize(scheduled_time)

    now = datetime.now(tz)
    if scheduled_time <= now:
        await update.message.reply_text(
            "❌ That time is in the past! Please send a <b>future</b> time.",
            parse_mode="HTML",
            reply_markup=build_date_keyboard(),
        )
        return WAITING_TIME

    # Ensure slot obeys rate limit
    rate_limit = database.get_rate_limit()
    final_time, was_adjusted, conflict = ensure_rate_limit_slot(scheduled_time, rate_limit)

    # Save to database
    data = user_data_store[user_id]
    post_id = database.add_post(
        caption=data["caption"],
        scheduled_time=final_time,
        media_file_id=data.get("media_file_id"),
        media_type=data.get("media_type", "none"),
        caption_entities=data.get("caption_entities"),
    )

    sched_module.schedule_post(post_id, final_time)
    del user_data_store[user_id]

    formatted_time = final_time.strftime("%b %d, %Y at %I:%M %p")
    media_type = data.get("media_type", "none")
    media_label = {"photo": "📸 Photo", "video": "📹 Video", "none": "📝 Text-only"}.get(media_type, "📝 Text-only")

    rate_note = ""
    if was_adjusted and conflict:
        rate_note = f"\n\n🛡️ <i>Auto-spaced by {format_duration(rate_limit)} rate limit (spaced out to avoid overlapping Post #{conflict['id']}).</i>"

    await update.message.reply_text(
        f"✅ <b>Post #{post_id} scheduled!</b>\n\n"
        f"📅 {formatted_time}\n"
        f"📎 {media_label}\n"
        f"📢 Channel: <code>{config.CHANNEL_ID}</code>"
        f"{rate_note}\n\n"
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

    tz = get_tz()
    lines = ["📋 <b>Pending Scheduled Posts:</b>\n"]
    for post in posts:
        scheduled_time = datetime.fromisoformat(post["scheduled_time"])
        if scheduled_time.tzinfo is None:
            scheduled_time = tz.localize(scheduled_time)
        formatted = scheduled_time.strftime("%b %d, %Y at %I:%M %p")
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


# ── /ratelimit command ───────────────────────────────────────────────────
@admin_only
async def cmd_ratelimit(update: Update, context):
    """View or change the minimum delay between posts."""
    if context.args:
        arg_text = " ".join(context.args)
        seconds = parse_duration(arg_text)
        if seconds is None:
            await update.message.reply_text(
                "❌ <b>Could not parse time format.</b>\n\n"
                "<b>Valid examples:</b>\n"
                "• <code>/ratelimit 1m 30s</code>\n"
                "• <code>/ratelimit 30s</code>\n"
                "• <code>/ratelimit 2m</code>\n"
                "• <code>/ratelimit 90</code> (seconds)",
                parse_mode="HTML",
            )
            return

        if seconds < 10:
            await update.message.reply_text(
                "⚠️ Minimum rate limit is <b>10 seconds</b> to keep your Telegram account safe from spam detection.",
                parse_mode="HTML",
            )
            return

        database.set_rate_limit(seconds)
        formatted = format_duration(seconds)
        await update.message.reply_text(
            f"✅ <b>Rate limit updated!</b>\n\n"
            f"Minimum delay between channel posts is now: <b>{formatted}</b> ({seconds}s).\n\n"
            f"Posts to <code>{config.CHANNEL_ID}</code> will always be spaced at least {formatted} apart.",
            parse_mode="HTML",
        )
        return

    current = database.get_rate_limit()
    formatted = format_duration(current)
    await update.message.reply_text(
        f"⏱️ <b>Post Rate Limit / Interval Settings</b>\n\n"
        f"Current minimum delay: <b>{formatted}</b> ({current}s)\n\n"
        f"This protects your Telegram account by ensuring posts to <code>{config.CHANNEL_ID}</code> are never sent too rapidly.\n\n"
        "👇 <b>Select a preset below or type a custom time:</b>\n"
        "<i>e.g. <code>/ratelimit 1m 30s</code> or <code>/ratelimit 45s</code></i>",
        parse_mode="HTML",
        reply_markup=build_ratelimit_keyboard(),
    )


async def handle_ratelimit_pick(update: Update, context):
    """Handle preset rate limit button clicks."""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != config.ADMIN_USER_ID:
        return

    seconds = int(query.data.replace("rl_", ""))
    database.set_rate_limit(seconds)
    formatted = format_duration(seconds)

    await query.edit_message_text(
        f"✅ <b>Rate limit updated!</b>\n\n"
        f"Minimum delay between channel posts is now: <b>{formatted}</b> ({seconds}s).\n\n"
        f"Posts to <code>{config.CHANNEL_ID}</code> will always be spaced at least {formatted} apart to keep your account safe.",
        parse_mode="HTML",
    )


# ── Post-init callback (scheduler setup) ────────────────────────────────
async def post_init(application):
    """Called after the bot application is initialized."""
    sched_module.init_scheduler(application.bot)


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    database.init_db()

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
                CallbackQueryHandler(handle_date_pick, pattern=r"^date_"),
                CallbackQueryHandler(handle_hour_pick, pattern=r"^hour_"),
                CallbackQueryHandler(handle_minute_pick, pattern=r"^min_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_time_text),
                CommandHandler("done", cmd_done),
            ],
        },
        fallbacks=[CommandHandler("done", cmd_done)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("ratelimit", cmd_ratelimit))
    app.add_handler(CommandHandler("delay", cmd_ratelimit))
    app.add_handler(CallbackQueryHandler(handle_ratelimit_pick, pattern=r"^rl_"))

    logger.info("🤖 Bot is starting...")
    logger.info("Channel: %s", config.CHANNEL_ID)
    logger.info("Admin user ID: %s", config.ADMIN_USER_ID)
    logger.info("Timezone: %s", config.TIMEZONE)
    logger.info("Current rate limit: %ds", database.get_rate_limit())
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
