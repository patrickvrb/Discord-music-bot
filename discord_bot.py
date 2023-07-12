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
async def play(ctx, url, volume=0.1):
    channel = ctx.message.author.voice.channel
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    if voice_client and voice_client.is_playing():
        # Queue the song if bot is already playing
        await ctx.send("Added to queue.")
        song_queue.append(url)
    else:
        voice_client = await channel.connect()
        await voice_client.guild.change_voice_state(channel=channel, self_deaf=True)
        await play_song(ctx, voice_client, url, volume)


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

    async def disconnect_callback(error):
        if error:
            print(f"Error during playback: {error}")
        await voice_client.disconnect()

    async def load_next_song():
        if len(song_queue) > 0:  # Check if there are songs in the queue
            next_song = song_queue.pop(0)  # Updated variable name
            await play_song(ctx, voice_client, next_song, volume)
        else:
            await voice_client.disconnect()

    async with ctx.typing():
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            url2 = info['formats'][0]['url']
            player = discord.FFmpegPCMAudio(url2, executable='ffmpeg', pipe=False,
                                            before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5')
            volume_adjusted = discord.PCMVolumeTransformer(player, volume)
            volume_adjusted.after = disconnect_callback
            voice_client.play(volume_adjusted)

        global current_song
        current_song = info['title']
        await ctx.send(f"Now playing: {current_song}")

        while voice_client.is_playing():
            await asyncio.sleep(2)
            if len(song_queue) > 0 and not voice_client.is_playing():
                await load_next_song()

    # Start loading the next song if it's already available in the queue
    if len(song_queue) > 0 and not voice_client.is_playing():
        await load_next_song()


@bot.command(aliases=['s'])
async def skip(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await ctx.send("Skipped the current song.")


@bot.command(aliases=['q'])
async def queue(ctx):
    if len(song_queue) > 0:  # Updated variable name
        queue_list = '\n'.join(song_queue)  # Updated variable name
        await ctx.send(f"Queue:\n{queue_list}")
    else:
        await ctx.send("The queue is empty.")


@bot.command()
async def pause(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send("Paused the music.")


@bot.command()
async def resume(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send("Resumed the music.")


@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}")

bot.run('MTEyODE2NDA5MjUwNzYwNzE0MA.GZz9Z8.THZKAYGygQsTZ1cHEYvyg8uF8GUislQn-satqk')
