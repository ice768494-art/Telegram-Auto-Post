ANIME AUTO POSTER BOT — RENDER WEB SERVICE

FILES
- main.py: Telegram bot code
- requirements.txt: Python dependencies
- render.yaml: Render Blueprint configuration

RENDER SETUP
1. Upload these files to a GitHub repository.
2. In Render, create a new Web Service from that repository (or use the Blueprint from render.yaml).
3. Build command: pip install -r requirements.txt
4. Start command: python main.py
5. Add these environment variables in Render:
   BOT_TOKEN = your Telegram bot token
   ADMIN_ID = your numeric Telegram user ID
   MONGO_URI = your MongoDB connection string
   MAIN_CHANNEL_ID = your Main Channel numeric ID (usually starts with -100)
6. Ensure the bot is an administrator in every source channel and in the Main Channel. It needs permission to post in the Main Channel.
7. Start the bot and send /start to it privately.

BOT COMMANDS
/start
/help
/addanime
/listanime
/removeanime Black Clover
/setmain -1001234567890
/cancel

HOW IT WORKS
Register each anime using /addanime. Provide the anime name, source channel ID, Join Channel URL, and a custom poster photo. When a new photo, video, or document post appears in a registered source channel, the bot posts an announcement in the Main Channel using the saved poster and two buttons: WATCH EPISODE (links to the original source post) and JOIN CHANNEL (uses the configured URL).

NOTE
This code posts a poster announcement with buttons; it does not copy/re-upload the episode media itself. Render polling requires the service to remain running. Never share BOT_TOKEN or MONGO_URI publicly.
