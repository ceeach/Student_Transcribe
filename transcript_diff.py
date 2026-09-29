"""Transcript Diff and Word-Level Alignment Engine for NotionTranscribe.

Uses difflib to align words between two Whisper transcripts, identify discrepancies,
extract surrounding context, calculate audio audition windows, and merge resolutions.
"""

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple


def clean_word(word: str) -> str:
    """Normalize a word for alignment comparison (lowercase, strip punctuation)."""
    return re.sub(r"[^\w\s]", "", word).strip().lower()


def extract_words_from_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten segment list into a flat list of word dictionaries with timing."""
    flat_words: List[Dict[str, Any]] = []

    for seg_idx, seg in enumerate(segments):
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", 0.0))
        raw_words = seg.get("words", [])

        if raw_words:
            for w in raw_words:
                w_text = w.get("word", "").strip()
                if not w_text:
                    continue
                flat_words.append({
                    "word": w_text,
                    "clean": clean_word(w_text),
                    "start": float(w.get("start", seg_start)),
                    "end": float(w.get("end", seg_end)),
                    "prob": float(w.get("prob", 1.0)),
                    "seg_idx": seg_idx,
                })
        else:
            # Fallback: split text by whitespace and interpolate timings
            tokens = seg.get("text", "").split()
            if not tokens:
                continue
            step = (seg_end - seg_start) / max(1, len(tokens))
            for i, token in enumerate(tokens):
                t_start = seg_start + i * step
                t_end = t_start + step
                flat_words.append({
                    "word": token,
                    "clean": clean_word(token),
                    "start": t_start,
                    "end": t_end,
                    "prob": 0.85,
                    "seg_idx": seg_idx,
                })

    return flat_words


class Discrepancy:
    """Represents a differing word or phrase between two models."""

    def __init__(
        self,
        diff_id: int,
        tag: str,
        text_a: str,
        text_b: str,
        start_sec: float,
        end_sec: float,
        context_before: str,
        context_after: str,
        prob_a: float = 0.0,
        prob_b: float = 0.0,
        a_range: Tuple[int, int] = (0, 0),
        b_range: Tuple[int, int] = (0, 0),
    ):
        self.diff_id = diff_id
        self.tag = tag                  # 'replace', 'delete', 'insert'
        self.text_a = text_a
        self.text_b = text_b
        self.start_sec = max(0.0, start_sec)
        self.end_sec = max(self.start_sec + 0.1, end_sec)
        self.context_before = context_before
        self.context_after = context_after
        self.prob_a = prob_a
        self.prob_b = prob_b
        self.a_range = a_range
        self.b_range = b_range

        # Default resolution: pick the one with higher confidence, or Model B
        if prob_b > prob_a and text_b:
            self.chosen_text = text_b
            self.chosen_source = "model_b"
        elif text_a:
            self.chosen_text = text_a
            self.chosen_source = "model_a"
        else:
            self.chosen_text = text_b
            self.chosen_source = "model_b"

    def set_choice(self, source: str, custom_text: Optional[str] = None):
        """Set user's decision ('model_a', 'model_b', or 'custom')."""
        self.chosen_source = source
        if source == "model_a":
            self.chosen_text = self.text_a
        elif source == "model_b":
            self.chosen_text = self.text_b
        elif source == "custom" and custom_text is not None:
            self.chosen_text = custom_text.strip()


