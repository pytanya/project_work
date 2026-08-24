"""
EduTutor — персистентные профили учеников.

Проблема, которую решает модуль: Wiki/мастерство/заметки глобальны по предмету,
поэтому ученики разных классов (имя, тип, класс) смешивали бы свои данные.
Профиль ученика (StudentProfile) хранится в `data/students/<student_id>.json`
и:
- даёт стабильный `student_id` (из localStorage на фронте / query-параметра в CLI);
- хранит имя и стабильные характеристики (learner_type, grade);
- используется как namespace для Knowledge Wiki: `knowledge_wiki/<student_id>/...`.

Сессия (TutorState.student_id / student_name) ссылается на профиль; профиль
создаётся автоматически при первом знакомстве и обновляется при заполнении
карточки intake (имя, тип, класс).
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .config import settings as default_settings

logger = logging.getLogger("edututor.student")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class StudentProfile(BaseModel):
    """Персистентные сведения об ученике.

    Стабильные атрибуты (имя, тип, класс) переживают сессии; subject/topic/mode
    остаются в сессии (TutorState), т.к. выбираются каждый раз.
    """

    student_id: str
    name: str = ""
    learner_type: Optional[str] = None
    grade: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def touch(self) -> None:
        self.updated_at = _now_iso()
        if not self.created_at:
            self.created_at = _now_iso()

    def prefill(self) -> Dict[str, Any]:
        """Поля для префилла intake новой сессии из профиля."""
        out: Dict[str, Any] = {"student_id": self.student_id}
        if self.name:
            out["student_name"] = self.name
        if self.learner_type:
            out["learner_type"] = self.learner_type
        if self.grade:
            out["grade"] = self.grade
        return out


class StudentStore:
    """Хранилище профилей учеников (JSON по файлу на ученика).

    Потокобезопасно по чтению; запись атомарная (tmp + rename), чтобы не
    портить профиль при параллельных сессиях одного ученика.
    """

    def __init__(self, root_dir: Optional[Any] = None) -> None:
        self.root = Path(root_dir or default_settings.STUDENTS_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, student_id: str) -> Path:
        return self.root / f"{student_id}.json"

    def get(self, student_id: str) -> Optional[StudentProfile]:
        if not student_id:
            return None
        p = self._path(student_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return StudentProfile.model_validate(data)
        except Exception:
            logger.warning("Не удалось прочитать профиль %s", student_id)
            return None

    def get_or_create(self, student_id: str) -> StudentProfile:
        """Профиль по ID; если нет — создаёт пустой и сохраняет."""
        profile = self.get(student_id)
        if profile is None:
            profile = StudentProfile(student_id=student_id)
            profile.touch()
            self.save(profile)
        return profile

    def save(self, profile: StudentProfile) -> None:
        profile.touch()
        p = self._path(profile.student_id)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(p)

    def delete(self, student_id: str) -> bool:
        p = self._path(student_id)
        if p.exists():
            p.unlink()
            return True
        return False

    def list_ids(self) -> List[str]:
        return sorted(
            p.stem for p in self.root.glob("*.json") if not p.name.endswith(".tmp")
        )

    def count(self) -> int:
        return len(self.list_ids())
