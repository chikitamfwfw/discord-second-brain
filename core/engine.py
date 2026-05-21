"""Engine — Discord 非依存のコマンドロジック中枢。

Claude Code / VSCode 拡張機能のどちらのフロントエンドからも、この Engine を
（デーモン経由で）呼び出す。会話・保存・同期・検索・スクレイピングを一手に担う。

会話エンジンの 2 モード:
  - 拡張機能モード: ``start`` / ``continue_session`` / ``save`` で Claude API を使う。
  - Claude Code モード: 会話は Claude Code 自身が担当。Engine 側は ``save_note`` /
    ``fetch_url`` / ``fetch_youtube`` / ``search`` / ``sync`` などの処理のみを提供する。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import config
from services.vault import Vault, ConflictError, GitError
from session.manager import Session, SessionStore
from services.indexer import index_vault
from utils.formatters import (
    make_zk_id,
    make_zk_filename,
    make_timestamp_filename,
    sanitize_tags,
    inject_tags,
)
from utils.knowledge_ref import build_knowledge_context

# note_type → (保存ディレクトリ, テンプレート名)
_NOTE_KINDS: dict[str, tuple[str, str]] = {
    "fleeting": (config.FLEETING_PATH, "fleeting-note"),
    "article": (config.ARTICLES_PATH, "literature-article"),
    "youtube": (config.YOUTUBE_PATH, "literature-youtube"),
    "research": (config.RESEARCH_PATH, "research"),
    "planning": (config.PLANNING_PATH, "planning"),
    "permanent": (config.PERMANENT_PATH, "permanent-note"),
}

# コマンド → note_type
_COMMAND_KIND = {
    "memo": "fleeting",
    "chat": "fleeting",
    "research": "research",
    "planning": "planning",
}


class Engine:
    def __init__(self, vault_path: str | None = None) -> None:
        self.vault = Vault(vault_path)
        self.sessions = SessionStore()
        self._knowledge: Any | None = None
        self._claude: Any | None = None
        self._tasks: Any | None = None

    # ── 遅延初期化（埋め込みモデル/Claude クライアントは重いので必要時のみ） ──
    @property
    def knowledge(self) -> Any:
        if self._knowledge is None:
            from services.knowledge_store import KnowledgeStore

            self._knowledge = KnowledgeStore()
        return self._knowledge

    @property
    def claude(self) -> Any:
        if self._claude is None:
            if not config.ANTHROPIC_API_KEY:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY が未設定です（拡張機能モードの会話に必要）。"
                )
            from services.claude_client import ClaudeClient

            self._claude = ClaudeClient(vault=self.vault)
        return self._claude

    @property
    def tasks(self) -> Any:
        if self._tasks is None:
            from core.task import TaskService

            self._tasks = TaskService()
        return self._tasks

    def warm_up(self) -> None:
        """デーモン起動時に埋め込みモデルを事前ロードし、以後の操作を高速化する。"""
        _ = self.knowledge

    # ── 同期・状態 ──────────────────────────────────────────────────────────
    def sync(self) -> dict:
        return self.vault.sync()

    def status(self) -> dict:
        st = self.vault.status()
        st["active_sessions"] = len(self.sessions.list(active_only=True))
        st["indexed_notes"] = self.knowledge.count() if self._knowledge else None
        return st

    def index(self) -> int:
        return index_vault(self.vault, self.knowledge)

    # ── 検索 ────────────────────────────────────────────────────────────────
    def search(self, query: str, n_results: int = 5) -> list[dict]:
        return self.knowledge.search(query, n_results)

    # ── コンテンツ取得 ──────────────────────────────────────────────────────
    def fetch_url(self, url: str) -> dict:
        from services.scraper import fetch_article

        result = asyncio.run(fetch_article(url))
        return {
            "url": result.url,
            "title": result.title,
            "text": result.text,
            "is_paywall": result.is_paywall,
            "page_count": result.page_count,
        }

    def fetch_youtube(self, url: str) -> dict:
        from services.youtube_client import get_transcript

        result = asyncio.run(get_transcript(url))
        return {
            "video_id": result.video_id,
            "title": result.title,
            "transcript": result.transcript,
            "language": result.language,
            "method": result.method,
            "channel": result.channel,
            "channel_url": result.channel_url,
        }

    # ── Claude Code モード: 完成済みノートの保存 ────────────────────────────
    def save_note(
        self,
        note_type: str,
        content: str,
        tags: list[str] | None = None,
        sync: bool = True,
    ) -> dict:
        """Claude Code が整形済みの Markdown ノートをそのまま保存する。

        会話・整形は Claude Code 自身が担当済み。Engine は配置・commit・push・索引のみ。
        """
        if note_type not in _NOTE_KINDS:
            raise ValueError(f"未知の note_type: {note_type}")
        if sync:
            self.sync()

        dt = datetime.now()
        note_id = make_zk_id(dt)
        directory, _ = _NOTE_KINDS[note_type]
        rel_path = f"{directory}/{make_zk_filename(dt)}"

        if tags:
            content = inject_tags(content, sanitize_tags(tags))
        rel = self.vault.write_note(rel_path, content)

        command = "link" if note_type in ("article", "youtube") else note_type
        sha = self.vault.commit_and_push(f"add({command}): {note_id}")
        self.knowledge.add_note(note_id, content, command, rel, tags or [])
        return {"note_id": note_id, "path": rel, "commit": sha}

    # ── 拡張機能モード: 会話セッション ──────────────────────────────────────
    def _knowledge_ctx(self, query: str, n: int = 3) -> str:
        try:
            related = self.knowledge.search(query[:500], n)
        except Exception:
            related = []
        return build_knowledge_context(related) if related else ""

    def _chat(self, session: Session, user_message: str, ctx: str) -> str:
        reply, _ = asyncio.run(
            self.claude.chat_with_tools(
                command=session.command,
                history=session.history,
                user_message=user_message,
                extra_system=ctx,
            )
        )
        session.pending_content = reply
        self.sessions.save(session)
        return reply

    def start(self, command: str, text: str, sync: bool = True) -> dict:
        """会話セッションを開始する（memo/research/planning/chat）。"""
        if command not in ("memo", "research", "planning", "chat"):
            raise ValueError(f"start 非対応のコマンド: {command}")
        if sync:
            self.sync()

        session = self.sessions.create(command)

        if command == "memo":
            # 生メモを 00-inbox に保存
            dt = datetime.now()
            inbox_rel = f"{config.INBOX_PATH}/{make_timestamp_filename(dt)}"
            self.vault.write_note(inbox_rel, f"# Inbox\n\n{text}\n")
            self.vault.commit_and_push(f"inbox: {make_timestamp_filename(dt)}")
            user_message = f"以下のメモについて話しましょう。\n\n{text}"
        elif command in ("research", "planning"):
            session.topic = text
            user_message = text
        else:  # chat
            user_message = text

        ctx = self._knowledge_ctx(text, 5 if command in ("research", "planning") else 3)
        reply = self._chat(session, user_message, ctx)
        return {"session_id": session.id, "reply": reply}

    def start_link(self, url: str, sync: bool = True) -> dict:
        """URL（記事 or YouTube）を取り込んで会話セッションを開始する。"""
        if sync:
            self.sync()
        session = self.sessions.create("link")
        session.references.append(url)

        is_youtube = any(
            h in url for h in ("youtube.com/watch", "youtu.be/", "youtube.com/shorts/")
        )
        if is_youtube:
            yt = self.fetch_youtube(url)
            if yt["method"] == "unavailable" or not yt["transcript"]:
                self.sessions.close(session.id)
                return {"session_id": session.id, "error": "字幕・書き起こしを取得できませんでした。"}
            session.topic = "youtube"
            session.raw_content = yt["transcript"]
            user_message = (
                f"以下の YouTube 動画の書き起こしを共有します。\n\n"
                f"**タイトル:** {yt['title']}\n**URL:** {url}\n"
                f"**チャンネル:** {yt['channel']}\n\n"
                f"**書き起こし全文:**\n{yt['transcript']}"
            )
            ctx = self._knowledge_ctx(f"{yt['title']} {yt['transcript'][:300]}")
        else:
            art = self.fetch_url(url)
            if art["is_paywall"]:
                self.sessions.close(session.id)
                return {
                    "session_id": session.id,
                    "error": "本文を取得できませんでした（ペイウォールの可能性）。",
                    "title": art["title"],
                }
            session.topic = "article"
            user_message = (
                f"以下の記事を読みました。\n\n"
                f"**タイトル:** {art['title']}\n**URL:** {url}\n\n"
                f"**本文:**\n{art['text'][:30000]}"
            )
            ctx = self._knowledge_ctx(f"{art['title']} {art['text'][:300]}")

        reply = self._chat(session, user_message, ctx)
        return {"session_id": session.id, "reply": reply}

    def continue_session(self, session_id: str, message: str) -> dict:
        session = self.sessions.get(session_id)
        if session is None or not session.active:
            raise KeyError(f"アクティブなセッションが見つかりません: {session_id}")
        ctx = self._knowledge_ctx(message)
        reply = self._chat(session, message, ctx)
        return {"session_id": session.id, "reply": reply}

    def save(self, session_id: str, sync: bool = True) -> dict:
        """会話セッションをテンプレートに沿って構造化ノートに保存する。"""
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"セッションが見つかりません: {session_id}")
        if sync:
            self.sync()

        if session.command == "link":
            note_type = "youtube" if session.topic == "youtube" else "article"
        else:
            note_type = _COMMAND_KIND.get(session.command, "fleeting")
        directory, template_name = _NOTE_KINDS[note_type]

        dt = datetime.now()
        note_id = make_zk_id(dt)
        rel_path = f"{directory}/{make_zk_filename(dt)}"
        template = self.vault.read_template(template_name)

        extra = ""
        if session.topic and session.command in ("research", "planning"):
            extra = f"トピック: {session.topic}"
        elif session.references:
            extra = f"URL: {session.references[0]}"

        content = asyncio.run(
            self.claude.compile_to_note(
                history=session.history,
                template=template,
                note_id=note_id,
                date_str=dt.strftime("%Y-%m-%d"),
                extra_context=extra,
            )
        )
        if note_type == "youtube" and session.raw_content:
            content += f"\n\n## 書き起こし全文\n\n{session.raw_content}\n"

        tags = sanitize_tags(asyncio.run(self.claude.generate_tags(content)))
        content = inject_tags(content, tags)

        rel = self.vault.write_note(rel_path, content)
        command = session.command
        sha = self.vault.commit_and_push(f"add({command}): {note_id}")
        self.knowledge.add_note(note_id, content, command, rel, tags)

        session.saved_note_id = note_id
        session.pending_content = content
        session.pending_note_id = note_id
        self.sessions.save(session)
        return {"note_id": note_id, "path": rel, "commit": sha, "tags": tags}

    def make_permanent(self, session_id: str, sync: bool = True) -> dict:
        """保存済み Fleeting ノートから Atomic な Permanent ノートを抽出する。"""
        session = self.sessions.get(session_id)
        if session is None or not session.pending_content:
            raise KeyError("Permanent 化できる保存済みノートがありません。")
        if sync:
            self.sync()

        atomic, _ = asyncio.run(
            self.claude.chat(
                command="permanent",
                history=[],
                user_message=(
                    "以下の Fleeting ノートから Atomic な Permanent ノートを抽出してください。"
                    "各ノートは独立した 1 つのアイデアを表します。\n\n"
                    f"{session.pending_content}"
                ),
            )
        )
        dt = datetime.now()
        note_id = make_zk_id(dt)
        rel_path = f"{config.PERMANENT_PATH}/{make_zk_filename(dt)}"
        rel = self.vault.write_note(rel_path, atomic)
        sha = self.vault.commit_and_push(f"add(permanent): {note_id}")
        self.knowledge.add_note(note_id, atomic, "permanent", rel, [])
        return {"note_id": note_id, "path": rel, "commit": sha}

    def discard(self, session_id: str) -> dict:
        self.sessions.close(session_id)
        return {"session_id": session_id, "active": False}
