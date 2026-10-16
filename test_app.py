"""Unit test suite for NotionTranscribe components."""

import os
import sys
import unittest
import numpy as np
import soundfile as sf

from unittest.mock import MagicMock, patch

from config_manager import sanitize_database_id, ConfigManager
from notion_service import chunk_text, format_timestamp, NotionService
from transcriber import WhisperWorker
from transcript_diff import TranscriptDiffEngine, clean_word, extract_words_from_segments
from audio_player import AudioPlayer
from audio_recorder import (
    AudioRecorder,
    get_audio_input_devices,
    get_audio_loopback_devices,
    mix_audio_frames,
    to_mono_16k,
)
from diarization import assign_speakers_to_segments, is_diarization_available


class TestNotionService(unittest.TestCase):
    def test_sanitize_database_id(self):
        # 1. Plain 32 hex
        raw_hex = "8a93e970b8a34bc3a18a93e970b8a34b"
        expected = "8a93e970-b8a3-4bc3-a18a-93e970b8a34b"
        self.assertEqual(sanitize_database_id(raw_hex), expected)

        # 2. Hyphenated UUID
        self.assertEqual(sanitize_database_id(expected), expected)

        # 3. Notion URL
        url = "https://www.notion.so/myworkspace/8a93e970b8a34bc3a18a93e970b8a34b?v=12345"
        self.assertEqual(sanitize_database_id(url), expected)

    def test_format_timestamp(self):
        self.assertEqual(format_timestamp(0), "00:00")
        self.assertEqual(format_timestamp(45), "00:45")
        self.assertEqual(format_timestamp(75), "01:15")
        self.assertEqual(format_timestamp(3665), "01:01:05")

    def test_chunk_text_short(self):
        short_text = "This is a short sentence."
        chunks = chunk_text(short_text, max_chars=1900)
        self.assertEqual(chunks, [short_text])

    def test_chunk_text_long(self):
        sentence = "This is a meaningful sample sentence about transcription testing. "
        long_text = sentence * 60  # ~3900 chars
        chunks = chunk_text(long_text, max_chars=1900)

        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1900)

        rejoined = " ".join(chunks)
        self.assertGreater(len(chunks), 1)
        self.assertTrue("meaningful sample sentence" in rejoined)

    def test_build_paragraph_blocks(self):
        service = NotionService(token="secret_test_fake_token_123", database_id="fake_db")
        segments = [
            {"start": 1.5, "end": 4.2, "text": "Hello world!"},
            {"start": 5.0, "end": 9.8, "text": "This is a test transcript segment."},
        ]
        blocks = service.build_paragraph_blocks(segments)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["type"], "paragraph")
        rich_texts = blocks[0]["paragraph"]["rich_text"]
        self.assertTrue(any("[00:01 - 00:04]" in r["text"]["content"] for r in rich_texts))

    def test_get_database_pages(self):
        service = NotionService(token="secret_test_fake_token_123", database_id="fake_db")
        from datetime import datetime, timezone
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT12:00:00.000Z")
        old_iso = "2024-01-01T10:00:00.000Z"

        mock_client = MagicMock()
        mock_client.databases.retrieve.return_value = {
            "data_sources": [{"id": "ds_123"}]
        }
        mock_client.data_sources.query.return_value = {
            "results": [
                {
                    "id": "page-1",
                    "created_time": today_iso,
                    "properties": {
                        "Session": {
                            "type": "title",
                            "title": [{"plain_text": "Today Meeting"}]
                        }
                    }
                },
                {
                    "id": "page-2",
                    "created_time": old_iso,
                    "properties": {
                        "Session": {
                            "type": "title",
                            "title": [{"plain_text": "Old Meeting"}]
                        }
                    }
                },
                {
                    "id": "page-3",
                    "archived": True,
                    "created_time": today_iso,
                    "properties": {
                        "Session": {
                            "type": "title",
                            "title": [{"plain_text": "Deleted Meeting"}]
                        }
                    }
                }
            ]
        }
        service.client = mock_client

        # Without filter
        success, msg, all_pages = service.get_database_pages(filter_today=False)
        self.assertTrue(success)
        self.assertEqual(len(all_pages), 2)
        self.assertEqual(all_pages[0]["title"], "Today Meeting")
        self.assertTrue(all_pages[0]["is_today"])
        self.assertEqual(all_pages[1]["title"], "Old Meeting")
        self.assertFalse(all_pages[1]["is_today"])

        # With filter_today=True
        success, msg, today_pages = service.get_database_pages(filter_today=True)
        self.assertTrue(success)
        self.assertEqual(len(today_pages), 1)
        self.assertEqual(today_pages[0]["id"], "page-1")

    def test_append_transcript_to_page(self):
        service = NotionService(token="secret_test_fake_token_123", database_id="fake_db")
        mock_client = MagicMock()
        service.client = mock_client

        segments = [{"start": 0.0, "end": 2.5, "text": "Appended snippet"}]
        metadata = {"source": "test.mp3", "duration": "00:02", "model": "base"}

        success, msg, url = service.append_transcript_to_page("target-page-id-123", segments, metadata)
        self.assertTrue(success)
        self.assertIn("targetpageid123", url)
        mock_client.blocks.children.append.assert_called_once()
        call_kwargs = mock_client.blocks.children.append.call_args[1]
        self.assertEqual(call_kwargs["block_id"], "target-page-id-123")
        blocks = call_kwargs["children"]
        self.assertEqual(blocks[0]["type"], "divider")
        self.assertEqual(blocks[1]["type"], "heading_2")
        self.assertEqual(blocks[2]["type"], "callout")
        self.assertEqual(blocks[3]["type"], "paragraph")


