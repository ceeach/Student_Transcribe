"""Hardware detection, benchmarking, and auto-tuning module for NotionTranscribe."""

import os
import sys
import time
import subprocess
import numpy as np
from typing import Dict, Any, List, Optional
from PyQt6.QtCore import QThread, pyqtSignal

import ctranslate2
from faster_whisper import WhisperModel


def get_hardware_specs() -> Dict[str, Any]:
    """Inspect system hardware: GPU name, total VRAM, free VRAM, and CPU cores."""
    specs: Dict[str, Any] = {
        "has_cuda": False,
        "gpu_name": "None (CPU Only)",
        "vram_total_mb": 0.0,
        "vram_free_mb": 0.0,
        "cpu_count": os.cpu_count() or 4,
        "recommended_diarization_threads": min(8, max(2, (os.cpu_count() or 4) // 2)),
    }

    try:
        cuda_count = ctranslate2.get_cuda_device_count()
        if cuda_count > 0:
            specs["has_cuda"] = True
    except Exception:
        pass

    # Query nvidia-smi for precise VRAM figures if available
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            parts = [p.strip() for p in proc.stdout.strip().split(",")]
            if len(parts) >= 3:
                specs["gpu_name"] = parts[0]
                specs["vram_total_mb"] = float(parts[1])
                specs["vram_free_mb"] = float(parts[2])
                specs["has_cuda"] = True
    except Exception:
        pass

    return specs


def generate_benchmark_audio(duration_sec: float = 5.0, sample_rate: int = 16000) -> np.ndarray:
    """Generate harmonic test audio simulating human speech formants."""
    num_samples = int(duration_sec * sample_rate)
    t = np.linspace(0, duration_sec, num_samples, endpoint=False)
    # Fundamental frequency + formant harmonics
    audio = (
        0.5 * np.sin(2 * np.pi * 220 * t)
        + 0.3 * np.sin(2 * np.pi * 440 * t)
        + 0.2 * np.sin(2 * np.pi * 880 * t)
    )
    # Apply envelope to avoid start/end clicks
    fade_len = int(0.05 * sample_rate)
    envelope = np.ones(num_samples, dtype=np.float32)
    envelope[:fade_len] = np.linspace(0, 1, fade_len)
    envelope[-fade_len:] = np.linspace(1, 0, fade_len)
    return (audio * envelope).astype(np.float32)


def evaluate_model_configuration(
    model_size: str,
    device: str,
    compute_type: str,
    beam_size: int = 1,
    audio_duration_sec: float = 5.0,
    test_audio: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """Benchmark a single model configuration and record speed and safety."""
    if test_audio is None:
        test_audio = generate_benchmark_audio(audio_duration_sec)

    specs = get_hardware_specs()
    vram_free_before = specs.get("vram_free_mb", 0.0)

    # Approximate model memory footprint in MB
    model_vram_footprints = {
        "tiny": 600,
        "base": 800,
        "small": 1400,
        "medium": 2400,
        "large-v3": 3600,
    }
    estimated_vram_needed = model_vram_footprints.get(model_size, 2000)

    # VRAM Safety evaluation
    if device == "cuda" and specs["has_cuda"]:
        if vram_free_before > 0:
            if vram_free_before < estimated_vram_needed * 0.9:
                vram_safety = "RISK"
                vram_note = f"Risk of PCIe paging (Needs ~{estimated_vram_needed}MB, Free: {int(vram_free_before)}MB)"
            elif vram_free_before < estimated_vram_needed * 1.3:
                vram_safety = "TIGHT"
                vram_note = f"VRAM Tight (~{int(vram_free_before)}MB free)"
            else:
                vram_safety = "SAFE"
                vram_note = f"VRAM Safe ({int(vram_free_before)}MB free)"
        else:
            vram_safety = "SAFE"
            vram_note = "CUDA Active"
    else:
        vram_safety = "SAFE"
        vram_note = "Runs on CPU RAM"

    t_load_start = time.time()
    try:
        model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )
        load_time = round(time.time() - t_load_start, 2)

        t_trans_start = time.time()
        list(
            model.transcribe(
                test_audio,
                beam_size=beam_size,
                vad_filter=True,
                word_timestamps=False,
            )[0]
        )
        transcribe_time = max(0.01, time.time() - t_trans_start)
        speedup_factor = round(audio_duration_sec / transcribe_time, 1)

        # Estimated time for a 1-hour audio recording in minutes
        est_1hr_minutes = round((3600.0 / speedup_factor) / 60.0, 1)

        return {
            "model": model_size,
            "device": device,
            "compute_type": compute_type,
            "beam_size": beam_size,
            "load_time_sec": load_time,
            "transcribe_time_sec": round(transcribe_time, 2),
            "speedup_factor": speedup_factor,
            "est_1hr_minutes": est_1hr_minutes,
            "vram_needed_mb": estimated_vram_needed,
            "vram_safety": vram_safety,
            "vram_note": vram_note,
            "success": True,
            "error": None,
        }
    except Exception as e:
        return {
            "model": model_size,
            "device": device,
            "compute_type": compute_type,
            "beam_size": beam_size,
            "load_time_sec": 0.0,
            "transcribe_time_sec": 0.0,
            "speedup_factor": 0.0,
            "est_1hr_minutes": 999.0,
            "vram_needed_mb": estimated_vram_needed,
            "vram_safety": "FAILED",
            "vram_note": str(e),
            "success": False,
            "error": str(e),
        }


def recommend_optimal_settings(
    benchmark_results: List[Dict[str, Any]],
    preference: str = "balanced",
) -> Dict[str, Any]:
    """Select the best configuration according to user preference."""
    valid_results = [r for r in benchmark_results if r.get("success")]
    if not valid_results:
        return {
            "whisper_model": "base",
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 1,
            "rationale": "Fallback default settings (no successful benchmarks).",
        }

    specs = get_hardware_specs()

    if preference == "max_accuracy":
        # Prefer highest parameter model that is SAFE or TIGHT
        safe_candidates = [r for r in valid_results if r["vram_safety"] in ("SAFE", "TIGHT")]
        order = ["large-v3", "medium", "small", "base", "tiny"]
        for m in order:
            match = next((r for r in safe_candidates if r["model"] == m), None)
            if match:
                return {
                    "whisper_model": match["model"],
                    "device": match["device"],
                    "compute_type": match["compute_type"],
                    "beam_size": match["beam_size"],
                    "rationale": (
                        f"Maximum accuracy configuration: '{match['model']}' ({match['speedup_factor']}x realtime, "
                        f"~{match['est_1hr_minutes']} mins for 1h audio)."
                    ),
                }

    elif preference == "max_speed":
        # Pick the fastest configuration
        fastest = max(valid_results, key=lambda r: r["speedup_factor"])
        return {
            "whisper_model": fastest["model"],
            "device": fastest["device"],
            "compute_type": fastest["compute_type"],
            "beam_size": fastest["beam_size"],
            "rationale": (
                f"Maximum speed configuration: '{fastest['model']}' ({fastest['speedup_factor']}x realtime, "
                f"~{fastest['est_1hr_minutes']} mins for 1h audio)."
            ),
        }

    # Default: "balanced"
    # Prioritizes medium or small with VRAM safety and 10x+ speed
    balanced_order = ["medium", "small", "large-v3", "base", "tiny"]
    safe_candidates = [r for r in valid_results if r["vram_safety"] == "SAFE"]
    if not safe_candidates:
        safe_candidates = [r for r in valid_results if r["vram_safety"] != "RISK"]

    for m in balanced_order:
        match = next((r for r in safe_candidates if r["model"] == m), None)
        if match:
            return {
                "whisper_model": match["model"],
                "device": match["device"],
                "compute_type": match["compute_type"],
                "beam_size": match["beam_size"],
                "rationale": (
                    f"Balanced optimal choice: '{match['model']}' ({match['speedup_factor']}x realtime, "
                    f"~{match['est_1hr_minutes']} mins for 1h audio, 100% VRAM safe)."
                ),
            }

    # Final fallback
    best = valid_results[0]
    return {
        "whisper_model": best["model"],
        "device": best["device"],
        "compute_type": best["compute_type"],
        "beam_size": best["beam_size"],
        "rationale": f"Auto-selected '{best['model']}'.",
    }


class BenchmarkWorker(QThread):
    """Background worker to benchmark hardware and auto-tune NotionTranscribe."""

    progress_updated = pyqtSignal(int, str)
    config_evaluated = pyqtSignal(dict)
    benchmark_finished = pyqtSignal(list, dict)

    def __init__(self, preference: str = "balanced", parent: Optional[Any] = None):
        super().__init__(parent)
        self.preference = preference
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        self.progress_updated.emit(5, "Detecting GPU and CPU hardware capabilities...")
        specs = get_hardware_specs()

        has_cuda = specs["has_cuda"]
        pref_device = "cuda" if has_cuda else "cpu"
        pref_compute = "float16" if has_cuda else "int8"

        candidate_models = ["base", "small", "medium", "large-v3"]
        if not has_cuda:
            candidate_models = ["tiny", "base", "small"]

        total_steps = len(candidate_models)
        results: List[Dict[str, Any]] = []

        test_audio = generate_benchmark_audio(5.0)

        for idx, model_name in enumerate(candidate_models):
            if self._is_cancelled:
                return

            pct = int(10 + (idx / total_steps) * 80)
            self.progress_updated.emit(pct, f"Benchmarking '{model_name}' on {pref_device.upper()}...")

            res = evaluate_model_configuration(
                model_size=model_name,
                device=pref_device,
                compute_type=pref_compute,
                beam_size=1,
                audio_duration_sec=5.0,
                test_audio=test_audio,
            )
            results.append(res)
            self.config_evaluated.emit(res)

        self.progress_updated.emit(95, "Calculating optimal recommendation...")
        recommendation = recommend_optimal_settings(results, preference=self.preference)
        recommendation["diarization_threads"] = specs["recommended_diarization_threads"]

        self.progress_updated.emit(100, "Hardware benchmark complete!")
        self.benchmark_finished.emit(results, recommendation)
