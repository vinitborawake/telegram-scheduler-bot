"""Post interval and gap manager module for Telegram Scheduler Bot.

Allows setting the rate limit / gap between scheduled posts from 1 to 60 minutes
with a 5-minute default, interactive quick presets, and a paginated 1-60 minute grid.
"""

import logging
import re
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import config
import database

logger = logging.getLogger(__name__)

# Default post gap: 300 seconds (5 minutes)
DEFAULT_GAP = 300


def format_gap(seconds: int) -> str:
    """Format seconds into a human-friendly string (e.g. 300 -> '5 mins', 60 -> '1 min')."""
    if seconds <= 0:
        return "0 sec"
    if seconds < 60:
        return f"{seconds} sec"
    minutes = seconds // 60
    rem = seconds % 60
    if rem == 0:
        return "1 min" if minutes == 1 else f"{minutes} mins"
    if minutes == 0:
        return f"{rem} sec"
    return f"{minutes} min {rem} sec"


def parse_gap(text: str) -> Optional[int]:
    """Parse a text string into seconds.

    Supports:
      - '5m', '5 min', '5 mins', '5 minute', '5 minutes' -> 300
      - '1h', '1 hr', '1 hour', '1 hours' -> 3600
      - '30s', '45 sec', '90 seconds' -> 30, 45, 90
      - '1m 30s', '1 min 30 sec', '1m30s', '1 min and 30 sec' -> 90
      - '1h 30m', '1 hr 30 min' -> 5400
      - '1.5m', '2.5 min' -> 90, 150
      - '0.5h', '1.5h' -> 1800, 5400
      - '300' (raw digits) -> 300
    """
    if not text or not isinstance(text, str):
        return None
    text = text.strip().lower()
    if not text:
        return None

    # Raw digits: e.g. "300", "90"
    if text.isdigit():
        return int(text)

    # Raw seconds: e.g. "90s", "90 sec", "90 secs", "90 seconds"
    m = re.match(r"^(\d+)\s*(?:s|sec|secs|second|seconds)$", text)
    if m:
        return int(m.group(1))

    # Hours and minutes: e.g. "1h 30m", "1 hr 30 min", "1 hour 30 mins"
    m = re.match(
        r"^(\d+)\s*(?:h|hr|hrs|hour|hours)\s*(?:and\s*)?(\d+)?\s*(?:m|min|mins|minute|minutes)?$",
        text,
    )
    if m:
        hours = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        return hours * 3600 + mins * 60

    # Minutes and seconds: e.g. "1m 30s", "1m30s", "1 min 30 sec", "1 minute 30 seconds"
    m = re.match(
        r"^(\d+)\s*(?:m|min|mins|minute|minutes)\s*(?:and\s*)?(\d+)?\s*(?:s|sec|secs|second|seconds)?$",
        text,
    )
    if m:
        mins = int(m.group(1))
        secs = int(m.group(2)) if m.group(2) else 0
        return mins * 60 + secs

    # Float hours: e.g. "1.5h", "0.5 hr", "1.5 hours"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)$", text)
    if m:
        return int(float(m.group(1)) * 3600)

    # Float minutes: e.g. "1.5m", "1.5 min", "2.5 mins"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)$", text)
    if m:
        return int(float(m.group(1)) * 60)

    # Float seconds: e.g. "10.5s", "10.5 sec"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)$", text)
    if m:
        return int(float(m.group(1)))

    return None


