"""
Pyrogram User Client Module for Telegram Scheduler Bot.

Allows posting via the admin's Telegram Premium user account, preserving 100% native
custom and animated premium emojis in channel posts.
"""

import asyncio
import logging
import os
from typing import Optional

from pyrogram import Client, errors
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config

logger = logging.getLogger(__name__)

# Standard Telegram desktop API credentials
API_ID = int(os.getenv("TELEGRAM_API_ID", 2040))
API_HASH = os.getenv("TELEGRAM_API_HASH", "b18441a1ff607e10a989891a5462e627")

# Session storage paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR = os.path.join(BASE_DIR, "data", "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)
SESSION_NAME = "admin"
SESSION_FILE = os.path.join(SESSION_DIR, f"{SESSION_NAME}.session")

# Login conversation states
LOGIN_PHONE, LOGIN_OTP, LOGIN_2FA = range(3)

# Global singleton client instance and lock
_client: Optional[Client] = None
_client_lock = asyncio.Lock()


def admin_only(func):
    """Decorator to restrict userbot management commands to the configured admin."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or user.id != config.ADMIN_USER_ID:
            if update.message:
                await update.message.reply_text("⛔ You are not authorized to perform this action.")
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapper


def _parse_chat_id(chat_id):
    """Normalize chat ID for Pyrogram (int for numerical IDs, str for @usernames)."""
    if isinstance(chat_id, int):
        return chat_id
    if isinstance(chat_id, str):
        s = chat_id.strip()
        if (s.startswith("-") and s[1:].isdigit()) or s.isdigit():
            return int(s)
        return s
    return chat_id


def has_user_session() -> bool:
    """Check whether a valid user session file exists on disk."""
    return os.path.exists(SESSION_FILE) and os.path.getsize(SESSION_FILE) > 0


async def get_user_client() -> Optional[Client]:
    """Get the active Pyrogram Client instance.
    
    If the client is not yet instantiated or connected, and a session exists,
    starts/connects the client and verifies authorization.
    Returns the connected Client instance, or None if not logged in.
    """
    global _client
    if not has_user_session():
        return None

    async with _client_lock:
        if _client is None:
            _client = Client(
                name=SESSION_NAME,
                workdir=SESSION_DIR,
                api_id=API_ID,
                api_hash=API_HASH,
                no_updates=True,
            )

        if not _client.is_connected:
            try:
                is_authorized = await _client.connect()
                if not is_authorized:
                    logger.warning("Session file exists but user client is not authorized.")
                    await _client.disconnect()
                    return None
                _client.me = await _client.get_me()
                if not _client.is_initialized:
                    await _client.initialize()
            except ConnectionError:
                # Already connected
                pass
            except Exception as e:
                logger.error(f"Failed to connect Pyrogram user client: {e}")
                return None

        return _client


async def send_post_as_user(
    channel_id: str,
    caption: str,
    media_type: str,
    media_file_id: str = None,
    source_chat_id: int = None,
    source_message_id: int = None,
) -> bool:
    """Send a post using the logged-in Telegram user account.
    
    If source_chat_id and source_message_id are provided, uses message copying
    which preserves 100% of Telegram Premium animated and custom emojis.
    Otherwise falls back to direct API sending (photo, video, or text).
    """
    client = await get_user_client()
    if not client:
        logger.warning("send_post_as_user called but user client is not logged in or available.")
        return False

    target_chat = _parse_chat_id(channel_id)

    # 1. Primary path: Copy message to preserve custom and animated premium emojis
    if source_chat_id and source_message_id:
        src_chat = _parse_chat_id(source_chat_id)
        try:
            if hasattr(client, "copy_message"):
                await client.copy_message(
                    chat_id=target_chat,
                    from_chat_id=src_chat,
                    message_id=source_message_id,
                )
            elif hasattr(client, "copy_messages"):
                await client.copy_messages(
                    chat_id=target_chat,
                    from_chat_id=src_chat,
                    message_ids=source_message_id,
                )
            logger.info("✅ Published post via Pyrogram user client copy_message (premium emojis preserved)")
            return True
        except Exception as e:
            logger.warning(
                f"Pyrogram copy_message failed from chat {src_chat} msg {source_message_id}: {e}. "
                "Attempting direct send fallback."
            )

    # 2. Fallback path: Direct send
    try:
        if media_type == "photo" and media_file_id:
            await client.send_photo(
                chat_id=target_chat,
                photo=media_file_id,
                caption=caption or "",
            )
        elif media_type == "video" and media_file_id:
            await client.send_video(
                chat_id=target_chat,
                video=media_file_id,
                caption=caption or "",
            )
        else:
            await client.send_message(
                chat_id=target_chat,
                text=caption or "",
            )
        logger.info(f"✅ Published post directly via Pyrogram user client to {channel_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Pyrogram direct send failed for channel {channel_id}: {e}")
        return False


# ── Login Conversation Handlers ─────────────────────────────────────────────

@admin_only
async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the userbot login conversation."""
    global _client
    if has_user_session():
        try:
            client = await get_user_client()
            if client and client.is_connected:
                me = client.me or await client.get_me()
                user_handle = f"@{me.username}" if me.username else "None"
                await update.message.reply_text(
                    f"⚠️ <b>Already Logged In</b>\n\n"
                    f"Connected account: <b>{me.first_name}</b> ({user_handle})\n\n"
                    f"If you want to log in with a different account, first run /logout.",
                    parse_mode="HTML",
                )
                return ConversationHandler.END
        except Exception:
            pass

    async with _client_lock:
        if _client is not None:
            try:
                if _client.is_connected:
                    await _client.disconnect()
            except Exception:
                pass
            _client = None

        _client = Client(
            name=SESSION_NAME,
            workdir=SESSION_DIR,
            api_id=API_ID,
            api_hash=API_HASH,
            no_updates=True,
        )

    await update.message.reply_text(
        "📱 <b>Telegram Premium Userbot Login</b>\n\n"
        "Please enter your phone number in international format, including your country code.\n"
        "<i>Example: <code>+1234567890</code> or <code>+919876543210</code></i>\n\n"
        "Send /cancel to abort at any time.",
        parse_mode="HTML",
    )
    return LOGIN_PHONE