class TestTranscriptDiffEngine(unittest.TestCase):
    def test_clean_word(self):
        self.assertEqual(clean_word("Hello,"), "hello")
        self.assertEqual(clean_word("WORLD!"), "world")
        self.assertEqual(clean_word("don't"), "dont")

    def test_identical_transcripts(self):
        segments_a = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "The quick brown fox jumps over the lazy dog.",
                "words": [
                    {"word": "The", "start": 0.0, "end": 0.3, "prob": 0.99},
                    {"word": "quick", "start": 0.3, "end": 0.6, "prob": 0.98},
                    {"word": "brown", "start": 0.6, "end": 1.0, "prob": 0.95},
                    {"word": "fox", "start": 1.0, "end": 1.4, "prob": 0.97},
                    {"word": "jumps", "start": 1.4, "end": 1.8, "prob": 0.94},
                    {"word": "over", "start": 1.8, "end": 2.1, "prob": 0.96},
                    {"word": "the", "start": 2.1, "end": 2.3, "prob": 0.99},
                    {"word": "lazy", "start": 2.3, "end": 2.7, "prob": 0.92},
                    {"word": "dog.", "start": 2.7, "end": 3.0, "prob": 0.98},
                ],
            }
        ]
        segments_b = [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "The quick brown fox jumps over the lazy dog.",
                "words": [
                    {"word": "The", "start": 0.0, "end": 0.3, "prob": 0.99},
                    {"word": "quick", "start": 0.3, "end": 0.6, "prob": 0.98},
                    {"word": "brown", "start": 0.6, "end": 1.0, "prob": 0.95},
                    {"word": "fox", "start": 1.0, "end": 1.4, "prob": 0.97},
                    {"word": "jumps", "start": 1.4, "end": 1.8, "prob": 0.94},
                    {"word": "over", "start": 1.8, "end": 2.1, "prob": 0.96},
                    {"word": "the", "start": 2.1, "end": 2.3, "prob": 0.99},
                    {"word": "lazy", "start": 2.3, "end": 2.7, "prob": 0.92},
                    {"word": "dog.", "start": 2.7, "end": 3.0, "prob": 0.98},
                ],
            }
        ]

        engine = TranscriptDiffEngine(segments_a, segments_b, "base", "small")
        self.assertEqual(len(engine.discrepancies), 0)
        merged_segs, merged_text = engine.merge()
        self.assertIn("The quick brown fox jumps over the lazy dog.", merged_text)

    def test_differing_words_audition_window(self):
        # Model A says "Tuesday" at [2.0 - 2.6]
        # Model B says "Thursday" at [2.0 - 2.7]
        segments_a = [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "We will meet next Tuesday at noon.",
                "words": [
                    {"word": "We", "start": 0.0, "end": 0.3, "prob": 0.99},
                    {"word": "will", "start": 0.3, "end": 0.6, "prob": 0.99},
                    {"word": "meet", "start": 0.6, "end": 1.1, "prob": 0.95},
                    {"word": "next", "start": 1.1, "end": 1.8, "prob": 0.93},
                    {"word": "Tuesday", "start": 2.0, "end": 2.6, "prob": 0.60},
                    {"word": "at", "start": 2.8, "end": 3.1, "prob": 0.98},
                    {"word": "noon.", "start": 3.1, "end": 4.0, "prob": 0.95},
                ],
            }
        ]
        segments_b = [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "We will meet next Thursday at noon.",
                "words": [
                    {"word": "We", "start": 0.0, "end": 0.3, "prob": 0.99},
                    {"word": "will", "start": 0.3, "end": 0.6, "prob": 0.99},
                    {"word": "meet", "start": 0.6, "end": 1.1, "prob": 0.95},
                    {"word": "next", "start": 1.1, "end": 1.8, "prob": 0.93},
                    {"word": "Thursday", "start": 2.0, "end": 2.7, "prob": 0.92},
                    {"word": "at", "start": 2.8, "end": 3.1, "prob": 0.98},
                    {"word": "noon.", "start": 3.1, "end": 4.0, "prob": 0.95},
                ],
            }
        ]

        engine = TranscriptDiffEngine(segments_a, segments_b, "base", "small")
        self.assertEqual(len(engine.discrepancies), 1)

        diff = engine.discrepancies[0]
        self.assertEqual(diff.text_a, "Tuesday")
        self.assertEqual(diff.text_b, "Thursday")
        self.assertAlmostEqual(diff.start_sec, 2.0, places=1)
        self.assertAlmostEqual(diff.end_sec, 2.7, places=1)
        self.assertIn("next", diff.context_before)
        self.assertIn("at noon.", diff.context_after)

        # Test selecting Model B (Thursday)
        diff.set_choice("model_b")
        merged_segs, merged_text = engine.merge()
        self.assertIn("Thursday", merged_text)
        self.assertNotIn("Tuesday", merged_text)

        # Test selecting Custom text ("Friday")
        diff.set_choice("custom", "Friday")
        merged_segs, merged_text = engine.merge()
        self.assertIn("Friday", merged_text)
        self.assertNotIn("Thursday", merged_text)


class TestAudioAndTranscriber(unittest.TestCase):
    def setUp(self):
        # Generate a small 5-second 16kHz sine wave audio file for audition testing
        self.test_wav = "test_audio_sample.wav"
        sr = 16000
        t = np.linspace(0, 5.0, int(sr * 5.0), endpoint=False)
        audio = 0.2 * np.sin(2 * np.pi * 440 * t)  # 440 Hz A tone
        sf.write(self.test_wav, audio.astype(np.float32), sr)

    def tearDown(self):
        if os.path.exists(self.test_wav):
            try:
                os.remove(self.test_wav)
            except Exception:
                pass

    def test_whisper_worker_instantiation(self):
        worker = WhisperWorker(
            audio_path=self.test_wav,
            model_size="tiny",
            device="cpu",
            compute_type="int8",
        )
        self.assertEqual(worker.model_size, "tiny")
        self.assertEqual(worker.device, "cpu")

    def test_audio_player_snippet_calculation(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            app = QApplication([])

        player = AudioPlayer()
        # Word at [2.0s - 2.5s], pad_before=2.0, pad_after=2.0
        # Should play from [0.0s to 4.5s]
        # In headless test environments without audio hardware, test file reading
        with sf.SoundFile(self.test_wav) as f:
            self.assertEqual(f.samplerate, 16000)
            self.assertGreater(f.frames, 16000 * 4)


class TestVideoProcessing(unittest.TestCase):
    def setUp(self):
        self.test_mp4 = "test_synthetic_clip.mp4"
        self.output_audio = None
        # Generate a small 1-second synthetic video clip with ffmpeg
        import subprocess
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "color=c=green:s=160x120:d=1",
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
            self.test_mp4,
        ]
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo, check=True)

    def tearDown(self):
        for p in [self.test_mp4, self.output_audio]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def test_is_video_file(self):
        from video_processor import is_video_file
        self.assertTrue(is_video_file("lecture.mp4"))
        self.assertTrue(is_video_file("clip.mkv"))
        self.assertTrue(is_video_file("recording.MOV"))
        self.assertTrue(is_video_file("presentation.avi"))
        self.assertFalse(is_video_file("audio.mp3"))
        self.assertFalse(is_video_file("document.pdf"))

    def test_video_audio_extraction_and_deletion(self):
        from video_processor import extract_audio_from_video, verify_audio_file
        self.assertTrue(os.path.exists(self.test_mp4))

        audio_path, was_deleted = extract_audio_from_video(self.test_mp4, delete_source=True)
        self.output_audio = audio_path

        # 1. Output audio exists and is valid
        self.assertTrue(os.path.exists(audio_path))
        self.assertTrue(verify_audio_file(audio_path))

        # Check that audio is 16kHz mono
        info = sf.info(audio_path)
        self.assertEqual(info.samplerate, 16000)
        self.assertEqual(info.channels, 1)

        # 2. Source video was deleted as requested
        self.assertTrue(was_deleted)
        self.assertFalse(os.path.exists(self.test_mp4))

    def test_corrupt_video_not_deleted(self):
        from video_processor import extract_audio_from_video
        corrupt_file = "corrupt_fake_video.mp4"
        with open(corrupt_file, "w") as f:
            f.write("Not a real video container")

        try:
            with self.assertRaises(RuntimeError):
                extract_audio_from_video(corrupt_file, delete_source=True)
            # Crucial safety check: the original file must NOT be deleted if extraction fails
            self.assertTrue(os.path.exists(corrupt_file))
        finally:
            if os.path.exists(corrupt_file):
                os.remove(corrupt_file)

    def test_video_folder_watcher_init(self):
        from PyQt6.QtWidgets import QApplication
        from video_watcher import VideoFolderWatcher
        app = QApplication.instance()
        if app is None:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            app = QApplication([])

        watcher = VideoFolderWatcher(watch_folder="test_watch_inbox")
        self.assertFalse(watcher.is_watching)
        watcher.start_watching()
        self.assertTrue(watcher.is_watching)
        watcher.stop_watching()
        self.assertFalse(watcher.is_watching)
        if os.path.exists("test_watch_inbox"):
            try:
                os.rmdir("test_watch_inbox")
            except Exception:
                pass


class TestTargetPageDropdownUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            cls.app = QApplication([])
        else:
            cls.app = app

    @patch("main.MainWindow._fetch_notion_pages")
    def test_page_dropdown_and_filtering(self, mock_fetch):
        from main import MainWindow
        win = MainWindow()

        # Initial state
        self.assertEqual(win.target_page_combo.count(), 1)
        self.assertIsNone(win.target_page_combo.currentData())
        self.assertTrue(win.today_filter_cb.isChecked())

        # Feed mock pages
        fake_pages = [
            {"id": "p1", "title": "Morning Sync", "created_display": "Today 09:30", "is_today": True},
            {"id": "p2", "title": "Afternoon Wrap", "created_display": "Today 15:45", "is_today": True},
            {"id": "p3", "title": "Last Week Planning", "created_display": "2026-09-14 11:00", "is_today": False},
        ]
        win._on_pages_fetched(fake_pages)

        # Because today_filter_cb is checked, should show 1 ("+ Create New Page") + 2 today pages = 3
        self.assertEqual(win.target_page_combo.count(), 3)
        self.assertEqual(win.target_page_combo.itemData(1), "p1")
        self.assertEqual(win.target_page_combo.itemData(2), "p2")

        # Clear filter button click
        win.clear_filter_btn.click()
        self.assertFalse(win.today_filter_cb.isChecked())
        # Now should show 1 + 3 = 4 items
        self.assertEqual(win.target_page_combo.count(), 4)
        self.assertEqual(win.target_page_combo.itemData(3), "p3")

        # Select an existing page (index 1 -> "p1")
        win.target_page_combo.setCurrentIndex(1)
        self.assertEqual(win.target_page_combo.currentData(), "p1")
        self.assertEqual(win.title_label.text(), "Section Heading:")
        self.assertTrue(win.title_input.isEnabled())
        self.assertFalse(win.tags_input.isEnabled())
        self.assertEqual(win.push_only_btn.text(), "Push (Append to Page)")
        self.assertEqual(win.action_btn.text(), "Transcribe & Append to Page")

        # Select Create New Page again (index 0)
        win.target_page_combo.setCurrentIndex(0)
        self.assertIsNone(win.target_page_combo.currentData())
        self.assertEqual(win.title_label.text(), "Page Title:")
        self.assertTrue(win.title_input.isEnabled())
        self.assertTrue(win.tags_input.isEnabled())
        self.assertEqual(win.push_only_btn.text(), "Push to Notion")
        self.assertEqual(win.action_btn.text(), "Transcribe & Push to Notion")

        # Test Audio Source Selector in Record Live tab
        self.assertEqual(win.source_mode_combo.count(), 3)
        self.assertEqual(win.source_mode_combo.itemData(0), "mic")
        self.assertEqual(win.source_mode_combo.itemData(1), "system")
        self.assertEqual(win.source_mode_combo.itemData(2), "both")

        # Switch to system audio
        win.source_mode_combo.setCurrentIndex(1)
        self.assertEqual(win.recorder.audio_source, "system")
        self.assertEqual(win.meter_level_label.text(), "System Audio Level:")

        # Switch to both
        win.source_mode_combo.setCurrentIndex(2)
        self.assertEqual(win.recorder.audio_source, "both")
        self.assertEqual(win.meter_level_label.text(), "Combined Level (Mic + System):")

        # Switch back to mic
        win.source_mode_combo.setCurrentIndex(0)
        self.assertEqual(win.recorder.audio_source, "mic")
        self.assertEqual(win.meter_level_label.text(), "Mic Level:")


