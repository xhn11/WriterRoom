"""
Datové modely: Project a Chapter – ukládání a načítání z JSON (.wrp).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class Chapter:
    title: str
    skeleton: str = ""
    text: str = ""
    audio_path: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class Project:
    title: str
    genre: str = ""
    style_notes: str = ""
    chapters: List[Chapter] = field(default_factory=list)

    def save(self, path: Path) -> None:
        data = {
            "title": self.title,
            "genre": self.genre,
            "style_notes": self.style_notes,
            "chapters": [
                {
                    "id": ch.id,
                    "title": ch.title,
                    "skeleton": ch.skeleton,
                    "text": ch.text,
                    "audio_path": ch.audio_path,
                }
                for ch in self.chapters
            ],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Project":
        data = json.loads(path.read_text(encoding="utf-8"))
        chapters = [
            Chapter(
                id=ch.get("id", str(uuid.uuid4())),
                title=ch["title"],
                skeleton=ch.get("skeleton", ""),
                text=ch.get("text", ""),
                audio_path=ch.get("audio_path", ""),
            )
            for ch in data.get("chapters", [])
        ]
        return cls(
            title=data["title"],
            genre=data.get("genre", ""),
            style_notes=data.get("style_notes", ""),
            chapters=chapters,
        )
