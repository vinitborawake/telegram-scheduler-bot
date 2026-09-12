import json
import logging
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from telegram import Bot, MessageEntity

import config
import database

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler: AsyncIOScheduler = None
bot: Bot = None


def init_scheduler(telegram_bot: Bot):
    """Initialize the scheduler and reload pending posts from the database."""
    global scheduler, bot
    bot = telegram_bot
    tz = pytz.timezone(config.TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.start()
    logger.info("Scheduler started with timezone: %s", config.TIMEZONE)

    # Reload pending posts from DB (crash recovery)
    pending = database.get_pending_posts()
    now = datetime.now(tz)
    reloaded = 0
    for post in pending:
        scheduled_time = datetime.fromisoformat(post["scheduled_time"])
        if scheduled_time.tzinfo is None:
            scheduled_time = tz.localize(scheduled_time)
        if scheduled_time > now:
            schedule_post(post["id"], scheduled_time)
            reloaded += 1
        else:
            # Post was missed (bot was down during scheduled time) — post it now
            logger.warning("Post #%d was missed, publishing now", post["id"])
            schedule_post(post["id"], now)
            reloaded += 1
    logger.info("Reloaded %d pending posts from database", reloaded)


def schedule_post(post_id: int, scheduled_time: datetime):
    """Schedule a post to be published at the given time."""
    tz = pytz.timezone(config.TIMEZONE)
    if scheduled_time.tzinfo is None:
        scheduled_time = tz.localize(scheduled_time)

    scheduler.add_job(
        publish_post,
        trigger=DateTrigger(run_date=scheduled_time),
        args=[post_id],
        id=f"post_{post_id}",
        replace_existing=True,
        misfire_grace_time=3600,  # Allow up to 1 hour late execution
    )
    logger.info("Scheduled post #%d for %s", post_id, scheduled_time)


def remove_scheduled_post(post_id: int):
    """Remove a scheduled job if it exists."""
    job_id = f"post_{post_id}"
    job = scheduler.get_job(job_id)
    if job:
        scheduler.remove_job(job_id)
        logger.info("Removed scheduled job for post #%d", post_id)


async def publish_post(post_id: int):
    """Publish a post to the Telegram channel."""
    post = database.get_post(post_id)
    if not post:
        logger.error("Post #%d not found in database", post_id)
        return

    if post["status"] != "pending":
        logger.info("Post #%d is no longer pending (status: %s), skipping", post_id, post["status"])
        return

    try:
        media_type = post.get("media_type", "photo")
        caption = post["caption"]
        media_file_id = post.get("media_file_id")

        # Deserialize entities from JSON if available
        entities = None
        entities_json = post.get("caption_entities")
        if entities_json:
            try:
                entities_list = json.loads(entities_json)
                entities = [MessageEntity.de_json(e, bot) for e in entities_list]
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("Failed to deserialize entities for post #%d: %s", post_id, e)

        if media_type == "photo" and media_file_id:
            if entities:
                await bot.send_photo(
                    chat_id=config.CHANNEL_ID,
                    photo=media_file_id,
                    caption=caption,
                    caption_entities=entities,
                )
            else:
                await bot.send_photo(
                    chat_id=config.CHANNEL_ID,
                    photo=media_file_id,
                    caption=caption,
                    parse_mode="HTML",
                )
        elif media_type == "video" and media_file_id:
            if entities:
                await bot.send_video(
                    chat_id=config.CHANNEL_ID,
                    video=media_file_id,
                    caption=caption,
                    caption_entities=entities,
                )
            else:
                await bot.send_video(
                    chat_id=config.CHANNEL_ID,
                    video=media_file_id,
                    caption=caption,
                    parse_mode="HTML",
                )
        else:
            # Text-only post
            if entities:
                await bot.send_message(
                    chat_id=config.CHANNEL_ID,
                    text=caption,
                    entities=entities,
                )
            else:
                await bot.send_message(
                    chat_id=config.CHANNEL_ID,
                    text=caption,
                    parse_mode="HTML",
                )

        database.mark_posted(post_id)
        logger.info("✅ Published post #%d to channel %s", post_id, config.CHANNEL_ID)

        # Notify admin
        try:
            await bot.send_message(
                chat_id=config.ADMIN_USER_ID,
                text=f"✅ Post #{post_id} has been published to the channel!",
            )
        except Exception:
            pass  # Don't fail if admin notification fails

    except Exception as e:
        logger.error("❌ Failed to publish post #%d: %s", post_id, e)
        # Notify admin about failure
        try:
            await bot.send_message(
                chat_id=config.ADMIN_USER_ID,
                text=f"❌ Failed to publish post #{post_id}!\nError: {e}",
            )
        except Exception:
            pass
