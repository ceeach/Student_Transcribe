"""Semantic Auto-Slide Matcher & Lecture Timeline Alignment for Student Transcribe.

Matches spoken lecture text against imported PDF slide decks using:
- Technical jargon & keyword extraction with stopword filtering
- Weighted title and technical term matching
- Sequential continuity priors (professors usually progress linearly)
- Monotonic timeline alignment to map full lecture transcripts to slides
"""

from collections import Counter
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from logger import get_logger
from slide_extractor import COMMON_STOPWORDS
from transcriber import format_timestamp

logger = get_logger("SlideMatcher")


def tokenize_text(text: str) -> List[str]:
    """Tokenize text into cleaned lowercase alphanumeric terms (minimum length 2)."""
    if not text:
        return []
    cleaned = re.sub(r"[^\w\s-]", " ", text.lower())
    words = [w.strip("-") for w in cleaned.split() if len(w.strip("-")) >= 2]
    return words


def extract_content_keywords(text: str) -> Set[str]:
    """Extract content terms excluding common stopwords."""
    words = tokenize_text(text)
    return {w for w in words if w not in COMMON_STOPWORDS and not w.isdigit()}


class SlideMatcher:
    """Pre-indexes slide texts and computes semantic matches against spoken words."""

    def __init__(self, slide_texts: List[str], slide_titles: Optional[List[str]] = None):
        self.slide_texts = slide_texts or []
        self.slide_titles = slide_titles or [f"Slide {i + 1}" for i in range(len(self.slide_texts))]
        self._indexed_slides: List[Dict[str, Any]] = []
        self._build_index()

    def _build_index(self):
        """Build weighted keyword maps for each slide."""
        self._indexed_slides = []
        all_doc_terms: List[Set[str]] = []

        for idx, text in enumerate(self.slide_texts):
            title = self.slide_titles[idx] if idx < len(self.slide_titles) else ""
            title_terms = extract_content_keywords(title)
            body_terms = extract_content_keywords(text)

            # Combined terms for IDF calculation
            all_doc_terms.append(title_terms | body_terms)

        total_slides = max(1, len(self.slide_texts))

        for idx, text in enumerate(self.slide_texts):
            title = self.slide_titles[idx] if idx < len(self.slide_titles) else ""
            title_terms = extract_content_keywords(title)
            body_terms = extract_content_keywords(text)

            term_weights: Dict[str, float] = {}

            for term in body_terms:
                # IDF weighting: terms appearing on fewer slides carry higher discriminative power
                df = sum(1 for doc in all_doc_terms if term in doc)
                idf = math.log((total_slides + 1) / (df + 1)) + 1.0
                term_weights[term] = idf

            for term in title_terms:
                df = sum(1 for doc in all_doc_terms if term in doc)
                idf = math.log((total_slides + 1) / (df + 1)) + 1.0
                # Title terms have a 2.5x multiplier
                term_weights[term] = term_weights.get(term, 0.0) + (idf * 2.5)

            total_weight = sum(term_weights.values()) or 1.0

            self._indexed_slides.append({
                "slide_idx": idx,
                "title": title or f"Slide {idx + 1}",
                "weights": term_weights,
                "total_weight": total_weight,
            })

    def match_speech(
        self,
        spoken_text: str,
        current_slide_idx: int = -1,
        min_confidence: float = 0.35,
        forward_only: bool = True,
    ) -> Optional[Tuple[int, float, List[str]]]:
        """Score spoken text against slides.

        When forward_only is True (default):
            - Strongly focuses on moving to the next future slide (idx > current_slide_idx).
            - Strictly skips older/previous slides to prevent recommending already covered material.
            - Prioritizes the immediate next slide (current_slide_idx + 1).

        Returns:
            Tuple of (matched_slide_idx, confidence, matched_keywords) or None if below min_confidence.
        """
        if not spoken_text or not self._indexed_slides:
            return None

        speech_terms = extract_content_keywords(spoken_text)
        if not speech_terms:
            return None

        candidates: List[Tuple[int, float, List[str]]] = []

        for item in self._indexed_slides:
            idx = item["slide_idx"]

            # If Forward-Only is active, strictly skip any previous slides
            if forward_only and current_slide_idx >= 0 and idx < current_slide_idx:
                continue

            weights = item["weights"]
            total_w = item["total_weight"]

            matching_terms = [t for t in speech_terms if t in weights]
            if not matching_terms:
                continue

            # Reject weak single-term coincidences unless the term has high discriminatory weight (> 2.4)
            max_term_w = max(weights[t] for t in matching_terms)
            if len(matching_terms) < 2 and max_term_w < 2.4:
                continue

            matched_weight = sum(weights[t] for t in matching_terms)
            # Base score normalized by slide complexity
            raw_score = matched_weight / math.sqrt(total_w * len(speech_terms) + 1.0)

            # Continuity Prior: Professors progress forward in logical order
            continuity_boost = 0.0
            if current_slide_idx >= 0:
                dist = idx - current_slide_idx
                if dist == 0:
                    continuity_boost = 0.15  # Currently on this slide (stays here unless next slide matches stronger)
                elif dist == 1:
                    continuity_boost = 0.28  # Primary target: immediate next slide
                elif dist == 2:
                    continuity_boost = 0.10  # Two slides ahead
                elif dist > 2:
                    continuity_boost = -0.15  # Discourage sudden skipping far ahead
                elif dist == -1:
                    continuity_boost = -0.12 if not forward_only else 0.0
                elif dist < -1:
                    continuity_boost = -0.25 if not forward_only else 0.0

            final_score = raw_score + continuity_boost
            if final_score > 0:
                candidates.append((idx, final_score, matching_terms))

        if not candidates:
            return None

        # Sort descending by final score
        candidates.sort(key=lambda x: x[1], reverse=True)
        best_idx, best_score, best_terms = candidates[0]

        # Convert score to an intuitive confidence percentage [0.0 - 1.0]
        confidence = min(0.99, max(0.10, best_score / (best_score + 0.6)))

        if confidence >= min_confidence:
            return best_idx, confidence, best_terms

        return None

    def align_transcript_timeline(
        self,
        segments: List[Dict[str, Any]],
        window_seconds: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """Align an entire lecture's transcript segments chronologically with slides.

        Returns a timeline list:
        [
            {
                "slide_idx": int,
                "title": str,
                "start_time": float,
                "end_time": float,
                "duration": float,
                "text_excerpt": str,
                "keywords": List[str]
            }, ...
        ]
        """
        if not segments or not self._indexed_slides:
            return []

        # 1. Group segments into ~window_seconds buckets for stable matching
        chunks: List[Dict[str, Any]] = []
        current_chunk_text = []
        chunk_start = segments[0].get("start", 0.0)
        chunk_end = chunk_start

        for seg in segments:
            current_chunk_text.append(seg.get("text", ""))
            chunk_end = seg.get("end", chunk_end)

            if chunk_end - chunk_start >= window_seconds:
                combined_txt = " ".join(current_chunk_text).strip()
                chunks.append({
                    "start": chunk_start,
                    "end": chunk_end,
                    "text": combined_txt,
                })
                current_chunk_text = []
                chunk_start = chunk_end

        if current_chunk_text:
            chunks.append({
                "start": chunk_start,
                "end": chunk_end,
                "text": " ".join(current_chunk_text).strip(),
            })

        # 2. Sequential forward alignment with hysteresis
        timeline: List[Dict[str, Any]] = []
        active_slide = 0
        last_recorded_slide = -1

        for c in chunks:
            match = self.match_speech(c["text"], current_slide_idx=active_slide, min_confidence=0.25)
            if match:
                matched_slide, conf, terms = match
                # Only transition if match is sufficiently confident
                if conf >= 0.35 or matched_slide == active_slide:
                    active_slide = matched_slide

            title = self.slide_titles[active_slide] if active_slide < len(self.slide_titles) else f"Slide {active_slide + 1}"

            if not timeline or timeline[-1]["slide_idx"] != active_slide:
                timeline.append({
                    "slide_idx": active_slide,
                    "title": title,
                    "start_time": c["start"],
                    "end_time": c["end"],
                    "duration": c["end"] - c["start"],
                    "text_excerpt": c["text"][:160],
                    "keywords": terms if match else [],
                })
            else:
                # Extend current slide entry
                timeline[-1]["end_time"] = c["end"]
                timeline[-1]["duration"] = timeline[-1]["end_time"] - timeline[-1]["start_time"]

        return timeline
