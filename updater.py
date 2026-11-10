"""Application Updater and GitHub Release Integration for Student Transcribe.

Provides:
- Asynchronous GitHub Release checking
- Semantic version comparison
- Background installer download with progress reporting
- 1-click in-place update execution
- Bug reporting URL generation with pre-populated diagnostic context
"""

import os
import platform
import subprocess
import sys
import tempfile
from typing import Optional, Dict, Any
from urllib.request import Request, urlopen
import json

from PyQt6.QtCore import QObject, QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QProgressBar,
)
from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    PrimaryPushButton,
    PushButton,
    InfoBar,
    InfoBarPosition,
)

from logger import get_logger, LOG_FILE

logger = get_logger("Updater")

APP_VERSION = "1.4.3"
GITHUB_REPO = "ceeach/Student_Transcribe"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_ISSUES_URL = f"https://github.com/ceeach/Student_Transcribe/issues/new"


def parse_version(ver_str: str) -> tuple:
    """Convert version string (e.g. 'v1.4.1' or '1.4.0') to a tuple of integers for comparison."""
    cleaned = ver_str.strip().lstrip("vV")
    parts = []
    for part in cleaned.split("."):
        digits = "".join(filter(str.isdigit, part))
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def open_bug_report():
    """Open GitHub New Issue page with pre-populated system diagnostics in browser."""
    import urllib.parse
    import webbrowser

    diag_info = [
        f"**App Version**: Student Transcribe v{APP_VERSION}",
        f"**OS**: {platform.system()} {platform.release()} (Build {platform.version()})",
        f"**Python**: {sys.version.split()[0]} ({platform.architecture()[0]})",
    ]
    diag_body = "\n".join(diag_info)

    body = (
        "### Describe the Bug\n"
        "A clear and concise description of what happened.\n\n"
        "### Steps to Reproduce\n"
        "1. Start recording with...\n"
        "2. Click on...\n"
        "3. See error...\n\n"
        "### Expected Behavior\n"
        "What you expected to happen.\n\n"
        "### System Diagnostics\n"
        f"{diag_body}\n"
    )

    query = urllib.parse.urlencode({
        "title": "[Bug]: ",
        "body": body,
        "labels": "bug",
    })
    url = f"{GITHUB_ISSUES_URL}?{query}"
    webbrowser.open(url)


