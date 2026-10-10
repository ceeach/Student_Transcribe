"""Centralized Logging and Crash Diagnostic System for NotionTranscribe.

Provides file-based rotating logging, safe stdout/stderr capture (preventing
Windows GUI PyInstaller console=False crashes), uncaught exception hooking,
and system environment diagnostics.
"""

from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback

LOGS_DIR = Path.home() / ".notion_transcribe" / "logs"
LOG_FILE = LOGS_DIR / "app.log"
CRASH_LOG_FILE = LOGS_DIR / "crash.log"

_is_initialized = False


class SafeStreamToLogger:
    """Fake stream that redirects write() and flush() calls to a logger.

    Prevents Windows PyInstaller GUI crashes where sys.stdout or sys.stderr is None.
    """

    def __init__(self, logger: logging.Logger, log_level: int):
        self.logger = logger
        self.log_level = log_level
        self._buffer = ""

    def write(self, buf: str):
        if not buf:
            return
        self._buffer += buf
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip("\r")
            if line:
                self.logger.log(self.log_level, line)

    def flush(self):
        if self._buffer.strip():
            self.logger.log(self.log_level, self._buffer.strip())
            self._buffer = ""

    def isatty(self):
        return False


def setup_logging() -> logging.Logger:
    """Initialize rotating file and console logging, and install crash handlers."""
    global _is_initialized
    root_logger = logging.getLogger("NotionTranscribe")
    if _is_initialized:
        return root_logger

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    # 1. Rotating File Handler (10MB max, keep 5 backups)
    file_handler = RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 2. Console Handler (only if stdout is actually available)
    if sys.stdout is not None and hasattr(sys.stdout, "write"):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # 3. Safe redirect of sys.stdout and sys.stderr
    sys.stdout = SafeStreamToLogger(root_logger, logging.INFO)
    sys.stderr = SafeStreamToLogger(root_logger, logging.ERROR)

    # 4. Uncaught Exception Hook
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        root_logger.critical(f"FATAL UNCAUGHT EXCEPTION:\n{err_msg}")

        # Also write to crash.log
        try:
            with open(CRASH_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*70}\n")
                f.write(f"CRASH TIMESTAMP: {datetime.now().isoformat()}\n")
                f.write(f"PLATFORM: {platform.platform()} | Python {platform.python_version()}\n")
                f.write(err_msg)
                f.write(f"{'='*70}\n")
        except Exception:
            pass

    sys.excepthook = handle_exception

    # 5. Log Environment Diagnostics on Startup
    root_logger.info("=" * 70)
    root_logger.info("NotionTranscribe Session Starting")
    root_logger.info(f"OS: {platform.system()} {platform.release()} ({platform.version()}) - Arch: {platform.machine()}")
    root_logger.info(f"Python: {platform.python_version()} ({sys.executable})")
    root_logger.info(f"Frozen executable: {getattr(sys, 'frozen', False)}")
    root_logger.info(f"Logs path: {LOG_FILE}")

    # Check CUDA support safely
    try:
        import ctranslate2
        cuda_types = ctranslate2.get_supported_compute_types("cuda")
        root_logger.info(f"ctranslate2 CUDA supported compute types: {cuda_types}")
    except Exception as ce:
        root_logger.info(f"ctranslate2 CUDA check result: {ce}")

    root_logger.info("=" * 70)

    _is_initialized = True
    return root_logger


def get_logger(name: str = "NotionTranscribe") -> logging.Logger:
    """Return a logger child instance."""
    if not _is_initialized:
        setup_logging()
    return logging.getLogger(f"NotionTranscribe.{name}")


def get_log_file_path() -> Path:
    """Return the absolute path to the current app.log file."""
    return LOG_FILE


def open_log_file() -> bool:
    """Open the log file using the default OS application (Notepad, etc.)."""
    if not LOG_FILE.exists():
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        LOG_FILE.touch()

    try:
        if sys.platform == "win32":
            os.startfile(str(LOG_FILE))
            return True
        elif sys.platform == "darwin":
            subprocess.run(["open", str(LOG_FILE)], check=True)
            return True
        else:
            subprocess.run(["xdg-open", str(LOG_FILE)], check=True)
            return True
    except Exception as e:
        logger = get_logger("System")
        logger.error(f"Failed to open log file {LOG_FILE}: {e}")
        return False
