"""ローカルの second-brain ボルトへの読み書きと Git 同期。

旧 ``github_client.py``（GitHub API 経由）を置き換える。ノートはローカルの
ファイルシステムに直接書き込み、Git で remote と同期する。GitHub を常に正とする。
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import config


class GitError(RuntimeError):
    """Git 操作の失敗。"""


class ConflictError(GitError):
    """マージ/リベースの競合。人間による手動解決が必要。"""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        super().__init__(
            "Git の競合が発生しました。処理を中断します。"
            "以下のファイルを手動で解決してください: " + ", ".join(files)
        )


class Vault:
    """ローカルボルトのファイル I/O と Git 同期を担うクラス。"""

    def __init__(self, path: str | None = None) -> None:
        self.root = Path(path or config.VAULT_PATH).expanduser().resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"vault が見つかりません: {self.root}")

    # ── ファイル読み取り ────────────────────────────────────────────────────
    def read_file(self, rel: str) -> str:
        p = self.root / rel
        if not p.is_file():
            raise FileNotFoundError(rel)
        return p.read_text(encoding="utf-8")

    def read_system_prompt(self) -> str:
        return self.read_file(config.SYSTEM_PROMPT_PATH)

    def read_prompt(self, command: str) -> str:
        return self.read_file(f"{config.PROMPTS_PATH}/{command}.md")

    def read_template(self, name: str) -> str:
        return self.read_file(f"{config.TEMPLATES_PATH}/{name}.md")

    def read_user_profile(self) -> str:
        return self.read_file(config.USER_PROFILE_PATH)

    # ── ファイル書き込み ────────────────────────────────────────────────────
    def write_note(self, rel: str, content: str) -> str:
        """ノートをローカルへ書き込み、相対パスを返す（commit はしない）。"""
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not content.endswith("\n"):
            content += "\n"
        p.write_text(content, encoding="utf-8")
        return rel

    # ── Git ─────────────────────────────────────────────────────────────────
    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
        return proc

    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    def _has_changes(self) -> bool:
        return bool(self._git("status", "--porcelain").stdout.strip())

    def _conflicted_files(self) -> list[str]:
        out = self._git(
            "diff", "--name-only", "--diff-filter=U", check=False
        ).stdout.strip()
        return [line for line in out.splitlines() if line]

    def sync(self) -> dict:
        """実行前ガード: fetch → 未コミットの手編集を自動コミット → pull --rebase。

        - ローカルの手編集は破棄せず自動コミットして保護する。
        - GitHub 側の直接編集は pull --rebase で取り込む。
        - 競合時は rebase を中断し ``ConflictError`` を送出する（自動破棄しない）。
        """
        branch = self.current_branch()
        self._git("fetch", "origin", branch, check=False)

        auto_committed = False
        if self._has_changes():
            self._git("add", "-A")
            self._git("commit", "-m", "chore: auto-save local edits")
            auto_committed = True

        pull = self._git("pull", "--rebase", "origin", branch, check=False)
        if pull.returncode != 0:
            conflicts = self._conflicted_files()
            if conflicts:
                self._git("rebase", "--abort", check=False)
                raise ConflictError(conflicts)
            # upstream 未設定など、競合以外の失敗
            raise GitError(f"git pull --rebase: {pull.stderr.strip()}")

        return {"branch": branch, "auto_committed": auto_committed}

    def commit_and_push(self, message: str) -> str:
        """ステージ済み変更をコミットして push する。コミット SHA を返す。

        push が remote 先行で失敗した場合は sync（fetch+rebase）してリトライする。
        """
        if not self._has_changes():
            return ""
        self._git("add", "-A")
        self._git("commit", "-m", message)
        sha = self._git("rev-parse", "HEAD").stdout.strip()

        branch = self.current_branch()
        last_err = ""
        for delay in (0, 2, 4, 8, 16):
            if delay:
                time.sleep(delay)
            push = self._git("push", "origin", branch, check=False)
            if push.returncode == 0:
                return sha
            last_err = push.stderr.strip()
            # remote が先行 → rebase して取り込み、再試行（競合は ConflictError）
            self.sync()
        raise GitError(f"git push に失敗しました（リトライ後）: {last_err}")

    def status(self) -> dict:
        """同期状態の要約を返す。"""
        branch = self.current_branch()
        self._git("fetch", "origin", branch, check=False)
        ahead = behind = 0
        rl = self._git(
            "rev-list", "--left-right", "--count",
            f"origin/{branch}...HEAD", check=False,
        )
        if rl.returncode == 0 and rl.stdout.strip():
            parts = rl.stdout.split()
            if len(parts) == 2:
                behind, ahead = int(parts[0]), int(parts[1])
        return {
            "root": str(self.root),
            "branch": branch,
            "dirty": self._has_changes(),
            "ahead": ahead,
            "behind": behind,
        }
