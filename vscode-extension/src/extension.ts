import * as vscode from "vscode";
import { EngineClient } from "./engineClient";
import { ChatPanel } from "./chatPanel";
import { TasksProvider, TaskTreeItem, TaskItem } from "./tasksView";

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function activate(context: vscode.ExtensionContext): void {
  const engine = new EngineClient();
  const tasks = new TasksProvider(engine);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("secondBrainTasks", tasks)
  );

  /** 会話セッションを開始してチャットパネルを開く。 */
  async function startConversation(command: string): Promise<void> {
    const text = await vscode.window.showInputBox({
      prompt: `${command} の内容を入力`,
      ignoreFocusOut: true,
    });
    if (!text) {
      return;
    }
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `${command} を開始中...` },
      async () => {
        try {
          const res = await engine.startSession(command, text);
          if (res.error) {
            vscode.window.showWarningMessage(res.error);
            return;
          }
          ChatPanel.open(engine, res.session_id, command, text, res.reply);
        } catch (e) {
          vscode.window.showErrorMessage(`開始に失敗しました: ${errMsg(e)}`);
        }
      }
    );
  }

  for (const cmd of ["memo", "research", "planning", "chat"]) {
    context.subscriptions.push(
      vscode.commands.registerCommand(`secondBrain.${cmd}`, () => startConversation(cmd))
    );
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("secondBrain.link", async () => {
      const url = await vscode.window.showInputBox({
        prompt: "取り込む URL（記事 or YouTube）",
        ignoreFocusOut: true,
      });
      if (!url) {
        return;
      }
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "URL を取り込み中..." },
        async () => {
          try {
            const res = await engine.startLink(url);
            if (res.error) {
              vscode.window.showWarningMessage(res.error);
              return;
            }
            ChatPanel.open(engine, res.session_id, "link", url, res.reply);
          } catch (e) {
            vscode.window.showErrorMessage(`取り込みに失敗しました: ${errMsg(e)}`);
          }
        }
      );
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("secondBrain.search", async () => {
      const query = await vscode.window.showInputBox({
        prompt: "ノートを意味検索",
        ignoreFocusOut: true,
      });
      if (!query) {
        return;
      }
      try {
        const res = await engine.search(query, 8);
        const results: any[] = res.results ?? [];
        if (results.length === 0) {
          vscode.window.showInformationMessage("該当するノートがありません。");
          return;
        }
        const pick = await vscode.window.showQuickPick(
          results.map((r) => {
            const meta = r.metadata ?? {};
            return {
              label: meta.note_id ?? r.id,
              description: `${Math.round((1 - (r.distance ?? 1)) * 100)}%`,
              detail: meta.file_path ?? "",
            };
          }),
          { placeHolder: "開くノートを選択" }
        );
        const ws = vscode.workspace.workspaceFolders?.[0];
        if (pick?.detail && ws) {
          const uri = vscode.Uri.joinPath(ws.uri, pick.detail);
          await vscode.window.showTextDocument(uri);
        }
      } catch (e) {
        vscode.window.showErrorMessage(`検索に失敗しました: ${errMsg(e)}`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("secondBrain.sync", async () => {
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "同期中..." },
        async () => {
          try {
            const res = await engine.sync();
            vscode.window.showInformationMessage(
              `同期完了（${res.branch}）${res.auto_committed ? " / 手編集を自動コミット" : ""}`
            );
          } catch (e) {
            vscode.window.showErrorMessage(`同期に失敗しました: ${errMsg(e)}`);
          }
        }
      );
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("secondBrain.taskAdd", async () => {
      const title = await vscode.window.showInputBox({
        prompt: "タスクのタイトル",
        ignoreFocusOut: true,
      });
      if (!title) {
        return;
      }
      const project = await vscode.window.showInputBox({
        prompt: "プロジェクト/テーマ（任意）",
        ignoreFocusOut: true,
      });
      try {
        const res = await engine.taskAdd(title, project || undefined);
        vscode.window.showInformationMessage(`タスク作成: #${res.number}（${res.status}）`);
        tasks.refresh();
      } catch (e) {
        vscode.window.showErrorMessage(`タスク作成に失敗しました: ${errMsg(e)}`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("secondBrain.tasksRefresh", () => tasks.refresh())
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("secondBrain.taskDone", async (item?: TaskTreeItem) => {
      if (!item) {
        return;
      }
      try {
        await engine.taskDone(item.task.number);
        vscode.window.showInformationMessage(`タスク #${item.task.number} を完了`);
        tasks.refresh();
      } catch (e) {
        vscode.window.showErrorMessage(`更新に失敗しました: ${errMsg(e)}`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("secondBrain.taskOpen", (task?: TaskItem) => {
      if (task?.url) {
        void vscode.env.openExternal(vscode.Uri.parse(task.url));
      }
    })
  );
}

export function deactivate(): void {
  /* no-op */
}