@admin_only
async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive phone number and request OTP code from Telegram."""
    global _client
    phone = update.message.text.strip().replace(" ", "").replace("-", "")

    if not phone.startswith("+") or not phone[1:].isdigit() or len(phone) < 8:
        await update.message.reply_text(
            "⚠️ <b>Invalid phone format.</b>\n\n"
            "Please provide a valid phone number starting with <code>+</code> and country code.\n"
            "<i>Example: <code>+1234567890</code></i>\n"
            "Or send /cancel to abort.",
            parse_mode="HTML",
        )
        return LOGIN_PHONE

    async with _client_lock:
        if _client is None:
            _client = Client(
                name=SESSION_NAME,
                workdir=SESSION_DIR,
                api_id=API_ID,
                api_hash=API_HASH,
                no_updates=True,
            )

        if not _client.is_connected:
            await _client.connect()

    try:
        sent_code = await _client.send_code(phone)
        context.user_data["userbot_phone"] = phone
        context.user_data["userbot_phone_code_hash"] = sent_code.phone_code_hash

        await update.message.reply_text(
            "📩 <b>Verification Code Sent!</b>\n\n"
            "A login code has been sent to your Telegram app (or via SMS).\n\n"
            "Please send the code here:\n"
            "<i>(e.g., <code>12345</code> or <code>1 2 3 4 5</code>)</i>\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML",
        )
        return LOGIN_OTP

    except errors.PhoneNumberInvalid:
        await update.message.reply_text(
            "❌ <b>Phone number is invalid</b> according to Telegram.\n"
            "Please check the country code and try /login again.",
            parse_mode="HTML",
        )
        return ConversationHandler.END
    except errors.FloodWait as e:
        await update.message.reply_text(
            f"⏳ <b>Flood Wait Triggered</b>\n"
            f"Telegram requires waiting <b>{e.value} seconds</b> before requesting another code.\n"
            f"Please wait and try /login later.",
            parse_mode="HTML",
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error sending login code: {e}")
        await update.message.reply_text(
            f"❌ <b>Failed to send verification code:</b> {e}\n"
            "Please run /login to try again.",
            parse_mode="HTML",
        )
        return ConversationHandler.END


@admin_only
async def receive_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive OTP and sign into Telegram."""
    global _client
    phone = context.user_data.get("userbot_phone")
    phone_code_hash = context.user_data.get("userbot_phone_code_hash")

    if not phone or not phone_code_hash:
        await update.message.reply_text(
            "⚠️ Login session expired or lost. Please run /login again.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    code = update.message.text.strip().replace(" ", "").replace("-", "")

    if _client is None or not _client.is_connected:
        try:
            if _client is not None:
                await _client.connect()
        except Exception:
            pass
        if _client is None or not _client.is_connected:
            await update.message.reply_text("⚠️ Client connection lost. Please run /login again.")
            return ConversationHandler.END

    try:
        await _client.sign_in(phone_number=phone, phone_code_hash=phone_code_hash, phone_code=code)
    except errors.SessionPasswordNeeded:
        await update.message.reply_text(
            "🔐 <b>Two-Step Verification (2FA) Required</b>\n\n"
            "Your account is protected by a Two-Step Verification cloud password.\n"
            "Please send your 2FA password below:\n\n"
            "<i>(Your message containing the password will be deleted immediately for privacy)</i>\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML",
        )
        return LOGIN_2FA
    except errors.PhoneCodeInvalid:
        await update.message.reply_text(
            "❌ <b>Invalid verification code.</b>\n"
            "Please check the code received on Telegram and try entering it again (or /cancel):",
            parse_mode="HTML",
        )
        return LOGIN_OTP
    except errors.PhoneCodeExpired:
        await update.message.reply_text(
            "❌ <b>Verification code has expired.</b>\nPlease run /login again.",
            parse_mode="HTML",
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Sign-in error: {e}")
        await update.message.reply_text(
            f"❌ <b>Sign-in failed:</b> {e}\nPlease run /login again.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    # Success without 2FA
    return await _finish_login(update, context)


@admin_only
async def receive_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive 2FA password and finalize login."""
    global _client
    password = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    if _client is None or not _client.is_connected:
        await update.message.reply_text("⚠️ Connection lost. Please run /login again.")
        return ConversationHandler.END

    try:
        await _client.check_password(password=password)
    except errors.PasswordHashInvalid:
        await update.message.reply_text(
            "❌ <b>Incorrect 2FA password.</b>\n"
            "Please enter your Two-Step Verification password again (or /cancel):",
            parse_mode="HTML",
        )
        return LOGIN_2FA
    except Exception as e:
        logger.error(f"2FA verification error: {e}")
        await update.message.reply_text(
            f"❌ <b>2FA verification failed:</b> {e}\nPlease run /login again.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    return await _finish_login(update, context)


async def _finish_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Helper to initialize client and notify admin of successful login."""
    global _client
    try:
        _client.me = await _client.get_me()
        if not _client.is_initialized:
            await _client.initialize()

        context.user_data.pop("userbot_phone", None)
        context.user_data.pop("userbot_phone_code_hash", None)

        me = _client.me
        is_premium = getattr(me, "is_premium", False)
        premium_badge = " ⭐ (Telegram Premium Active)" if is_premium else ""
        user_handle = f"@{me.username}" if me.username else "None"

        await update.message.reply_text(
            f"🎉 <b>Userbot Login Successful!</b>\n\n"
            f"👤 <b>Account:</b> {me.first_name} {me.last_name or ''} ({user_handle}){premium_badge}\n"
            f"🆔 <b>User ID:</b> <code>{me.id}</code>\n"
            f"💾 <b>Session saved:</b> <code>data/sessions/admin.session</code>\n\n"
            f"✨ <b>Features unlocked:</b>\n"
            f"• 100% native custom & animated Telegram Premium emojis in scheduled channel posts!\n"
            f"• Seamless message copying with exact original styling and assets.\n\n"
            f"Use /userstatus to check status or /logout to disconnect at any time.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error finishing userbot login: {e}")
        await update.message.reply_text(
            f"⚠️ Signed in, but encountered error during setup: {e}\n"
            "Run /userstatus to verify connectivity.",
            parse_mode="HTML",
        )
    return ConversationHandler.END


@admin_only
async def cancel_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel ongoing login conversation."""
    global _client
    async with _client_lock:
        if _client is not None and not has_user_session():
            try:
                if _client.is_connected:
                    await _client.disconnect()
            except Exception:
                pass
            _client = None

    context.user_data.pop("userbot_phone", None)
    context.user_data.pop("userbot_phone_code_hash", None)

    if update.message:
        await update.message.reply_text("❌ Login cancelled.")
    return ConversationHandler.END


@admin_only
async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disconnect and delete the userbot session."""
    global _client
    async with _client_lock:
        if _client is not None:
            try:
                if _client.is_connected:
                    await _client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting client on logout: {e}")
            _client = None

        deleted = False
        for suffix in ["", "-journal"]:
            fpath = SESSION_FILE + suffix
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                    deleted = True
                except Exception as e:
                    logger.error(f"Error removing {fpath}: {e}")

    if deleted:
        await update.message.reply_text(
            "👋 <b>Logged out successfully.</b>\n\n"
            "The user session file has been deleted. Posts will now be published using the standard bot client.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "ℹ️ No active user session found to log out.",
            parse_mode="HTML",
        )


@admin_only
async def cmd_user_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show whether the user account is currently logged in and active."""
    if not has_user_session():
        await update.message.reply_text(
            "ℹ️ <b>User Client Status:</b> <code>Not Logged In</code>\n\n"
            "Channel posts are currently sent via standard Bot API.\n"
            "Run /login to connect your Telegram Premium user account for animated and custom emojis.",
            parse_mode="HTML",
        )
        return

    try:
        client = await get_user_client()
        if client and client.is_connected:
            me = client.me or await client.get_me()
            is_premium = getattr(me, "is_premium", False)
            premium_str = "🌟 Yes (Custom & Animated Emojis Enabled!)" if is_premium else "No"
            user_handle = f"@{me.username}" if me.username else "None"

            await update.message.reply_text(
                f"✅ <b>User Client Status: Active & Connected</b>\n\n"
                f"👤 <b>Name:</b> {me.first_name} {me.last_name or ''}\n"
                f"🆔 <b>User ID:</b> <code>{me.id}</code>\n"
                f"🏷️ <b>Username:</b> {user_handle}\n"
                f"⭐ <b>Telegram Premium:</b> {premium_str}\n"
                f"📁 <b>Session Path:</b> <code>data/sessions/admin.session</code>\n\n"
                f"Scheduled posts will be copied/sent through this account.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "⚠️ <b>User Client Status: Session file exists, but client is disconnected.</b>\n\n"
                "Run /login to re-authenticate or /logout to reset.",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Error in cmd_user_status: {e}")
        await update.message.reply_text(
            f"❌ <b>Error checking user status:</b> {e}\nRun /login to re-authenticate.",
            parse_mode="HTML",
        )


def get_login_conversation_handler() -> ConversationHandler:
    """Create and return the ConversationHandler for userbot /login."""
    return ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login)],
        states={
            LOGIN_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone),
                CommandHandler("cancel", cancel_login),
                CommandHandler("done", cancel_login),
            ],
            LOGIN_OTP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_otp),
                CommandHandler("cancel", cancel_login),
                CommandHandler("done", cancel_login),
            ],
            LOGIN_2FA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_2fa),
                CommandHandler("cancel", cancel_login),
                CommandHandler("done", cancel_login),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_login),
            CommandHandler("done", cancel_login),
        ],
    )


__all__ = [
    "API_ID",
    "API_HASH",
    "LOGIN_PHONE",
    "LOGIN_OTP",
    "LOGIN_2FA",
    "has_user_session",
    "get_user_client",
    "send_post_as_user",
    "cmd_login",
    "receive_phone",
    "receive_otp",
    "receive_2fa",
    "cmd_logout",
    "cmd_user_status",
    "cancel_login",
    "get_login_conversation_handler",
]
