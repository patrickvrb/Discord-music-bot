import asyncio
import os
import unittest
from collections import deque
from unittest.mock import AsyncMock, patch

os.environ.setdefault('DISCORD_BOT_KEY', 'test-token')

import discord_bot


class NormalizeYouTubePlaylistUrlTests(unittest.TestCase):
    def test_normalizes_combined_watch_and_playlist_url(self):
        url = 'https://www.youtube.com/watch?v=video-id&list=playlist-id'

        self.assertEqual(
            discord_bot.normalize_youtube_playlist_url(url),
            'https://www.youtube.com/playlist?list=playlist-id',
        )
        self.assertEqual(
            discord_bot.parse_youtube_playlist_url(url),
            ('https://www.youtube.com/playlist?list=playlist-id', 'video-id'),
        )

    def test_normalizes_supported_youtube_hosts(self):
        urls = (
            'https://youtube.com/watch?v=video-id&list=playlist-id',
            'https://m.youtube.com/watch?v=video-id&list=playlist-id',
            'https://music.youtube.com/watch?v=video-id&list=playlist-id',
            'https://youtu.be/video-id?list=playlist-id',
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(
                    discord_bot.normalize_youtube_playlist_url(url),
                    'https://www.youtube.com/playlist?list=playlist-id',
                )

    def test_leaves_canonical_playlist_url_unchanged(self):
        url = 'https://www.youtube.com/playlist?list=playlist-id'

        self.assertEqual(discord_bot.normalize_youtube_playlist_url(url), url)

    def test_leaves_plain_video_url_unchanged(self):
        url = 'https://www.youtube.com/watch?v=video-id'

        self.assertEqual(discord_bot.normalize_youtube_playlist_url(url), url)

    def test_leaves_empty_playlist_parameter_unchanged(self):
        url = 'https://www.youtube.com/watch?v=video-id&list='

        self.assertEqual(discord_bot.normalize_youtube_playlist_url(url), url)


class ExtractTracksTests(unittest.TestCase):
    @patch.object(discord_bot.youtube_dl, 'YoutubeDL')
    def test_combined_url_extracts_playlist_entries_in_order(self, youtube_dl):
        youtube_dl.return_value.extract_info.return_value = {
            '_type': 'playlist',
            'title': 'Test playlist',
            'entries': [
                {
                    'id': 'before-id',
                    'title': 'Earlier track',
                    'webpage_url': 'https://www.youtube.com/watch?v=before-id',
                },
                {
                    'id': 'first-id',
                    'title': 'First track',
                    'webpage_url': 'https://www.youtube.com/watch?v=first-id',
                },
                {
                    'id': 'second-id',
                    'title': 'Second track',
                    'url': 'second-id',
                },
            ],
        }
        url = 'https://www.youtube.com/watch?v=first-id&list=playlist-id'
        channel = object()

        tracks, unavailable, is_playlist = discord_bot.extract_tracks(
            url, 0.75, channel
        )

        youtube_dl.return_value.extract_info.assert_called_once_with(
            'https://www.youtube.com/playlist?list=playlist-id', download=False
        )
        self.assertTrue(is_playlist)
        self.assertEqual(unavailable, 0)
        self.assertEqual(
            [(track.title, track.url) for track in tracks],
            [
                ('First track', 'https://www.youtube.com/watch?v=first-id'),
                ('Second track', 'https://www.youtube.com/watch?v=second-id'),
            ],
        )
        self.assertTrue(all(track.volume == 0.75 for track in tracks))
        self.assertTrue(all(track.channel is channel for track in tracks))


    @patch.object(discord_bot.youtube_dl, 'YoutubeDL')
    def test_missing_selected_video_falls_back_to_single_video(self, youtube_dl):
        youtube_dl.return_value.extract_info.side_effect = [
            {
                '_type': 'playlist',
                'entries': [
                    {
                        'id': 'other-id',
                        'title': 'Other track',
                        'webpage_url': 'https://www.youtube.com/watch?v=other-id',
                    }
                ],
            },
            {
                'id': 'selected-id',
                'title': 'Selected track',
                'webpage_url': 'https://www.youtube.com/watch?v=selected-id',
            },
        ]
        url = 'https://www.youtube.com/watch?v=selected-id&list=playlist-id'

        tracks, unavailable, is_playlist = discord_bot.extract_tracks(
            url, 0.5, object()
        )

        self.assertEqual([track.title for track in tracks], ['Selected track'])
        self.assertEqual(unavailable, 0)
        self.assertFalse(is_playlist)
        self.assertTrue(
            youtube_dl.call_args_list[1].args[0]['noplaylist']
        )


class PlayerWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_plays_every_queued_track_before_disconnect(self):
        guild = type('Guild', (), {'id': 8675309})()
        voice_client = type(
            'VoiceClient',
            (),
            {
                'connected': True,
                'is_connected': lambda self: self.connected,
            },
        )()
        tracks = [object(), object()]
        discord_bot.song_queues[guild.id] = deque(tracks)

        async def disconnect(_guild):
            voice_client.connected = False

        try:
            with (
                patch.object(
                    discord_bot, 'play_track', new=AsyncMock()
                ) as play_track,
                patch.object(
                    discord_bot,
                    'disconnect_voice_client',
                    new=AsyncMock(side_effect=disconnect),
                ) as disconnect_voice_client,
                patch.object(discord_bot.asyncio, 'sleep', new=AsyncMock()),
            ):
                await discord_bot.player_worker(guild, voice_client)

            self.assertEqual(
                [call.args for call in play_track.await_args_list],
                [(voice_client, tracks[0]), (voice_client, tracks[1])],
            )
            disconnect_voice_client.assert_awaited_once_with(guild)
            self.assertNotIn(guild.id, discord_bot.song_queues)
        finally:
            discord_bot.song_queues.pop(guild.id, None)
            discord_bot.queue_locks.pop(guild.id, None)
            discord_bot.player_tasks.pop(guild.id, None)


    async def test_skip_advances_to_next_queued_track_before_disconnect(self):
        guild = type('Guild', (), {'id': 424242})()
        first_track = object()
        second_track = object()
        first_started = asyncio.Event()
        first_stopped = asyncio.Event()

        class VoiceClient:
            connected = True

            def is_connected(self):
                return self.connected

            def stop(self):
                first_stopped.set()

        voice_client = VoiceClient()
        ctx = type(
            'Context',
            (),
            {'guild': guild, 'send': AsyncMock()},
        )()
        discord_bot.song_queues[guild.id] = deque([first_track, second_track])

        async def play_track(_voice_client, track):
            if track is first_track:
                first_started.set()
                await first_stopped.wait()

        async def disconnect(_guild):
            voice_client.connected = False

        try:
            with (
                patch.object(
                    discord_bot, 'play_track', new=AsyncMock(side_effect=play_track)
                ) as mocked_play_track,
                patch.object(
                    discord_bot,
                    'disconnect_voice_client',
                    new=AsyncMock(side_effect=disconnect),
                ) as disconnect_voice_client,
                patch.object(discord_bot.asyncio, 'sleep', new=AsyncMock()),
                patch.object(
                    discord_bot.discord.utils,
                    'get',
                    return_value=voice_client,
                ),
            ):
                worker = asyncio.create_task(
                    discord_bot.player_worker(guild, voice_client)
                )
                await first_started.wait()
                await discord_bot.skip.callback(ctx)
                await worker

            self.assertEqual(
                [call.args[1] for call in mocked_play_track.await_args_list],
                [first_track, second_track],
            )
            ctx.send.assert_awaited_once_with('Skipping...')
            disconnect_voice_client.assert_awaited_once_with(guild)
        finally:
            discord_bot.song_queues.pop(guild.id, None)
            discord_bot.queue_locks.pop(guild.id, None)
            discord_bot.player_tasks.pop(guild.id, None)


if __name__ == '__main__':
    unittest.main()
