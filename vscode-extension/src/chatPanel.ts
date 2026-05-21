import * as vscode from "vscode";
import { EngineClient } from "./engineClient";

/** 会話セッション用の Webview チャットパネル。 */
export class ChatPanel {
  private static current: ChatPanel | undefined;
  private readonly panel: vscode.WebviewPanel;
  private disposables: vscode.Disposable[] = [];

  private constructor(
    private readonly engine: EngineClient,
    private sessionId: string,
    private readonly command: string
  ) {
    this.panel = vscode.window.createWebviewPanel(
      "secondBrainChat",
      `Second Brain: ${command}`,
      vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true }
    );
    this.panel.webview.html = this.html();
    this.panel.webview.onDidReceiveMessage(
      (msg) => this.onMessage(msg),
      null,
      this.disposables
    );
    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
  }

  /** セッションを開始済みの状態でパネルを開く。 */
  static open(
    engine: EngineClient,
    sessionId: string,
    command: string,
    firstUser: string,
    firstReply: string
  ): void {
    const panel = new ChatPanel(engine, sessionId, command);
    ChatPanel.current = panel;
    panel.post({ type: "append", role: "user", text: firstUser });
    panel.post({ type: "append", role: "assistant", text: firstReply });
  }

  private post(msg: unknown): void {
    void this.panel.webview.postMessage(msg);
  }

  private async onMessage(msg: any): Promise<void> {
    if (msg.type === "send") {
      this.post({ type: "append", role: "user", text: msg.text });
      this.post({ type: "busy", value: true });
      try {
        const res = await this.engine.continueSession(this.sessionId, msg.text);
        this.post({ type: "append", role: "assistant", text: res.reply });
      } catch (e) {
        this.post({ type: "status", text: `エラー: ${this.errMsg(e)}` });
      } finally {
        this.post({ type: "busy", value: false });
      }
    } else if (msg.type === "save") {
      this.post({ type: "busy", value: true });
      try {
        const res = await this.engine.saveSession(this.sessionId);
        this.post({
          type: "status",
          text: `保存しました: ${res.note_id}（${res.path}）`,
        });
        vscode.window.showInformationMessage(`ノートを保存しました: ${res.note_id}`);
      } catch (e) {
        this.post({ type: "status", text: `保存失敗: ${this.errMsg(e)}` });
      } finally {
        this.post({ type: "busy", value: false });
      }
    }
  }

  private errMsg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  private dispose(): void {
    ChatPanel.current = undefined;
    this.panel.dispose();
    while (this.disposables.length) {
      this.disposables.pop()?.dispose();
    }
  }

  private html(): string {
    return `<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8" />
<style>
  body { font-family: var(--vscode-font-family); padding: 0; margin: 0; }
  #log { padding: 12px; overflow-y: auto; height: calc(100vh - 92px); }
  .msg { margin: 8px 0; padding: 8px 10px; border-radius: 6px; white-space: pre-wrap; }
  .user { background: var(--vscode-editor-inactiveSelectionBackground); }
  .assistant { background: var(--vscode-textBlockQuote-background); }
  .role { font-size: 11px; opacity: .6; margin-bottom: 2px; }
  #bar { position: fixed; bottom: 0; left: 0; right: 0; display: flex; gap: 6px;
         padding: 8px; background: var(--vscode-editor-background);
         border-top: 1px solid var(--vscode-panel-border); }
  #input { flex: 1; padding: 6px; background: var(--vscode-input-background);
           color: var(--vscode-input-foreground);
           border: 1px solid var(--vscode-input-border); }
  button { padding: 6px 12px; cursor: pointer;
           background: var(--vscode-button-background);
           color: var(--vscode-button-foreground); border: none; }
  #status { font-size: 12px; opacity: .8; padding: 2px 12px; }
</style></head>
<body>
  <div id="log"></div>
  <div id="status"></div>
  <div id="bar">
    <input id="input" placeholder="続けて話しかける..." />
    <button id="send">送信</button>
    <button id="save">保存</button>
  </div>
<script>
  const vscode = acquireVsCodeApi();
  const log = document.getElementById('log');
  const input = document.getElementById('input');
  const status = document.getElementById('status');
  function append(role, text) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.innerHTML = '<div class="role">' + (role === 'user' ? 'あなた' : 'AI') + '</div>';
    const body = document.createElement('div');
    body.textContent = text;
    div.appendChild(body);
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }
  function send() {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    vscode.postMessage({ type: 'send', text });
  }
  document.getElementById('send').addEventListener('click', send);
  document.getElementById('save').addEventListener('click', () => vscode.postMessage({ type: 'save' }));
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  window.addEventListener('message', (e) => {
    const m = e.data;
    if (m.type === 'append') append(m.role, m.text);
    else if (m.type === 'status') status.textContent = m.text;
    else if (m.type === 'busy') status.textContent = m.value ? '処理中...' : '';
  });
</script>
</body></html>`;
  }
}
