import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from backend import main


class BilibiliAudioCacheTests(unittest.TestCase):
    def test_cache_lookup_removes_expired_audio_and_stale_partial_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            audio_path = cache_dir / "BVEXPIRED.m4a"
            partial_path = cache_dir / "BVFAILED.m4s.part"
            audio_path.write_bytes(b"audio")
            partial_path.write_bytes(b"partial")

            old_timestamp = time.time() - 7200
            os.utime(audio_path, (old_timestamp, old_timestamp))
            os.utime(partial_path, (old_timestamp, old_timestamp))

            with (
                patch.object(main, "BILIBILI_AUDIO_DIR", cache_dir),
                patch.object(main, "BILIBILI_AUDIO_CACHE_TTL_SECONDS", 3600, create=True),
                patch.object(main, "BILIBILI_AUDIO_CACHE_MAX_BYTES", 0, create=True),
                patch.object(main, "BILIBILI_AUDIO_PARTIAL_TTL_SECONDS", 3600, create=True),
            ):
                cached = main._find_cached_bilibili_audio("BVEXPIRED")

            self.assertEqual("", cached)
            self.assertFalse(audio_path.exists())
            self.assertFalse(partial_path.exists())

    def test_cache_lookup_evicts_oldest_audio_when_size_limit_is_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            oldest_path = cache_dir / "BVOLDEST.m4a"
            newest_path = cache_dir / "BVNEWEST.m4a"
            oldest_path.write_bytes(b"a" * 700_000)
            newest_path.write_bytes(b"b" * 700_000)

            now = time.time()
            os.utime(oldest_path, (now - 120, now - 120))
            os.utime(newest_path, (now - 60, now - 60))

            with (
                patch.object(main, "BILIBILI_AUDIO_DIR", cache_dir),
                patch.object(main, "BILIBILI_AUDIO_CACHE_TTL_SECONDS", 0, create=True),
                patch.object(main, "BILIBILI_AUDIO_CACHE_MAX_BYTES", 1_000_000, create=True),
            ):
                main._find_cached_bilibili_audio("BVMISSING")

            self.assertFalse(oldest_path.exists())
            self.assertTrue(newest_path.exists())

    def test_cache_lookup_does_not_evict_the_requested_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            requested_path = cache_dir / "BVREQUESTED.m4a"
            other_path = cache_dir / "BVOTHER.m4a"
            requested_path.write_bytes(b"a" * 700_000)
            other_path.write_bytes(b"b" * 700_000)

            now = time.time()
            os.utime(requested_path, (now - 120, now - 120))
            os.utime(other_path, (now - 60, now - 60))

            with (
                patch.object(main, "BILIBILI_AUDIO_DIR", cache_dir),
                patch.object(main, "BILIBILI_AUDIO_CACHE_TTL_SECONDS", 0),
                patch.object(main, "BILIBILI_AUDIO_CACHE_MAX_BYTES", 1_000_000),
            ):
                cached = main._find_cached_bilibili_audio("BVREQUESTED")

            self.assertEqual(str(requested_path.resolve()), cached)
            self.assertTrue(requested_path.exists())
            self.assertFalse(other_path.exists())


class AdminCookieStatusTests(unittest.TestCase):
    def test_encrypted_empty_cookie_is_not_reported_as_configured(self) -> None:
        session = unittest.mock.Mock()
        session.get.return_value = unittest.mock.Mock(value="encrypted-empty-cookie")

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "decrypt_text", return_value=""),
        ):
            result = main.admin_status(object(), session)

        self.assertFalse(result["admin_cookie_set"])

    def test_encrypted_empty_cookie_is_treated_as_not_configured(self) -> None:
        session = unittest.mock.Mock()
        session.get.return_value = unittest.mock.Mock(value="encrypted-empty-cookie")

        with patch.object(main, "decrypt_text", return_value=""):
            with self.assertRaises(HTTPException) as raised:
                main._get_admin_cookie(session)

        self.assertEqual(400, raised.exception.status_code)

    def test_metadata_only_cookie_is_treated_as_not_configured(self) -> None:
        session = unittest.mock.Mock()
        session.get.return_value = unittest.mock.Mock(value="encrypted-metadata-cookie")
        metadata_cookie = "NMTID=device-id; __csrf=csrf-token"

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "decrypt_text", return_value=metadata_cookie),
        ):
            status = main.admin_status(object(), session)
            with self.assertRaises(HTTPException) as raised:
                main._get_admin_cookie(session)

        self.assertFalse(status["admin_cookie_set"])
        self.assertEqual(400, raised.exception.status_code)

    def test_metadata_only_cookie_is_not_saved_manually(self) -> None:
        session = unittest.mock.Mock()
        metadata_cookie = "NMTID=device-id; __csrf=csrf-token"

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "_set_secret") as set_secret,
            self.assertRaises(HTTPException) as raised,
        ):
            main.admin_set_cookie(main.AdminCookieSetRequest(cookie=metadata_cookie), object(), session)

        self.assertEqual(400, raised.exception.status_code)
        set_secret.assert_not_called()


class NeteaseQrCookieTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_success_without_core_auth_cookie_is_rejected(self) -> None:
        qr_response = {
            "code": 803,
            "cookie": "NMTID=device-id; __csrf=csrf-token; MUSIC_SNS=",
        }

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "_set_secret") as set_secret,
            patch.object(main.netease, "qr_check", AsyncMock(return_value=qr_response)),
        ):
            result = await main.admin_qr_check("qr-key", object(), object())

        self.assertEqual(803, result["code"])
        self.assertFalse(result["admin_cookie_set"])
        set_secret.assert_not_called()

    async def test_qr_cookie_keeps_valid_auth_value_when_later_duplicate_is_empty(self) -> None:
        qr_response = {
            "code": 803,
            "cookie": (
                "MUSIC_U=valid-token; Path=/;;"
                "MUSIC_U=; Max-Age=0; Path=/; __csrf=csrf-token"
            ),
        }

        with (
            patch.object(main, "_require_admin_token"),
            patch.object(main, "_set_secret") as set_secret,
            patch.object(main.netease, "qr_check", AsyncMock(return_value=qr_response)),
        ):
            result = await main.admin_qr_check("qr-key", object(), object())

        self.assertTrue(result["admin_cookie_set"])
        set_secret.assert_called_once_with(
            unittest.mock.ANY,
            "netease_cookie",
            "MUSIC_U=valid-token; __csrf=csrf-token",
        )


class TsChatCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main._ts_playlist_results.clear()

    async def test_playlist_search_then_select_enqueues_netease_tracks(self) -> None:
        search_result = {
            "result": {
                "playlists": [
                    {
                        "id": 123,
                        "name": "测试歌单",
                        "creator": {"nickname": "创建者"},
                        "trackCount": 2,
                    }
                ]
            }
        }
        tracks = [
            {"id": 1, "name": "歌曲一", "ar": [{"name": "歌手一"}], "al": {"name": "专辑一"}},
            {"id": 2, "name": "歌曲二", "ar": [{"name": "歌手二"}], "al": {"name": "专辑二"}},
        ]

        with (
            patch.object(main.netease, "search", AsyncMock(return_value=search_result)) as search,
            patch.object(main.netease, "playlist_detail", AsyncMock(return_value={"playlist": {"name": "测试歌单"}})),
            patch.object(main, "_load_netease_playlist_tracks", AsyncMock(return_value=("测试歌单", tracks))),
            patch.object(main, "_enqueue_netease_song", AsyncMock(return_value=(1, False))) as enqueue,
            patch.object(main, "_get_admin_cookie_or_none", return_value="cookie"),
            patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_PLAYING"))),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Alice", "playlist 测试", invoker_unique_id="alice-uid")
            await main._handle_chat_command("Alice", "select 1", invoker_unique_id="alice-uid")

        search.assert_awaited_once_with(keywords="测试", limit=5, type_=1000)
        self.assertEqual(2, enqueue.await_count)
        self.assertEqual("1", enqueue.await_args_list[0].kwargs["song_id"])
        self.assertEqual("2", enqueue.await_args_list[1].kwargs["song_id"])
        self.assertIn("使用 select <编号>", notice.await_args_list[0].args[0])
        self.assertIn("已从歌单《测试歌单》加入 2 首歌曲", notice.await_args_list[1].args[0])

    async def test_play_without_argument_plays_first_queue_item(self) -> None:
        row = SimpleNamespace(id=42, title="队首歌曲", artist="歌手")
        session = unittest.mock.Mock()
        session.execute.return_value.scalars.return_value.first.return_value = row

        with (
            patch.object(main, "new_session", return_value=session),
            patch.object(main, "_play_queue_item_internal", AsyncMock(return_value=True)) as play_item,
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Bob", "play")

        session.close.assert_called_once_with()
        play_item.assert_awaited_once_with(42, requested_by="Bob")
        self.assertIn("已播放队列第一首: 队首歌曲 - 歌手", notice.await_args.args[0])

    async def test_random_and_order_commands_switch_shuffle_mode(self) -> None:
        with (
            patch.object(main, "_set_shuffle_enabled", AsyncMock(return_value={"ok": True})) as shuffle,
            patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_PLAYING"))),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Carol", "随机播放")
            await main._handle_chat_command("Carol", "顺序播放")

        self.assertEqual([True, False], [call.args[0] for call in shuffle.await_args_list])
        self.assertIn("已切换为随机播放", notice.await_args_list[0].args[0])
        self.assertIn("已切换为顺序播放", notice.await_args_list[1].args[0])

    async def test_clear_command_clears_queue_and_playback_state(self) -> None:
        count_result = unittest.mock.Mock()
        count_result.scalar.return_value = 3
        session = unittest.mock.Mock()
        session.execute.side_effect = [count_result, unittest.mock.Mock()]

        with (
            patch.object(main, "new_session", return_value=session),
            patch.object(main, "_shuffle_queue", [1, 2, 3]),
            patch.object(main, "_current_shuffle_index", 1),
            patch.object(main, "_invalidate_play_requests", AsyncMock()) as invalidate,
            patch.object(main, "_set_now_playing_queue_item", AsyncMock()) as clear_now_playing,
            patch.object(main, "_schedule_ts_description_update"),
            patch.object(main.voice, "stop", AsyncMock()) as stop,
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Dave", "清空")

            self.assertEqual([], main._shuffle_queue)
            self.assertEqual(-1, main._current_shuffle_index)

        session.commit.assert_called_once_with()
        session.close.assert_called_once_with()
        invalidate.assert_awaited_once_with()
        clear_now_playing.assert_awaited_once_with(None)
        stop.assert_awaited_once_with()
        self.assertIn("已清空播放队列（3 首）", notice.await_args.args[0])

    def test_playlist_results_are_isolated_by_unique_id_and_expire(self) -> None:
        alice_key = main._ts_playlist_result_key(invoker_unique_id="alice-uid", invoker_name="SameName")
        bob_key = main._ts_playlist_result_key(invoker_unique_id="bob-uid", invoker_name="SameName")
        playlists = [{"id": "123", "name": "测试", "creator": "", "track_count": "1"}]

        with patch.object(main.time, "monotonic", return_value=100.0):
            main._remember_ts_playlist_results(alice_key, playlists)

        with patch.object(main.time, "monotonic", return_value=101.0):
            self.assertEqual(playlists, main._get_ts_playlist_results(alice_key))
            self.assertEqual([], main._get_ts_playlist_results(bob_key))

        with patch.object(main.time, "monotonic", return_value=100.0 + main._TS_PLAYLIST_RESULTS_TTL_S):
            self.assertEqual([], main._get_ts_playlist_results(alice_key))

    def test_netease_search_metadata_accepts_artists_field(self) -> None:
        raw = {
            "result": {
                "songs": [
                    {
                        "id": 123,
                        "name": "歌曲",
                        "ar": [],
                        "artists": [{"name": "歌手一"}, {"name": "歌手二"}],
                    }
                ]
            }
        }

        self.assertEqual(("123", "歌曲", "歌手一, 歌手二"), main._extract_song_meta_from_search_first(raw))


class PlaybackCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeat_one_replays_finished_item_without_deleting_it(self) -> None:
        with (
            patch.object(main, "_repeat_mode", "one"),
            patch.object(main, "_take_now_playing_if_match", AsyncMock(return_value=7)),
            patch.object(main, "_play_queue_item_internal", AsyncMock(return_value=True)) as replay,
            patch.object(main, "_delete_queue_item", AsyncMock()) as delete_item,
            patch.object(main, "_auto_play_next_from_queue", AsyncMock()) as play_next,
        ):
            await main._handle_playback_finished("source")

        replay.assert_awaited_once_with(7, requested_by="auto")
        delete_item.assert_not_awaited()
        play_next.assert_not_awaited()

    async def test_normal_completion_deletes_item_and_plays_next(self) -> None:
        with (
            patch.object(main, "_repeat_mode", "none"),
            patch.object(main, "_take_now_playing_if_match", AsyncMock(return_value=8)),
            patch.object(main, "_play_queue_item_internal", AsyncMock()) as replay,
            patch.object(main, "_delete_queue_item", AsyncMock()) as delete_item,
            patch.object(main, "_auto_play_next_from_queue", AsyncMock()) as play_next,
        ):
            await main._handle_playback_finished("source")

        replay.assert_not_awaited()
        delete_item.assert_awaited_once_with(8)
        play_next.assert_awaited_once_with()


class VoiceStatusFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_unavailable_voice_service_returns_offline_status(self) -> None:
        with (
            patch.object(main.voice, "get_status", AsyncMock(side_effect=RuntimeError("offline"))),
            patch.object(main, "_current_queue_item_id", None),
        ):
            result = await main.voice_status()

        self.assertFalse(result["voice_connected"])
        self.assertEqual("idle", result["state"])
        self.assertEqual("", result["now_playing_title"])


class ChatSourceParsingTests(unittest.TestCase):
    def test_source_prefixes_and_aliases(self) -> None:
        self.assertEqual(("qqmusic", "稻香"), main._parse_chat_source("qq 稻香"))
        self.assertEqual(("qqmusic", "稻香"), main._parse_chat_source("QQ音乐:稻香"))
        self.assertEqual(("bilibili", "猫"), main._parse_chat_source("bili 猫"))
        self.assertEqual(("netease", "夜曲"), main._parse_chat_source("网易云 夜曲"))
        self.assertEqual(("netease", "周杰伦 稻香"), main._parse_chat_source("周杰伦 稻香"))

    def test_links_and_bv_ids_are_auto_detected(self) -> None:
        self.assertEqual(("bilibili", "BV1xx411c7mD"), main._parse_chat_source("BV1xx411c7mD"))
        self.assertEqual(
            ("bilibili", "https://www.bilibili.com/video/BV1xx411c7mD"),
            main._parse_chat_source("https://www.bilibili.com/video/BV1xx411c7mD"),
        )
        self.assertEqual(
            ("qqmusic", "https://y.qq.com/n/ryqq/songDetail/003OUlho2HcRHC"),
            main._parse_chat_source("https://y.qq.com/n/ryqq/songDetail/003OUlho2HcRHC"),
        )
        self.assertEqual("003OUlho2HcRHC", main._extract_query_qq_songmid("songmid=003OUlho2HcRHC"))
        self.assertEqual("", main._extract_query_qq_songmid("稻香"))

    def test_scrolling_nickname_format_and_truncation(self) -> None:
        text = main._format_ts_scrolling_nickname("歌名", "这是一句很长的歌词内容用来测试截断行为", "歌手")
        self.assertTrue(text.startswith("♪ 歌名 - "))
        self.assertLessEqual(len(text), main._TS_NICKNAME_MAX_CHARS)
        self.assertEqual("♪ 歌名 - 歌手", main._format_ts_scrolling_nickname("歌名", "", "歌手"))

    def test_long_title_still_shows_lyric(self) -> None:
        """B站长标题不能把 30 字符预算吃光，否则昵称永远不会滚动。"""
        title = "【4K60帧】超清修复周杰伦/张惠妹《不该》MV！缘若尽了就不该重来"
        first = main._format_ts_scrolling_nickname(title, "假装我们还在一块")
        second = main._format_ts_scrolling_nickname(title, "缘若尽了就不该重来")
        self.assertNotEqual(first, second)
        self.assertIn("假装我们还在一块"[:4], first)
        for text in (first, second):
            self.assertLessEqual(len(text), main._TS_NICKNAME_MAX_CHARS)
            self.assertLessEqual(len(text.encode("utf-8")), main._TS_NICKNAME_MAX_BYTES)

    def test_nickname_respects_byte_limit(self) -> None:
        text = main._format_ts_scrolling_nickname("一" * 40, "二" * 40)
        self.assertLessEqual(len(text.encode("utf-8")), main._TS_NICKNAME_MAX_BYTES)

    def test_lyric_line_selection_uses_latest_timestamp(self) -> None:
        lines = [
            main.LyricLine(time=0.0, text="a"),
            main.LyricLine(time=5.0, text="b"),
            main.LyricLine(time=10.0, text="c"),
        ]
        self.assertEqual("", main._lyric_line_at(lines, -1.0))
        self.assertEqual("a", main._lyric_line_at(lines, 1.0))
        self.assertEqual("b", main._lyric_line_at(lines, 5.0))
        self.assertEqual("c", main._lyric_line_at(lines, 30.0))

    def test_avatar_resize_produces_bounded_image(self) -> None:
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (600, 600), (200, 30, 30)).save(buffer, format="PNG")
        data = main._resize_ts_avatar(buffer.getvalue())
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))
        self.assertLessEqual(len(data), main._TS_AVATAR_MAX_BYTES)
        resized = Image.open(BytesIO(data))
        self.assertLessEqual(max(resized.size), main._TS_AVATAR_MAX_EDGE)

    def test_avatar_resize_rejects_invalid_bytes(self) -> None:
        self.assertEqual(b"", main._resize_ts_avatar(b"not-an-image"))


class MultiSourceChatCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        main._ts_playlist_results.clear()

    async def test_search_qq_lists_song_mids(self) -> None:
        songs = [{"mid": "003OUlho2HcRHC", "name": "稻香", "singer": [{"name": "周杰伦"}]}]
        with (
            patch.object(main.qqmusic, "search_songs_simple", AsyncMock(return_value=songs)),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Eve", "搜索 qq 稻香")

        self.assertIn("003OUlho2HcRHC 稻香 - 周杰伦", notice.await_args.args[0])
        self.assertIn("QQ音乐搜索结果", notice.await_args.args[0])

    async def test_play_qq_enqueues_by_keyword(self) -> None:
        songs = [
            {
                "mid": "003OUlho2HcRHC",
                "name": "稻香",
                "singer": [{"name": "周杰伦"}],
                "album": {"mid": "album-mid", "name": "魔杰座"},
            }
        ]
        with (
            patch.object(main.qqmusic, "search_songs_simple", AsyncMock(return_value=songs)),
            patch.object(main, "_enqueue_qqmusic_song", AsyncMock(return_value=(7, False))) as enqueue,
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Eve", "播放 qq 稻香")

        enqueue.assert_awaited_once()
        self.assertEqual("003OUlho2HcRHC", enqueue.await_args.kwargs["song_mid"])
        self.assertTrue(enqueue.await_args.kwargs["play_now"])
        self.assertIn("立即播放: #7 稻香 - 周杰伦", notice.await_args.args[0])

    async def test_play_bilibili_bv_id_enqueues(self) -> None:
        metadata = {
            "title": "测试视频",
            "artist": "UP主",
            "album": "",
            "duration_ms": 60000,
            "artwork_url": "https://i0.hdslb.com/bfs/archive/x.jpg",
        }
        with (
            patch.object(main, "_extract_bilibili_video_info", AsyncMock(return_value=metadata)),
            patch.object(main, "_enqueue_bilibili_song", AsyncMock(return_value=(9, False))) as enqueue,
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Eve", "播放 bili BV1xx411c7mD")

        self.assertEqual("BV1xx411c7mD", enqueue.await_args.kwargs["video_id"])
        self.assertEqual("测试视频", enqueue.await_args.kwargs["title"])
        self.assertIn("立即播放: #9 测试视频 - UP主", notice.await_args.args[0])

    async def test_qq_playlist_search_then_select_enqueues_tracks(self) -> None:
        playlists = [
            {"dissid": "7011264340", "dissname": "测试歌单", "song_count": 2, "creator": {"name": "UP"}}
        ]
        tracks = [
            {
                "songmid": "003OUlho2HcRHC",
                "songname": "稻香",
                "singer": [{"name": "周杰伦"}],
                "albummid": "album-mid",
                "interval": 223,
            }
        ]
        with (
            patch.object(main.qqmusic, "search_playlists_simple", AsyncMock(return_value=playlists)),
            patch.object(main.qqmusic, "get_song_list_simple", AsyncMock(return_value=tracks)),
            patch.object(main, "_enqueue_qqmusic_song", AsyncMock(return_value=(1, False))) as enqueue,
            patch.object(main.voice, "get_status", AsyncMock(return_value=SimpleNamespace(state="STATE_PLAYING"))),
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
            patch.object(main, "_auto_play_next_from_queue", AsyncMock()),
        ):
            await main._handle_chat_command("Eve", "歌单 qq 测试", invoker_unique_id="eve-uid")
            await main._handle_chat_command("Eve", "选择 1", invoker_unique_id="eve-uid")

        self.assertIn("QQ音乐歌单搜索结果", notice.await_args_list[0].args[0])
        self.assertIn("已从QQ音乐歌单《测试歌单》加入 1 首歌曲", notice.await_args_list[1].args[0])
        self.assertEqual("003OUlho2HcRHC", enqueue.await_args.kwargs["song_mid"])
        self.assertEqual("稻香", enqueue.await_args.kwargs["title"])

    async def test_bilibili_playlist_is_rejected(self) -> None:
        with (
            patch.object(main.voice, "send_notice", AsyncMock()) as notice,
        ):
            await main._handle_chat_command("Eve", "歌单 bili 猫")

        self.assertIn("B站暂不支持歌单搜索", notice.await_args.args[0])


class TsPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_presence_uses_base_nickname_and_default_avatar(self) -> None:
        with (
            patch.object(main.settings, "ts3_nickname", "测试机器人"),
            patch.object(main, "_ts_last_nickname", "♪ 歌名 - 歌词"),
            patch.object(main, "_ts_presence_generation", 5),
            patch.object(main.voice, "set_client_nickname", AsyncMock()) as set_nickname,
            patch.object(main.voice, "set_client_avatar", AsyncMock()) as set_avatar,
        ):
            await main._restore_ts_nickname(5)
            await main._restore_ts_default_avatar(5)

        set_nickname.assert_awaited_once_with("测试机器人")
        set_avatar.assert_awaited_once_with(restore_default=True)

    async def test_stale_generation_is_ignored(self) -> None:
        with (
            patch.object(main, "_ts_presence_generation", 6),
            patch.object(main.voice, "set_client_avatar", AsyncMock()) as set_avatar,
            patch.object(main.voice, "set_client_nickname", AsyncMock()) as set_nickname,
        ):
            await main._restore_ts_default_avatar(5)
            await main._restore_ts_nickname(5)

        set_avatar.assert_not_awaited()
        set_nickname.assert_not_awaited()

    async def test_cover_avatar_uses_resized_bytes(self) -> None:
        with (
            patch.object(main, "_ts_presence_generation", 3),
            patch.object(main, "_fetch_ts_avatar_bytes", AsyncMock(return_value=b"img-bytes")),
            patch.object(main.settings, "voice_cover_avatar_enabled", True),
            patch.object(main.voice, "set_client_avatar", AsyncMock()) as set_avatar,
        ):
            await main._apply_ts_cover_avatar(3, "https://example.com/a.jpg")

        set_avatar.assert_awaited_once_with(b"img-bytes")

    async def test_cover_avatar_skipped_when_disabled(self) -> None:
        with (
            patch.object(main, "_ts_presence_generation", 4),
            patch.object(main, "_fetch_ts_avatar_bytes", AsyncMock(return_value=b"img-bytes")) as fetch,
            patch.object(main.settings, "voice_cover_avatar_enabled", False),
            patch.object(main.voice, "set_client_avatar", AsyncMock()) as set_avatar,
        ):
            await main._apply_ts_cover_avatar(4, "https://example.com/a.jpg")

        fetch.assert_not_awaited()
        set_avatar.assert_not_awaited()


class NewSettingDefinitionsTests(unittest.TestCase):
    def test_new_teamspeak_settings_are_registered(self) -> None:
        from backend.runtime_config import DEFINITION_BY_KEY

        for key in (
            "voice.ts3_cover_avatar",
            "voice.ts3_lyric_nickname",
            "voice.ts3_lyric_update_interval_ms",
            "voice.ts3_filetransfer_port",
        ):
            self.assertIn(key, DEFINITION_BY_KEY)
            self.assertEqual("teamspeak", DEFINITION_BY_KEY[key].group)

        self.assertTrue(DEFINITION_BY_KEY["voice.ts3_cover_avatar"].default)
        self.assertTrue(DEFINITION_BY_KEY["voice.ts3_lyric_nickname"].default)
        self.assertEqual(2000, DEFINITION_BY_KEY["voice.ts3_lyric_update_interval_ms"].default)
        self.assertEqual("ts3_nickname", DEFINITION_BY_KEY["voice.ts3_nickname"].backend_attr)


class BilibiliBrowserSubtitleTests(unittest.IsolatedAsyncioTestCase):
    def test_browser_subtitle_defaults_to_disabled(self) -> None:
        from backend.runtime_config import DEFINITION_BY_KEY

        definition = DEFINITION_BY_KEY["backend.bilibili_browser_subtitle"]
        self.assertFalse(definition.default)
        self.assertEqual("bilibili_browser_subtitle_enabled", definition.backend_attr)

    def test_qr_login_does_not_need_playwright(self) -> None:
        # 二维码登录走 HTTP 接口；只有 AI 字幕补抓才会启动 Chromium
        import inspect

        from backend import bilibili_auth

        source = inspect.getsource(bilibili_auth.start_bilibili_qr_login_session)
        self.assertNotIn("async_playwright", source)

    async def test_status_reports_playwright_disabled(self) -> None:
        with (
            patch.object(main.settings, "bilibili_browser_subtitle_enabled", False),
            patch.object(main, "is_playwright_runtime_available", AsyncMock(return_value=False)),
            patch.object(main, "is_playwright_available", return_value=True),
            patch.object(main, "new_session", return_value=unittest.mock.Mock()),
            patch.object(main, "_require_admin_token"),
        ):
            result = await main.admin_bilibili_status(request=unittest.mock.Mock(), session=unittest.mock.Mock())

        self.assertTrue(result["playwright_disabled"])
        self.assertFalse(result["playwright_available"])


class ChatTtsTests(unittest.IsolatedAsyncioTestCase):
    def test_tts_route_token_drops_extension(self) -> None:
        from backend.tts import tts_audio_path

        with_extension = tts_audio_path("3588b933d723067aad39680ed3703837.mp3")
        without_extension = tts_audio_path("3588b933d723067aad39680ed3703837")
        self.assertEqual(without_extension, with_extension)
        self.assertEqual("3588b933d723067aad39680ed3703837.mp3", with_extension.name)
        self.assertTrue(with_extension.parent.name == "tts")

    def test_command_detection_without_prefix(self) -> None:
        self.assertTrue(main._is_ts_chat_command("play 稻香"))
        self.assertTrue(main._is_ts_chat_command("播放 qq 稻香"))
        self.assertTrue(main._is_ts_chat_command("!stop"))
        self.assertTrue(main._is_ts_chat_command("点歌 bili BV1xx411c7mD"))
        self.assertFalse(main._is_ts_chat_command("今天天气不错"))
        self.assertFalse(main._is_ts_chat_command("这首歌不错"))

    def test_skip_rules(self) -> None:
        with (
            patch.object(main.settings, "chat_tts_enabled", True),
            patch.object(main.settings, "chat_tts_ignore_names", "TS3AudioBot"),
            patch.object(main.settings, "ts3_nickname", "qzh先生"),
        ):
            self.assertTrue(main._chat_tts_should_skip("Alice", "play 稻香"))
            self.assertTrue(main._chat_tts_should_skip("TS3AudioBot", "大家好啊"))
            self.assertTrue(main._chat_tts_should_skip("qzh先生", "大家好啊"))
            self.assertTrue(main._chat_tts_should_skip("♪ 歌名 - 歌词", "大家好啊"))
            self.assertTrue(main._chat_tts_should_skip("任意名字", "立即播放: #12 歌名 - 歌手"))
            self.assertTrue(main._chat_tts_should_skip("任意名字", "已加入队列: #12 歌名"))
            self.assertTrue(main._chat_tts_should_skip("Alice", "https://example.com/a"))
            self.assertTrue(main._chat_tts_should_skip("Alice", "🎵🎵"))
            self.assertFalse(main._chat_tts_should_skip("Alice", "大家好啊"))

    async def test_speaker_self_plays_through_our_voice_service(self) -> None:
        fake_file = SimpleNamespace(name="abc123.mp3")
        with (
            patch.object(main.settings, "chat_tts_enabled", True),
            patch.object(main.settings, "chat_tts_ignore_names", "TS3AudioBot"),
            patch.object(main.settings, "ts3_nickname", "qzh先生"),
            patch.object(main.settings, "chat_tts_cooldown_s", 0),
            patch.object(main.settings, "chat_tts_speaker", "SELF"),
            patch.object(main.settings, "chat_tts_public_base", "http://backend:8009"),
            patch.object(main.settings, "chat_tts_api_base", "http://ts3audiobot:58913"),
            patch.object(main.settings, "chat_tts_prefix", "{name}说："),
            patch.object(main, "_ts_tts_last_spoken_at", 0.0),
            patch.object(main, "synthesize_tts_file", AsyncMock(return_value=fake_file)),
            patch.object(main.voice, "play", AsyncMock()) as play,
        ):
            await main._maybe_speak_chat_message("Alice", "大家好啊")

        play.assert_awaited_once()
        self.assertEqual("http://backend:8009/tts/abc123.mp3", play.await_args.kwargs["source_url"])

    def test_disabled_skips_everything(self) -> None:
        with patch.object(main.settings, "chat_tts_enabled", False):
            self.assertTrue(main._chat_tts_should_skip("Alice", "大家好啊"))

    async def test_speak_calls_ts3audiobot_with_encoded_url(self) -> None:
        calls: list[str] = []

        class FakeResponse:
            status_code = 200
            text = "ok"

        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "FakeClient":
                return self

            async def __aexit__(self, *exc) -> None:
                return None

            async def get(self, url: str) -> FakeResponse:
                calls.append(url)
                return FakeResponse()

        fake_file = SimpleNamespace(name="abc123.mp3")

        with (
            patch.object(main.settings, "chat_tts_enabled", True),
            patch.object(main.settings, "chat_tts_ignore_names", "TS3AudioBot"),
            patch.object(main.settings, "ts3_nickname", "qzh先生"),
            patch.object(main.settings, "chat_tts_cooldown_s", 0),
            patch.object(main.settings, "chat_tts_max_chars", 60),
            patch.object(main.settings, "chat_tts_api_base", "http://ts3audiobot:58913"),
            patch.object(main.settings, "chat_tts_public_base", "http://backend:8009"),
            patch.object(main.settings, "chat_tts_prefix", "{name}说："),
            patch.object(main, "_ts_tts_last_spoken_at", 0.0),
            patch.object(main, "synthesize_tts_file", AsyncMock(return_value=fake_file)) as synth,
            patch.object(main.httpx, "AsyncClient", FakeClient),
        ):
            await main._maybe_speak_chat_message("Alice", "大家好啊")

        synth.assert_awaited_once()
        self.assertEqual("Alice说：大家好啊", synth.await_args.args[0])
        self.assertEqual(1, len(calls))
        self.assertIn("/api/bot/use/0/(/play/", calls[0])
        self.assertIn("http%3A%2F%2Fbackend%3A8009%2Ftts%2Fabc123.mp3", calls[0])

    async def test_speak_truncates_long_message(self) -> None:
        captured: list[str] = []

        with (
            patch.object(main.settings, "chat_tts_enabled", True),
            patch.object(main.settings, "chat_tts_ignore_names", "TS3AudioBot"),
            patch.object(main.settings, "ts3_nickname", "qzh先生"),
            patch.object(main.settings, "chat_tts_cooldown_s", 0),
            patch.object(main.settings, "chat_tts_max_chars", 5),
            patch.object(main.settings, "chat_tts_prefix", "{name}说："),
            patch.object(main, "_ts_tts_last_spoken_at", 0.0),
            patch.object(main, "synthesize_tts_file", AsyncMock(side_effect=lambda text: captured.append(text) or None)),
        ):
            await main._maybe_speak_chat_message("Bob", "一二三四五六七八九十")

        self.assertEqual(["Bob说：一二三四五…"], captured)

    async def test_cooldown_blocks_second_message(self) -> None:
        synth = AsyncMock(return_value=None)
        with (
            patch.object(main.settings, "chat_tts_enabled", True),
            patch.object(main.settings, "chat_tts_ignore_names", "TS3AudioBot"),
            patch.object(main.settings, "ts3_nickname", "qzh先生"),
            patch.object(main.settings, "chat_tts_cooldown_s", 60),
            patch.object(main, "_ts_tts_last_spoken_at", time.monotonic()),
            patch.object(main, "synthesize_tts_file", synth),
        ):
            await main._maybe_speak_chat_message("Alice", "大家好啊")

        synth.assert_not_awaited()


class NicknameDriftTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_keeps_retrying_after_send_failure(self) -> None:
        """voice-service 暂不可用时（如容器重启）worker 不能退出，恢复后要能继续更新昵称。"""
        item = SimpleNamespace(id=9, title="测试视频", artist="UP主")
        session = unittest.mock.Mock()
        session.get.return_value = item
        sent: list[str] = []
        calls = {"n": 0}

        async def flaky(name: str) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("voice-service unavailable")
            sent.append(name)

        main._ts_presence_generation = 0
        main._ts_last_nickname = ""

        with (
            patch.object(main, "new_session", return_value=session),
            patch.object(main, "_fetch_lyrics_for_item", AsyncMock(return_value=[])),
            patch.object(main, "_ts_lyric_interval_s", return_value=0.05),
            patch.object(main, "_current_queue_item_id", 9),
            patch.object(main, "_play_started_at", time.monotonic()),
            patch.object(main, "_paused_at", None),
            patch.object(main, "_paused_total_s", 0.0),
            patch.object(main.voice, "set_client_nickname", AsyncMock(side_effect=flaky)),
        ):
            generation = main._bump_ts_presence_generation()
            task = asyncio.create_task(main._ts_lyric_worker(9, generation))
            await asyncio.sleep(0.4)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.assertGreaterEqual(len(sent), 1)
        self.assertEqual("♪ 测试视频 - UP主", sent[0])

    async def test_worker_reasserts_nickname_after_external_reset(self) -> None:
        """切歌后遗留的“还原默认昵称”覆盖了昵称时，worker 应在一个周期内改回来。"""
        item = SimpleNamespace(id=7, title="测试视频", artist="UP主")
        session = unittest.mock.Mock()
        session.get.return_value = item
        sent: list[str] = []

        main._ts_presence_generation = 0
        main._ts_last_nickname = "qzh先生"  # 被外部改回了默认昵称

        with (
            patch.object(main, "new_session", return_value=session),
            patch.object(main, "_fetch_lyrics_for_item", AsyncMock(return_value=[])),
            patch.object(main, "_ts_lyric_interval_s", return_value=0.05),
            patch.object(main, "_current_queue_item_id", 7),
            patch.object(main, "_play_started_at", time.monotonic()),
            patch.object(main, "_paused_at", None),
            patch.object(main, "_paused_total_s", 0.0),
            patch.object(main.voice, "set_client_nickname", AsyncMock(side_effect=lambda name: sent.append(name))),
        ):
            generation = main._bump_ts_presence_generation()
            task = asyncio.create_task(main._ts_lyric_worker(7, generation))
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.assertTrue(sent)
        self.assertEqual("♪ 测试视频 - UP主", sent[0])


if __name__ == "__main__":
    unittest.main()
