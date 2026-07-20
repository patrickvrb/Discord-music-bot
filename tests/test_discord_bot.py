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


    def test_strips_generated_radio_context_from_video_url(self):
        url = (
            'https://www.youtube.com/watch?v=_Z5-P9v3F8w'
            '&list=RD_Z5-P9v3F8w&start_radio=1'
        )

        self.assertEqual(
            discord_bot.normalize_youtube_playlist_url(url),
            'https://www.youtube.com/watch?v=_Z5-P9v3F8w',
        )

    def test_strips_radio_context_from_supported_video_urls(self):
        urls = (
            (
                'https://m.youtube.com/watch?v=video-id&list=RDvideo-id'
                '&start_radio=1',
                'https://m.youtube.com/watch?v=video-id',
            ),
            (
                'https://music.youtube.com/watch?v=video-id&list=RDvideo-id'
                '&start_radio=1',
                'https://music.youtube.com/watch?v=video-id',
            ),
            (
                'https://youtu.be/video-id?list=RDvideo-id&start_radio=1',
                'https://youtu.be/video-id',
            ),
        )

        for url, expected in urls:
            with self.subTest(url=url):
                self.assertEqual(
                    discord_bot.normalize_youtube_playlist_url(url), expected
                )


class ExtractTracksTests(unittest.TestCase):
    @patch.object(discord_bot.youtube_dl, 'YoutubeDL')
    def test_generated_radio_url_extracts_only_selected_video(self, youtube_dl):
        youtube_dl.return_value.extract_info.return_value = {
            'id': '_Z5-P9v3F8w',
            'title': 'Selected track',
            'webpage_url': 'https://www.youtube.com/watch?v=_Z5-P9v3F8w',
        }
        url = (
            'https://www.youtube.com/watch?v=_Z5-P9v3F8w'
            '&list=RD_Z5-P9v3F8w&start_radio=1'
        )

        tracks, unavailable, is_playlist = discord_bot.extract_tracks(
            url, 0.5, object()
        )

        youtube_dl.return_value.extract_info.assert_called_once_with(
            'https://www.youtube.com/watch?v=_Z5-P9v3F8w', download=False
        )
        self.assertTrue(youtube_dl.call_args.args[0]['noplaylist'])
        self.assertEqual([track.title for track in tracks], ['Selected track'])
        self.assertEqual(unavailable, 0)
        self.assertFalse(is_playlist)

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


class PlaybackAttemptResultTests(unittest.TestCase):
    def test_classifies_startup_and_late_failures(self):
        startup = discord_bot.PlaybackAttemptResult(False, 0, 154, None)
        late = discord_bot.PlaybackAttemptResult(True, 10, 154, None)

        self.assertTrue(startup.startup_failure)
        self.assertTrue(late.failed)
        self.assertFalse(late.startup_failure)

    def test_accepts_a_completed_short_track(self):
        result = discord_bot.PlaybackAttemptResult(True, 3, 3, None)

        self.assertFalse(result.failed)


class PlayTrackAttemptTests(unittest.IsolatedAsyncioTestCase):
    async def run_attempt(self, chunks):
        class Source(discord_bot.discord.AudioSource):
            def __init__(self):
                self.chunks = deque(chunks)
                self.cleaned = False

            def read(self):
                return self.chunks.popleft() if self.chunks else b''

            def is_opus(self):
                return False

            def cleanup(self):
                self.cleaned = True

        class VoiceClient:
            def play(self, source, after):
                while source.read():
                    pass
                after(None)
                source.cleanup()

        channel = AsyncMock()
        track = discord_bot.Track('track-url', 'Queued title', 0.5, channel)
        source = Source()
        info = {
            'url': 'media-url',
            'title': 'Extracted title',
            'duration': 154,
        }
        with (
            patch.object(discord_bot, 'extract_audio', return_value=info),
            patch.object(
                discord_bot.discord, 'FFmpegPCMAudio', return_value=source
            ),
            patch.object(
                discord_bot.discord,
                'PCMVolumeTransformer',
                side_effect=lambda original, _volume: original,
            ),
            patch.object(
                discord_bot.imageio_ffmpeg,
                'get_ffmpeg_exe',
                return_value='ffmpeg',
            ),
        ):
            result = await discord_bot.play_track_attempt(
                VoiceClient(), track, announce=True
            )
        return result, channel, source

    async def test_does_not_announce_before_the_first_audio_frame(self):
        result, channel, source = await self.run_attempt([])

        self.assertFalse(result.started)
        self.assertEqual(result.decoded_seconds, 0)
        channel.send.assert_not_awaited()
        self.assertTrue(source.cleaned)

    async def test_announces_after_the_first_audio_frame(self):
        result, channel, source = await self.run_attempt([b'pcm-frame'])

        self.assertTrue(result.started)
        self.assertEqual(result.decoded_seconds, 0.02)
        channel.send.assert_awaited_once_with('Now playing: Extracted title')
        self.assertTrue(source.cleaned)


class PlayTrackRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def make_playback(self, guild_id):
        guild = type('Guild', (), {'id': guild_id})()
        voice_client = type('VoiceClient', (), {'guild': guild})()
        channel = AsyncMock()
        track = discord_bot.Track('track-url', 'Flores', 0.5, channel)
        return guild, voice_client, channel, track

    async def test_retries_a_startup_failure_once(self):
        guild, voice_client, channel, track = self.make_playback(1001)
        results = [
            discord_bot.PlaybackAttemptResult(False, 0, 154, None),
            discord_bot.PlaybackAttemptResult(True, 154, 154, None),
        ]
        try:
            with (
                patch.object(
                    discord_bot,
                    'play_track_attempt',
                    new=AsyncMock(side_effect=results),
                ) as attempt,
                patch.object(discord_bot, 'log_playback_attempt'),
            ):
                await discord_bot.play_track(voice_client, track)

            self.assertEqual(attempt.await_count, 2)
            self.assertEqual(
                [call.kwargs['announce'] for call in attempt.await_args_list],
                [True, True],
            )
            channel.send.assert_awaited_once_with(
                'Playback interrupted: Flores. Retrying...'
            )
        finally:
            discord_bot.active_playbacks.pop(guild.id, None)

    async def test_does_not_repeat_now_playing_after_a_partial_start(self):
        guild, voice_client, _channel, track = self.make_playback(1002)
        results = [
            discord_bot.PlaybackAttemptResult(True, 1, 154, None),
            discord_bot.PlaybackAttemptResult(True, 154, 154, None),
        ]
        try:
            with (
                patch.object(
                    discord_bot,
                    'play_track_attempt',
                    new=AsyncMock(side_effect=results),
                ) as attempt,
                patch.object(discord_bot, 'log_playback_attempt'),
            ):
                await discord_bot.play_track(voice_client, track)

            self.assertEqual(
                [call.kwargs['announce'] for call in attempt.await_args_list],
                [True, False],
            )
        finally:
            discord_bot.active_playbacks.pop(guild.id, None)

    async def test_raises_after_two_startup_failures(self):
        guild, voice_client, channel, track = self.make_playback(1003)
        failure = discord_bot.PlaybackAttemptResult(False, 0, 154, None)
        try:
            with (
                patch.object(
                    discord_bot,
                    'play_track_attempt',
                    new=AsyncMock(side_effect=[failure, failure]),
                ) as attempt,
                patch.object(discord_bot, 'log_playback_attempt'),
            ):
                with self.assertRaises(discord_bot.PlaybackFailure):
                    await discord_bot.play_track(voice_client, track)

            self.assertEqual(attempt.await_count, 2)
            channel.send.assert_awaited_once_with(
                'Playback interrupted: Flores. Retrying...'
            )
            self.assertNotIn(guild.id, discord_bot.active_playbacks)
        finally:
            discord_bot.active_playbacks.pop(guild.id, None)

    async def test_reports_late_failure_without_retrying(self):
        guild, voice_client, channel, track = self.make_playback(1004)
        late_failure = discord_bot.PlaybackAttemptResult(True, 10, 154, None)
        try:
            with (
                patch.object(
                    discord_bot,
                    'play_track_attempt',
                    new=AsyncMock(return_value=late_failure),
                ) as attempt,
                patch.object(discord_bot, 'log_playback_attempt'),
            ):
                await discord_bot.play_track(voice_client, track)

            attempt.assert_awaited_once()
            channel.send.assert_awaited_once_with(
                'Playback ended early: Flores.'
            )
        finally:
            discord_bot.active_playbacks.pop(guild.id, None)

    async def test_skip_during_a_failed_attempt_prevents_retry(self):
        guild, voice_client, channel, track = self.make_playback(1005)
        failure = discord_bot.PlaybackAttemptResult(False, 0, 154, None)

        async def fail_after_skip(*_args, **_kwargs):
            discord_bot.active_playbacks[guild.id].skip_requested = True
            return failure

        try:
            with (
                patch.object(
                    discord_bot,
                    'play_track_attempt',
                    new=AsyncMock(side_effect=fail_after_skip),
                ) as attempt,
                patch.object(discord_bot, 'log_playback_attempt'),
            ):
                await discord_bot.play_track(voice_client, track)

            attempt.assert_awaited_once()
            channel.send.assert_not_awaited()
        finally:
            discord_bot.active_playbacks.pop(guild.id, None)

    async def test_manual_skip_marks_the_active_session(self):
        guild, voice_client, _channel, track = self.make_playback(1005)
        voice_client.is_connected = lambda: True
        voice_client.stop = unittest.mock.Mock()
        ctx = type(
            'Context',
            (),
            {'guild': guild, 'send': AsyncMock()},
        )()
        session = discord_bot.PlaybackSession(track)
        discord_bot.active_playbacks[guild.id] = session
        try:
            with patch.object(
                discord_bot.discord.utils, 'get', return_value=voice_client
            ):
                await discord_bot.skip.callback(ctx)

            self.assertTrue(session.skip_requested)
            voice_client.stop.assert_called_once_with()
            ctx.send.assert_awaited_once_with('Skipping...')
        finally:
            discord_bot.active_playbacks.pop(guild.id, None)


class PlayerWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_final_skip_after_playback_retries_fail(self):
        guild = type('Guild', (), {'id': 6060})()
        voice_client = type(
            'VoiceClient',
            (),
            {
                'connected': True,
                'is_connected': lambda self: self.connected,
            },
        )()
        channel = AsyncMock()
        track = discord_bot.Track('track-url', 'Flores', 0.5, channel)
        discord_bot.song_queues[guild.id] = deque([track])

        async def disconnect(_guild):
            voice_client.connected = False

        try:
            with (
                patch.object(
                    discord_bot,
                    'play_track',
                    new=AsyncMock(
                        side_effect=discord_bot.PlaybackFailure('failed')
                    ),
                ),
                patch.object(
                    discord_bot,
                    'disconnect_voice_client',
                    new=AsyncMock(side_effect=disconnect),
                ),
                patch.object(discord_bot.asyncio, 'sleep', new=AsyncMock()),
            ):
                await discord_bot.player_worker(guild, voice_client)

            channel.send.assert_awaited_once_with(
                'Skipped: Flores (playback failed)'
            )
        finally:
            discord_bot.song_queues.pop(guild.id, None)
            discord_bot.queue_locks.pop(guild.id, None)
            discord_bot.player_tasks.pop(guild.id, None)

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
