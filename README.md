# Discord bot to play YouTube URLs

Discord bot python application to reproduce YouTube audio. Supports queueing.

## Features

### !play | !p \<youtube url>

Plays Youtube's URL audio in the best quality possible

### !pause

Pause's the currently playing music

### !skip

Skips the currently playing music. If the last music from queue is skipped, the disconnects from the channel

### !resume

Resumes paused playback

## Instructions

- Create a Discord application at https://discord.com/developers
- Under "bot" panel, save your `token`, you'll need it later
- Clone repository
- (Recomended but optional) Create a virtual environment: `python -m venv venv` for a `venv` named environment
- Install dependencies: `pip install -r requirements.txt`
- Create a `.env` file and place your discord bot token under `DISCORD_BOT_KEY` variable name: `DISCORD_BOT_KEY=<your token here>`
- Run: `python discord_bot.py`
- Enjoy :)
