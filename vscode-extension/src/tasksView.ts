import * as vscode from "vscode";
import { EngineClient } from "./engineClient";

export interface TaskItem {
  number: number;
  title: string;
  url: string;
  state: string;
  board_status?: string;
}

export class TaskTreeItem extends vscode.TreeItem {
  constructor(public readonly task: TaskItem) {
    super(`#${task.number} ${task.title}`, vscode.TreeItemCollapsibleState.None);
    const status = task.board_status ?? task.state;
    this.description = status;
    this.contextValue = "task";
    this.tooltip = `${task.title}\n${status}\n${task.url}`;
    this.command = {
      command: "secondBrain.taskOpen",
      title: "Open",
      arguments: [task],
    };
    this.iconPath = new vscode.ThemeIcon(
      status === "Done"
        ? "pass-filled"
        : status === "In Progress"
          ? "sync"
          : "circle-large-outline"
    );
  }
}

/** Second Brain タスク（GitHub Issue）の TreeView プロバイダ。 */
export class TasksProvider implements vscode.TreeDataProvider<TaskTreeItem> {
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  constructor(private readonly engine: EngineClient) {}

  refresh(): void {
    this._onDidChange.fire();
  }

  getTreeItem(element: TaskTreeItem): vscode.TreeItem {
    return element;
  }

  async getChildren(): Promise<TaskTreeItem[]> {
    try {
      const res = await this.engine.taskList();
      const tasks: TaskItem[] = res.tasks ?? [];
      return tasks.map((t) => new TaskTreeItem(t));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      vscode.window.showWarningMessage(`タスク取得に失敗しました: ${msg}`);
      return [];
    }
  }
}