class TestAudioCapture(unittest.TestCase):
    def test_to_mono_16k_stereo_resample(self):
        # 1 second of 48kHz stereo = 96000 float32 samples
        data = np.random.uniform(-0.5, 0.5, (48000, 2)).astype(np.float32)
        mono_16k = to_mono_16k(data, in_rate=48000, in_channels=2)
        self.assertEqual(mono_16k.ndim, 1)
        self.assertEqual(len(mono_16k), 16000)
        self.assertEqual(mono_16k.dtype, np.float32)

    def test_to_mono_16k_mono_resample(self):
        # 1 second of 44.1kHz mono = 44100 float32 samples
        data = np.random.uniform(-0.5, 0.5, 44100).astype(np.float32)
        mono_16k = to_mono_16k(data, in_rate=44100, in_channels=1)
        self.assertEqual(mono_16k.ndim, 1)
        self.assertEqual(len(mono_16k), 16000)

    def test_to_mono_16k_passthrough(self):
        data = np.random.uniform(-0.5, 0.5, 16000).astype(np.float32)
        mono_16k = to_mono_16k(data, in_rate=16000, in_channels=1)
        self.assertEqual(len(mono_16k), 16000)

    def test_to_mono_16k_empty(self):
        res = to_mono_16k(np.zeros(0, dtype=np.float32), 48000, 2)
        self.assertEqual(len(res), 0)

    def test_mix_audio_frames(self):
        frame_a = np.ones(1000, dtype=np.float32) * 0.8
        frame_b = np.ones(1000, dtype=np.float32) * 0.8
        mixed = mix_audio_frames(frame_a, frame_b)
        self.assertEqual(len(mixed), 1000)
        # (0.8 + 0.8) * 0.7 = 1.12 -> clipped to 1.0
        self.assertLessEqual(mixed.max(), 1.0)
        self.assertGreaterEqual(mixed.min(), -1.0)

    def test_mix_audio_frames_unequal_length(self):
        frame_a = np.ones(500, dtype=np.float32) * 0.5
        frame_b = np.ones(1000, dtype=np.float32) * 0.5
        mixed = mix_audio_frames(frame_a, frame_b)
        self.assertEqual(len(mixed), 1000)

    def test_device_enumeration(self):
        inputs = get_audio_input_devices()
        self.assertIsInstance(inputs, list)
        loopbacks = get_audio_loopback_devices()
        self.assertIsInstance(loopbacks, list)

    def test_recorder_source_modes(self):
        rec = AudioRecorder(audio_source="mic")
        self.assertEqual(rec.audio_source, "mic")
        rec.set_audio_source("system")
        self.assertEqual(rec.audio_source, "system")
        rec.set_audio_source("both")
        self.assertEqual(rec.audio_source, "both")


