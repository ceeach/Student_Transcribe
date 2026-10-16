"""Slide & Document Context Extractor for Transcribe Notion.

Extracts text and course jargon/vocabulary from lecture slides and documents:
- PDF files (.pdf): Digital text via PyMuPDF / pypdf + automatic Windows OCR fallback for scanned/image slides
- PowerPoint presentations (.pptx): Native XML slide text & speaker notes extraction
- Images & Screenshots (.png, .jpg, .jpeg, .bmp, .webp): Native Windows Media OCR + optional Gemini Vision
- Plain text / Markdown (.txt, .md): Direct ingestion

Distills unique technical terms, proper nouns, acronyms, and formulas to feed into
faster-whisper's `initial_prompt` and `hotwords` parameters for accurate domain transcription.
"""

from collections import Counter
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Set, Tuple
import zipfile
import xml.etree.ElementTree as ET

from PyQt6.QtCore import QThread, pyqtSignal

from logger import get_logger

logger = get_logger("SlideExtractor")

SUPPORTED_SLIDE_EXTENSIONS = {
    ".pdf", ".pptx", ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".txt", ".md"
}

# Common English stopwords & generic slide filler words to exclude from Whisper hotwords/jargon
COMMON_STOPWORDS: Set[str] = {
    "the", "and", "for", "that", "this", "with", "from", "your", "have", "more",
    "will", "can", "are", "was", "were", "been", "being", "has", "had", "does",
    "did", "doing", "about", "into", "through", "during", "before", "after",
    "above", "below", "between", "under", "again", "further", "then", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "both", "each",
    "few", "most", "other", "some", "such", "only", "own", "same", "than", "too",
    "very", "just", "should", "now", "also", "what", "which", "who", "whom",
    "these", "those", "they", "them", "their", "theirs", "our", "ours", "you",
    "your", "yours", "his", "her", "hers", "its", "but", "not", "nor", "yet",
    "slide", "lecture", "page", "overview", "introduction", "summary", "conclusion",
    "questions", "agenda", "today", "next", "week", "chapter", "section", "part",
    "example", "examples", "exercise", "exercises", "homework", "assignment",
    "university", "college", "course", "professor", "instructor", "dr", "mr", "ms",
    "figure", "table", "chart", "diagram", "graph", "note", "notes", "reference",
    "references", "source", "sources", "thank", "thanks", "welcome", "objectives",
    "learning", "outcomes", "goal", "goals", "topic", "topics", "review", "quiz",
    "exam", "midterm", "final", "grading", "syllabus", "schedule", "reading",
    "readings", "textbook", "edition", "copyright", "rights", "reserved", "http",
    "https", "www", "com", "org", "edu", "net", "pdf", "pptx", "using", "used",
    "use", "based", "different", "various", "following", "given", "show", "shows",
    "shown", "find", "found", "make", "made", "take", "taken", "know", "known",
    "called", "first", "second", "third", "one", "two", "three", "four", "five",
    "many", "much", "well", "good", "best", "better", "new", "old", "high", "low",
    "large", "small", "long", "short", "right", "left", "top", "bottom", "main",
    "key", "important", "general", "specific", "basic", "advanced", "simple",
    "complex", "common", "type", "types", "form", "forms", "way", "ways", "case",
    "cases", "point", "points", "time", "times", "year", "years", "day", "days",
    "work", "works", "working", "system", "systems", "process", "processes",
    "data", "information", "value", "values", "number", "numbers", "set", "sets",
    "group", "groups", "level", "levels", "order", "change", "changes", "result",
    "results", "effect", "effects", "state", "states", "area", "areas", "fact",
    "line", "lines", "side", "sides", "kind", "kinds", "need", "needs", "want",
    "like", "look", "see", "seen", "come", "comes", "going", "went", "get", "gets",
    "got", "give", "gives", "gave", "put", "puts", "keep", "keeps", "let", "lets",
    "seem", "seems", "help", "helps", "talk", "turn", "start", "might", "could",
    "would", "must", "shall", "may", "within", "without", "upon", "along", "across",
    "behind", "beyond", "near", "since", "until", "while", "although", "though",
    "whether", "because", "unless", "however", "therefore", "thus", "hence",
    "otherwise", "instead", "meanwhile", "furthermore", "moreover", "nevertheless",
    "always", "never", "often", "sometimes", "usually", "already", "still", "even",
    "almost", "enough", "quite", "rather", "really", "actually", "especially",
    "particularly", "generally", "usually", "typically", "ostęp", "possible",
    "available", "necessary", "able", "free", "true", "false", "real", "full",
    "sure", "clear", "certain", "likely", "current", "previous", "local", "global",
}


