"""GitHub の全ノートを ChromaDB に同期する。
起動時に1回フル同期し、その後 SYNC_INTERVAL_SECONDS ごとに再同期する。
GitHub で直接編集・追加されたノートも自動的に反映される。
"""
from __future__ import annotations
import asyncio
import re
from github import GithubException

from services.github_client import GitHubClient
from services.knowledge_store import KnowledgeStore
import config

SYNC_INTERVAL_SECONDS = 600  # 10分

_NOTE_DIRS = [
    config.FLEETING_PATH,
    config.ARTICLES_PATH,
    config.YOUTUBE_PATH,
    config.PERMANENT_PATH,
    config.RESEARCH_PATH,
    config.PLANNING_PATH,
]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_TYPE_TO_COMMAND = {
    "fleeting": "memo",
    "literature/article": "link",
    "literature/youtube": "link",
    "permanent": "memo",
    "research": "research",
    "planning": "planning",
}


def _parse_frontmatter(content: str) -> dict[str, str]:
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^([\w_-]+):\s*(.*)", line)
        if kv:
            fields[kv.group(1)] = kv.group(2).strip()
    return fields


def _parse_tags(raw: str) -> list[str]:
    raw = raw.strip().strip("[]")
    if not raw:
        return []
    return [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]


def sync_once(github: GitHubClient, knowledge: KnowledgeStore) -> int:
    """GitHub の全ノートを ChromaDB に upsert する。戻り値は処理件数。"""
    total = 0
    for directory in _NOTE_DIRS:
        try:
            items = github._repo.get_contents(directory)
            if not isinstance(items, list):
                items = [items]
        except GithubException as e:
            if e.status == 404:
                continue  # ディレクトリ未作成はスキップ
            print(f"[SYNC] list error {directory}: {e}")
            continue

        for item in items:
            if not item.name.endswith(".md"):
                continue
            try:
                content = github.read_file(item.path, use_cache=False)
                fm = _parse_frontmatter(content)
                note_id = fm.get("id", "")
                if not note_id:
                    continue
                command = _TYPE_TO_COMMAND.get(fm.get("type", ""), "memo")
                tags = _parse_tags(fm.get("tags", ""))
                knowledge.add_note(
                    note_id=note_id,
                    content=content,
                    command=command,
                    file_path=item.path,
                    tags=tags,
                )
                total += 1
            except Exception as e:
                print(f"[SYNC] skip {item.path}: {e}")

    return total


async def run_sync_loop(github: GitHubClient, knowledge: KnowledgeStore) -> None:
    """起動後、SYNC_INTERVAL_SECONDS ごとに GitHub→ChromaDB を再同期するバックグラウンドタスク。"""
    while True:
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
        try:
            n = await asyncio.to_thread(sync_once, github, knowledge)
            print(f"[SYNC] periodic sync: {n} notes upserted")
        except Exception as e:
            print(f"[SYNC] periodic sync failed: {e}")