def build_gap_menu_keyboard(current_seconds: int = DEFAULT_GAP) -> InlineKeyboardMarkup:
    """Build the main preset gap menu keyboard.

    Top quick presets:
      [ 1 min ] [ 2 min ] [ 3 min ]
      [ 5 min (Default) ] [ 10 min ] [ 15 min ]
      [ 20 min ] [ 30 min ] [ 45 min ] [ 60 min ]
    Next row:
      [ 🔢 Pick Specific Minute (1-60) ] -> opens grid/pagination
    """
    _ = current_seconds  # Provided for context and parity
    buttons = [
        [
            InlineKeyboardButton("1 min", callback_data="gap_60"),
            InlineKeyboardButton("2 min", callback_data="gap_120"),
            InlineKeyboardButton("3 min", callback_data="gap_180"),
        ],
        [
            InlineKeyboardButton("5 min (Default)", callback_data="gap_300"),
            InlineKeyboardButton("10 min", callback_data="gap_600"),
            InlineKeyboardButton("15 min", callback_data="gap_900"),
        ],
        [
            InlineKeyboardButton("20 min", callback_data="gap_1200"),
            InlineKeyboardButton("30 min", callback_data="gap_1800"),
            InlineKeyboardButton("45 min", callback_data="gap_2700"),
            InlineKeyboardButton("60 min", callback_data="gap_3600"),
        ],
        [
            InlineKeyboardButton("🔢 Pick Specific Minute (1-60)", callback_data="gappage_1"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def build_minutes_grid_keyboard(page: int = 1) -> InlineKeyboardMarkup:
    """Build a 4-page grid allowing picking ANY minute from 1 to 60.

    Pages:
      Page 1: 1-15 min
      Page 2: 16-30 min
      Page 3: 31-45 min
      Page 4: 46-60 min

    Layout: 3 buttons per row (5 rows) + pagination navigation row:
      [ ⬅️ Prev ] [ Page 1/4 ] [ Next ➡️ ] [ 🔙 Back to Presets ]
    """
    if page < 1:
        page = 1
    elif page > 4:
        page = 4

    start_min = (page - 1) * 15 + 1
    end_min = page * 15

    buttons = []
    current_row = []
    for m in range(start_min, end_min + 1):
        current_row.append(
            InlineKeyboardButton(f"{m}m", callback_data=f"gap_{m * 60}")
        )
        if len(current_row) == 3:
            buttons.append(current_row)
            current_row = []
    if current_row:
        buttons.append(current_row)

    prev_page = 4 if page <= 1 else page - 1
    next_page = 1 if page >= 4 else page + 1

    nav_row = [
        InlineKeyboardButton("⬅️ Prev", callback_data=f"gappage_{prev_page}"),
        InlineKeyboardButton(f"Page {page}/4", callback_data=f"gappage_{page}"),
        InlineKeyboardButton("Next ➡️", callback_data=f"gappage_{next_page}"),
        InlineKeyboardButton("🔙 Back to Presets", callback_data="gap_menu"),
    ]
    buttons.append(nav_row)

    return InlineKeyboardMarkup(buttons)


async def handle_gap_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle gap callbacks (gap_<seconds>, gap_menu) and delegates gappage_ if needed."""
    query = update.callback_query
    await query.answer()

    if config.ADMIN_USER_ID and query.from_user.id != config.ADMIN_USER_ID:
        return

    data = query.data
    if not data:
        return

    if data.startswith("gappage_"):
        return await handle_gappage_callback(update, context)

    if data in ("gap_menu", "gap_presets"):
        current = database.get_rate_limit()
        formatted = format_gap(current)
        text = (
            f"⏱️ <b>Post Interval / Gap Settings</b>\n\n"
            f"Current post gap: <b>{formatted}</b> ({current}s)\n\n"
            f"This protects your Telegram channel by ensuring scheduled posts are spaced at least {formatted} apart.\n\n"
            "👇 <b>Select a preset below or pick a specific minute (1-60):</b>"
        )
        try:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=build_gap_menu_keyboard(current),
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise
        return

    if data.startswith("gap_"):
        try:
            seconds = int(data.replace("gap_", ""))
        except ValueError:
            return

        database.set_rate_limit(seconds)
        formatted = format_gap(seconds)

        text = (
            f"✅ <b>Post interval updated!</b>\n\n"
            f"Minimum delay between channel posts is now: <b>{formatted}</b> ({seconds}s).\n\n"
            f"Posts to <code>{config.CHANNEL_ID}</code> will always be spaced at least {formatted} apart to keep your account safe."
        )
        try:
            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=build_gap_menu_keyboard(seconds),
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise


async def handle_gappage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle minutes grid pagination callbacks (gappage_<page>)."""
    query = update.callback_query
    await query.answer()

    if config.ADMIN_USER_ID and query.from_user.id != config.ADMIN_USER_ID:
        return

    data = query.data
    if not data:
        return

    if data.startswith("gap_") and not data.startswith("gappage_"):
        return await handle_gap_callback(update, context)

    if data in ("gappage_0", "gappage_presets", "gappage_menu", "gap_menu"):
        return await handle_gap_callback(update, context)

    raw_page = data.replace("gappage_", "")
    try:
        page = int(raw_page)
    except ValueError:
        page = 1

    if page < 1:
        page = 1
    elif page > 4:
        page = 4

    current = database.get_rate_limit()
    formatted = format_gap(current)
    text = (
        f"🔢 <b>Pick Specific Post Gap (1-60 Minutes) — Page {page}/4</b>\n\n"
        f"Current gap: <b>{formatted}</b> ({current}s)\n\n"
        "Tap any minute below to set the post interval:"
    )
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=build_minutes_grid_keyboard(page),
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise


async def cmd_gap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View or change the post interval/gap via /gap or /interval."""
    user = update.effective_user
    if config.ADMIN_USER_ID and user and user.id != config.ADMIN_USER_ID:
        if update.message:
            await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    if context.args:
        arg_text = " ".join(context.args).strip()
        # If user typed just a number between 1 and 60 with no units, treat as minutes
        if arg_text.isdigit() and 1 <= int(arg_text) <= 60:
            seconds = int(arg_text) * 60
        else:
            seconds = parse_gap(arg_text)

        if seconds is None:
            await update.message.reply_text(
                "❌ <b>Could not parse gap format.</b>\n\n"
                "<b>Valid examples:</b>\n"
                "• <code>/gap 5m</code>\n"
                "• <code>/gap 10 min</code>\n"
                "• <code>/gap 15</code> (minutes)\n"
                "• <code>/gap 1h</code>\n"
                "• <code>/gap 300s</code>",
                parse_mode="HTML",
            )
            return

        if seconds < 10:
            await update.message.reply_text(
                "⚠️ Minimum post gap is <b>10 seconds</b> to keep your Telegram account safe from spam detection.",
                parse_mode="HTML",
            )
            return

        database.set_rate_limit(seconds)
        formatted = format_gap(seconds)
        await update.message.reply_text(
            f"✅ <b>Post interval updated!</b>\n\n"
            f"Minimum delay between channel posts is now: <b>{formatted}</b> ({seconds}s).\n\n"
            f"Posts to <code>{config.CHANNEL_ID}</code> will always be spaced at least {formatted} apart.",
            parse_mode="HTML",
        )
        return

    current = database.get_rate_limit()
    formatted = format_gap(current)
    await update.message.reply_text(
        f"⏱️ <b>Post Interval / Gap Settings</b>\n\n"
        f"Current post gap: <b>{formatted}</b> ({current}s)\n\n"
        f"This protects your Telegram channel by ensuring posts to <code>{config.CHANNEL_ID}</code> are never sent too rapidly.\n\n"
        "👇 <b>Select a preset below or pick a specific minute (1-60):</b>",
        parse_mode="HTML",
        reply_markup=build_gap_menu_keyboard(current),
    )


cmd_interval = cmd_gap


def register_gap_handlers(app: Application):
    """Register gap management command and callback handlers on the Telegram application."""
    app.add_handler(CommandHandler("gap", cmd_gap))
    app.add_handler(CommandHandler("interval", cmd_gap))
    app.add_handler(CallbackQueryHandler(handle_gap_callback, pattern=r"^gap_"))
    app.add_handler(CallbackQueryHandler(handle_gappage_callback, pattern=r"^gappage_"))
