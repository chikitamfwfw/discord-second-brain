"""セッションの永続化ストア。

旧実装はメモリ上の辞書 + Discord ボタンの timeout でセッションを管理しており、
タイムアウトや再起動で会話が消失していた。本実装はセッションを JSON で
ディスクに永続化し、**タイムアウトを撤廃**する。エンジン再起動後も復元できる。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config


@dataclass
class Session:
    id: str
    command: str
    history: list[dict] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    active: bool = True
    pending_content: str = ""
    pending_path: str = ""
    pending_note_id: str = ""
    topic: str = ""
    raw_content: str = ""
    related_note_ids: list[str] = field(default_factory=list)
    saved_note_id: str = ""

    def add_turn(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})


class SessionStore:
    """セッションをディスク（1 セッション = 1 JSON）に永続化するストア。"""

    def __init__(self, directory: str | None = None) -> None:
        self.dir = Path(directory or config.SESSION_DIR).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def create(self, command: str) -> Session:
        session_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        session = Session(id=session_id, command=command)
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        session.updated_at = time.time()
        self._path(session.id).write_text(
            json.dumps(asdict(session), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, session_id: str) -> Session | None:
        path = self._path(session_id)
        if not path.is_file():
            return None
        try:
            return Session(**json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return None

    def list(self, active_only: bool = True) -> list[Session]:
        out: list[Session] = []
        for path in self.dir.glob("*.json"):
            try:
                session = Session(**json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, TypeError):
                continue
            if active_only and not session.active:
                continue
            out.append(session)
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    def close(self, session_id: str) -> None:
        """セッションを終了状態にする（履歴ファイルは保持し、復元可能なまま残す）。"""
        session = self.get(session_id)
        if session is not None:
            session.active = False
            self.save(session)
