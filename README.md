# Discord bot to play YouTube URLs

Discord bot Python application to reproduce YouTube audio. Supports per-server queues and YouTube playlists.

## Features

### !play | !p \<youtube url> [volume]

Plays a YouTube video or queues up to 50 tracks from a YouTube playlist in order. YouTube radio/mix links (`list=RD...`) play only the selected video. Volume defaults to `0.5` and accepts values from `0` to `2`.

### !playp \<youtube url> [volume]

Adds a video as the next track to play, ahead of the waiting queue, without interrupting the current track. For playlists, all extracted tracks are placed ahead of the waiting queue in playlist order. Volume defaults to `0.5` and accepts values from `0` to `2`.

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

## Deploying to Railway with GitHub Actions

The workflow in `.github/workflows/deploy-railway.yml` runs the test suite for
pull requests and pushes. A successful push to `main` deploys the application
to Railway.

1. Create a Railway project and a service named `discord-music-bot`.
2. In the Railway service's Variables tab, add
   `DISCORD_BOT_KEY=<your Discord bot token>`.
3. In the Railway project settings, create a project token for the production
   environment.
4. In the GitHub repository, open **Settings > Secrets and variables > Actions**
   and add the project token as a repository secret named `RAILWAY_TOKEN`.
5. Disable Railway's GitHub autodeploy for this service if it is enabled. The
   GitHub Actions workflow is responsible for deployment, so enabling both
   would create duplicate deployments.
6. Push to `main` and follow the **Test and deploy to Railway** workflow in the
   repository's Actions tab.

Railway uses `railway.json` to build with Railpack, run
`python discord_bot.py`, restart the always-on worker, and avoid overlapping
bot instances during deployment. `railpack.json` installs the native Opus
runtime library required for Discord PCM voice playback. This bot does not need
a public domain.
