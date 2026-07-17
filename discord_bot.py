import asyncio
import os
import sys
from pathlib import Path
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import chain, islice
from urllib.parse import parse_qs, urlencode, urlsplit

import discord
import imageio_ffmpeg
import yt_dlp as youtube_dl
from discord.ext import commands
from dotenv import load_dotenv

PLAYLIST_LIMIT = 50
IDLE_DISCONNECT_DELAY = 2
YOUTUBE_HOSTS = {
    'youtube.com',
    'www.youtube.com',
    'm.youtube.com',
    'music.youtube.com',
    'youtu.be',
    'www.youtu.be',
}


@dataclass
class Track:
    url: str
    title: str
    volume: float
    channel: object


def acquire_instance_lock():
    lock_path = Path(__file__).with_name('.bot.lock')
    try:
        lock_file = lock_path.open('a+b')
        lock_file.seek(0)
        if lock_file.read(1) == b'':
            lock_file.write(b'0')
            lock_file.flush()
        lock_file.seek(0)
    except OSError as error:
        raise RuntimeError('Another bot instance is already running.') from error

    if os.name == 'nt':
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            lock_file.close()
            raise RuntimeError('Another bot instance is already running.') from error
    else:
        import fcntl

        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            lock_file.close()
            raise RuntimeError('Another bot instance is already running.') from error
    return lock_file


def configure_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure:
            reconfigure(encoding='utf-8', errors='backslashreplace')


configure_output()
load_dotenv()
intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

DISCORD_BOT_KEY = os.getenv('DISCORD_BOT_KEY')
if not DISCORD_BOT_KEY:
    raise RuntimeError('Bot key not found')

song_queues = defaultdict(deque)
queue_locks = defaultdict(asyncio.Lock)
connection_locks = defaultdict(asyncio.Lock)
player_tasks = {}
pending_links = set()


def parse_youtube_playlist_url(url):
    """Return a playlist extraction URL and the explicitly selected video ID."""
    try:
        parsed_url = urlsplit(url)
    except ValueError:
        return url, None

    hostname = (parsed_url.hostname or '').lower().rstrip('.')
    if hostname not in YOUTUBE_HOSTS:
        return url, None

    playlist_ids = parse_qs(parsed_url.query, keep_blank_values=True).get('list')
    if not playlist_ids or not playlist_ids[0].strip():
        return url, None

    if parsed_url.path.rstrip('/') == '/playlist':
        return url, None

    query = urlencode({'list': playlist_ids[0]})
    video_ids = parse_qs(parsed_url.query).get('v')
    if parsed_url.path.rstrip('/') == '/watch':
        selected_video_id = video_ids[0].strip() if video_ids else None
    elif hostname in {'youtu.be', 'www.youtu.be'}:
        selected_video_id = parsed_url.path.strip('/') or None
    else:
        selected_video_id = None
    return f'https://www.youtube.com/playlist?{query}', selected_video_id


def normalize_youtube_playlist_url(url):
    """Turn a YouTube watch URL with a playlist ID into a playlist URL."""
    extraction_url, _ = parse_youtube_playlist_url(url)
    return extraction_url


def track_from_info(info, original_url, volume, channel):
    title = info.get('title') or original_url
    webpage_url = info.get('webpage_url') or info.get('original_url') or original_url
    return Track(webpage_url, title, volume, channel)


def extract_single_track(url, volume, channel):
    ydl_opts = {
        'ignoreerrors': True,
        'noplaylist': True,
        'no_warnings': True,
        'quiet': True,
    }
    info = youtube_dl.YoutubeDL(ydl_opts).extract_info(url, download=False)
    if not info:
        return []
    return [track_from_info(info, url, volume, channel)]


def extract_tracks(url, volume, channel):
    ydl_opts = {
        'extract_flat': True,
        'ignoreerrors': True,
        'lazy_playlist': True,
        'noplaylist': False,
        'no_warnings': True,
        'quiet': True,
    }
    extraction_url, selected_video_id = parse_youtube_playlist_url(url)
    if selected_video_id is None:
        ydl_opts['playlistend'] = PLAYLIST_LIMIT
    info = youtube_dl.YoutubeDL(ydl_opts).extract_info(extraction_url, download=False)
    if not info:
        return [], 1, False

    entries = info.get('entries')
    if entries is None:
        return [track_from_info(info, url, volume, channel)], 0, False

    tracks = []
    unavailable = 0
    selected_video_found = selected_video_id is None
    selected_entries = iter(entries)
    if not selected_video_found:
        for entry in selected_entries:
            if entry and entry.get('id') == selected_video_id:
                selected_entries = chain((entry,), selected_entries)
                selected_video_found = True
                break

    if not selected_video_found:
        return extract_single_track(url, volume, channel), 0, False

    for entry in islice(selected_entries, PLAYLIST_LIMIT):
        if not entry:
            unavailable += 1
            continue
        track_url = entry.get('webpage_url') or entry.get('url')
        if not track_url:
            unavailable += 1
            continue
        if not track_url.startswith(('http://', 'https://')) and entry.get('id'):
            track_url = f"https://www.youtube.com/watch?v={entry['id']}"
        tracks.append(Track(track_url, entry.get('title') or track_url, volume, channel))
    return tracks, unavailable, True


