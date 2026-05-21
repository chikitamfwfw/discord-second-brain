"""GitHub タスク連携クライアント。

タスクの実体は **GitHub Issue**（REST API）、ビューは **Projects v2 ボード**
（GraphQL API）。Issue が source of truth で、Projects v2 への登録・Status 設定は
ベストエフォート（失敗しても Issue は作成される）。

`GITHUB_TOKEN`（repo + project スコープ）と `GITHUB_REPO`（owner/repo）が必要。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import config

_REST = "https://api.github.com"
_GQL = "https://api.github.com/graphql"

# Projects v2 の Status フィールドで使う値
STATUS_VALUES = ("Todo", "In Progress", "Done")


class GitHubError(RuntimeError):
    """GitHub API 呼び出しの失敗。"""


class GitHubTasks:
    def __init__(self) -> None:
        if not config.GITHUB_TOKEN:
            raise GitHubError("GITHUB_TOKEN が未設定です（タスク管理に必要）。")
        if "/" not in config.GITHUB_REPO:
            raise GitHubError("GITHUB_REPO は 'owner/repo' 形式で設定してください。")
        self.owner, self.repo = config.GITHUB_REPO.split("/", 1)
        self._token = config.GITHUB_TOKEN
        self._project_cache: dict | None = None

    # ── 低レベル HTTP ───────────────────────────────────────────────────────
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "second-brain-engine",
        }

    def _rest(self, method: str, path: str, body: dict | None = None) -> dict | list:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            _REST + path, data=data, method=method, headers=self._headers()
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise GitHubError(f"GitHub REST {method} {path}: {e.code} {e.read().decode('utf-8', 'ignore')}") from e

    def _gql(self, query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = urllib.request.Request(_GQL, data=body, method="POST", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise GitHubError(f"GitHub GraphQL: {e.code} {e.read().decode('utf-8', 'ignore')}") from e
        if payload.get("errors"):
            raise GitHubError(f"GitHub GraphQL: {payload['errors']}")
        return payload["data"]

    # ── Issues（タスクの実体） ──────────────────────────────────────────────
    def create_issue(self, title: str, body: str = "", labels: list[str] | None = None) -> dict:
        issue = self._rest(
            "POST", f"/repos/{self.owner}/{self.repo}/issues",
            {"title": title, "body": body, "labels": labels or []},
        )
        return {
            "number": issue["number"],
            "title": issue["title"],
            "url": issue["html_url"],
            "node_id": issue["node_id"],
            "state": issue["state"],
        }

    def list_issues(self, state: str = "open") -> list[dict]:
        items = self._rest(
            "GET",
            f"/repos/{self.owner}/{self.repo}/issues"
            f"?state={state}&per_page=100&filter=all",
        )
        out = []
        for it in items:
            if "pull_request" in it:  # PR は除外
                continue
            out.append({
                "number": it["number"],
                "title": it["title"],
                "url": it["html_url"],
                "state": it["state"],
                "labels": [lb["name"] for lb in it.get("labels", [])],
            })
        return out

    def get_issue(self, number: int) -> dict:
        it = self._rest("GET", f"/repos/{self.owner}/{self.repo}/issues/{number}")
        return {
            "number": it["number"], "title": it["title"], "url": it["html_url"],
            "state": it["state"], "body": it.get("body", ""),
            "node_id": it["node_id"],
            "labels": [lb["name"] for lb in it.get("labels", [])],
        }

    def update_issue(self, number: int, **fields: object) -> dict:
        it = self._rest(
            "PATCH", f"/repos/{self.owner}/{self.repo}/issues/{number}", dict(fields)
        )
        return {"number": it["number"], "state": it["state"], "url": it["html_url"]}

    def close_issue(self, number: int) -> dict:
        return self.update_issue(number, state="closed")

    # ── Projects v2（ビュー） ───────────────────────────────────────────────
    def _owner_id(self) -> str:
        data = self._gql(
            "query($l:String!){ repositoryOwner(login:$l){ id } }", {"l": self.owner}
        )
        owner = data.get("repositoryOwner")
        if not owner:
            raise GitHubError(f"owner が見つかりません: {self.owner}")
        return owner["id"]

    def ensure_project(self) -> dict | None:
        """Projects v2 ボードを取得（無ければ作成）し、Status フィールド情報を返す。

        GITHUB_PROJECT_NUMBER が設定されていればその番号のボードを使う。
        失敗時は None を返す（Issue 運用は継続できる）。
        """
        if self._project_cache is not None:
            return self._project_cache
        try:
            number = config.GITHUB_PROJECT_NUMBER
            project = None
            if number:
                data = self._gql(
                    "query($l:String!,$n:Int!){ user(login:$l){ projectV2(number:$n){ id title } } "
                    "organization(login:$l){ projectV2(number:$n){ id title } } }",
                    {"l": self.owner, "n": number},
                )
                holder = data.get("user") or data.get("organization") or {}
                project = holder.get("projectV2")
            if project is None:
                # ボードを新規作成
                created = self._gql(
                    "mutation($o:ID!,$t:String!){ createProjectV2(input:{ownerId:$o,title:$t})"
                    "{ projectV2{ id title number } } }",
                    {"o": self._owner_id(), "t": config.GITHUB_PROJECT_TITLE},
                )
                project = created["createProjectV2"]["projectV2"]

            status = self._status_field(project["id"])
            self._project_cache = {"id": project["id"], "status": status}
            return self._project_cache
        except GitHubError as e:
            print(f"[github_tasks] Projects v2 連携をスキップ: {e}")
            return None

    def _status_field(self, project_id: str) -> dict:
        data = self._gql(
            "query($p:ID!){ node(id:$p){ ... on ProjectV2 { field(name:\"Status\"){ "
            "... on ProjectV2SingleSelectField { id options{ id name } } } } } }",
            {"p": project_id},
        )
        field = (data.get("node") or {}).get("field") or {}
        options = {o["name"]: o["id"] for o in field.get("options", [])}
        return {"field_id": field.get("id"), "options": options}

    def add_to_board(self, project_id: str, issue_node_id: str) -> str:
        data = self._gql(
            "mutation($p:ID!,$c:ID!){ addProjectV2ItemById(input:{projectId:$p,contentId:$c})"
            "{ item{ id } } }",
            {"p": project_id, "c": issue_node_id},
        )
        return data["addProjectV2ItemById"]["item"]["id"]

    def board_items(self, project_id: str) -> list[dict]:
        """ボード上の項目（Issue 番号・item ID・Status 名）を一覧で返す。"""
        data = self._gql(
            "query($p:ID!){ node(id:$p){ ... on ProjectV2 { items(first:100){ nodes{ id "
            "content{ ... on Issue { number } } "
            "fieldValueByName(name:\"Status\"){ "
            "... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } }",
            {"p": project_id},
        )
        nodes = (((data.get("node") or {}).get("items")) or {}).get("nodes") or []
        out = []
        for n in nodes:
            content = n.get("content") or {}
            if "number" not in content:
                continue
            value = n.get("fieldValueByName") or {}
            out.append({
                "item_id": n["id"],
                "number": content["number"],
                "status": value.get("name"),
            })
        return out

    def set_status(self, project_id: str, item_id: str, field_id: str, option_id: str) -> None:
        self._gql(
            "mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue("
            "input:{projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}})"
            "{ projectV2Item{ id } } }",
            {"p": project_id, "i": item_id, "f": field_id, "o": option_id},
        )
