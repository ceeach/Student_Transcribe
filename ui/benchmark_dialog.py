"""Hardware Benchmark and Auto-Tuning Dialog for NotionTranscribe."""

from typing import Optional, Dict, Any, List
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidgetItem,
    QHeaderView,
    QWidget,
)

from qfluentwidgets import (
    TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel, StrongBodyLabel,
    PushButton, PrimaryPushButton,
    ProgressBar,
    TableWidget,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    setTheme,
    Theme
)

from benchmark import get_hardware_specs, BenchmarkWorker
from config_manager import config_manager


class BenchmarkDialog(QDialog):
    """Modal dialog to benchmark hardware and auto-tune NotionTranscribe."""

    settings_applied = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("⚡ Hardware Benchmark & Auto-Tune")
        self.resize(680, 600)
        self.setModal(True)
        setTheme(Theme.DARK)

        self._worker: Optional[BenchmarkWorker] = None
        self._specs = get_hardware_specs()
        self._latest_recommendation: Optional[Dict[str, Any]] = None

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        # 1. Header
        header = TitleLabel("⚡ Hardware Benchmark & Auto-Tune", self)
        layout.addWidget(header)

        sub_desc = BodyLabel(
            "Benchmark your GPU and CPU to automatically select the optimal Whisper model and "
            "compute settings for maximum performance without memory slowdowns.",
            self,
        )
        sub_desc.setWordWrap(True)
        layout.addWidget(sub_desc)

        # 2. Hardware Specs Card
        specs_box = CardWidget(self)
        specs_layout = QVBoxLayout(specs_box)
        specs_layout.setSpacing(6)
        
        specs_title = StrongBodyLabel("Detected Hardware", specs_box)
        specs_layout.addWidget(specs_title)

        gpu_text = f"🎮 GPU: {self._specs.get('gpu_name', 'None')}"
        if self._specs.get("has_cuda"):
            gpu_text += " (CUDA Acceleration Enabled ✓)"
        self.gpu_label = BodyLabel(gpu_text, self)
        specs_layout.addWidget(self.gpu_label)

        vram_total = int(self._specs.get("vram_total_mb", 0))
        vram_free = int(self._specs.get("vram_free_mb", 0))
        if vram_total > 0:
            vram_text = f"💾 VRAM: {vram_free} MB Free / {vram_total} MB Total"
        else:
            vram_text = "💾 VRAM: No dedicated GPU memory detected (using System RAM)"

        self.vram_label = BodyLabel(vram_text, self)
        specs_layout.addWidget(self.vram_label)

        cpu_cores = self._specs.get("cpu_count", 4)
        diar_threads = self._specs.get("recommended_diarization_threads", 4)
        self.cpu_label = BodyLabel(
            f"🧠 CPU: {cpu_cores} Logical Cores | Diarization Threads: {diar_threads}",
            self,
        )
        specs_layout.addWidget(self.cpu_label)

        layout.addWidget(specs_box)

        # 3. Preference & Run Bar
        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        pref_label = StrongBodyLabel("Tuning Goal:", self)
        action_row.addWidget(pref_label)

        self.pref_combo = ComboBox(self)
        self.pref_combo.addItem("🎯 Balanced (Recommended: High Accuracy & Speed)", "balanced")
        self.pref_combo.addItem("🏆 Maximum Accuracy (Largest Safe Model)", "max_accuracy")
        self.pref_combo.addItem("🚀 Maximum Speed (Fastest Turnaround)", "max_speed")
        action_row.addWidget(self.pref_combo, 1)

        self.run_btn = PushButton("▶ Run Benchmark", self)
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.clicked.connect(self._start_benchmark)
        action_row.addWidget(self.run_btn)

        layout.addLayout(action_row)

        # 4. Progress Bar & Status Label
        self.progress_bar = ProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = CaptionLabel("Ready to benchmark system performance.", self)
        layout.addWidget(self.status_label)

        # 5. Results Table
        self.table = TableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Model",
            "Speedup",
            "Est. 1h Audio Time",
            "VRAM Needed",
            "Safety Status",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        # 6. Recommendation Banner Card
        self.rec_card = CardWidget(self)
        self.rec_card.setVisible(False)
        rec_layout = QVBoxLayout(self.rec_card)
        rec_layout.setSpacing(4)
        rec_layout.setContentsMargins(10, 8, 10, 8)

        self.rec_title = StrongBodyLabel("⚡ Recommended Configuration", self.rec_card)
        rec_layout.addWidget(self.rec_title)

        self.rec_desc = BodyLabel("", self.rec_card)
        self.rec_desc.setWordWrap(True)
        rec_layout.addWidget(self.rec_desc)

        layout.addWidget(self.rec_card)

        # 7. Bottom Dialog Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.apply_btn = PrimaryPushButton("⚡ Apply Recommended Settings", self)
        self.apply_btn.setEnabled(False)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.clicked.connect(self._apply_settings)
        btn_layout.addWidget(self.apply_btn)

        btn_layout.addStretch()

        close_btn = PushButton("Close", self)
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _start_benchmark(self):
        self.run_btn.setEnabled(False)
        self.pref_combo.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.table.setRowCount(0)
        self.rec_card.setVisible(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(5)

        preference = self.pref_combo.currentData() or "balanced"
        self._worker = BenchmarkWorker(preference=preference, parent=self)
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.config_evaluated.connect(self._on_config_evaluated)
        self._worker.benchmark_finished.connect(self._on_benchmark_finished)
        self._worker.start()

    def _on_progress(self, percent: int, message: str):
        self.progress_bar.setValue(percent)
        self.status_label.setText(message)

    def _on_config_evaluated(self, res: Dict[str, Any]):
        row = self.table.rowCount()
        self.table.insertRow(row)

        model_item = QTableWidgetItem(f"🎙 {res['model']}")
        model_item.setFont(self.font())
        self.table.setItem(row, 0, model_item)

        if res.get("success"):
            speed_str = f"⚡ {res['speedup_factor']}x realtime"
            est_time_str = f"⏱ ~{res['est_1hr_minutes']} mins"
            vram_str = f"~{res['vram_needed_mb']} MB"

            safety = res["vram_safety"]
            if safety == "SAFE":
                status_str = "✅ Safe (No Spilling)"
            elif safety == "TIGHT":
                status_str = "⚠️ VRAM Tight"
            else:
                status_str = "⛔ High Risk of Slowdown"
        else:
            speed_str = "—"
            est_time_str = "—"
            vram_str = f"~{res.get('vram_needed_mb', 0)} MB"
            status_str = f"❌ Failed: {res.get('error', '')}"

        self.table.setItem(row, 1, QTableWidgetItem(speed_str))
        self.table.setItem(row, 2, QTableWidgetItem(est_time_str))
        self.table.setItem(row, 3, QTableWidgetItem(vram_str))
        self.table.setItem(row, 4, QTableWidgetItem(status_str))

    def _on_benchmark_finished(self, results: List[Dict[str, Any]], recommendation: Dict[str, Any]):
        self.run_btn.setEnabled(True)
        self.pref_combo.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText("Benchmark complete!")

        self._latest_recommendation = recommendation
        self.apply_btn.setEnabled(True)

        best_model = recommendation.get("whisper_model", "")
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and best_model in item.text():
                for c in range(self.table.columnCount()):
                    cell = self.table.item(r, c)
                    if cell:
                        cell.setBackground(Qt.GlobalColor.darkGreen)

        self.rec_card.setVisible(True)
        self.rec_desc.setText(
            f"<b>Model:</b> {recommendation.get('whisper_model')} &nbsp;|&nbsp; "
            f"<b>Device:</b> {recommendation.get('device', '').upper()} &nbsp;|&nbsp; "
            f"<b>Compute Type:</b> {recommendation.get('compute_type')} &nbsp;|&nbsp; "
            f"<b>Beam Size:</b> {recommendation.get('beam_size')}<br>"
            f"<b>Rationale:</b> {recommendation.get('rationale', '')}"
        )

    def _apply_settings(self):
        if not self._latest_recommendation:
            return

        rec = self._latest_recommendation
        config_manager.set("whisper_model", rec.get("whisper_model", "medium"))
        config_manager.set("device", rec.get("device", "cuda"))
        config_manager.set("compute_type", rec.get("compute_type", "float16"))
        config_manager.set("beam_size", rec.get("beam_size", 1))
        config_manager.save()

        self.settings_applied.emit(rec)

        InfoBar.success(
            title="Settings Applied",
            content=f"Optimal settings successfully applied!\n\n"
                    f"• Whisper Model: {rec.get('whisper_model')}\n"
                    f"• Compute Device: {rec.get('device', '').upper()}\n"
                    f"• Compute Type: {rec.get('compute_type')}\n"
                    f"• Beam Size: {rec.get('beam_size')} (Fast Mode)\n\n"
                    "Your transcription runs will now achieve maximum throughput without VRAM slowdowns.",
            orient=Qt.Orientation.Vertical,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self.parent() or self
        )
        self.accept()