def extract_audio(track):
    ydl_opts = {
        'format': 'bestaudio/best',
        'nocheckcertificate': True,
        'noplaylist': True,
        'no_warnings': True,
        'quiet': True,
        'source_address': '0.0.0.0',
    }
    return youtube_dl.YoutubeDL(ydl_opts).extract_info(track.url, download=False)


def finish_playback(future, error):
    if not future.done():
        future.set_result(error)


async def play_track(voice_client, track):
    info = await asyncio.to_thread(extract_audio, track)
    media_url = info.get('url')
    if not media_url:
        raise RuntimeError('no audio stream was returned')

    headers = ''.join(
        f'{name}: {value}\r\n' for name, value in info.get('http_headers', {}).items()
    )
    before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
    if headers:
        before_options += f' -headers "{headers}"'

    player = discord.FFmpegPCMAudio(
        media_url,
        executable=imageio_ffmpeg.get_ffmpeg_exe(),
        before_options=before_options,
    )
    source = discord.PCMVolumeTransformer(player, track.volume)
    finished = bot.loop.create_future()
    voice_client.play(
        source,
        after=lambda error: bot.loop.call_soon_threadsafe(finish_playback, finished, error),
    )
    await track.channel.send(f"Now playing: {info.get('title') or track.title}")
    error = await finished
    if error:
        raise error


async def player_worker(guild, voice_client):
    guild_id = guild.id
    try:
        while voice_client.is_connected():
            async with queue_locks[guild_id]:
                track = song_queues[guild_id].popleft() if song_queues[guild_id] else None

            if track is None:
                await asyncio.sleep(IDLE_DISCONNECT_DELAY)
                async with queue_locks[guild_id]:
                    if song_queues[guild_id]:
                        continue
                    song_queues.pop(guild_id, None)
                await disconnect_voice_client(guild)
                return

            try:
                await play_track(voice_client, track)
            except Exception as error:
                print(f'Playback skipped: {track.title!r}: {error!r}')
                await track.channel.send(f"Skipped: {track.title}")
    finally:
        current_task = asyncio.current_task()
        if player_tasks.get(guild_id) is current_task:
            player_tasks.pop(guild_id, None)


async def ensure_player(guild, voice_channel):
    guild_id = guild.id
    async with connection_locks[guild_id]:
        voice_client = discord.utils.get(bot.voice_clients, guild=guild)
        if voice_client and voice_client.is_connected():
            if voice_client.channel != voice_channel:
                await voice_client.move_to(voice_channel)
        else:
            voice_client = await voice_channel.connect()
            await voice_client.guild.change_voice_state(channel=voice_channel, self_deaf=True)

        task = player_tasks.get(guild_id)
        if task is None or task.done():
            player_tasks[guild_id] = asyncio.create_task(player_worker(guild, voice_client))


@bot.command(aliases=['p'])
async def play(ctx, url, volume='0.5'):
    if not ctx.guild or not ctx.message.author.voice:
        await ctx.send('Join a voice channel first.')
        return

    try:
        requested_volume = float(volume)
        if not 0 <= requested_volume <= 2:
            raise ValueError
    except ValueError:
        await ctx.send('Volume must be a number between 0 and 2.')
        return

    request_key = (ctx.guild.id, url)
    async with queue_locks[ctx.guild.id]:
        if request_key in pending_links:
            return
        pending_links.add(request_key)

    try:
        tracks, unavailable, is_playlist = await asyncio.to_thread(
            extract_tracks, url, requested_volume, ctx.channel
        )
    except Exception as error:
        print(f'Link extraction failed: {error!r}')
        await ctx.send('Could not read that link.')
        return
    finally:
        async with queue_locks[ctx.guild.id]:
            pending_links.discard(request_key)

    if not tracks:
        await ctx.send('No playable tracks were found.')
        return

    async with queue_locks[ctx.guild.id]:
        song_queues[ctx.guild.id].extend(tracks)

    if is_playlist:
        message = f"Queued {len(tracks)} playlist tracks"
        if len(tracks) == PLAYLIST_LIMIT:
            message += f" (limited to {PLAYLIST_LIMIT})"
        if unavailable:
            message += f"; {unavailable} unavailable skipped"
        await ctx.send(message + '.')
    else:
        await ctx.send(f"Queued: {tracks[0].title}")

    try:
        await ensure_player(ctx.guild, ctx.message.author.voice.channel)
    except Exception as error:
        print(f'Voice connection failed: {error!r}')
        await ctx.send('Could not connect to the voice channel.')


@bot.command()
async def pause(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send('Paused the music.')


@bot.command()
async def skip(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client and voice_client.is_connected():
        await ctx.send('Skipping...')
        voice_client.stop()


@bot.command()
async def resume(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send('Resumed the music.')


@bot.event
async def on_ready():
    print(f'Bot connected as {bot.user}')


async def disconnect_voice_client(guild):
    voice_client = discord.utils.get(bot.voice_clients, guild=guild)
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        print(f'Command failed: {error.original!r}')
        await ctx.send('The command failed. Check the bot log for details.')
    else:
        await ctx.send(str(error))


if __name__ == '__main__':
    instance_lock = acquire_instance_lock()
    bot.run(DISCORD_BOT_KEY)