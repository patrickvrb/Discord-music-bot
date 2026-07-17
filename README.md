# Discord bot to play YouTube URLs

Discord bot Python application to reproduce YouTube audio. Supports per-server queues and YouTube playlists.

## Features

### !play | !p \<youtube url> [volume]

Plays a YouTube video or queues up to 50 tracks from a YouTube playlist in order. Volume defaults to `0.5` and accepts values from `0` to `2`.

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
