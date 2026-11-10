"""Gemini Live Audio Transcriber for NotionTranscribe.

Provides real-time bidirectional streaming speech-to-text using Google Gemini's Live API
(BidiGenerateContent via WebSockets).
Automatically activates during live recording whenever an internet connection is available,
streaming live transcript segments, speaker badges, and timestamps with sub-second latency
and zero local GPU overhead.
"""

import asyncio
import base64
import json
import socket
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from logger import get_logger
from transcriber import format_timestamp

logger = get_logger("GeminiLive")


def is_internet_available(timeout: float = 1.2) -> bool:
    """Test internet connectivity via fast socket connection."""
    targets = [
        ("8.8.8.8", 53),
        ("1.1.1.1", 53),
        ("generativelanguage.googleapis.com", 443),
    ]
    for host, port in targets:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            continue
    return False


class GeminiLiveTranscriber(QObject):
    """Real-time streaming speech-to-text transcriber powered by Google Gemini Live WebSocket API."""

    segment_received = pyqtSignal(dict)             # {"start": float, "end": float, "text": str, "timestamp_str": str, "speaker": str}
    interim_received = pyqtSignal(str)              # Live streaming interim text (sub-second words)
    transcription_completed = pyqtSignal(list, str)   # (segments_list, full_text)
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        api_key: str,
        model: str = "Gemini 3.5 Transcribe Live",
        sample_rate: int = 16000,
        chunk_duration_sec: float = 0.5,
        language: str = "en",
        jargon_prompt: str = "",
        slide_context: str = "",
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.api_key = api_key.strip()
        self.display_model = model.strip() or "Gemini 3.5 Transcribe Live"
        self.model = self._slugify_model(self.display_model)
        self.sample_rate = sample_rate
        self.language = (language or "en").strip().lower()
        self.jargon_prompt = jargon_prompt.strip()
        self.slide_context = slide_context.strip()

        # State tracking
        self.segments: List[Dict[str, Any]] = []
        self._is_running = True
        self._accepting_chunks = True
        self._latest_interim = ""
        self._current_ts = 0.0
        self._last_seg_ts = 0.0

        # Background asyncio thread & queue
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._ws = None

        self._thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._thread.start()

    @staticmethod
    def _slugify_model(name: str) -> str:
        s = name.strip()
        if not s:
            return "gemini-3.5-transcribe-live"
        return s.lower().replace(" ", "-")

    def _build_system_prompt(self) -> str:
        parts = [
            "You are a verbatim real-time speech-to-text transcriber for academic lectures and meetings.",
            "Transcribe all user spoken words verbatim with accurate punctuation.",
            "Do not add any conversational responses, notes, summaries, or introductions.",
        ]

        LANG_NAMES = {
            "en": "English",
            "de": "German",
            "fr": "French",
            "es": "Spanish",
            "it": "Italian",
            "nl": "Dutch",
            "pt": "Portuguese",
            "zh": "Chinese (Mandarin)",
            "ja": "Japanese",
            "ar": "Arabic",
            "ru": "Russian",
            "ko": "Korean",
        }
        lang_code = getattr(self, "language", "en")
        if lang_code and lang_code != "auto":
            target_lang = LANG_NAMES.get(lang_code, lang_code.capitalize())
            parts.append(
                f"Target Language: The lecture is delivered in {target_lang}. "
                f"Transcribe strictly and exclusively in {target_lang}. "
                f"Do NOT switch languages or misinterpret accented speech, technical terms, or foreign names into other languages."
            )
        else:
            parts.append("Language: Transcribe speech in whatever natural language is being spoken.")

        if self.jargon_prompt:
            parts.append(f"Course Vocabulary / Technical Terms: {self.jargon_prompt}")
        if self.slide_context:
            parts.append(f"Current Slide Topic: {self.slide_context}")
        return "\n".join(parts)

    def update_context(
        self,
        jargon: Optional[str] = None,
        slide_context: Optional[str] = None,
        language: Optional[str] = None,
    ):
        """Update live technical context so Gemini accurately transcribes current slide concepts."""
        if jargon is not None:
            self.jargon_prompt = jargon.strip()
        if slide_context is not None:
            self.slide_context = slide_context.strip()
        if language is not None:
            self.language = language.strip().lower()

    def process_audio_chunk(self, samples: np.ndarray, timestamp_sec: float):
        """Queue raw audio chunk from AudioRecorder for immediate streaming."""
        try:
            if not getattr(self, "_is_running", False) or not getattr(self, "_accepting_chunks", True) or samples is None or len(samples) == 0:
                return

            self._current_ts = max(getattr(self, "_current_ts", 0.0), float(timestamp_sec))

            # Low RMS threshold to keep streaming continuously without dropping soft speech
            rms = float(np.sqrt(np.mean(samples**2)))
            if rms < 0.0005:
                return

            # Convert float32 [-1.0, 1.0] to 16-bit signed PCM mono bytes
            int16_samples = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
            pcm_bytes = int16_samples.tobytes()
            b64_data = base64.b64encode(pcm_bytes).decode("utf-8")

            loop = getattr(self, "_loop", None)
            queue = getattr(self, "_queue", None)
            if loop and queue and not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, (b64_data, self._current_ts))
        except Exception as e:
            logger.debug(f"Process audio chunk ignored error: {e}")

    def _run_event_loop(self):
        """Dedicated thread running the asyncio WebSocket connection."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._queue = asyncio.Queue()

        try:
            self._loop.run_until_complete(self._websocket_session())
        except Exception as e:
            logger.warning(f"Gemini Live session finished: {e}")
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    async def _websocket_session(self):
        """Connect to Gemini Live API via WebSocket and handle streaming with automatic session rotation."""
        import websockets

        url = (
            f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
            f"?key={self.api_key}"
        )

        reconnect_attempts = 0
        max_reconnects = 5

        while self._is_running:
            ws = None
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=15) as websocket:
                    ws = websocket
                    self._ws = ws
                    reconnect_attempts = 0
                    logger.info(f"Connected to Gemini Live API WebSocket ({self.model})")
                    self.status_changed.emit(f"Connected to Gemini Live ({self.display_model})")

                    # Send initial setup configuration frame
                    setup_msg = {
                        "setup": {
                            "model": f"models/{self.model}",
                            "generationConfig": {
                                "responseModalities": ["TEXT"],
                            },
                            "systemInstruction": {
                                "parts": [
                                    {"text": self._build_system_prompt()}
                                ]
                            }
                        }
                    }
                    await ws.send(json.dumps(setup_msg))

                    # Wait for setup confirmation from server
                    setup_resp_raw = await ws.recv()
                    setup_resp = json.loads(setup_resp_raw)
                    if "setupComplete" not in setup_resp:
                        logger.warning(f"Unexpected setup response: {setup_resp}")

                    logger.info("Gemini Live session initialized successfully.")

                    # Concurrently stream audio and listen for transcriptions
                    sender_task = asyncio.create_task(self._audio_sender(ws))
                    receiver_task = asyncio.create_task(self._message_receiver(ws))

                    done, pending = await asyncio.wait(
                        [sender_task, receiver_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass

                    self._ws = None

            except websockets.exceptions.InvalidStatusCode as e:
                err_msg = f"Gemini Live connection refused (Status {e.status_code})"
                logger.error(err_msg)
                self.error_occurred.emit(err_msg)
                break
            except Exception as e:
                self._ws = None
                if not self._is_running:
                    break

                err_str = str(e)
                # Session rotation (GoAway / 1008 / connection closed by server)
                is_rotation = (
                    "GoAway" in err_str
                    or "1008" in err_str
                    or "1000" in err_str
                    or "session durat" in err_str.lower()
                    or "closed" in err_str.lower()
                )

                if is_rotation:
                    logger.info(f"Gemini Live session rotated smoothly (session duration reached): {e}")
                    reconnect_attempts = 0  # Expected session duration rotation, not an error
                else:
                    logger.warning(f"Gemini Live transient disconnect: {e}")
                    reconnect_attempts += 1

                if reconnect_attempts > max_reconnects:
                    err_msg = f"Gemini Live disconnected after {max_reconnects} reconnect attempts: {e}"
                    logger.error(err_msg)
                    self.error_occurred.emit(err_msg)
                    break

                self.status_changed.emit("Refreshing live transcription connection...")
                await asyncio.sleep(0.3)

    async def _audio_sender(self, ws):
        """Read queued PCM audio chunks and send to Gemini Live WebSocket."""
        while self._is_running:
            item = await self._queue.get()
            if item is None:  # Shutdown sentinel
                # Put back so finish() knows shutdown is requested
                self._queue.put_nowait(None)
                break

            b64_data, ts = item
            msg = {
                "realtimeInput": {
                    "mediaChunks": [
                        {
                            "mimeType": "audio/pcm;rate=16000",
                            "data": b64_data,
                        }
                    ]
                }
            }
            try:
                await ws.send(json.dumps(msg))
            except Exception as e:
                # If connection closed or rotating, re-queue the un-sent chunk
                if self._is_running and item is not None:
                    try:
                        self._queue.put_nowait(item)
                    except Exception:
                        pass
                logger.debug(f"Audio sender socket closed ({e}), rotating connection.")
                break

    async def _message_receiver(self, ws):
        """Listen for streaming text and transcription segments from Gemini."""
        try:
            async for raw_msg in ws:
                if not self._is_running:
                    break
                try:
                    data = json.loads(raw_msg)

                    # 0. Check for GoAway signal (Gemini Live session duration limit warning)
                    if "goAway" in data:
                        logger.info(f"Gemini Live received GoAway signal: {data.get('goAway')}. Seamlessly rotating session...")
                        try:
                            await ws.close(code=1000, reason="Rotating session after GoAway")
                        except Exception:
                            pass
                        break

                    sc = data.get("serverContent", {})

                    # 1. Real-time interim partial transcription (token-by-token sub-second stream)
                    if "interimInputTranscription" in sc:
                        interim_text = sc["interimInputTranscription"].get("text", "").strip()
                        if interim_text:
                            clean_interim = interim_text.replace('"', "").replace("`", "").strip()
                            self._latest_interim = clean_interim
                            self.interim_received.emit(clean_interim)

                    # 2. Native finalized input transcription from Gemini 3.5 Transcribe Live
                    elif "inputTranscription" in sc:
                        text = sc["inputTranscription"].get("text", "").strip()
                        if text:
                            self._latest_interim = ""
                            start_s = self._last_seg_ts
                            end_s = max(start_s + 0.8, self._current_ts)
                            self._last_seg_ts = end_s

                            clean_text = text.replace('"', "").replace("`", "").strip()
                            seg = {
                                "start": round(start_s, 2),
                                "end": round(end_s, 2),
                                "text": clean_text,
                                "timestamp_str": format_timestamp(start_s),
                                "speaker": "Speaker 1",
                            }
                            self.segments.append(seg)
                            self.segment_received.emit(seg)

                    # 3. General model turn parts (for text generation mode)
                    elif "modelTurn" in sc:
                        parts = sc["modelTurn"].get("parts", [])
                        for p in parts:
                            t = p.get("text", "").strip()
                            if t:
                                self._latest_interim = ""
                                start_s = self._last_seg_ts
                                end_s = max(start_s + 1.0, self._current_ts)
                                self._last_seg_ts = end_s
                                clean_text = t.replace('"', "").replace("`", "").strip()
                                seg = {
                                    "start": round(start_s, 2),
                                    "end": round(end_s, 2),
                                    "text": clean_text,
                                    "timestamp_str": format_timestamp(start_s),
                                    "speaker": "Speaker 1",
                                }
                                self.segments.append(seg)
                                self.segment_received.emit(seg)

                except Exception as e:
                    logger.warning(f"Error parsing Gemini Live message: {e}")
        except Exception as e:
            logger.debug(f"Message receiver socket ended: {e}")

    def finish(self):
        """Signal end of recording, flush audio queue, and emit full transcript."""
        if not self._is_running:
            return

        self._accepting_chunks = False

        if self._loop and self._queue and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

        # Allow brief time for remaining in-flight responses to settle without closing receiver
        for _ in range(8):
            if not self._is_running:
                break
            time.sleep(0.1)

        self._is_running = False

        if self._ws and self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(code=1000), self._loop)
            except Exception:
                pass

        # If there is remaining interim text that was never finalized, commit it into segments
        if self._latest_interim:
            start_s = self._last_seg_ts
            end_s = max(start_s + 0.5, self._current_ts)
            self._last_seg_ts = end_s
            seg = {
                "start": round(start_s, 2),
                "end": round(end_s, 2),
                "text": self._latest_interim,
                "timestamp_str": format_timestamp(start_s),
                "speaker": "Speaker 1",
            }
            self.segments.append(seg)
            self.segment_received.emit(seg)
            self._latest_interim = ""

        # Emit completion
        full_text = " ".join(s["text"] for s in self.segments).strip()
        self.transcription_completed.emit(self.segments, full_text)
        logger.info(f"Gemini Live transcription completed with {len(self.segments)} segments.")

    def cancel(self):
        """Immediately abort session without emitting completion."""
        self._is_running = False
        if self._ws and self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(code=1000), self._loop)
            except Exception:
                pass
        if self._loop and self._queue and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
