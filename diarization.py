"""Speaker Diarization module for NotionTranscribe.

Performs offline speaker segmentation, embedding extraction, and clustering
using sherpa-onnx and pre-trained ONNX models (pyannote-segmentation-3-0 and 3D-Speaker).
Assigns speaker labels ('Speaker 1', 'Speaker 2', etc.) to Whisper transcription segments.
"""

from pathlib import Path
import tarfile
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import urllib.request

import numpy as np

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    import sherpa_onnx
except ImportError:
    sherpa_onnx = None

from audio_recorder import to_mono_16k

DIARIZATION_MODELS_DIR = Path.home() / ".notion_transcribe" / "models" / "diarization"

SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/"
    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)


def get_segmentation_model_path() -> Path:
    return DIARIZATION_MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"


def get_embedding_model_path() -> Path:
    return DIARIZATION_MODELS_DIR / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"


def is_diarization_available() -> bool:
    """Check if sherpa-onnx library and required ONNX models are present on disk."""
    if sherpa_onnx is None:
        return False
    return get_segmentation_model_path().exists() and get_embedding_model_path().exists()


def download_diarization_models(
    progress_callback: Optional[Callable[[str, int], None]] = None
) -> Tuple[bool, str]:
    """Download and extract required ONNX diarization models (~44 MB total)."""
    try:
        DIARIZATION_MODELS_DIR.mkdir(parents=True, exist_ok=True)
        seg_model = get_segmentation_model_path()
        emb_model = get_embedding_model_path()

        # 1. Download & extract segmentation model (6.6 MB)
        if not seg_model.exists():
            if progress_callback:
                progress_callback("Downloading speaker segmentation model (6.6 MB)...", 10)
            tar_path = DIARIZATION_MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
            urllib.request.urlretrieve(SEGMENTATION_URL, tar_path)

            if progress_callback:
                progress_callback("Extracting segmentation model...", 30)
            with tarfile.open(tar_path, "r:bz2") as tar:
                tar.extractall(DIARIZATION_MODELS_DIR)
            if tar_path.exists():
                tar_path.unlink()

        # 2. Download speaker embedding model (37.8 MB)
        if not emb_model.exists():
            if progress_callback:
                progress_callback("Downloading speaker embedding model (37.8 MB)...", 50)
            urllib.request.urlretrieve(EMBEDDING_URL, emb_model)

        if progress_callback:
            progress_callback("Diarization models ready.", 100)
        return True, "Diarization models downloaded successfully."
    except Exception as e:
        return False, f"Failed to download diarization models: {e}"


def assign_speakers_to_segments(
    segments: List[Dict[str, Any]],
    diarization_turns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map diarization speaker turns to Whisper speech segments using temporal overlap.

    Args:
        segments: List of Whisper segments with 'start', 'end', and 'text'.
        diarization_turns: List of speaker intervals with 'start', 'end', 'speaker_id'.

    Returns:
        Updated segments list where each item contains 'speaker' (e.g. 'Speaker 1')
        and 'speaker_id' (int, 0-indexed).
    """
    if not segments:
        return []

    if not diarization_turns:
        for seg in segments:
            seg["speaker_id"] = 0
            seg["speaker"] = "Speaker 1"
        return segments

    for seg in segments:
        s_start = float(seg.get("start", 0.0))
        s_end = float(seg.get("end", s_start + 0.1))

        # Calculate overlap duration with each detected speaker
        overlap_by_speaker: Dict[int, float] = {}
        for turn in diarization_turns:
            t_start = float(turn.get("start", 0.0))
            t_end = float(turn.get("end", t_start + 0.1))

            overlap = max(0.0, min(s_end, t_end) - max(s_start, t_start))
            if overlap > 0.0:
                spk_id = int(turn.get("speaker_id", 0))
                overlap_by_speaker[spk_id] = overlap_by_speaker.get(spk_id, 0.0) + overlap

        if overlap_by_speaker:
            best_speaker_id = max(overlap_by_speaker.keys(), key=lambda k: overlap_by_speaker[k])
        else:
            # Fallback: select closest speaker turn by midpoint distance
            s_mid = (s_start + s_end) / 2.0
            closest_turn = min(
                diarization_turns,
                key=lambda t: abs(((float(t.get("start", 0.0)) + float(t.get("end", 0.0))) / 2.0) - s_mid),
            )
            best_speaker_id = int(closest_turn.get("speaker_id", 0))

        seg["speaker_id"] = best_speaker_id
        seg["speaker"] = f"Speaker {best_speaker_id + 1}"

    return segments


class SpeakerDiarizer:
    """Offline speaker diarization engine using sherpa-onnx."""

    def __init__(
        self,
        num_speakers: int = -1,
        threshold: float = 0.5,
        min_duration_on: float = 0.3,
        min_duration_off: float = 0.5,
    ):
        self.num_speakers = int(num_speakers)
        self.threshold = float(threshold)
        self.min_duration_on = float(min_duration_on)
        self.min_duration_off = float(min_duration_off)
        self._sd = None

    def _ensure_initialized(self):
        if self._sd is not None:
            return

        if not is_diarization_available():
            ok, msg = download_diarization_models()
            if not ok:
                raise RuntimeError(msg)

        seg_model = str(get_segmentation_model_path())
        emb_model = str(get_embedding_model_path())

        cpu_count = os.cpu_count() or 4
        optimal_threads = min(8, max(2, cpu_count // 2))

        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=seg_model,
                ),
                num_threads=optimal_threads,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=emb_model,
                num_threads=optimal_threads,
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=self.num_speakers,
                threshold=self.threshold,
            ),
            min_duration_on=self.min_duration_on,
            min_duration_off=self.min_duration_off,
        )

        if not config.validate():
            raise RuntimeError("Sherpa-onnx diarization config validation failed.")

        self._sd = sherpa_onnx.OfflineSpeakerDiarization(config)

    def diarize(
        self,
        audio_input: Union[str, np.ndarray],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Run speaker diarization on audio file path or float32 1D numpy array at 16kHz.

        Returns:
            List of turn dictionaries:
            [{'start': 0.5, 'end': 3.2, 'speaker_id': 0, 'speaker': 'Speaker 1'}, ...]
        """
        self._ensure_initialized()

        if isinstance(audio_input, str):
            if not sf:
                raise RuntimeError("soundfile is required to read audio files for diarization.")
            audio_data, sample_rate = sf.read(audio_input, dtype="float32", always_2d=True)
            audio = audio_data[:, 0]
            if sample_rate != 16000:
                audio = to_mono_16k(audio, sample_rate, 1)
        elif isinstance(audio_input, np.ndarray):
            audio = audio_input.astype(np.float32)
        else:
            raise ValueError(f"Unsupported audio input type: {type(audio_input)}")

        if len(audio) == 0:
            return []

        def _cb(processed: int, total: int) -> int:
            if progress_callback:
                progress_callback(processed, total)
            return 0

        raw_result = self._sd.process(audio, callback=_cb if progress_callback else None)
        sorted_result = raw_result.sort_by_start_time()

        turns: List[Dict[str, Any]] = []
        for r in sorted_result:
            turns.append({
                "start": round(float(r.start), 2),
                "end": round(float(r.end), 2),
                "speaker_id": int(r.speaker),
                "speaker": f"Speaker {int(r.speaker) + 1}",
            })

        return turns
