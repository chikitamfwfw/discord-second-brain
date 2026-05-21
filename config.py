"""Engine configuration.

Discord 依存を廃止し、ローカルの second-brain ボルト上で動作する設定。
値は環境変数（任意で .env）で上書きできる。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_ENGINE_DIR = Path(__file__).resolve().parent

# ── Vault（ローカルの second-brain リポジトリ） ──────────────────────────────
# 既定では本リポジトリと同階層の ``second-brain`` を使う。
VAULT_PATH: str = os.getenv("VAULT_PATH", str(_ENGINE_DIR.parent / "second-brain"))
VAULT_GIT_BRANCH: str = os.getenv("VAULT_GIT_BRANCH", "main")

# ── Anthropic（拡張機能/API モードの会話で使用） ────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
MODEL_MAIN: str = os.getenv("MODEL_MAIN", "claude-opus-4-7")
MODEL_TAGGING: str = os.getenv("MODEL_TAGGING", "claude-haiku-4-5-20251001")

# ── Tavily（拡張機能モードの Web 検索、任意） ───────────────────────────────
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

# ── GitHub（タスク管理 = Issues / Projects v2 のみで使用） ──────────────────
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO: str = os.getenv("GITHUB_REPO", "")
# Projects v2 ボード番号（0 = 未設定なら自動作成）とタイトル
GITHUB_PROJECT_NUMBER: int = int(os.getenv("GITHUB_PROJECT_NUMBER", "0") or "0")
GITHUB_PROJECT_TITLE: str = os.getenv("GITHUB_PROJECT_TITLE", "Second Brain Tasks")

# ── 常駐エンジン（デーモン） ────────────────────────────────────────────────
DAEMON_HOST: str = os.getenv("BRAIN_DAEMON_HOST", "127.0.0.1")
DAEMON_PORT: int = int(os.getenv("BRAIN_DAEMON_PORT", "8765"))

# ── ChromaDB ────────────────────────────────────────────────────────────────
CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", str(_ENGINE_DIR / "chroma_db"))

# ── セッション永続化 ────────────────────────────────────────────────────────
SESSION_DIR: str = os.getenv("SESSION_DIR", str(_ENGINE_DIR / ".brain" / "sessions"))

# ── ボルト内フォルダパス（VAULT_PATH からの相対） ───────────────────────────
INBOX_PATH: str = "00-inbox"
FLEETING_PATH: str = "10-notes/fleeting"
ARTICLES_PATH: str = "10-notes/literature/articles"
YOUTUBE_PATH: str = "10-notes/literature/youtube"
PERMANENT_PATH: str = "10-notes/permanent"
RESEARCH_PATH: str = "20-research"
PLANNING_PATH: str = "30-planning"

SYSTEM_PROMPT_PATH: str = "_config/system-prompt.md"
PROMPTS_PATH: str = "_config/prompts"
TEMPLATES_PATH: str = "_templates"
USER_PROFILE_PATH: str = "_config/user-profile.md"

# 記事スクレイピング/YouTube のクッキー（任意）
COOKIES_FILE: str = os.getenv("COOKIES_FILE", "")

# バックグラウンド同期間隔（秒）。0 で無効。
SYNC_INTERVAL_SECONDS: int = int(os.getenv("SYNC_INTERVAL_SECONDS", "600"))