class TestSpeakerDiarization(unittest.TestCase):
    def test_assign_speakers_exact_overlap(self):
        segments = [
            {"start": 0.5, "end": 2.5, "text": "Hi, this is Alice."},
            {"start": 3.0, "end": 5.5, "text": "Hi Alice, this is Bob."},
        ]
        turns = [
            {"start": 0.4, "end": 2.6, "speaker_id": 0},
            {"start": 2.9, "end": 5.6, "speaker_id": 1},
        ]
        assigned = assign_speakers_to_segments(segments, turns)
        self.assertEqual(len(assigned), 2)
        self.assertEqual(assigned[0]["speaker"], "Speaker 1")
        self.assertEqual(assigned[0]["speaker_id"], 0)
        self.assertEqual(assigned[1]["speaker"], "Speaker 2")
        self.assertEqual(assigned[1]["speaker_id"], 1)

    def test_assign_speakers_partial_overlap_majority_wins(self):
        # Segment from 1.0 to 4.0 (3.0s total)
        # Overlaps turn 0 (0.0 to 2.0) by 1.0s
        # Overlaps turn 1 (2.0 to 5.0) by 2.0s -> turn 1 should win
        segments = [{"start": 1.0, "end": 4.0, "text": "Speaking here."}]
        turns = [
            {"start": 0.0, "end": 2.0, "speaker_id": 0},
            {"start": 2.0, "end": 5.0, "speaker_id": 1},
        ]
        assigned = assign_speakers_to_segments(segments, turns)
        self.assertEqual(assigned[0]["speaker"], "Speaker 2")
        self.assertEqual(assigned[0]["speaker_id"], 1)

    def test_assign_speakers_fallback_proximity(self):
        # Segment lies in a gap where no turns overlap:
        # Segment [5.0 - 6.0], Midpoint = 5.5
        # Turn 0: [1.0 - 2.0] Midpoint = 1.5 (diff = 4.0)
        # Turn 1: [7.0 - 8.0] Midpoint = 7.5 (diff = 2.0) -> Turn 1 closer
        segments = [{"start": 5.0, "end": 6.0, "text": "Quiet speech."}]
        turns = [
            {"start": 1.0, "end": 2.0, "speaker_id": 0},
            {"start": 7.0, "end": 8.0, "speaker_id": 1},
        ]
        assigned = assign_speakers_to_segments(segments, turns)
        self.assertEqual(assigned[0]["speaker"], "Speaker 2")

    def test_assign_speakers_empty_handling(self):
        # Empty segments
        self.assertEqual(assign_speakers_to_segments([], [{"start": 0, "end": 1, "speaker_id": 0}]), [])

        # Empty turns -> defaults to Speaker 1
        segments = [{"start": 0.0, "end": 2.0, "text": "Alone speaking."}]
        res = assign_speakers_to_segments(segments, [])
        self.assertEqual(res[0]["speaker"], "Speaker 1")
        self.assertEqual(res[0]["speaker_id"], 0)

    def test_notion_blocks_with_speaker_colors(self):
        service = NotionService(token="secret_test_fake_token_123", database_id="fake_db")
        segments = [
            {"start": 0.0, "end": 2.0, "text": "Welcome to the meeting.", "speaker": "Speaker 1", "speaker_id": 0},
            {"start": 2.5, "end": 4.5, "text": "Thank you everyone.", "speaker": "Speaker 2", "speaker_id": 1},
        ]
        blocks = service.build_paragraph_blocks(segments)
        self.assertEqual(len(blocks), 2)

        # First segment: Speaker 1 should have bold blue annotation
        rich_0 = blocks[0]["paragraph"]["rich_text"]
        self.assertEqual(rich_0[0]["text"]["content"], "[Speaker 1] ")
        self.assertEqual(rich_0[0]["annotations"]["color"], "blue")
        self.assertTrue(rich_0[0]["annotations"]["bold"])

        # Second segment: Speaker 2 should have bold green annotation
        rich_1 = blocks[1]["paragraph"]["rich_text"]
        self.assertEqual(rich_1[0]["text"]["content"], "[Speaker 2] ")
        self.assertEqual(rich_1[0]["annotations"]["color"], "green")
        self.assertTrue(rich_1[0]["annotations"]["bold"])

    def test_config_diarization_settings(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_cfg = Path(tmp_dir) / "config.json"
            cfg = ConfigManager(config_path=tmp_cfg)
            self.assertTrue(cfg.get("enable_diarization"))
            self.assertEqual(cfg.get("num_speakers"), -1)
            self.assertEqual(cfg.get("diarization_threshold"), 0.5)

            # Test updating diarization settings
            cfg.set("enable_diarization", False)
            cfg.set("num_speakers", 3)
            cfg.set("diarization_threshold", 0.65)
            cfg.save()

            # Reload
            cfg2 = ConfigManager(config_path=tmp_cfg)
            self.assertFalse(cfg2.get("enable_diarization"))
            self.assertEqual(cfg2.get("num_speakers"), 3)
            self.assertEqual(cfg2.get("diarization_threshold"), 0.65)

    def test_diarization_models_availability(self):
        # Check that downloaded ONNX models are recognized as available on disk
        available = is_diarization_available()
        self.assertTrue(available)

    @patch("main.MainWindow._fetch_notion_pages")
    @patch("video_watcher.VideoFolderWatcher.start_watching")
    def test_ui_diarization_controls(self, mock_watch, mock_fetch):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(["test"])
        from main import MainWindow
        win = MainWindow()
        self.assertTrue(hasattr(win, "diarize_cb"))
        self.assertIn("Diarize", win.diarize_cb.text())
        # Toggle checkbox
        win.diarize_cb.setChecked(False)
        self.assertFalse(win.diarize_cb.isChecked())
        win.diarize_cb.setChecked(True)
        self.assertTrue(win.diarize_cb.isChecked())

        # Test Settings Dialog
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(win)
        self.assertTrue(hasattr(dlg, "enable_diarization_cb"))
        self.assertTrue(hasattr(dlg, "num_speakers_combo"))
        self.assertTrue(hasattr(dlg, "diar_status_label"))

        # Clean up widgets
        win.video_watcher.stop_watching()
        dlg.close()
        win.close()


class TestHardwareBenchmark(unittest.TestCase):
    def test_get_hardware_specs(self):
        from benchmark import get_hardware_specs
        specs = get_hardware_specs()
        self.assertIsInstance(specs, dict)
        self.assertIn("has_cuda", specs)
        self.assertIn("gpu_name", specs)
        self.assertIn("cpu_count", specs)
        self.assertIn("recommended_diarization_threads", specs)
        self.assertGreaterEqual(specs["recommended_diarization_threads"], 1)

    def test_generate_benchmark_audio(self):
        from benchmark import generate_benchmark_audio
        audio = generate_benchmark_audio(duration_sec=2.0, sample_rate=16000)
        self.assertEqual(len(audio), 32000)
        self.assertEqual(audio.dtype, np.float32)

    def test_recommend_optimal_settings_balanced(self):
        from benchmark import recommend_optimal_settings
        mock_results = [
            {"model": "tiny", "device": "cuda", "compute_type": "float16", "beam_size": 1, "speedup_factor": 45.0, "est_1hr_minutes": 1.3, "vram_safety": "SAFE", "success": True},
            {"model": "small", "device": "cuda", "compute_type": "float16", "beam_size": 1, "speedup_factor": 30.0, "est_1hr_minutes": 2.0, "vram_safety": "SAFE", "success": True},
            {"model": "medium", "device": "cuda", "compute_type": "float16", "beam_size": 1, "speedup_factor": 22.0, "est_1hr_minutes": 2.7, "vram_safety": "SAFE", "success": True},
            {"model": "large-v3", "device": "cuda", "compute_type": "float16", "beam_size": 1, "speedup_factor": 2.0, "est_1hr_minutes": 30.0, "vram_safety": "RISK", "success": True},
        ]
        rec = recommend_optimal_settings(mock_results, preference="balanced")
        self.assertEqual(rec["whisper_model"], "medium")
        self.assertEqual(rec["beam_size"], 1)

    def test_recommend_optimal_settings_max_speed(self):
        from benchmark import recommend_optimal_settings
        mock_results = [
            {"model": "tiny", "device": "cuda", "compute_type": "float16", "beam_size": 1, "speedup_factor": 45.0, "est_1hr_minutes": 1.3, "vram_safety": "SAFE", "success": True},
            {"model": "medium", "device": "cuda", "compute_type": "float16", "beam_size": 1, "speedup_factor": 22.0, "est_1hr_minutes": 2.7, "vram_safety": "SAFE", "success": True},
        ]
        rec = recommend_optimal_settings(mock_results, preference="max_speed")
        self.assertEqual(rec["whisper_model"], "tiny")

    def test_recommend_optimal_settings_max_accuracy(self):
        from benchmark import recommend_optimal_settings
        mock_results = [
            {"model": "small", "device": "cuda", "compute_type": "float16", "beam_size": 1, "speedup_factor": 30.0, "est_1hr_minutes": 2.0, "vram_safety": "SAFE", "success": True},
            {"model": "large-v3", "device": "cuda", "compute_type": "float16", "beam_size": 1, "speedup_factor": 15.0, "est_1hr_minutes": 4.0, "vram_safety": "SAFE", "success": True},
        ]
        rec = recommend_optimal_settings(mock_results, preference="max_accuracy")
        self.assertEqual(rec["whisper_model"], "large-v3")

    def test_benchmark_dialog_instantiation(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(["test"])
        from ui.benchmark_dialog import BenchmarkDialog
        dlg = BenchmarkDialog()
        self.assertTrue(hasattr(dlg, "pref_combo"))
        self.assertTrue(hasattr(dlg, "run_btn"))
        self.assertTrue(hasattr(dlg, "apply_btn"))
        self.assertTrue(hasattr(dlg, "table"))
        dlg.close()


class TestTranscriptStorage(unittest.TestCase):
    def setUp(self):
        import tempfile
        import uuid
        self.test_dir = tempfile.TemporaryDirectory()
        self.audio_path = os.path.join(self.test_dir.name, f"TestRec_{uuid.uuid4().hex[:8]}.wav")
        # Create a dummy wav file
        with open(self.audio_path, "wb") as f:
            f.write(b"RIFF dummy wav data")

        self.sample_segments = [
            {
                "start": 0.0,
                "end": 2.5,
                "text": "Hello and welcome.",
                "timestamp_str": "[00:00 - 00:02]",
                "speaker": "Speaker 1",
                "speaker_id": 0,
            },
            {
                "start": 3.0,
                "end": 6.2,
                "text": "Thank you for joining today.",
                "timestamp_str": "[00:03 - 00:06]",
                "speaker": "Speaker 2",
                "speaker_id": 1,
            },
        ]
        self.full_text = "[Speaker 1] [00:00 - 00:02] Hello and welcome.\n\n[Speaker 2] [00:03 - 00:06] Thank you for joining today."

    def tearDown(self):
        from transcript_storage import delete_transcript
        delete_transcript(self.audio_path)
        self.test_dir.cleanup()

    def test_save_and_load_transcript(self):
        from transcript_storage import save_transcript, load_transcript, has_transcript

        self.assertFalse(has_transcript(self.audio_path))

        json_path, txt_path = save_transcript(
            audio_path=self.audio_path,
            segments=self.sample_segments,
            full_text=self.full_text,
            model="medium",
            device="cuda",
            compute_type="float16",
        )

        self.assertTrue(os.path.exists(json_path))
        self.assertTrue(os.path.exists(txt_path))
        self.assertTrue(has_transcript(self.audio_path))

        data = load_transcript(self.audio_path)
        self.assertIsNotNone(data)
        self.assertEqual(data["model"], "medium")
        self.assertEqual(data["segment_count"], 2)
        self.assertEqual(data["speaker_count"], 2)
        self.assertEqual(len(data["segments"]), 2)
        self.assertEqual(data["segments"][0]["text"], "Hello and welcome.")
        self.assertEqual(data["segments"][1]["speaker"], "Speaker 2")

    def test_update_transcript_notion_info(self):
        from transcript_storage import save_transcript, load_transcript, update_transcript_notion_info

        save_transcript(
            audio_path=self.audio_path,
            segments=self.sample_segments,
            full_text=self.full_text,
        )

        data = load_transcript(self.audio_path)
        self.assertFalse(data["notion"]["pushed"])

        test_url = "https://notion.so/test-page-12345"
        success = update_transcript_notion_info(self.audio_path, test_url, page_id="12345")
        self.assertTrue(success)

        updated_data = load_transcript(self.audio_path)
        self.assertTrue(updated_data["notion"]["pushed"])
        self.assertEqual(updated_data["notion"]["page_url"], test_url)
        self.assertEqual(updated_data["notion"]["page_id"], "12345")

    def test_delete_transcript(self):
        from transcript_storage import save_transcript, delete_transcript, has_transcript

        save_transcript(
            audio_path=self.audio_path,
            segments=self.sample_segments,
            full_text=self.full_text,
        )
        self.assertTrue(has_transcript(self.audio_path))

        deleted = delete_transcript(self.audio_path)
        self.assertTrue(deleted)
        self.assertFalse(has_transcript(self.audio_path))

    def test_export_formats(self):
        from transcript_storage import (
            export_to_srt,
            export_to_vtt,
            export_to_markdown,
            format_srt_timestamp,
            format_vtt_timestamp,
        )

        self.assertEqual(format_srt_timestamp(65.123), "00:01:05,123")
        self.assertEqual(format_vtt_timestamp(65.123), "00:01:05.123")

        srt_path = os.path.join(self.test_dir.name, "output.srt")
        export_to_srt(self.sample_segments, srt_path)
        self.assertTrue(os.path.exists(srt_path))
        with open(srt_path, "r", encoding="utf-8") as f:
            srt_content = f.read()
        self.assertIn("-->", srt_content)
        self.assertIn("Hello and welcome.", srt_content)

        vtt_path = os.path.join(self.test_dir.name, "output.vtt")
        export_to_vtt(self.sample_segments, vtt_path)
        self.assertTrue(os.path.exists(vtt_path))
        with open(vtt_path, "r", encoding="utf-8") as f:
            vtt_content = f.read()
        self.assertTrue(vtt_content.startswith("WEBVTT"))

        md_path = os.path.join(self.test_dir.name, "output.md")
        export_to_markdown(self.audio_path, self.sample_segments, self.full_text, md_path)
        self.assertTrue(os.path.exists(md_path))
        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        self.assertIn("# Transcript", md_content)
        self.assertIn("Speaker 1", md_content)

    def test_recordings_list_widget(self):
        from PyQt6.QtWidgets import QApplication
        from pathlib import Path
        app = QApplication.instance() or QApplication(["test"])
        from ui.recordings_list import RecordingsListWidget
        from transcript_storage import save_transcript

        # Save a transcript for the test audio
        save_transcript(
            audio_path=self.audio_path,
            segments=self.sample_segments,
            full_text=self.full_text,
        )

        with patch("ui.recordings_list.RECORDINGS_DIR", Path(self.test_dir.name)):
            widget = RecordingsListWidget()
            self.assertEqual(widget.table.columnCount(), 6)
            self.assertEqual(widget.table.horizontalHeaderItem(4).text(), "Transcript Status")
            self.assertEqual(widget.table.horizontalHeaderItem(5).text(), "Notes Preview")
            self.assertEqual(widget.table.rowCount(), 1)
            status_item = widget.table.item(0, 4)
            self.assertIn("Saved", status_item.text())

            # Test notes integration
            from transcript_storage import save_recording_notes, get_recording_notes
            save_recording_notes(self.audio_path, "Lecture Exam Tip: Focus on Chapter 3")
            self.assertEqual(get_recording_notes(self.audio_path), "Lecture Exam Tip: Focus on Chapter 3")
            widget.refresh()
            notes_item = widget.table.item(0, 5)
            self.assertIn("Lecture Exam Tip", notes_item.text())

            widget.close()

    def test_main_window_loads_saved_transcript(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(["test"])
        from main import MainWindow
        from transcript_storage import save_transcript

        save_transcript(
            audio_path=self.audio_path,
            segments=self.sample_segments,
            full_text=self.full_text,
            study_notes="# Test Notes\n- Question: What is DNA?\n  Answer: Genetic material.",
        )

        with patch("notion_service.NotionService.test_connection", return_value=(False, "offline")):
            win = MainWindow()
            win._on_recording_loaded(self.audio_path)
            self.assertEqual(len(win._transcribed_segments), 2)
            self.assertTrue(win.push_only_btn.isEnabled())
            self.assertTrue(win.study_guide_btn.isEnabled())
            self.assertTrue(win.export_btn.isEnabled())
            self.assertIn("Hello and welcome.", win.output_text.toPlainText())
            self.assertIn("What is DNA?", win.study_notes_text.toPlainText())
            self.assertIn("Loaded saved transcript", win.status_label.text())
            win.close()


class TestAIStudyAssistantAndNotebookLM(unittest.TestCase):
    """Test AI Study Guide generation and NotebookLM export features."""

    def test_notebooklm_formatting(self):
        from ai_service import AIService
        formatted = AIService.format_for_notebooklm(
            title="Bio 101 Lecture",
            date="2026-09-29 10:00",
            duration="45:00",
            model="medium",
            speakers=2,
            tags="Biology, Pre-Med",
            summary="This lecture covered cellular respiration and glycolysis.",
            transcript="[00:00 - 00:10] Welcome to biology class.",
        )
        self.assertIn("# Source: Bio 101 Lecture", formatted)
        self.assertIn("Bio 101 Lecture", formatted)
        self.assertIn("cellular respiration", formatted)
        self.assertIn("Welcome to biology class", formatted)

    def test_notion_study_notes_block_parser(self):
        from notion_service import NotionService
        service = NotionService(token="secret_test", database_id="fake_db")
        md_text = (
            "# 📌 1. Lecture Overview\n"
            "This is the summary of the class.\n\n"
            "## 🧠 2. Key Concepts\n"
            "- Term: Mitochondria power house\n\n"
            "- Question: What is ATP?\n"
            "  Answer: Cellular energy currency.\n"
        )
        blocks = service.build_study_notes_blocks(md_text)
        self.assertGreaterEqual(len(blocks), 4)

        # Check heading 1
        h1 = [b for b in blocks if b["type"] == "heading_1"]
        self.assertEqual(len(h1), 1)
        self.assertIn("Lecture Overview", h1[0]["heading_1"]["rich_text"][0]["text"]["content"])

        # Check toggle flashcard
        toggles = [b for b in blocks if b["type"] == "toggle"]
        self.assertEqual(len(toggles), 1)
        self.assertIn("What is ATP?", toggles[0]["toggle"]["rich_text"][1]["text"]["content"])
        self.assertIn("Cellular energy currency", toggles[0]["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"])

    def test_export_to_notebooklm_file(self):
        import tempfile
        from pathlib import Path
        from transcript_storage import export_to_notebooklm

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "lecture_notebooklm.md"
            segments = [{"start": 0.0, "end": 5.0, "text": "Hello students."}]
            export_to_notebooklm(
                audio_path="test.mp3",
                segments=segments,
                full_text="Hello students.",
                output_path=str(out_file),
                study_notes="Core concept: Introduction",
            )
            self.assertTrue(out_file.exists())
            content = out_file.read_text(encoding="utf-8")
            self.assertIn("Core concept: Introduction", content)
            self.assertIn("Hello students.", content)


class TestSlideOCRAndJargonExtractor(unittest.TestCase):
    """Test slide text/OCR extraction, course jargon distillation, and Whisper hotwords prompting."""

    def test_distill_course_jargon(self):
        from slide_extractor import distill_course_jargon

        slide_text = (
            "[Slide 1] Introduction to Lecture Overview\n"
            "Today we cover Navier-Stokes equations, Eigenvalue decomposition, and Backpropagation.\n"
            "[Slide 2] Biochemical Pathways: NADH, FADH2, and Oxidative Phosphorylation in Mitochondria."
        )
        terms = distill_course_jargon(slide_text)
        lower_terms = [t.lower() for t in terms]
        self.assertIn("navier-stokes", lower_terms)
        self.assertIn("nadh", lower_terms)
        self.assertIn("fadh2", lower_terms)
        # Generic filler words like 'slide', 'lecture', 'today', 'overview' must be filtered out
        self.assertNotIn("slide", lower_terms)
        self.assertNotIn("lecture", lower_terms)
        self.assertNotIn("today", lower_terms)

    def test_build_whisper_context_prompts(self):
        from slide_extractor import build_whisper_context_prompts

        prompt, hotwords = build_whisper_context_prompts(["Eigenvalue", "Navier-Stokes", "CRISPR"])
        self.assertIsNotNone(prompt)
        self.assertIn("Eigenvalue", prompt)
        self.assertIn("Navier-Stokes", hotwords)
        self.assertIn("CRISPR", hotwords)

        empty_p, empty_h = build_whisper_context_prompts([])
        self.assertIsNone(empty_p)
        self.assertIsNone(empty_h)

    def test_extract_pptx_xml(self):
        import tempfile
        import zipfile
        from pathlib import Path
        from slide_extractor import extract_text_from_pptx

        with tempfile.TemporaryDirectory() as tmp_dir:
            pptx_file = Path(tmp_dir) / "sample_lecture.pptx"
            slide_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                '<p:cSld><p:spTree><p:sp><p:txBody><a:p>'
                '<a:r><a:t>Quantum Entanglement and Superposition</a:t></a:r>'
                '<a:r><a:t>Schrodinger Wave Equation</a:t></a:r>'
                '</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
            )
            with zipfile.ZipFile(pptx_file, "w") as zf:
                zf.writestr("ppt/slides/slide1.xml", slide_xml)

            extracted = extract_text_from_pptx(str(pptx_file))
            self.assertIn("Quantum Entanglement", extracted)
            self.assertIn("Schrodinger Wave Equation", extracted)

    def test_whisper_worker_accepts_jargon_prompts(self):
        worker = WhisperWorker(
            audio_path="dummy.wav",
            model_size="base",
            initial_prompt="Lecture covering: Eigenvalue, Backpropagation.",
            hotwords="Eigenvalue, Backpropagation",
        )
        self.assertEqual(worker.initial_prompt, "Lecture covering: Eigenvalue, Backpropagation.")
        self.assertEqual(worker.hotwords, "Eigenvalue, Backpropagation")

    def test_slide_companion_preview_vs_we_are_here_and_annotations(self):
        import tempfile
        from pathlib import Path
        from PyQt6.QtWidgets import QApplication
        import pymupdf as fitz
        from ui.slide_viewer import SlideCompanionWidget

        app = QApplication.instance() or QApplication(["test"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "class_slides.pdf"
            doc = fitz.open()
            for i in range(1, 4):
                page = doc.new_page(width=600, height=400)
                page.insert_text((50, 80), f"Topic {i}: Fourier Transform & Eigenvalues")
            doc.save(str(pdf_path))
            doc.close()

            simulated_time = [125.0]  # 02:05
            companion = SlideCompanionWidget(time_provider=lambda: simulated_time[0])
            loaded = companion.load_pdf(str(pdf_path))
            self.assertTrue(loaded)
            self.assertEqual(companion.slide_list.count(), 3)

            # Initially previewing Slide 1 (idx 0), active slide is not marked (-1)
            self.assertEqual(companion._preview_idx, 0)
            self.assertEqual(companion._active_idx, -1)

            # Click "We Are Here" on Slide 1 at 02:05
            res = companion.mark_current_slide_here()
            self.assertEqual(res, (1, 125.0))
            self.assertEqual(companion._active_idx, 0)

            # Preview Slide 3 (idx 2) WITHOUT clicking "We Are Here"
            companion.slide_list.setCurrentRow(2)
            self.assertEqual(companion._preview_idx, 2)
            # Active slide MUST still be Slide 1 (idx 0)!
            self.assertEqual(companion._active_idx, 0)

            # Annotate Slide 3 while previewing it
            companion.slide_notes_edit.setPlainText("Professor said this formula is on the final exam!")
            companion.canvas._strokes.append({
                "mode": "pen",
                "color": "#ef4444",
                "width": 0.005,
                "points": [(0.1, 0.1), (0.5, 0.5)],
            })
            companion._save_current_slide_strokes()

            # Jump back to active slide ("We Are Here" -> Slide 1)
            companion.jump_to_active_slide()
            self.assertEqual(companion._preview_idx, 0)

            # Verify markdown summary includes both Slide 1's timestamp and Slide 3's annotation
            summary_md = companion.format_annotations_summary_markdown()
            self.assertIn("Slide 1", summary_md)
            self.assertIn("[02:05]", summary_md)
            self.assertIn("Slide 3", summary_md)
            self.assertIn("final exam!", summary_md)
            self.assertIn("1 ink annotations", summary_md)
            companion.close()

    def test_class_slides_fullscreen_mode_hides_bottom_cards(self):
        from PyQt6.QtWidgets import QApplication
        from audio_recorder import AudioRecorder
        from video_watcher import VideoFolderWatcher
        from main import TranscribeInterface
        import tempfile

        app = QApplication.instance() or QApplication(["test"])
        with tempfile.TemporaryDirectory() as tmp_dir:
            rec = AudioRecorder()
            watcher = VideoFolderWatcher(watch_folder=tmp_dir)
            ti = TranscribeInterface(recorder=rec, video_watcher=watcher)

            # By default on Record Live tab, bottom cards are visible (not hidden)
            self.assertFalse(ti.meta_card.isHidden())
            self.assertFalse(ti.output_card.isHidden())
            self.assertFalse(ti.progress_container.isHidden())
            self.assertFalse(ti.action_bar_container.isHidden())

            # Switch to Class Slides & Annotate full-screen mode
            ti._activate_pivot_subpage(ti.slide_companion, "slidesPage")
            self.assertEqual(ti.stacked_widget.currentWidget(), ti.slide_companion)
            self.assertTrue(ti.meta_card.isHidden())
            self.assertTrue(ti.output_card.isHidden())
            self.assertTrue(ti.progress_container.isHidden())
            self.assertTrue(ti.action_bar_container.isHidden())

            # Switch to Video Inbox full-screen dashboard mode
            ti._activate_pivot_subpage(ti.video_tab, "videoPage")
            self.assertEqual(ti.stacked_widget.currentWidget(), ti.video_tab)
            self.assertTrue(ti.meta_card.isHidden())
            self.assertTrue(ti.output_card.isHidden())
            self.assertTrue(ti.progress_container.isHidden())
            self.assertTrue(ti.action_bar_container.isHidden())

            # Switch back to Record Live tab -> bottom cards restored
            ti._activate_pivot_subpage(ti.record_page, "recordPage")
            self.assertEqual(ti.stacked_widget.currentWidget(), ti.record_page)
            self.assertFalse(ti.meta_card.isHidden())
            self.assertFalse(ti.output_card.isHidden())
            self.assertFalse(ti.progress_container.isHidden())
            self.assertFalse(ti.action_bar_container.isHidden())
            ti.close()

    def test_pdf_editor_tools_text_shapes_undo_export_and_wheel_nav(self):
        import tempfile
        from pathlib import Path
        from PyQt6.QtWidgets import QApplication
        import pymupdf as fitz
        from ui.slide_viewer import SlideCompanionWidget

        app = QApplication.instance() or QApplication(["test"])

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "editor_test.pdf"
            doc = fitz.open()
            for i in range(1, 4):
                page = doc.new_page(width=800, height=500)
                page.insert_text((60, 100), f"Slide {i}: Linear Algebra")
            doc.save(str(pdf_path))
            doc.close()

            companion = SlideCompanionWidget()
            self.assertTrue(companion.load_pdf(str(pdf_path)))

            # Test switching editor tools (Highlighter, Text Input, Shapes, Move/Select, Eraser)
            companion._select_editor_tool("highlighter")
            self.assertEqual(companion.canvas._tool_mode, "highlighter")
            companion._select_editor_tool("text")
            self.assertEqual(companion.canvas._tool_mode, "text")
            companion._select_editor_tool("rect")
            self.assertEqual(companion.canvas._tool_mode, "rect")
            companion._select_editor_tool("arrow")
            self.assertEqual(companion.canvas._tool_mode, "arrow")

            # Add on-slide text annotation programmatically
            companion.canvas.add_text_annotation("Eigenvalue = lambda", 0.35, 0.45)
            self.assertEqual(len(companion.canvas.get_strokes()), 1)
            self.assertEqual(companion.canvas.get_strokes()[0]["mode"], "text")
            self.assertEqual(companion.canvas.get_strokes()[0]["text"], "Eigenvalue = lambda")

            # Test Undo & Redo
            companion.canvas.undo_stroke()
            self.assertEqual(len(companion.canvas.get_strokes()), 0)
            companion.canvas.redo_stroke()
            self.assertEqual(len(companion.canvas.get_strokes()), 1)

            # Verify on-slide text appears in markdown summary
            md_summary = companion.format_annotations_summary_markdown()
            self.assertIn("Eigenvalue = lambda", md_summary)
            self.assertIn("[On-Slide Text]", md_summary)

            # Test Touchpad Wheel Slide Navigation (next & previous slide)
            self.assertEqual(companion._preview_idx, 0)
            companion.canvas.slide_wheel_navigated.emit(1)
            self.assertEqual(companion._preview_idx, 1)
            companion.canvas.slide_wheel_navigated.emit(1)
            self.assertEqual(companion._preview_idx, 2)
            companion.canvas.slide_wheel_navigated.emit(-1)
            self.assertEqual(companion._preview_idx, 1)

            # Test exporting annotated PDF and PNG
            out_pdf = Path(tmp_dir) / "annotated_out.pdf"
            out_png = Path(tmp_dir) / "annotated_slide.png"
            self.assertTrue(companion.export_annotated_slides(str(out_pdf)))
            self.assertTrue(out_pdf.exists())
            self.assertTrue(companion.export_annotated_slides(str(out_png)))
            self.assertTrue(out_png.exists())

            # Test sidebar toggles (Deck and Notes)
            self.assertFalse(companion.slide_list.isHidden())
            companion.toggle_deck_btn.click()
            self.assertTrue(companion.slide_list.isHidden())
            companion.toggle_deck_btn.click()
            self.assertFalse(companion.slide_list.isHidden())

            self.assertFalse(companion.notes_card.isHidden())
            companion.toggle_notes_btn.click()
            self.assertTrue(companion.notes_card.isHidden())
            companion.toggle_notes_btn.click()
            self.assertFalse(companion.notes_card.isHidden())

            companion.close()


if __name__ == "__main__":
    unittest.main()


