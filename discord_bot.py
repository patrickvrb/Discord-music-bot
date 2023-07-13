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

    async with ctx.typing():
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            url2 = info['formats'][0]['url']
            player = discord.FFmpegPCMAudio(url2, executable='ffmpeg', pipe=False,
                                            before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5')
            volume_adjusted = discord.PCMVolumeTransformer(player, volume)
            voice_client.play(volume_adjusted)

        global current_song
        current_song = info['title']
        await ctx.send(f"Now playing: {current_song}")

        while voice_client.is_playing():
            await asyncio.sleep(1)
        await voice_client.disconnect()


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
