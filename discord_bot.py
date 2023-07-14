import discord
from discord.ext import commands
import youtube_dl
import asyncio

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

song_queue = []
current_song = None


@bot.command(aliases=['p'])
async def play(ctx, url, volume='0.1'):
    channel = ctx.message.author.voice.channel
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    if voice_client and voice_client.is_playing():
        song_queue.append(url)  # Add the URL to the song queue
        await ctx.send("Added to the song queue.")
    else:
        if voice_client and voice_client.is_connected():
            await voice_client.move_to(channel)
        else:
            voice_client = await channel.connect()

        await voice_client.guild.change_voice_state(channel=channel, self_deaf=True)
        await play_song(ctx, voice_client, url, float(volume))


async def play_next(ctx, voice_client, volume):
    if len(song_queue) > 0:
        url = song_queue.pop(0)  # Remove and get the first URL from the queue
        await play_song(ctx, voice_client, url, volume)
    else:
        await disconnect_voice_client(ctx.guild)


async def play_song(ctx, voice_client, url, volume):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'opus',
                'preferredquality': '192',
            }
        ],
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0'
    }

    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        url2 = info['formats'][0]['url']
        player = discord.FFmpegPCMAudio(url2, executable='ffmpeg', pipe=False,
                                        before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5')
        volume_adjusted = discord.PCMVolumeTransformer(player, volume)
        voice_client.play(volume_adjusted, after=lambda e: asyncio.run_coroutine_threadsafe(
            play_next(ctx, voice_client, volume), bot.loop))

        global current_song
        current_song = info['title']
        await ctx.send(f"Now playing: {current_song}")


@bot.command()
async def pause(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send("Paused the music.")


@bot.command()
async def skip(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    if voice_client and voice_client.is_connected():
        voice_client.stop()
        await ctx.send("Skipping...")


@bot.command()
async def resume(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send("Resumed the music.")


@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}")


async def disconnect_voice_client(guild):
    voice_client = discord.utils.get(bot.voice_clients, guild=guild)
    if voice_client and voice_client.is_connected():
        await voice_client.disconnect()

bot.run('MTEyODE2NDA5MjUwNzYwNzE0MA.GZz9Z8.THZKAYGygQsTZ1cHEYvyg8uF8GUislQn-satqk')