def run_windows_ocr_on_images(image_paths: List[str]) -> str:
    """Run native Windows.Media.Ocr on a batch of image files via PowerShell."""
    if sys.platform != "win32" or not image_paths:
        return ""

    valid_paths = [os.path.abspath(p) for p in image_paths if os.path.exists(p)]
    if not valid_paths:
        return ""

    # Build a temporary file listing all image paths (UTF-8) to avoid command-line escaping issues
    list_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as f:
            list_file = f.name
            for p in valid_paths:
                f.write(p + "\n")

        ps_script = f"""
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
}})[0]
Function Await($WinRtTask, $ResultType) {{
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}}
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {{
    exit 0
}}

$paths = Get-Content -LiteralPath '{list_file}' -Encoding UTF8
foreach ($p in $paths) {{
    if ([string]::IsNullOrWhiteSpace($p) -or -not (Test-Path -LiteralPath $p)) {{ continue }}
    try {{
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($p)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        foreach ($line in $result.Lines) {{
            Write-Output $line.Text
        }}
        Write-Output "---"
        $stream.Dispose()
    }} catch {{
        # Skip unreadable image
    }}
}}
"""
        creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            creationflags=creationflags,
        )
        return proc.stdout.strip()
    except Exception as e:
        logger.warning(f"Windows native OCR error: {e}")
        return ""
    finally:
        if list_file and os.path.exists(list_file):
            try:
                os.remove(list_file)
            except Exception:
                pass


def extract_text_from_pptx(pptx_path: str) -> str:
    """Extract all slide text and speaker notes from a .pptx file using pure zipfile + XML."""
    lines: List[str] = []
    try:
        with zipfile.ZipFile(pptx_path, "r") as zf:
            # Find slide XML files and sort numerically
            slide_files = [
                name for name in zf.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ]
            slide_files.sort(key=lambda x: int(re.sub(r"\D", "", x) or 0))

            for idx, slide_xml in enumerate(slide_files, start=1):
                xml_bytes = zf.read(slide_xml)
                root = ET.fromstring(xml_bytes)
                # Extract all <a:t> text elements
                slide_texts = []
                for elem in root.iter():
                    if elem.tag.endswith("}t") and elem.text:
                        t = elem.text.strip()
                        if t:
                            slide_texts.append(t)
                if slide_texts:
                    lines.append(f"[Slide {idx}] " + " | ".join(slide_texts))

            # Also extract speaker notes if available
            notes_files = [
                name for name in zf.namelist()
                if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml")
            ]
            notes_files.sort(key=lambda x: int(re.sub(r"\D", "", x) or 0))
            for notes_xml in notes_files:
                xml_bytes = zf.read(notes_xml)
                root = ET.fromstring(xml_bytes)
                note_texts = [
                    elem.text.strip()
                    for elem in root.iter()
                    if elem.tag.endswith("}t") and elem.text and elem.text.strip()
                ]
                if note_texts:
                    lines.append(" ".join(note_texts))
    except Exception as e:
        logger.error(f"Failed to extract PPTX text from {pptx_path}: {e}")
    return "\n".join(lines)


def extract_text_from_pdf(pdf_path: str, max_ocr_pages: int = 25) -> str:
    """Extract text from PDF using PyMuPDF (or pypdf) + Windows OCR for image-based slides."""
    extracted_pages: List[str] = []
    temp_ocr_images: List[str] = []

    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            fitz = None

    if fitz is not None:
        try:
            doc = fitz.open(pdf_path)
            ocr_page_indices: List[int] = []

            for page_idx in range(len(doc)):
                page = doc[page_idx]
                text = page.get_text("text").strip()
                if len(text) >= 25:
                    extracted_pages.append(f"[Slide {page_idx + 1}]\n{text}")
                else:
                    # Page has very little digital text — likely a scanned or image slide
                    if text:
                        extracted_pages.append(f"[Slide {page_idx + 1}]\n{text}")
                    if len(ocr_page_indices) < max_ocr_pages:
                        ocr_page_indices.append(page_idx)

            # Render image-heavy slides to temporary PNGs and run Windows OCR
            if ocr_page_indices and sys.platform == "win32":
                logger.info(f"Running OCR on {len(ocr_page_indices)} image-based PDF slide(s)...")
                for page_idx in ocr_page_indices:
                    try:
                        page = doc[page_idx]
                        pix = page.get_pixmap(dpi=150)
                        tmp_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                        tmp_img.close()
                        pix.save(tmp_img.name)
                        temp_ocr_images.append(tmp_img.name)
                    except Exception as render_err:
                        logger.warning(f"Could not render PDF page {page_idx + 1} for OCR: {render_err}")

                if temp_ocr_images:
                    ocr_text = run_windows_ocr_on_images(temp_ocr_images)
                    if ocr_text:
                        extracted_pages.append("[OCR Extracted Slide Text]\n" + ocr_text)

            doc.close()
            return "\n\n".join(extracted_pages)
        except Exception as e:
            logger.warning(f"PyMuPDF extraction failed on {pdf_path}, trying pypdf fallback: {e}")
        finally:
            for img_p in temp_ocr_images:
                try:
                    if os.path.exists(img_p):
                        os.remove(img_p)
                except Exception:
                    pass

    # Fallback to pypdf if PyMuPDF is unavailable
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        for i, page in enumerate(reader.pages):
            txt = (page.extract_text() or "").strip()
            if txt:
                extracted_pages.append(f"[Slide {i + 1}]\n{txt}")
    except Exception as e:
        logger.error(f"pypdf fallback also failed on {pdf_path}: {e}")

    return "\n\n".join(extracted_pages)


