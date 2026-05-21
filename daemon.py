"""常駐ローカルエンジン（デーモン）。

ChromaDB の埋め込みモデルとセッションをウォーム保持し、localhost の HTTP API として
Engine の機能を公開する。Claude Code（`brain` CLI 経由）と VSCode 拡張機能の双方が
この同じデーモンを叩くことで、2 回目以降の操作が高速になる。

起動:
    python daemon.py
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config
from core.engine import Engine
from services.vault import ConflictError, GitError

_engine: Engine | None = None
_lock = threading.Lock()


def _route(method: str, path: str, body: dict, query: dict) -> tuple[int, dict]:
    """1 リクエストを処理して (HTTP ステータス, レスポンス dict) を返す。"""
    assert _engine is not None
    eng = _engine

    if method == "GET" and path == "/status":
        return 200, eng.status()
    if method == "GET" and path == "/health":
        return 200, {"ok": True}
    if method == "POST" and path == "/sync":
        return 200, eng.sync()
    if method == "POST" and path == "/index":
        return 200, {"indexed": eng.index()}
    if method == "POST" and path == "/search":
        return 200, {"results": eng.search(body["query"], int(body.get("n", 5)))}
    if method == "POST" and path == "/fetch-url":
        return 200, eng.fetch_url(body["url"])
    if method == "POST" and path == "/fetch-youtube":
        return 200, eng.fetch_youtube(body["url"])
    if method == "POST" and path == "/note":
        return 200, eng.save_note(
            body["note_type"], body["content"], body.get("tags"),
        )
    if method == "POST" and path == "/session/start":
        return 200, eng.start(body["command"], body["text"])
    if method == "POST" and path == "/session/start-link":
        return 200, eng.start_link(body["url"])
    if method == "POST" and path == "/session/continue":
        return 200, eng.continue_session(body["session_id"], body["message"])
    if method == "POST" and path == "/session/save":
        return 200, eng.save(body["session_id"])
    if method == "POST" and path == "/session/permanent":
        return 200, eng.make_permanent(body["session_id"])
    if method == "POST" and path == "/session/discard":
        return 200, eng.discard(body["session_id"])
    if method == "POST" and path == "/task/add":
        return 200, eng.tasks.add(
            body["title"], body.get("body", ""), body.get("project"),
            body.get("note"), body.get("labels"),
        )
    if method == "POST" and path == "/task/list":
        return 200, {"tasks": eng.tasks.list(body.get("status"), body.get("project"))}
    if method == "POST" and path == "/task/update":
        return 200, eng.tasks.update(int(body["number"]), body["status"])
    if method == "POST" and path == "/task/done":
        return 200, eng.tasks.done(int(body["number"]))
    if method == "GET" and path == "/task/show":
        return 200, eng.tasks.show(int((query.get("number") or ["0"])[0]))

    if method == "GET" and path == "/session/list":
        return 200, {
            "sessions": [
                {"id": s.id, "command": s.command, "active": s.active,
                 "updated_at": s.updated_at, "saved_note_id": s.saved_note_id}
                for s in eng.sessions.list(active_only=False)
            ]
        }
    if method == "GET" and path == "/session/get":
        sid = (query.get("id") or [""])[0]
        s = eng.sessions.get(sid)
        if s is None:
            return 404, {"error": f"session not found: {sid}"}
        return 200, {
            "id": s.id, "command": s.command, "active": s.active,
            "history": s.history, "topic": s.topic,
            "saved_note_id": s.saved_note_id,
        }

    return 404, {"error": f"unknown route: {method} {path}"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102 - quiet logging
        pass

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        body: dict = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send(400, {"error": "invalid JSON body"})
                    return
        query = parse_qs(parsed.query)
        try:
            with _lock:
                status, payload = _route(method, parsed.path, body, query)
        except ConflictError as e:
            status, payload = 409, {"error": str(e), "conflict": e.files}
        except (KeyError, ValueError) as e:
            status, payload = 400, {"error": str(e)}
        except GitError as e:
            status, payload = 502, {"error": str(e)}
        except Exception as e:  # noqa: BLE001 - surface everything to the client
            status, payload = 500, {"error": f"{type(e).__name__}: {e}"}
        self._send(status, payload)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")


def _background_sync() -> None:
    """SYNC_INTERVAL_SECONDS ごとにバックグラウンドで GitHub と同期する。"""
    import time

    if config.SYNC_INTERVAL_SECONDS <= 0:
        return
    while True:
        time.sleep(config.SYNC_INTERVAL_SECONDS)
        try:
            with _lock:
                assert _engine is not None
                _engine.sync()
        except Exception as e:  # noqa: BLE001
            print(f"[daemon] background sync skipped: {e}")


def main() -> None:
    global _engine
    print("[daemon] starting — loading engine and embedding model...")
    _engine = Engine()
    _engine.warm_up()
    threading.Thread(target=_background_sync, daemon=True).start()

    server = ThreadingHTTPServer((config.DAEMON_HOST, config.DAEMON_PORT), Handler)
    print(f"[daemon] ready on http://{config.DAEMON_HOST}:{config.DAEMON_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[daemon] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