class CheckUpdateWorker(QThread):
    """Background worker to check for updates from GitHub Releases API."""

    update_available = pyqtSignal(dict)   # Emits release info dict
    up_to_date = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, current_version: str = APP_VERSION, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.current_version = current_version

    def run(self):
        try:
            req = Request(
                GITHUB_API_URL,
                headers={
                    "User-Agent": f"StudentTranscribe-Desktop/{self.current_version}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            with urlopen(req, timeout=6.0) as resp:
                if resp.status != 200:
                    self.error_occurred.emit(f"HTTP {resp.status}")
                    return
                data = json.loads(resp.read().decode("utf-8"))

            latest_tag = data.get("tag_name", "")
            if not latest_tag:
                self.up_to_date.emit()
                return

            latest_tuple = parse_version(latest_tag)
            current_tuple = parse_version(self.current_version)

            if latest_tuple > current_tuple:
                # Find the Windows setup .exe asset
                download_url = ""
                asset_size = 0
                for asset in data.get("assets", []):
                    name = asset.get("name", "").lower()
                    if name.endswith(".exe") and ("setup" in name or "transcribe" in name):
                        download_url = asset.get("browser_download_url", "")
                        asset_size = asset.get("size", 0)
                        break

                # Fallback to first .exe asset if no setup-named asset found
                if not download_url:
                    for asset in data.get("assets", []):
                        if asset.get("name", "").lower().endswith(".exe"):
                            download_url = asset.get("browser_download_url", "")
                            asset_size = asset.get("size", 0)
                            break

                release_info = {
                    "tag_name": latest_tag,
                    "title": data.get("name") or latest_tag,
                    "body": data.get("body", "No release notes provided."),
                    "html_url": data.get("html_url", ""),
                    "download_url": download_url,
                    "asset_size": asset_size,
                }
                self.update_available.emit(release_info)
            else:
                self.up_to_date.emit()

        except Exception as e:
            logger.debug(f"Update check completed without new release: {e}")
            self.error_occurred.emit(str(e))


class DownloadWorker(QThread):
    """Downloads updated setup executable with progress percentage."""

    progress = pyqtSignal(int)          # 0 - 100%
    finished = pyqtSignal(str)          # Downloaded file path
    error = pyqtSignal(str)

    def __init__(self, download_url: str, tag_name: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.download_url = download_url
        self.tag_name = tag_name
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            temp_dir = tempfile.gettempdir()
            clean_tag = self.tag_name.replace("/", "_")
            dest_path = os.path.join(temp_dir, f"StudentTranscribe-Setup-{clean_tag}.exe")

            req = Request(
                self.download_url,
                headers={"User-Agent": f"StudentTranscribe-Updater/{APP_VERSION}"},
            )

            with urlopen(req, timeout=15.0) as resp:
                total_size = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 64 * 1024

                with open(dest_path, "wb") as f:
                    while not self._is_cancelled:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            pct = int((downloaded / total_size) * 100)
                            self.progress.emit(min(100, pct))

            if self._is_cancelled:
                try:
                    if os.path.exists(dest_path):
                        os.remove(dest_path)
                except Exception:
                    pass
                return

            self.progress.emit(100)
            self.finished.emit(dest_path)

        except Exception as e:
            logger.error(f"Failed to download update: {e}")
            self.error.emit(str(e))


class UpdateDialog(MessageBoxBase):
    """Modern Fluent Dialog displaying release info and providing 1-click update."""

    def __init__(self, release_info: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.release_info = release_info
        self.download_worker: Optional[DownloadWorker] = None

        self.title_label = SubtitleLabel(f"🚀 New Update Available: {release_info['tag_name']}", self)
        self.viewLayout.addWidget(self.title_label)

        curr_label = CaptionLabel(f"Installed Version: v{APP_VERSION}  •  Latest Release: {release_info['tag_name']}", self)
        self.viewLayout.addWidget(curr_label)

        # Release notes summary
        notes_box = BodyLabel(self)
        notes_text = release_info.get("body", "").strip() or "Various performance improvements and bug fixes."
        # Truncate if very long
        if len(notes_text) > 400:
            notes_text = notes_text[:400] + "..."
        notes_box.setText(f"Release Highlights:\n{notes_text}")
        notes_box.setWordWrap(True)
        notes_box.setStyleSheet("background-color: rgba(255, 255, 255, 0.04); padding: 10px; border-radius: 6px;")
        self.viewLayout.addWidget(notes_box)

        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.viewLayout.addWidget(self.progress_bar)

        self.progress_status = CaptionLabel("", self)
        self.progress_status.setVisible(False)
        self.viewLayout.addWidget(self.progress_status)

        # Customize buttons
        if release_info.get("download_url"):
            self.yesButton.setText("⚡ Download & Install Update")
        else:
            self.yesButton.setText("🌐 View on GitHub")
        self.cancelButton.setText("Later")

        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._on_action_clicked)

    def _on_action_clicked(self):
        download_url = self.release_info.get("download_url")
        if not download_url:
            import webbrowser
            webbrowser.open(self.release_info.get("html_url", f"https://github.com/{GITHUB_REPO}/releases"))
            self.accept()
            return

        # Start downloading
        self.yesButton.setEnabled(False)
        self.yesButton.setText("Downloading...")
        self.progress_bar.setVisible(True)
        self.progress_status.setVisible(True)
        self.progress_status.setText("Connecting to GitHub...")

        self.download_worker = DownloadWorker(download_url, self.release_info["tag_name"], parent=self)
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.finished.connect(self._on_download_finished)
        self.download_worker.error.connect(self._on_download_error)
        self.download_worker.start()

    def _on_download_progress(self, pct: int):
        self.progress_bar.setValue(pct)
        self.progress_status.setText(f"Downloading update: {pct}% complete...")

    def _on_download_finished(self, setup_path: str):
        self.progress_status.setText("Download complete! Launching installer...")
        try:
            # Launch setup in a separate detached process and exit this app so the installer can update files
            if sys.platform == "win32":
                subprocess.Popen([setup_path, "/SILENT"], close_fds=True)
                sys.exit(0)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", setup_path])
                self.accept()
        except Exception as e:
            self.progress_status.setText(f"Error launching installer: {e}")
            self.yesButton.setEnabled(True)

    def _on_download_error(self, err_msg: str):
        self.progress_status.setText(f"Download error: {err_msg}")
        self.yesButton.setEnabled(True)
        self.yesButton.setText("Retry Download")