class TranscriptDiffEngine:
    """Compares two transcripts and generates audio-aligned discrepancies."""

    def __init__(
        self,
        segments_a: List[Dict[str, Any]],
        segments_b: List[Dict[str, Any]],
        model_a_name: str = "Model A",
        model_b_name: str = "Model B",
    ):
        self.segments_a = segments_a
        self.segments_b = segments_b
        self.model_a_name = model_a_name
        self.model_b_name = model_b_name

        self.words_a = extract_words_from_segments(segments_a)
        self.words_b = extract_words_from_segments(segments_b)

        self.discrepancies: List[Discrepancy] = []
        self._align_and_diff()

    def _align_and_diff(self):
        """Align clean word tokens and identify discrepancy blocks."""
        tokens_a = [w["clean"] for w in self.words_a]
        tokens_b = [w["clean"] for w in self.words_b]

        matcher = difflib.SequenceMatcher(None, tokens_a, tokens_b)
        diff_id = 1

        for tag, a1, a2, b1, b2 in matcher.get_opcodes():
            if tag == "equal":
                continue

            # Text slices
            slice_a = self.words_a[a1:a2]
            slice_b = self.words_b[b1:b2]

            text_a = " ".join(w["word"] for w in slice_a)
            text_b = " ".join(w["word"] for w in slice_b)

            # Skip trivial identical texts that might only differ by whitespace
            if text_a.strip().lower() == text_b.strip().lower() and text_a.strip():
                continue

            # Calculate start and end audio timestamps
            start_sec = 0.0
            end_sec = 0.0

            if slice_a and slice_b:
                start_sec = min(slice_a[0]["start"], slice_b[0]["start"])
                end_sec = max(slice_a[-1]["end"], slice_b[-1]["end"])
            elif slice_a:
                start_sec = slice_a[0]["start"]
                end_sec = slice_a[-1]["end"]
            elif slice_b:
                start_sec = slice_b[0]["start"]
                end_sec = slice_b[-1]["end"]

            # Surrounding context (up to 4 words before and after)
            ctx_start = max(0, a1 - 4)
            ctx_end = min(len(self.words_a), a2 + 4)
            context_before = " ".join(w["word"] for w in self.words_a[ctx_start:a1])
            context_after = " ".join(w["word"] for w in self.words_a[a2:ctx_end])

            # Confidence scores
            prob_a = (
                sum(w["prob"] for w in slice_a) / len(slice_a) if slice_a else 0.0
            )
            prob_b = (
                sum(w["prob"] for w in slice_b) / len(slice_b) if slice_b else 0.0
            )

            disc = Discrepancy(
                diff_id=diff_id,
                tag=tag,
                text_a=text_a,
                text_b=text_b,
                start_sec=start_sec,
                end_sec=end_sec,
                context_before=context_before,
                context_after=context_after,
                prob_a=prob_a,
                prob_b=prob_b,
                a_range=(a1, a2),
                b_range=(b1, b2),
            )
            self.discrepancies.append(disc)
            diff_id += 1

    def merge(self) -> Tuple[List[Dict[str, Any]], str]:
        """Merge verified choices into a finalized segment list and full text."""
        # Use tokens_a as the base skeleton and replace discrepancy ranges
        # Map a_range to chosen text
        resolved_map: Dict[Tuple[int, int], str] = {
            d.a_range: d.chosen_text for d in self.discrepancies
        }

        # Build final words sequence
        final_words: List[str] = []
        i = 0
        total_a = len(self.words_a)

        # Sort discrepancies by a_range[0]
        sorted_diffs = sorted(self.discrepancies, key=lambda d: d.a_range[0])
        diff_idx = 0

        while i < total_a or diff_idx < len(sorted_diffs):
            if diff_idx < len(sorted_diffs):
                d = sorted_diffs[diff_idx]
                a1, a2 = d.a_range
                if i < a1:
                    final_words.append(self.words_a[i]["word"])
                    i += 1
                elif i == a1:
                    if d.chosen_text:
                        final_words.append(d.chosen_text)
                    i = max(i + 1, a2)
                    diff_idx += 1
                else:
                    diff_idx += 1
            else:
                if i < total_a:
                    final_words.append(self.words_a[i]["word"])
                    i += 1

        merged_text = " ".join(final_words)

        # Also rebuild segments with the original segment structure and timestamps
        merged_segments: List[Dict[str, Any]] = []
        # Fallback to segments_b if segments_a was empty
        base_segments = self.segments_a if self.segments_a else self.segments_b

        # We construct formatted segments
        if base_segments:
            # Reconstruct clean paragraphs from base segments
            curr_pos = 0
            for seg in base_segments:
                seg_word_count = len(seg.get("text", "").split())
                seg_words = final_words[curr_pos : curr_pos + seg_word_count]
                curr_pos += seg_word_count
                text_part = " ".join(seg_words) if seg_words else seg.get("text", "")
                merged_segments.append({
                    "start": seg.get("start", 0),
                    "end": seg.get("end", 0),
                    "text": text_part,
                })
            # Any trailing words
            if curr_pos < len(final_words):
                remaining = " ".join(final_words[curr_pos:])
                if merged_segments:
                    merged_segments[-1]["text"] += " " + remaining

        return merged_segments, merged_text
