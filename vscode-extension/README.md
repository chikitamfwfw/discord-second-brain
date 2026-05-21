# Second Brain — VSCode 拡張機能

VSCode から Second Brain を操作する拡張機能。会話・整形は Claude API、保存・同期・
検索・タスクは常駐エンジン（デーモン）が担う。Claude Code を使わない操作経路。

## セットアップ

```bash
cd vscode-extension
npm install
npm run compile
```

VSCode で `vscode-extension/` を開き、F5（拡張機能ホストの起動）でデバッグ実行できる。
配布用にパッケージするには `npx vsce package`。

## 前提

- 隣接する `digital-brain`（旧 `discord-second-brain`）エンジンが利用可能なこと。
  拡張機能はデーモンを自動起動する（未起動時）。
- 拡張機能モードの会話には `ANTHROPIC_API_KEY` が必要（エンジン側 `.env`）。

## 設定（settings.json）

| キー | 既定値 | 説明 |
|---|---|---|
| `secondBrain.enginePath` | （空） | エンジンのディレクトリ。空ならワークスペース隣接を自動探索 |
| `secondBrain.pythonPath` | `python3` | Python 実行パス |
| `secondBrain.daemonUrl` | `http://127.0.0.1:8765` | デーモンの URL |

## 機能

- コマンドパレット: `Second Brain: メモ / リサーチ / プランニング / チャット /
  リンク取り込み / 検索 / 同期 / タスク追加`
- 会話コマンドは Webview チャットパネルを開き、続けて対話・保存できる
- エクスプローラの「Second Brain タスク」ビューで GitHub Issue を一覧・完了
