"""Academic Subject & Course Management Dialog for Transcribe Studio."""

from typing import Optional, List
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QListWidgetItem,
)
from qfluentwidgets import (
    TitleLabel,
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
    StrongBodyLabel,
    LineEdit,
    SearchLineEdit,
    PlainTextEdit,
    PushButton,
    PrimaryPushButton,
    TransparentPushButton,
    CardWidget,
    ListWidget,
    MessageBox,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    setTheme,
    Theme,
)

from subject_manager import Subject, subject_manager


class SubjectManagementDialog(QDialog):
    """Modal dialog allowing users to add, edit, rename, and delete subjects and modules."""

    subjects_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Academic Subjects & Course Management")
        self.resize(780, 560)
        self.setModal(True)
        setTheme(Theme.DARK)

        self._current_editing_original_name: Optional[str] = None
        self._is_new_mode: bool = False

        self._init_ui()
        self._populate_list()

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(14)
        root_layout.setContentsMargins(24, 20, 24, 20)

        # 1. Header
        header = TitleLabel("📚 Academic Subjects & Courses", self)
        root_layout.addWidget(header)

        sub_desc = BodyLabel(
            "Organize your academic courses, course codes, and syllabus modules. "
            "These categorize your lecture transcripts and optimize AI notes generation.",
            self,
        )
        root_layout.addWidget(sub_desc)

        # 2. Main Master-Detail Area
        content_layout = QHBoxLayout()
        content_layout.setSpacing(16)

        # --- Left Panel: Subject List ---
        left_panel = QVBoxLayout()
        left_panel.setSpacing(10)

        self.search_bar = SearchLineEdit(self)
        self.search_bar.setPlaceholderText("Search subjects...")
        self.search_bar.textChanged.connect(self._filter_list)
        left_panel.addWidget(self.search_bar)

        self.subject_list = ListWidget(self)
        self.subject_list.itemSelectionChanged.connect(self._on_selection_changed)
        left_panel.addWidget(self.subject_list, 1)

        left_actions = QHBoxLayout()
        left_actions.setSpacing(8)

        self.new_btn = PushButton("+ New Subject", self)
        self.new_btn.setIcon(FIF.ADD)
        self.new_btn.clicked.connect(self._start_new_subject)
        left_actions.addWidget(self.new_btn)

        self.reset_btn = TransparentPushButton("Reset Defaults", self)
        self.reset_btn.setToolTip("Restore standard default subjects")
        self.reset_btn.clicked.connect(self._confirm_reset_defaults)
        left_actions.addWidget(self.reset_btn)

        left_panel.addLayout(left_actions)
        content_layout.addLayout(left_panel, 2)

        # --- Right Panel: Edit Card ---
        self.editor_card = CardWidget(self)
        editor_layout = QVBoxLayout(self.editor_card)
        editor_layout.setSpacing(12)
        editor_layout.setContentsMargins(18, 18, 18, 18)

        self.editor_title = StrongBodyLabel("Subject Details", self.editor_card)
        editor_layout.addWidget(self.editor_title)

        # Subject Name
        editor_layout.addWidget(CaptionLabel("Subject / Course Name:", self.editor_card))
        self.name_input = LineEdit(self.editor_card)
        self.name_input.setPlaceholderText("e.g. Artificial Intelligence, Macroeconomics")
        editor_layout.addWidget(self.name_input)

        # Course Code
        editor_layout.addWidget(CaptionLabel("Course Code (optional):", self.editor_card))
        self.code_input = LineEdit(self.editor_card)
        self.code_input.setPlaceholderText("e.g. CS-402, ECON-101")
        editor_layout.addWidget(self.code_input)

        # Modules Covered
        editor_layout.addWidget(
            CaptionLabel("Syllabus Modules / Topics (one per line):", self.editor_card)
        )
        self.modules_input = PlainTextEdit(self.editor_card)
        self.modules_input.setPlaceholderText(
            "Module 1: Foundations\nModule 2: Neural Architectures\nModule 3: Optimization"
        )
        editor_layout.addWidget(self.modules_input, 1)

        # Action Buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        self.delete_btn = PushButton("Delete Subject", self.editor_card)
        self.delete_btn.setIcon(FIF.DELETE)
        self.delete_btn.clicked.connect(self._confirm_delete_subject)
        button_row.addWidget(self.delete_btn)

        button_row.addStretch(1)

        self.save_btn = PrimaryPushButton("Save Changes", self.editor_card)
        self.save_btn.setIcon(FIF.SAVE)
        self.save_btn.clicked.connect(self._save_current_subject)
        button_row.addWidget(self.save_btn)

        editor_layout.addLayout(button_row)
        content_layout.addWidget(self.editor_card, 3)

        root_layout.addLayout(content_layout, 1)

        # 3. Bottom Close Button
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch(1)
        self.close_btn = PushButton("Done", self)
        self.close_btn.clicked.connect(self.accept)
        bottom_bar.addWidget(self.close_btn)
        root_layout.addLayout(bottom_bar)

    def _populate_list(self, select_name: Optional[str] = None):
        self.subject_list.blockSignals(True)
        self.subject_list.clear()

        query = self.search_bar.text().strip().lower()
        subjects = subject_manager.get_all_subjects()
        target_item = None

        for subj in subjects:
            display_text = f"[{subj.code}] {subj.name}" if subj.code else subj.name
            mod_count = len(subj.modules)
            full_label = f"{display_text}  ({mod_count} module{'s' if mod_count != 1 else ''})"

            if query and query not in full_label.lower():
                continue

            item = QListWidgetItem(full_label)
            item.setData(Qt.ItemDataRole.UserRole, subj.name)
            self.subject_list.addItem(item)

            if select_name and subj.name == select_name:
                target_item = item

        self.subject_list.blockSignals(False)

        if target_item:
            self.subject_list.setCurrentItem(target_item)
        elif self.subject_list.count() > 0:
            self.subject_list.setCurrentRow(0)
        else:
            self._start_new_subject()

    def _filter_list(self):
        curr_selected = self._get_selected_subject_name()
        self._populate_list(select_name=curr_selected)

    def _get_selected_subject_name(self) -> Optional[str]:
        item = self.subject_list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _on_selection_changed(self):
        subj_name = self._get_selected_subject_name()
        if not subj_name:
            return

        subj = subject_manager.get_subject(subj_name)
        if not subj:
            return

        self._is_new_mode = False
        self._current_editing_original_name = subj.name
        self.editor_title.setText(f"Editing: {subj.name}")
        self.name_input.setText(subj.name)
        self.code_input.setText(subj.code)
        self.modules_input.setPlainText("\n".join(subj.modules))
        self.save_btn.setText("Save Changes")
        self.delete_btn.setEnabled(True)

    def _start_new_subject(self):
        self._is_new_mode = True
        self._current_editing_original_name = None
        self.subject_list.clearSelection()
        self.editor_title.setText("Create New Subject / Course")
        self.name_input.clear()
        self.code_input.clear()
        self.modules_input.clear()
        self.name_input.setFocus()
        self.save_btn.setText("Add Subject")
        self.delete_btn.setEnabled(False)

    def _save_current_subject(self):
        name = self.name_input.text().strip()
        code = self.code_input.text().strip()
        raw_modules = self.modules_input.toPlainText().splitlines()
        modules = [m.strip() for m in raw_modules if m.strip()]

        if not name:
            InfoBar.warning(
                title="Invalid Name",
                content="Please enter a valid course or subject name.",
                isClosable=True,
                duration=4000,
                position=InfoBarPosition.TOP,
                parent=self,
            )
            return

        try:
            if self._is_new_mode:
                subject_manager.add_subject(name=name, code=code, modules=modules)
                InfoBar.success(
                    title="Subject Added",
                    content=f"'{name}' added successfully.",
                    duration=3000,
                    parent=self,
                )
            else:
                orig_name = self._current_editing_original_name or name
                subject_manager.update_subject(
                    old_name=orig_name,
                    new_name=name,
                    code=code,
                    modules=modules,
                )
                InfoBar.success(
                    title="Subject Updated",
                    content=f"'{name}' updated successfully.",
                    duration=3000,
                    parent=self,
                )

            self.subjects_changed.emit()
            self._populate_list(select_name=name)
        except Exception as e:
            InfoBar.error(
                title="Save Error",
                content=str(e),
                duration=5000,
                parent=self,
            )

    def _confirm_delete_subject(self):
        subj_name = self._current_editing_original_name
        if not subj_name:
            return

        mb = MessageBox("Delete Subject", f"Are you sure you want to delete '{subj_name}'?", self)
        if mb.exec():
            subject_manager.delete_subject(subj_name)
            self.subjects_changed.emit()
            InfoBar.info(
                title="Subject Deleted",
                content=f"'{subj_name}' was removed.",
                duration=3000,
                parent=self,
            )
            self._populate_list()

    def _confirm_reset_defaults(self):
        mb = MessageBox(
            "Reset Defaults",
            "Are you sure you want to reset all subjects to default academic courses?",
            self,
        )
        if mb.exec():
            subject_manager.reset_defaults()
            self.subjects_changed.emit()
            InfoBar.success(
                title="Defaults Restored",
                content="Standard academic subjects restored.",
                duration=3000,
                parent=self,
            )
            self._populate_list()
