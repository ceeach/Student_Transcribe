"""Subject and Module Management module for NotionTranscribe.

Handles academic subjects, course codes, syllabus modules/topics,
and session types (Lecture vs. Exercise).
Persists subjects in ~/.notion_transcribe/subjects.json.
"""

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from logger import get_logger

logger = get_logger("SubjectManager")

SUBJECTS_FILE = Path.home() / ".notion_transcribe" / "subjects.json"

DEFAULT_SUBJECTS = [
    {
        "name": "General / Other",
        "code": "GEN",
        "color": "#38bdf8",
        "modules": ["Module 1: Introduction", "Module 2: Fundamentals"],
    },
    {
        "name": "Computer Science",
        "code": "CS",
        "color": "#10b981",
        "modules": [
            "Module 1: Architecture & Systems",
            "Module 2: Data Structures & Algorithms",
            "Module 3: Operating Systems & Networks",
            "Module 4: Machine Learning & AI",
        ],
    },
    {
        "name": "Business & Strategy",
        "code": "BUS",
        "color": "#f59e0b",
        "modules": [
            "Module 1: Industry Analysis (Porter's 5 Forces)",
            "Module 2: Resource-Based View (RBV)",
            "Module 3: Competitive Advantage",
            "Module 4: Financial Valuation & Risk",
        ],
    },
    {
        "name": "Mathematics & Engineering",
        "code": "MATH",
        "color": "#8b5cf6",
        "modules": [
            "Module 1: Linear Algebra & Matrices",
            "Module 2: Multivariable Calculus",
            "Module 3: Probability & Statistics",
            "Module 4: Differential Equations",
        ],
    },
]


@dataclass
class Subject:
    name: str
    code: str = ""
    color: str = "#38bdf8"
    modules: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Subject":
        return cls(
            name=data.get("name", "Untitled Subject"),
            code=data.get("code", ""),
            color=data.get("color", "#38bdf8"),
            modules=list(data.get("modules", [])),
        )


class SubjectManager:
    """Manages academic subjects, syllabus modules, and session categorization."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or SUBJECTS_FILE
        self._subjects: Dict[str, Subject] = {}
        self._last_mtime: float = 0.0
        self.load()

    def _ensure_fresh(self):
        """Reload from disk if file modified by another instance or dialog."""
        if self.storage_path.exists():
            try:
                mtime = self.storage_path.stat().st_mtime
                if mtime > getattr(self, "_last_mtime", 0.0):
                    self.load()
            except Exception:
                pass

    def load(self):
        """Load subjects from persistent JSON file or load defaults."""
        if not self.storage_path.exists():
            for item in DEFAULT_SUBJECTS:
                subj = Subject.from_dict(item)
                self._subjects[subj.name] = subj
            self.save()
            return

        try:
            self._last_mtime = self.storage_path.stat().st_mtime
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._subjects.clear()
            if isinstance(data, list):
                for item in data:
                    s = Subject.from_dict(item)
                    self._subjects[s.name] = s
            elif isinstance(data, dict):
                for k, item in data.items():
                    s = Subject.from_dict(item)
                    self._subjects[s.name] = s
            logger.info(f"Loaded {len(self._subjects)} subjects from {self.storage_path}")
        except Exception as e:
            logger.error(f"Error loading subjects from {self.storage_path}: {e}")
            for item in DEFAULT_SUBJECTS:
                subj = Subject.from_dict(item)
                self._subjects[subj.name] = subj

    def save(self):
        """Save subjects dictionary to persistent JSON file."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            export_list = [s.to_dict() for s in self._subjects.values()]
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(export_list, f, indent=2, ensure_ascii=False)
            self._last_mtime = self.storage_path.stat().st_mtime
            logger.info(f"Saved {len(self._subjects)} subjects to {self.storage_path}")
        except Exception as e:
            logger.error(f"Error saving subjects: {e}")

    def get_all(self) -> List[Subject]:
        """Return list of all subjects sorted by name."""
        self._ensure_fresh()
        return sorted(self._subjects.values(), key=lambda s: s.name.lower())

    def get_all_subjects(self) -> List[Subject]:
        """Alias for get_all."""
        return self.get_all()

    def get_subject_names(self) -> List[str]:
        self._ensure_fresh()
        return [s.name for s in self.get_all()]

    def get_subject(self, name: str) -> Optional[Subject]:
        self._ensure_fresh()
        return self._subjects.get(name)

    def add_subject(
        self,
        name: Any = None,
        code: str = "",
        color: str = "#38bdf8",
        modules: Optional[List[str]] = None,
        name_or_subject: Any = None,
    ) -> Subject:
        actual_name = name if name is not None else name_or_subject
        if isinstance(actual_name, Subject):
            clean_name = actual_name.name.strip()
            code = actual_name.code or code
            color = actual_name.color or color
            modules = actual_name.modules if modules is None else modules
        else:
            clean_name = str(actual_name).strip() if actual_name is not None else ""
        if not clean_name:
            raise ValueError("Subject name cannot be empty.")
        if clean_name in self._subjects:
            subj = self._subjects[clean_name]
            if code:
                subj.code = code
            if color:
                subj.color = color
            if modules:
                for m in modules:
                    if m.strip() and m.strip() not in subj.modules:
                        subj.modules.append(m.strip())
            self.save()
            return subj

        new_subj = Subject(
            name=clean_name,
            code=code.strip(),
            color=color or "#38bdf8",
            modules=[m.strip() for m in (modules or []) if m.strip()],
        )
        self._subjects[clean_name] = new_subj
        self.save()
        return new_subj

    def update_subject(
        self,
        old_name: str,
        new_name: str,
        code: str = "",
        modules: Optional[List[str]] = None,
        color: str = "#38bdf8",
    ) -> Subject:
        clean_new = new_name.strip()
        if not clean_new:
            raise ValueError("Subject name cannot be empty.")
        if clean_new != old_name and clean_new in self._subjects:
            raise ValueError(f"A subject named '{clean_new}' already exists.")

        subj = self._subjects.pop(old_name, None)
        if subj is None:
            subj = Subject(name=clean_new)

        subj.name = clean_new
        subj.code = code.strip()
        subj.color = color or subj.color
        if modules is not None:
            subj.modules = [m.strip() for m in modules if m.strip()]

        self._subjects[clean_new] = subj
        self.save()
        return subj

    def reset_defaults(self):
        """Restore default academic subjects and syllabus modules."""
        self._subjects.clear()
        for item in DEFAULT_SUBJECTS:
            subj = Subject.from_dict(item)
            self._subjects[subj.name] = subj
        self.save()

    def delete_subject(self, name: str) -> bool:
        if name in self._subjects:
            del self._subjects[name]
            self.save()
            return True
        return False

    def add_module(self, subject_name: str, module_name: str) -> bool:
        clean_mod = module_name.strip()
        if not clean_mod:
            return False
        subj = self.get_subject(subject_name)
        if not subj:
            subj = self.add_subject(subject_name)
        if clean_mod not in subj.modules:
            subj.modules.append(clean_mod)
            self.save()
            return True
        return False

    def get_modules_for_subject(self, subject_name: str) -> List[str]:
        subj = self.get_subject(subject_name)
        return list(subj.modules) if subj else []


# Global singleton manager
subject_manager = SubjectManager()
