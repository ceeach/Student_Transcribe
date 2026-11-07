"""Document to PDF Conversion Engine for NotionTranscribe.

Supports converting PowerPoint presentations (.ppt, .pptx) and Word documents (.doc, .docx)
into PDFs suitable for slide viewing, annotation, and OCR jargon extraction.

Utilizes:
1. Native Windows COM automation via Microsoft Office (PowerPoint & Word) for 100% pixel-perfect conversion.
2. Headless LibreOffice CLI fallback (`soffice --headless --convert-to pdf`) if available.
3. Persistent caching in ~/.notion_transcribe/converted_slides/ to avoid reconverting unchanged files.
"""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from logger import get_logger

logger = get_logger("DocumentConverter")

CACHE_DIR = Path.home() / ".notion_transcribe" / "converted_slides"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED_PPT_EXTENSIONS = {".ppt", ".pptx"}
SUPPORTED_WORD_EXTENSIONS = {".doc", ".docx"}
SUPPORTED_DOCUMENT_EXTENSIONS = SUPPORTED_PPT_EXTENSIONS | SUPPORTED_WORD_EXTENSIONS
SUPPORTED_SLIDE_EXTENSIONS = {".pdf"} | SUPPORTED_DOCUMENT_EXTENSIONS


def is_convertible_document(file_path: str) -> bool:
    """Return True if the file extension is a PowerPoint or Word document."""
    if not file_path:
        return False
    ext = Path(file_path).suffix.lower()
    return ext in SUPPORTED_DOCUMENT_EXTENSIONS


def get_cached_pdf_path(input_path: str) -> Path:
    """Generate a stable, cached PDF file path based on input path and file modification time."""
    p = Path(input_path).resolve()
    stat = p.stat() if p.exists() else None
    mtime = stat.st_mtime if stat else 0
    size = stat.st_size if stat else 0
    hash_str = hashlib.md5(f"{p}_{mtime}_{size}".encode("utf-8")).hexdigest()[:10]
    safe_stem = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in p.stem).strip()
    return CACHE_DIR / f"{safe_stem}_{hash_str}.pdf"


