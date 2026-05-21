"""ローカルボルトのノートを ChromaDB に索引する。

旧 ``github_syncer.py``（GitHub API でノートを取得）を置き換える。GitHub API を
使わず、同期済みのローカルファイルから直接インデックスを構築する。
"""
from __future__ import annotations

import re

from services.knowledge_store import KnowledgeStore
from services.vault import Vault
import config

_NOTE_DIRS = [
    config.FLEETING_PATH,
    config.ARTICLES_PATH,
    config.YOUTUBE_PATH,
    config.PERMANENT_PATH,
    config.RESEARCH_PATH,
    config.PLANNING_PATH,
]

_FRONTMATTER_RE = re.compile(
    r"---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL
)

_TYPE_TO_COMMAND = {
    "fleeting": "memo",
    "literature/article": "link",
    "literature/youtube": "link",
    "permanent": "memo",
    "research": "research",
    "planning": "planning",
}


def _parse_frontmatter(content: str) -> dict[str, str]:
    content = content.lstrip("﻿")
    for match in _FRONTMATTER_RE.finditer(content):
        fields: dict[str, str] = {}
        for line in match.group(1).splitlines():
            kv = re.match(r"^([\w_-]+):\s*(.*)", line)
            if kv:
                fields[kv.group(1)] = kv.group(2).strip()
        if "id" in fields:
            return fields
    return {}


def _parse_tags(raw: str) -> list[str]:
    raw = raw.strip().strip("[]")
    if not raw:
        return []
    return [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]


def index_vault(vault: Vault, knowledge: KnowledgeStore) -> int:
    """ローカルボルトの全ノートを ChromaDB に upsert する。処理件数を返す。"""
    total = 0
    for directory in _NOTE_DIRS:
        dir_path = vault.root / directory
        if not dir_path.is_dir():
            continue
        for path in sorted(dir_path.glob("*.md")):
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = _parse_frontmatter(content)
            note_id = fm.get("id", "")
            if not note_id:
                continue
            knowledge.add_note(
                note_id=note_id,
                content=content,
                command=_TYPE_TO_COMMAND.get(fm.get("type", ""), "memo"),
                file_path=str(path.relative_to(vault.root)),
                tags=_parse_tags(fm.get("tags", "")),
            )
            total += 1
    return total
