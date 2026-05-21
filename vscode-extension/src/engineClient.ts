import * as http from "http";
import * as cp from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

/** 常駐エンジン（デーモン）への HTTP クライアント。未起動なら自動起動する。 */
export class EngineClient {
  private get cfg() {
    return vscode.workspace.getConfiguration("secondBrain");
  }

  private get baseUrl(): string {
    return this.cfg.get<string>("daemonUrl", "http://127.0.0.1:8765");
  }

  /** エンジン（cli.py / daemon.py）のディレクトリを解決する。 */
  private resolveEnginePath(): string | undefined {
    const explicit = this.cfg.get<string>("enginePath", "");
    if (explicit && fs.existsSync(path.join(explicit, "daemon.py"))) {
      return explicit;
    }
    const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (ws) {
      for (const sibling of ["digital-brain", "discord-second-brain"]) {
        const candidate = path.join(path.dirname(ws), sibling);
        if (fs.existsSync(path.join(candidate, "daemon.py"))) {
          return candidate;
        }
      }
    }
    return undefined;
  }

  private requestRaw(method: string, p: string, body?: unknown): Promise<any> {
    const url = new URL(this.baseUrl + p);
    const data = body !== undefined ? JSON.stringify(body) : undefined;
    return new Promise((resolve, reject) => {
      const req = http.request(
        {
          hostname: url.hostname,
          port: url.port,
          path: url.pathname + url.search,
          method,
          headers: data
            ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) }
            : {},
          timeout: 600000,
        },
        (res) => {
          let raw = "";
          res.on("data", (c) => (raw += c));
          res.on("end", () => {
            let payload: any = {};
            try {
              payload = raw ? JSON.parse(raw) : {};
            } catch {
              payload = { error: raw };
            }
            if ((res.statusCode ?? 500) >= 400) {
              reject(new Error(payload.error || `HTTP ${res.statusCode}`));
            } else {
              resolve(payload);
            }
          });
        }
      );
      req.on("error", reject);
      req.on("timeout", () => req.destroy(new Error("daemon timeout")));
      if (data) {
        req.write(data);
      }
      req.end();
    });
  }

  private async isUp(): Promise<boolean> {
    try {
      await this.requestRaw("GET", "/health");
      return true;
    } catch {
      return false;
    }
  }

  /** デーモンが起動していなければ起動し、応答可能になるまで待つ。 */
  private async ensureDaemon(): Promise<void> {
    if (await this.isUp()) {
      return;
    }
    const enginePath = this.resolveEnginePath();
    if (!enginePath) {
      throw new Error(
        "エンジンが見つかりません。設定 secondBrain.enginePath に digital-brain のパスを指定してください。"
      );
    }
    const python = this.cfg.get<string>("pythonPath", "python3");
    const child = cp.spawn(python, [path.join(enginePath, "daemon.py")], {
      cwd: enginePath,
      detached: true,
      stdio: "ignore",
    });
    child.unref();

    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 500));
      if (await this.isUp()) {
        return;
      }
    }
    throw new Error("デーモンの起動がタイムアウトしました。");
  }

  async call(method: string, p: string, body?: unknown): Promise<any> {
    await this.ensureDaemon();
    return this.requestRaw(method, p, body);
  }

  // ── 型付きメソッド ────────────────────────────────────────────────────
  sync() {
    return this.call("POST", "/sync");
  }
  status() {
    return this.call("GET", "/status");
  }
  search(query: string, n = 5) {
    return this.call("POST", "/search", { query, n });
  }
  startSession(command: string, text: string) {
    return this.call("POST", "/session/start", { command, text });
  }
  startLink(url: string) {
    return this.call("POST", "/session/start-link", { url });
  }
  continueSession(sessionId: string, message: string) {
    return this.call("POST", "/session/continue", { session_id: sessionId, message });
  }
  saveSession(sessionId: string) {
    return this.call("POST", "/session/save", { session_id: sessionId });
  }
  permanentSession(sessionId: string) {
    return this.call("POST", "/session/permanent", { session_id: sessionId });
  }
  taskList() {
    return this.call("POST", "/task/list", {});
  }
  taskAdd(title: string, project?: string, note?: string) {
    return this.call("POST", "/task/add", { title, project, note });
  }
  taskDone(num: number) {
    return this.call("POST", "/task/done", { number: num });
  }
}
