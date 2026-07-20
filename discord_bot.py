import asyncio
import io
import os
import sys
from pathlib import Path
from collections import defaultdict, deque
from dataclasses import dataclass
from itertools import chain, islice
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import discord
import imageio_ffmpeg
import yt_dlp as youtube_dl
from discord.ext import commands
from dotenv import load_dotenv

PLAYLIST_LIMIT = 50
IDLE_DISCONNECT_DELAY = 2
STARTUP_FAILURE_SECONDS = 5
PLAYBACK_END_TOLERANCE_SECONDS = 5
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


@dataclass
class PlaybackSession:
    track: Track
    skip_requested: bool = False


@dataclass
class PlaybackAttemptResult:
    started: bool
    decoded_seconds: float
    expected_duration: float | None
    error: Exception | None
    ffmpeg_diagnostics: str = ''

    @property
    def ended_prematurely(self):
        if self.expected_duration is None:
            return not self.started
        return (
            self.decoded_seconds + PLAYBACK_END_TOLERANCE_SECONDS
            < self.expected_duration
        )

    @property
    def failed(self):
        return self.error is not None or self.ended_prematurely

    @property
    def startup_failure(self):
        return self.failed and self.decoded_seconds < STARTUP_FAILURE_SECONDS


class PlaybackSource(discord.AudioSource):
    """Track decoded PCM frames while delegating playback to another source."""

    def __init__(self, source, loop, first_frame):
        self.source = source
        self.loop = loop
        self.first_frame = first_frame
        self.frame_count = 0

    @property
    def decoded_seconds(self):
        return self.frame_count * 0.02

    def read(self):
        data = self.source.read()
        if data:
            self.frame_count += 1
            if self.frame_count == 1:
                self.loop.call_soon_threadsafe(mark_first_frame, self.first_frame)
        return data

    def is_opus(self):
        return self.source.is_opus()

    def cleanup(self):
        self.source.cleanup()


class PlaybackFailure(RuntimeError):
    pass


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
active_playbacks = {}


def normalize_youtube_radio_url(url):
    """Strip generated YouTube radio context from an individual video URL."""
    try:
        parsed_url = urlsplit(url)
    except ValueError:
        return url, False

    hostname = (parsed_url.hostname or '').lower().rstrip('.')
    if hostname not in YOUTUBE_HOSTS:
        return url, False

    query_items = parse_qsl(parsed_url.query, keep_blank_values=True)
    playlist_id = next(
        (value.strip() for name, value in query_items if name == 'list'),
        '',
    )
    if not playlist_id.startswith('RD'):
        return url, False

    query = parse_qs(parsed_url.query)
    if parsed_url.path.rstrip('/') == '/watch':
        video_ids = query.get('v')
        selected_video_id = video_ids[0].strip() if video_ids else None
    elif hostname in {'youtu.be', 'www.youtu.be'}:
        selected_video_id = parsed_url.path.strip('/') or None
    else:
        selected_video_id = None
    if not selected_video_id:
        return url, False

    filtered_query = [
        (name, value)
        for name, value in query_items
        if name not in {'list', 'start_radio'}
    ]
    normalized_url = urlunsplit(
        parsed_url._replace(query=urlencode(filtered_query))
    )
    return normalized_url, True


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
    radio_url, is_radio = normalize_youtube_radio_url(url)
    if is_radio:
        return radio_url
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
    radio_url, is_radio = normalize_youtube_radio_url(url)
    if is_radio:
        return extract_single_track(radio_url, volume, channel), 0, False

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


def mark_first_frame(future):
    if not future.done():
        future.set_result(None)


def parse_duration(info):
    try:
        duration = float(info.get('duration'))
    except (TypeError, ValueError):
        return None
    return duration if duration > 0 else None


def format_ffmpeg_diagnostics(buffer):
    diagnostics = buffer.getvalue().decode(errors='replace').strip()
    if not diagnostics:
        return ''
    return ' '.join(diagnostics[-2000:].split())


def log_playback_attempt(track, attempt, result):
    print(
        'Playback attempt: '
        f'title={track.title!r}, url={track.url!r}, attempt={attempt}, '
        f'expected_seconds={result.expected_duration!r}, '
        f'decoded_seconds={result.decoded_seconds:.2f}, '
        f'error={result.error!r}, ffmpeg={result.ffmpeg_diagnostics!r}'
    )


async def play_track_attempt(voice_client, track, announce):
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

    stderr = io.BytesIO()
    player = discord.FFmpegPCMAudio(
        media_url,
        executable=imageio_ffmpeg.get_ffmpeg_exe(),
        before_options=before_options,
        stderr=stderr,
    )
    source = discord.PCMVolumeTransformer(player, track.volume)
    loop = asyncio.get_running_loop()
    finished = loop.create_future()
    first_frame = loop.create_future()
    tracked_source = PlaybackSource(source, loop, first_frame)
    try:
        voice_client.play(
            tracked_source,
            after=lambda error: loop.call_soon_threadsafe(
                finish_playback, finished, error
            ),
        )
    except Exception:
        tracked_source.cleanup()
        raise

    done, _ = await asyncio.wait(
        (first_frame, finished), return_when=asyncio.FIRST_COMPLETED
    )
    started = first_frame in done or first_frame.done()
    if started and announce:
        try:
            await track.channel.send(
                f"Now playing: {info.get('title') or track.title}"
            )
        except Exception as error:
            print(f'Now-playing message failed: {track.title!r}: {error!r}')

    error = await finished
    if not first_frame.done():
        first_frame.cancel()
    return PlaybackAttemptResult(
        started=tracked_source.frame_count > 0,
        decoded_seconds=tracked_source.decoded_seconds,
        expected_duration=parse_duration(info),
        error=error,
        ffmpeg_diagnostics=format_ffmpeg_diagnostics(stderr),
    )


async def play_track(voice_client, track):
    guild_id = voice_client.guild.id
    session = PlaybackSession(track)
    active_playbacks[guild_id] = session
    announced = False
    try:
        for attempt in range(1, 3):
            if session.skip_requested:
                return
            try:
                result = await play_track_attempt(
                    voice_client, track, announce=not announced
                )
            except Exception as error:
                result = PlaybackAttemptResult(False, 0, None, error)

            announced = announced or result.started
            log_playback_attempt(track, attempt, result)

            if session.skip_requested:
                return
            if not result.failed:
                return
            if result.startup_failure and attempt == 1:
                await track.channel.send(
                    f"Playback interrupted: {track.title}. Retrying..."
                )
                continue
            if result.startup_failure:
                raise PlaybackFailure(f'{track.title} failed during startup')

            await track.channel.send(f"Playback ended early: {track.title}.")
            return
    finally:
        if active_playbacks.get(guild_id) is session:
            active_playbacks.pop(guild_id, None)


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
            except PlaybackFailure as error:
                print(f'Playback skipped: {track.title!r}: {error!r}')
                await track.channel.send(
                    f"Skipped: {track.title} (playback failed)"
                )
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
        session = active_playbacks.get(ctx.guild.id)
        if session:
            session.skip_requested = True
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