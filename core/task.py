"""タスク操作ロジック。

GitHub Issue をタスクの実体、Projects v2 ボードをビューとして扱う。
Issue の作成・更新は必ず成功し、Projects v2 への登録・Status 設定はベストエフォート。
"""
from __future__ import annotations

from services.github_tasks import GitHubTasks, GitHubError, STATUS_VALUES


class TaskService:
    def __init__(self) -> None:
        self._gh: GitHubTasks | None = None

    @property
    def gh(self) -> GitHubTasks:
        if self._gh is None:
            self._gh = GitHubTasks()
        return self._gh

    def _board_status(self, issue_number: int) -> str | None:
        """ボード上の Status 名を返す（取得できなければ None）。"""
        project = self.gh.ensure_project()
        if not project:
            return None
        try:
            for item in self.gh.board_items(project["id"]):
                if item["number"] == issue_number:
                    return item["status"]
        except GitHubError:
            pass
        return None

    def _set_board_status(self, issue_number: int, status: str) -> bool:
        """ボード上の Issue の Status を設定する。成功可否を返す。"""
        project = self.gh.ensure_project()
        if not project:
            return False
        option_id = project["status"]["options"].get(status)
        field_id = project["status"]["field_id"]
        if not option_id or not field_id:
            return False
        try:
            for item in self.gh.board_items(project["id"]):
                if item["number"] == issue_number:
                    self.gh.set_status(project["id"], item["item_id"], field_id, option_id)
                    return True
        except GitHubError:
            return False
        return False

    def add(
        self,
        title: str,
        body: str = "",
        project: str | None = None,
        note: str | None = None,
        labels: list[str] | None = None,
    ) -> dict:
        """タスク（Issue）を作成し、ボードに登録して Status=Todo にする。

        - ``note``: 紐づける ZK ノートの ID またはパス（Issue 本文に追記）。
        - ``project``: 紐づけるプロジェクト/テーマ名（Issue 本文に追記）。
        """
        lines = [body] if body else []
        if project:
            lines.append(f"\n**プロジェクト:** {project}")
        if note:
            lines.append(f"**関連ノート:** {note}")
        full_body = "\n".join(lines).strip()

        issue = self.gh.create_issue(title, full_body, labels)

        board = False
        proj = self.gh.ensure_project()
        if proj:
            try:
                item_id = self.gh.add_to_board(proj["id"], issue["node_id"])
                option_id = proj["status"]["options"].get("Todo")
                field_id = proj["status"]["field_id"]
                if item_id and option_id and field_id:
                    self.gh.set_status(proj["id"], item_id, field_id, option_id)
                board = True
            except GitHubError as e:
                print(f"[task] ボード登録をスキップ: {e}")

        return {
            "number": issue["number"],
            "title": issue["title"],
            "url": issue["url"],
            "status": "Todo" if board else "(ボード未連携)",
            "on_board": board,
        }

    def list(self, status: str | None = None, project: str | None = None) -> list[dict]:
        """タスク一覧を返す（status / project で絞り込み可）。"""
        state = "all"
        if status == "Done":
            state = "closed"
        elif status in ("Todo", "In Progress"):
            state = "open"

        issues = self.gh.list_issues(state=state)

        # ボードの Status を一括取得してマージ
        board_map: dict[int, str] = {}
        proj = self.gh.ensure_project()
        if proj:
            try:
                board_map = {
                    it["number"]: it["status"]
                    for it in self.gh.board_items(proj["id"])
                }
            except GitHubError:
                pass

        out = []
        for it in issues:
            st = board_map.get(it["number"]) or ("Done" if it["state"] == "closed" else "Todo")
            if status and st != status:
                continue
            if project and f"プロジェクト:** {project}" not in self._safe_body(it["number"]):
                continue
            out.append({**it, "board_status": st})
        return out

    def _safe_body(self, number: int) -> str:
        try:
            return self.gh.get_issue(number).get("body", "") or ""
        except GitHubError:
            return ""

    def show(self, number: int) -> dict:
        issue = self.gh.get_issue(number)
        issue["board_status"] = self._board_status(number) or (
            "Done" if issue["state"] == "closed" else "Todo"
        )
        return issue

    def update(self, number: int, status: str) -> dict:
        """タスクの Status を更新する。Done なら Issue もクローズする。"""
        if status not in STATUS_VALUES:
            raise ValueError(f"status は {STATUS_VALUES} のいずれか: {status}")
        board_ok = self._set_board_status(number, status)
        if status == "Done":
            self.gh.close_issue(number)
        else:
            self.gh.update_issue(number, state="open")
        return {"number": number, "status": status, "board_updated": board_ok}

    def done(self, number: int) -> dict:
        return self.update(number, "Done")