def distill_course_jargon(raw_text: str, max_terms: int = 80) -> List[str]:
    """Distill domain-specific jargon, technical vocabulary, acronyms, and proper nouns from slide text.

    Prioritizes:
    1. Acronyms & technical identifiers (e.g., NADH, CRISPR, O(n log n), TCP/IP, H2SO4, Eigenvalue)
    2. CamelCase / hyphenated technical compounds (e.g., Navier-Stokes, Backpropagation, GraphQL)
    3. Multi-word capitalized technical concepts (e.g., Fourier Transform, Dynamic Programming)
    4. High-value domain terms that are not in common everyday stopwords
    """
    if not raw_text or not raw_text.strip():
        return []

    # Clean slide markers
    cleaned = re.sub(r"\[Slide \d+\]", " ", raw_text)
    cleaned = re.sub(r"\[OCR Extracted Slide Text\]", " ", cleaned)

    scored_terms: Counter = Counter()
    canonical_casing: Dict[str, str] = {}

    def register_term(term: str, weight: int):
        t = term.strip(".,;:!?()[]{}\"'`~*•-–—/|\\")
        if len(t) < 2 or len(t) > 45:
            return
        # Skip pure numbers or dates
        if re.fullmatch(r"[\d\W]+", t):
            return
        lower = t.lower()
        if lower in COMMON_STOPWORDS:
            return
        # Check if all words in a multi-word phrase are stopwords
        words = lower.split()
        if all(w in COMMON_STOPWORDS or len(w) <= 2 for w in words):
            return

        scored_terms[lower] += weight
        # Prefer capitalized or mixed-case representation over all-lowercase
        if lower not in canonical_casing:
            canonical_casing[lower] = t
        else:
            existing = canonical_casing[lower]
            if existing.islower() and not t.islower():
                canonical_casing[lower] = t

    # 1. Extract Acronyms & Alphanumeric Technical Codes (e.g., DNA, mRNA, FADH2, H2SO4, SHA-256, GPT-4)
    for match in re.finditer(r"\b(?:[A-Z][A-Z0-9]{1,8}(?:-\d+)?|[a-z]+[A-Z][A-Za-z0-9]*|[A-Z][a-z]*\d+[A-Za-z0-9]*)\b", cleaned):
        token = match.group(0)
        if token.lower() not in COMMON_STOPWORDS:
            register_term(token, weight=6)

    # 2. Extract Hyphenated Technical Terms (e.g., Navier-Stokes, alfa-helix, cross-entropy)
    for match in re.finditer(r"\b[A-Za-z]{3,}-[A-Za-z]{3,}(?:-[A-Za-z]{2,})?\b", cleaned):
        register_term(match.group(0), weight=5)

    # 3. Extract Multi-Word Capitalized Phrases (2 words, e.g., "Gradient Descent", "Mitral Valve")
    for match in re.finditer(r"\b([A-Z][a-z]{2,}\s+[A-Z][a-z]{2,})\b", cleaned):
        phrase = match.group(1)
        w1, w2 = phrase.lower().split()
        if w1 not in COMMON_STOPWORDS and w2 not in COMMON_STOPWORDS:
            register_term(phrase, weight=4)

    # 4. Extract Single Technical / Academic Words (capitalized mid-sentence or longer domain words >= 6 chars)
    for match in re.finditer(r"\b[A-Za-z][a-z]{4,}\b", cleaned):
        word = match.group(0)
        lower = word.lower()
        if lower in COMMON_STOPWORDS:
            continue
        # Bonus weight for academic/scientific suffixes or longer technical words
        weight = 1
        if word[0].isupper():
            weight += 2
        if len(word) >= 8:
            weight += 2
        if lower.endswith((
            "tion", "sion", "ism", "ist", "logy", "graphy", "metry", "nomy",
            "phoresis", "synthesis", "lysis", "genesis", "morphism", "theorem",
            "matrix", "vector", "tensor", "function", "equation", "algorithm",
            "protocol", "catalyst", "enzyme", "polymer", "receptor", "kinase",
            "pathway", "circuit", "impedance", "frequency", "wavelength", "quantum",
        )):
            weight += 3
        register_term(word, weight=weight)

    # Sort by descending score, then length
    ranked_keys = sorted(
        scored_terms.keys(),
        key=lambda k: (scored_terms[k], len(k)),
        reverse=True,
    )

    result: List[str] = []
    seen_lowers: Set[str] = set()
    for k in ranked_keys:
        if k not in seen_lowers:
            seen_lowers.add(k)
            result.append(canonical_casing[k])
            if len(result) >= max_terms:
                break

    return result


