# Telegram Channel Scheduler Bot

A Telegram bot that lets you schedule posts (image + text) to your Telegram channel. Send posts via private chat, set a date/time, and the bot automatically publishes them on schedule.

## Features

- 📸 Send image + caption to schedule a post
- 🕐 Set exact date & time for publishing
- 📋 List all pending scheduled posts
- ❌ Cancel any scheduled post
- 🔄 Crash recovery — reloads pending posts on restart
- 🔒 Admin-only access (only your Telegram account can use it)
- 📬 Notifications when posts are published or fail

## Prerequisites

- Python 3.10 or higher
- A Telegram account

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the **Bot Token** (looks like `123456:ABC-DEF...`)

### 2. Set Up Your Channel

1. Create a Telegram channel (or use an existing one)
2. Go to channel settings → **Administrators** → **Add Admin**
3. Search for your bot by username and add it
4. Give it **"Post Messages"** permission

### 3. Get Your IDs

**Channel ID:**
- If your channel has a public username (e.g., `@mychannel`), use that
- For private channels: forward a message from the channel to [@userinfobot](https://t.me/userinfobot) to get the numeric ID (starts with `-100`)

**Your User ID:**
- Message [@userinfobot](https://t.me/userinfobot) on Telegram
- It will reply with your numeric user ID

### 4. Configure the Bot

```bash
# Copy the example env file
copy .env.example .env
```

Edit `.env` with your values:
```
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
CHANNEL_ID=@your_channel_username
ADMIN_USER_ID=123456789
TIMEZONE=Asia/Kolkata
```

### 5. Install Dependencies

```bash
pip install -r requirements.txt
```

### 6. Run the Bot

```bash
python bot.py
```

You should see:
```
🤖 Bot is starting...
Channel: @your_channel
Admin user ID: 123456789
Timezone: Asia/Kolkata
```

## Usage

### Schedule a Post

1. Open your bot in Telegram (private chat)
2. Send `/newpost`
3. Send a photo 📸
4. Type the caption/text for the post ✏️
5. Enter the date and time: `2026-09-15 14:30` 🕐
6. Done! ✅ The bot will post at the scheduled time

### View Pending Posts

Send `/list` to see all scheduled posts with their IDs and times.

### Cancel a Post

Send `/cancel 3` (replace `3` with the post ID from `/list`).

### Cancel Current Operation

Send `/done` at any point during the `/newpost` flow to cancel.

## Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/newpost` | Schedule a new post |
| `/list` | View all pending posts |
| `/cancel <id>` | Cancel a scheduled post |
| `/ratelimit` | Set or view minimum delay between posts (e.g. `1m 30s` or `30s`) |
| `/done` | Cancel current operation |
| `/help` | Show help message |

## Notes

- The bot uses **HTML formatting** for captions. You can use:
  - `<b>bold</b>` for **bold**
  - `<i>italic</i>` for *italic*
  - `<a href="https://example.com">link text</a>` for links
- All times use the timezone configured in `.env` (default: Asia/Kolkata / IST)
- If the bot is offline during a scheduled time, it will post missed posts when it restarts
- The database (`scheduled_posts.db`) is created automatically in the project folder
- Keep the bot running (e.g., on a VPS or your PC) for posts to be published on time

## Keeping the Bot Running

For the bot to post at the right time, it needs to be running continuously. Options:

1. **Keep your PC on** — simplest option, just leave the terminal open
2. **Use a VPS** (like DigitalOcean, AWS, etc.) — run it on a cloud server
3. **Use `nohup`** (Linux/Mac): `nohup python bot.py &`
4. **Use Task Scheduler** (Windows) to auto-start the bot on login