def _find_soffice() -> Optional[str]:
    """Locate LibreOffice soffice executable if present."""
    which_soffice = shutil.which("soffice")
    if which_soffice:
        return which_soffice
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _convert_with_libreoffice(input_path: str, output_dir: str) -> Optional[str]:
    """Convert a document to PDF using headless LibreOffice."""
    soffice = _find_soffice()
    if not soffice:
        return None
    try:
        cmd = [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            input_path,
            "--outdir",
            output_dir,
        ]
        logger.info(f"Running LibreOffice conversion: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if res.returncode == 0:
            expected_pdf = Path(output_dir) / f"{Path(input_path).stem}.pdf"
            if expected_pdf.exists():
                return str(expected_pdf)
    except Exception as e:
        logger.warning(f"LibreOffice conversion failed: {e}")
    return None


def convert_powerpoint_to_pdf(input_path: str, output_path: str) -> str:
    """Convert .ppt or .pptx to .pdf using Microsoft PowerPoint COM automation."""
    abs_input = str(Path(input_path).resolve())
    abs_output = str(Path(output_path).resolve())
    Path(abs_output).parent.mkdir(parents=True, exist_ok=True)

    # 1. Try Microsoft PowerPoint COM
    com_error = None
    try:
        import win32com.client
        import pythoncom

        pythoncom.CoInitialize()
        try:
            ppt_app = win32com.client.DispatchEx("PowerPoint.Application")
            # Format 32 = ppSaveAsPDF
            # WithWindow=False runs quietly in background
            try:
                presentation = ppt_app.Presentations.Open(
                    abs_input,
                    ReadOnly=True,
                    Untitled=False,
                    WithWindow=False,
                )
                presentation.SaveAs(abs_output, 32)
                presentation.Close()
                logger.info(f"PowerPoint COM successfully converted {input_path} -> {output_path}")
                return abs_output
            finally:
                try:
                    ppt_app.Quit()
                except Exception:
                    pass
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        com_error = e
        logger.warning(f"PowerPoint COM conversion failed: {e}")

    # 2. Fallback to LibreOffice
    lo_pdf = _convert_with_libreoffice(abs_input, str(Path(abs_output).parent))
    if lo_pdf and os.path.exists(lo_pdf):
        if str(Path(lo_pdf).resolve()) != abs_output:
            shutil.move(lo_pdf, abs_output)
        return abs_output

    raise RuntimeError(
        f"Could not convert PowerPoint presentation to PDF. "
        f"Microsoft PowerPoint COM error: {com_error}. LibreOffice was not found."
    )


def convert_word_to_pdf(input_path: str, output_path: str) -> str:
    """Convert .doc or .docx to .pdf using Microsoft Word COM automation."""
    abs_input = str(Path(input_path).resolve())
    abs_output = str(Path(output_path).resolve())
    Path(abs_output).parent.mkdir(parents=True, exist_ok=True)

    # 1. Try Microsoft Word COM
    com_error = None
    try:
        import win32com.client
        import pythoncom

        pythoncom.CoInitialize()
        try:
            word_app = win32com.client.DispatchEx("Word.Application")
            word_app.Visible = False
            # Format 17 = wdFormatPDF
            try:
                doc = word_app.Documents.Open(abs_input, ReadOnly=True, ConfirmConversions=False)
                doc.SaveAs2(abs_output, FileFormat=17)
                doc.Close(False)
                logger.info(f"Word COM successfully converted {input_path} -> {output_path}")
                return abs_output
            finally:
                try:
                    word_app.Quit()
                except Exception:
                    pass
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        com_error = e
        logger.warning(f"Word COM conversion failed: {e}")

    # 2. Fallback to LibreOffice
    lo_pdf = _convert_with_libreoffice(abs_input, str(Path(abs_output).parent))
    if lo_pdf and os.path.exists(lo_pdf):
        if str(Path(lo_pdf).resolve()) != abs_output:
            shutil.move(lo_pdf, abs_output)
        return abs_output

    raise RuntimeError(
        f"Could not convert Word document to PDF. "
        f"Microsoft Word COM error: {com_error}. LibreOffice was not found."
    )


def convert_to_pdf(input_path: str, output_path: Optional[str] = None) -> str:
    """Convert PowerPoint or Word document to PDF.
    
    If output_path is not specified, uses the persistent cache directory.
    If cached PDF exists and is newer than input_path, returns cached PDF immediately.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Source document not found: {input_path}")

    ext = Path(input_path).suffix.lower()
    if ext == ".pdf":
        return str(Path(input_path).resolve())

    target_pdf = Path(output_path) if output_path else get_cached_pdf_path(input_path)

    # Cache hit check
    if target_pdf.exists() and target_pdf.stat().st_size > 0:
        if target_pdf.stat().st_mtime >= Path(input_path).stat().st_mtime:
            logger.info(f"Using cached PDF for {input_path}: {target_pdf}")
            return str(target_pdf.resolve())

    if ext in SUPPORTED_PPT_EXTENSIONS:
        return convert_powerpoint_to_pdf(input_path, str(target_pdf))
    elif ext in SUPPORTED_WORD_EXTENSIONS:
        return convert_word_to_pdf(input_path, str(target_pdf))
    else:
        raise ValueError(f"Unsupported document format for conversion: {ext}")


class DocumentConvertWorker(QThread):
    """Background worker thread for non-blocking document to PDF conversion."""

    started_converting = pyqtSignal(str)              # input_path
    conversion_finished = pyqtSignal(str, str)        # (input_path, converted_pdf_path)
    conversion_error = pyqtSignal(str, str)           # (input_path, error_message)

    def __init__(self, input_path: str, output_path: Optional[str] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path

    def run(self):
        try:
            self.started_converting.emit(self.input_path)
            pdf_path = convert_to_pdf(self.input_path, self.output_path)
            self.conversion_finished.emit(self.input_path, pdf_path)
        except Exception as e:
            logger.exception(f"Document conversion worker failed for {self.input_path}: {e}")
            self.conversion_error.emit(self.input_path, str(e))
