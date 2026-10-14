# CHANNEL OS

A private Telegram channel-management bot with multi-channel registration, immediate publishing, one-time scheduling, and daily/weekly recurring posts.

## Features

- Admin-only commands
- Register channels by `@username`
- Register a channel by forwarding a post when Telegram exposes the source channel
- Publish text, photos, videos, and documents
- Add URL buttons to posts
- One-time, daily, and weekly scheduled posts
- SQLite storage for channels and schedules
- Uses `Asia/Kolkata` by default

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather).
2. Create a GitHub repository and upload `main.py`, `requirements.txt`, and `render.yaml`.
3. Add your bot as an administrator to every destination channel, with permission to post messages.
4. Find your numeric Telegram user ID using a trusted user-ID bot.
5. Deploy the repository to Render as a Background Worker.
6. Add these environment variables in Render:
   - `BOT_TOKEN`: token from BotFather
   - `ADMIN_ID`: your numeric Telegram user ID
   - `DB_PATH`: `/var/data/channel_os.db` (if using the included persistent disk)
   - `TZ`: `Asia/Kolkata` (optional)
7. Start the bot and open it in Telegram.

The included `render.yaml` defines a paid `starter` worker and a 1 GB persistent disk. Review Render's current pricing before deploying. Persistent disks and worker plans may incur charges.

## Commands

- `/start` — help and menu
- `/addchannel @username` — register a public channel
- `/channels` — list registered channels
- `/removechannel @username` — remove a channel from the list
- `/publish` — publish a post immediately
- `/schedule` — schedule a post
- `/scheduled` — list active schedules
- `/cancel ID` — cancel a schedule

You can also forward a channel post to the bot to attempt channel registration. Telegram may hide source information for protected or certain forwarded posts; use `/addchannel` if forwarding does not expose the channel.

## Publishing flow

1. Send `/publish` or `/schedule` in the bot's private chat.
2. Select a registered destination channel.
3. Send one text, photo, video, or document message.
4. Add URL buttons, one per line, using `Label | https://example.com`, or send `none`.
5. For schedules, select one-time, daily, or weekly, then enter the first publish time as `YYYY-MM-DD HH:MM` in the configured time zone.

This initial version supports one Telegram message per post, not media albums. Daily/weekly posts continue until cancelled.

## Notes

- Keep the bot token secret. Never commit it to GitHub or share it in chat.
- SQLite is stored at `DB_PATH`. Use persistent storage on your host or data may be lost on redeploy/restart.
- The bot must remain running for timely scheduling. Render free services may sleep or have limitations, so the included worker uses a paid plan.
- Telegram limits and permissions apply. Analytics, subscriber tracking, and post editing are not included in this initial version.