def build_whisper_context_prompts(jargon_terms: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Build `(initial_prompt, hotwords)` strings for `faster-whisper` from extracted jargon terms.

    - `initial_prompt`: A concise natural sentence introducing the domain terminology (kept under ~220 tokens).
    - `hotwords`: Comma-separated list of key terms for ctranslate2's decoder biasing.
    """
    clean_terms = [t.strip() for t in jargon_terms if t and t.strip()]
    if not clean_terms:
        return None, None

    # Limit to top 60 terms so we never exceed Whisper's 224-token prompt context window
    selected = clean_terms[:60]
    terms_str = ", ".join(selected)
    initial_prompt = f"Lecture covering technical terminology including: {terms_str}."
    hotwords = ", ".join(selected)
    return initial_prompt, hotwords


def extract_slides_and_jargon(file_paths: List[str], max_terms: int = 80) -> Dict[str, object]:
    """Process one or more slide decks / images and return extracted text + distilled jargon list."""
    all_texts: List[str] = []
    image_batch: List[str] = []
    processed_files: List[str] = []

    for path in file_paths:
        if not path or not os.path.exists(path):
            continue
        ext = Path(path).suffix.lower()
        fname = os.path.basename(path)

        if ext == ".pdf":
            logger.info(f"Extracting slide context from PDF: {fname}")
            txt = extract_text_from_pdf(path)
            if txt:
                all_texts.append(f"### {fname}\n{txt}")
            processed_files.append(fname)
        elif ext == ".pptx":
            logger.info(f"Extracting slide context from PowerPoint: {fname}")
            txt = extract_text_from_pptx(path)
            if txt:
                all_texts.append(f"### {fname}\n{txt}")
            processed_files.append(fname)
        elif ext in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
            image_batch.append(path)
            processed_files.append(fname)
        elif ext in {".txt", ".md"}:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    txt = f.read().strip()
                if txt:
                    all_texts.append(f"### {fname}\n{txt}")
                processed_files.append(fname)
            except Exception as e:
                logger.warning(f"Failed to read text file {path}: {e}")

    if image_batch:
        logger.info(f"Running OCR on {len(image_batch)} slide image(s)...")
        ocr_out = run_windows_ocr_on_images(image_batch)
        if ocr_out:
            all_texts.append("### Slide Screenshots (OCR)\n" + ocr_out)

    combined_text = "\n\n".join(all_texts).strip()
    jargon_list = distill_course_jargon(combined_text, max_terms=max_terms)

    return {
        "files": processed_files,
        "raw_text": combined_text,
        "jargon_terms": jargon_list,
    }


class SlideExtractorWorker(QThread):
    """Background QThread for extracting text and OCR jargon from slides without freezing UI."""

    status_changed = pyqtSignal(str)
    extraction_completed = pyqtSignal(list, str, list)  # (jargon_terms, raw_slide_text, file_names)
    error_occurred = pyqtSignal(str)

    def __init__(self, file_paths: List[str], parent=None):
        super().__init__(parent)
        self.file_paths = file_paths

    def run(self):
        try:
            self.status_changed.emit("Extracting text & OCR jargon from lecture slides...")
            result = extract_slides_and_jargon(self.file_paths)
            jargon = result.get("jargon_terms", [])
            raw_text = result.get("raw_text", "")
            files = result.get("files", [])
            self.extraction_completed.emit(jargon, raw_text, files)
        except Exception as e:
            logger.exception(f"SlideExtractorWorker failed: {e}")
            self.error_occurred.emit(str(e))
