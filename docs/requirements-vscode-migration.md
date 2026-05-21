# 要件定義書: Second Brain の Discord 運用 → VSCode 運用への移行

> 本ドキュメントは「要件定義」です。実装は別途フェーズで進める。
> 対象リポジトリ: `digital-brain`（旧 `discord-second-brain`、エンジン）/ `second-brain`（ボルト）

## 1. Context（背景・目的）

現状 `discord-second-brain`（移行後 `digital-brain` へ改名）は Discord 上で動く Bot で、`/memo` `/link` `/research`
`/planning` `/search` `/chat` のスラッシュコマンドと「💾 保存」ボタン UI で
`second-brain` ボルト（Zettelkasten 形式の Markdown ノート群）に知識を蓄積している。

### 現状の課題
- **Discord 依存**で VSCode 中心の作業フローから外れる。
- **セッション消失事故**: セッションはメモリ上の辞書（`session/manager.py`）で管理され、
  Discord ボタンのタイムアウト（保存ビュー30分・Permanent化10分・ペイウォール5分／
  `on_timeout` で `SessionManager.close()`）や Bot 再起動で消える。長い会話の途中で
  「セッションが見つかりません」となり、ゼロからやり直す事故が発生している。
- **保存が GitHub API 経由**（`github_client.py` の PyGithub）で、ローカル編集と乖離しやすい。
- **既存ノートの形式不具合**: `compile_to_note` の出力整形バグで、一部ノートが
  二重フロントマター + ```` ```markdown ```` コードフェンスで包まれた壊れた形式になっている
  （確認済み: `10-notes/fleeting/ZK-20260504-025550.md`,
  `20-research/ZK-20260504-032559.md` など。新しい `30-planning/ZK-20260515-013313.md` は正常）。
  ※ コード側の整形バグ修正はコミット `964d41c` で対応済み。既存の壊れたノートの正規化は別途必要。

### 目的
Discord を完全廃止し、**Claude Code と VSCode 拡張機能の両方**から VSCode 上で
操作できるようにする。GitHub を常に最新（= 正）に保ち、**セッションは絶対に切れない**ようにする。

## 2. 確定方針（ユーザー回答）

1. 操作形態: **Claude Code と VSCode 拡張機能の両方**。
2. 保存: **ローカル + Git**。実行前に必ずローカル⇄GitHub の同期を確認し、
   GitHub を正として整合を取る。
3. 機能範囲: **全機能維持**。かつ**セッションは絶対に切れない**（永続化）。
4. 会話エンジン: **速度が絶対条件**。Claude Code 経由では Claude Code 自身が会話を担当
   （高速）。拡張機能経由では Claude API。どちらのモードでも、AI は必ず
   「同期済みボルト（= GitHub = ローカル が一致した状態）」を参照する
   （ローカルだけ／GitHub だけを参照する運用は禁止）。
5. Discord: **完全廃止**（`discord.py` 依存・`bot.py`・handlers の Discord UI を削除）。
6. 同期競合: ローカル手編集は**自動コミットで保護**。GitHub 直接編集も想定し、
   §6 の同期ポリシーで対応する。
7. リポジトリ改名: `discord-second-brain` → **`digital-brain`**。`second-brain` は名称維持。
   ※ GitHub リポジトリ名の変更は GitHub の Settings 画面での手動操作。
   改名後は GitHub が旧名を自動リダイレクトするため既存 clone はそのまま使える。本タスクの
   開発自体は現リポジトリ名のブランチ上で進める。
8. タスク管理を新規追加: GitHub Issues をタスクの実体、Projects v2 ボードをビューとして
   `second-brain` に導入する（§5.1）。
9. Web 検索は両モードで利用可能（Claude Code モード = Claude Code の検索ツール、拡張機能
   モード = Tavily）。AI による会話・整形・調査機能は両モード共通の必須要件。

## 3. 移行後アーキテクチャ

中心に **常駐ローカルエンジン（デーモン）** を置く。
理由: 「動作が早いこと」が絶対条件のため、ChromaDB の埋め込みモデル（約420MB,
`paraphrase-multilingual-mpnet-base-v2`）を毎回ロードしないよう、エンジンプロセスを
起動しっぱなしにしてモデルとセッションをウォーム保持する。

```
┌──────────────────── VSCode ────────────────────┐
│  Claude Code                    VSCode 拡張機能  │
│  (.claude/commands を実行)       (TypeScript)    │
│         │                            │          │
│         └─────────────┬──────────────┘          │
│                       ▼                          │
│        常駐ローカルエンジン (localhost daemon)    │
│        - ChromaDB + 埋め込みモデル (warm 保持)    │
│        - セッション (メモリ + ディスク永続)       │
│        - Git 同期ガード                           │
│        - スクレイピング / YouTube 書き起こし      │
│        - 意味検索                                 │
│        - Claude API (拡張機能モードの会話のみ)    │
└───────────────────────┬──────────────────────────┘
                        ▼
              second-brain ボルト (ローカル git)
                        ▲
                        │ fetch / pull / commit / push
                        ▼
                     GitHub (常に最新 = 正)
```

### 会話エンジンの 2 モード
- **Claude Code モード**: 会話・ノート整形・タグ付けは Claude Code 自身が担当。
  Web 検索も Claude Code のツールを使用。エンジンは「保存・Git 同期・スクレイピング・
  意味検索」の処理専用。`ANTHROPIC_API_KEY` 不要。セッションは Claude Code のチャット
  自体（本質的に永続）。
- **拡張機能モード**: 会話・整形・タグ付けは Claude API（`claude_client.py` 流用）。
  Web 検索は Tavily（`tavily_client.py`）。セッションはエンジンがディスク永続化。
- 両モードとも**同じノート形式・同じ Git 同期・同じ ChromaDB**を共有する。

## 4. リポジトリ別の役割

- **`digital-brain`（エンジン、旧 `discord-second-brain`）**: Discord 依存を全削除し、
  常駐エンジン + `brain` CLI + VSCode 拡張機能 + GitHub タスク連携に再構成。
- **`second-brain`（ボルト、名称維持）**: ノート本体に加え、Claude Code 用の
  `.claude/commands/` と `CLAUDE.md` を追加。タスク管理用の Issues / Projects v2 もここに持つ。
- リポジトリ改名は GitHub Settings での手動操作（§2-7）。自動リダイレクトにより改名前後
  どちらでも既存のリモート設定はそのまま使える。

## 5. 機能要件（全機能維持）

共通フロー「会話 → 保存」を維持する。コマンド実行直後にテンプレートは出さず、
自然な会話で深掘りした後に保存操作で構造化ノートに整理する。

| コマンド | 入力 | 振る舞い | 保存先 |
|---|---|---|---|
| memo | テキスト | メモを深掘り会話。保存で Fleeting ノート化、Permanent 化も可 | `10-notes/fleeting/`（実行時に `00-inbox/` にも生メモ） |
| link | URL | 記事スクレイピング or YouTube 字幕/Whisper 書き起こし → 議論 | `10-notes/literature/articles/` or `youtube/` |
| research | トピック | Web 検索 + 蓄積知識で調査・会話 | `20-research/` |
| planning | テーマ | ブレスト・目標整理の会話 | `30-planning/` |
| chat | メッセージ | Web 検索 + 過去ノート参照の自由会話。任意で保存 | `10-notes/fleeting/` |
| search | クエリ | ChromaDB 意味検索（保存なし） | — |
| permanent 化 | — | 保存済み Fleeting から Atomic な Permanent ノートを抽出 | `10-notes/permanent/` |
| task | タスク内容 | GitHub Issue を作成/更新/一覧。Projects v2 ボードへ自動追加。会話からの自動抽出も可（§5.1） | `second-brain` の Issues / Projects v2 |

- 各コマンドの実行前に必ず Git 同期ガード（§6）を通す。
- 保存時: ノート書き込み → `git add` → `git commit` → `git push` → ChromaDB upsert。
- ペイウォール時の「URL のみ保存」、複数ページ記事、YouTube の API→Whisper
  フォールバックも維持。

### 5.1 タスク管理（新規機能）

GitHub Issues と Projects v2 を使い、`second-brain` ボルト上でタスクを管理する。

- **データモデル**: GitHub **Issue がタスクの実体**、GitHub **Projects v2 ボード1枚が
  唯一のビュー**（`Status` フィールド: Todo / In Progress / Done）。新規 Issue はボードへ
  自動追加。両者は二重管理ではなく「実体」と「見せ方」の関係 → これにより**ボード表示と
  一覧表示の両方**を1か所の管理で提供できる。ラベル・マイルストーンは期限/分類用の任意の補助。
- **プロジェクト紐づけ**: タスクは「プロジェクト（テーマ）」に紐づける。Projects v2 の
  カスタムフィールド `Project` でグルーピングし、`30-planning/` の planning ノートと対応させる。
- **作成方法（両方）**:
  - 明示作成: `/task`（Claude Code）/ `brain task` CLI / 拡張機能のタスクパネルから作成・更新。
  - 自動抽出: planning / memo セッションの保存時に AI が会話からアクションアイテムを抽出し、
    ユーザー確認の上で Issue 化する。
- **ノートとの相互リンク**: Issue 本文に元ノート（ZK-id / パス）を記載。ノートのフロントマターに
  `tasks:`（Issue 番号 / URL）を追記し、双方向に辿れるようにする。
- **一覧確認**: `brain task list`（`--project` / `--status` で絞り込み）、Claude Code の
  `/task list`、拡張機能の TreeView パネル。
- **実装メモ**: Issue 操作は GitHub REST API で容易。**Projects v2 は GitHub GraphQL API が
  必須**のため、エンジンに GitHub API クライアント（`GITHUB_TOKEN` 使用）を持たせる。
  ノート保存はローカル git のまま、タスク操作のみ GitHub API を使う。

## 6. Git 同期ポリシー（最重要）

### 実行前ガード（全コマンド共通）
1. `git fetch origin <branch>`。
2. ローカルに未コミット変更があれば**自動コミット**（例: `chore: auto-save local edits`）。
   → VSCode での手編集を失わない。
3. `git pull --rebase origin <branch>` → GitHub 直接編集を取り込む。
4. rebase でコンフリクト発生時は rebase を中断し、**処理を停止して競合ファイルを通知**。
   人間が手動解決する（自動で破棄しない）。

### 保存後
5. ノート書き込み → `git add` → `git commit`（`add(memo): ZK-...` 形式）→ `git push`。
6. push が remote 先行で失敗 → fetch + rebase + リトライ（指数バックオフ 2/4/8/16s）。
   競合は §6-4 同様に通知。

### GitHub 直接編集についての提案
- GitHub を直接編集しても、次回操作時の §6-1 + §6-3 で自動的にローカルへ取り込まれる。
  基本的に GitHub 直接編集は問題なく扱える。
- 注意点: **同一ファイルをローカルと GitHub の両方で「操作と操作の間」に編集すると競合**する。
  その場合はツールが停止し競合を通知する（人間が解決）。
- 推奨運用: GitHub で直接編集したら、次の操作前に `/sync`（拡張機能では Sync コマンド）を
  1 回手動実行する。加えてエンジンは**バックグラウンドで定期 fetch**（旧 10 分同期ループ相当）
  を行い、ローカルと GitHub の乖離を常時最小化する。
- 原則: AI が動く前に必ず同期を完了させ、AI は「同期済みローカルボルト（= GitHub と一致）」
  を参照する。同期せずに動かさない。

## 7. セッション永続化（「絶対に切れない」要件）

- **拡張機能モード**: セッションを JSON でディスク永続化
  （例: `digital-brain/.brain/sessions/<id>.json`、gitignore 対象）。
  **タイムアウトを撤廃**。エンジン再起動・VSCode 再起動でも復元可能。
- **Claude Code モード**: 会話は Claude Code のチャット自体が担うため本質的に永続
  （Web/アプリのセッション継続・コンテキスト圧縮により会話長も問題なし）。
  保存待ち（pending）状態はエンジンのセッションファイルにも記録し、別フロントエンドからも
  継続できるようにする。
- 旧 `session/manager.py` のメモリ辞書 + Discord ボタン `on_timeout` による `close()`
  を撤廃する。

## 8. データ移行（既存不具合の修正）

- **`compile_to_note` 整形修正**: 出力から ```` ```markdown ```` フェンスを除去し、
  フロントマターを 1 つに統一。タグはノート自身のフロントマター `tags:` に書き込む
  （`utils/formatters.py` の `inject_tags` を修正し、外側に別ブロックを作らない）。
  ※ コード側の修正はコミット `964d41c` で対応済み。
- **既存ノート正規化スクリプト**（一回限り）: 二重フロントマター/コードフェンスを持つ
  ノートを正常形式へ変換。確認済み対象: `10-notes/fleeting/ZK-20260504-025550.md`,
  `20-research/ZK-20260504-032559.md`。要確認: `10-notes/fleeting/ZK-20260503-235019.md`,
  `10-notes/literature/youtube/` 配下の 3 ファイル。

## 9. 主な変更ファイル

### digital-brain（エンジン、旧 discord-second-brain）
- **削除**: `bot.py`, `utils/discord_utils.py`、`requirements.txt` の `discord.py`。
- **Discord UI 除去**: `handlers/*.py` の `ui.View` / `app_commands` / `interaction`
  部分を削除し、コアロジックを Discord 非依存の `core/` モジュールへ抽出。
- **置換**: `services/github_client.py` → `services/vault.py`
  （ローカル FS 読み書き + git 操作。`_config`/`_templates` もローカルから読む）。
- **置換**: `services/github_syncer.py` → ローカルノートから ChromaDB を index
  （GitHub API 不使用）。
- **置換**: `session/manager.py` → ディスク永続セッションストア（timeout 廃止）。
- **流用**: `services/claude_client.py`（拡張モードの会話）, `knowledge_store.py`,
  `scraper.py`, `youtube_client.py`, `tavily_client.py`, `utils/formatters.py`,
  `utils/knowledge_ref.py`。
- **新規**: `daemon.py`（常駐エンジン, localhost API）, `cli.py`（`brain` CLI =
  デーモンの薄いクライアント）, `core/`（コマンドロジック）。
- **新規**: `services/github_tasks.py`（GitHub Issues = REST + Projects v2 = GraphQL の
  タスク連携クライアント。`GITHUB_TOKEN` 使用）, `core/task.py`（タスク作成/更新/一覧 +
  会話からのアクションアイテム自動抽出ロジック）。
- **新規**: `vscode-extension/`（TypeScript 拡張機能。コマンドパレット + チャット/Webview
  パネル。エンジンの localhost API を叩く）。
- `config.py`: Discord 系設定を削除し、`VAULT_PATH` 等を追加。
  `update_all_content.py` はローカルボルトへ書き込む形へ調整。

### second-brain（ボルト）
- **新規**: `.claude/commands/{memo,link,research,planning,chat,search,sync,permanent,task}.md`
  — Claude Code 用スラッシュコマンド。各 md に「同期 → 会話 → 保存」の手順を記述し、
  必要に応じて `brain` CLI サブコマンドを呼ぶ。
- **新規**: `CLAUDE.md` — ボルト操作手順・ノート形式・同期ルールを Claude Code に指示。
- `_templates` にタスク連携用フロントマター `tasks:` を追加。Projects v2 ボード
  （`Status` / `Project` フィールド）の初期セットアップ手順も `CLAUDE.md` に記載。
- 既存ノートの正規化（§8）。
- 必要に応じ `.gitignore` 調整。

## 10. CLI コマンド（`brain` = デーモンの薄いクライアント）

- `brain sync` / `brain status` — 同期実行 / 同期・セッション状態表示
- `brain search <query>` — 意味検索
- `brain index [path]` — ローカルノートから ChromaDB を再構築
- `brain fetch-url <url>` / `brain fetch-youtube <url>` — スクレイピング / 書き起こし
- `brain memo|link|research|planning|chat <input>` — 拡張/API モードのセッション開始
- `brain session list|show|continue|save|discard <id>` — セッション操作
- `brain task add|list|show|update|done [...]` — タスク（GitHub Issue / Projects v2）操作

## 11. 実装フェーズ案

- **フェーズ 0**: ノート整形バグ修正（コード側は `964d41c` で完了）+ 既存ノート正規化（§8）。
- **フェーズ 1**: エンジン再構成（Discord 除去・ローカル git 化・セッション永続化・
  ChromaDB ローカル index・常駐デーモン・`brain` CLI）。
- **フェーズ 2**: Claude Code 連携（`.claude/commands/`, `CLAUDE.md`）。
- **フェーズ 3**: タスク管理（GitHub Issues + Projects v2 連携、`/task` コマンド、自動抽出）。
- **フェーズ 4**: VSCode 拡張機能（チャットパネル + タスク TreeView パネル）。

## 12. 検証方法（移行後）

- `brain sync`: 実行後にローカル⇄GitHub が一致する。GitHub を直接編集 → 次操作前に
  自動で取り込まれる。
- 各コマンドを **Claude Code スラッシュ経由**と **CLI 経由**の両方で実行 → ノートが
  ローカルに書き込まれ、commit・push・ChromaDB index されることを確認。
- **セッション永続性**: 拡張モードでセッション開始 → デーモン/VSCode を再起動 →
  `brain session continue` で会話が復元される（旧仕様のタイムアウト消失が起きない）。
- **競合**: ローカルと GitHub で同一ファイルを別々に編集 → ツールが停止し競合を通知する。
- `brain search`: 意味検索が結果を返す。
- **タスク管理**: `/task` または `brain task add` で Issue が作成され Projects v2 ボードへ
  追加される。`brain task list` で一覧表示。planning 会話からアクションアイテムが抽出され、
  確認後に Issue 化される。Issue とノートが相互リンクされる。
- **速度**: 2 回目以降の操作で埋め込みモデルの再ロードが発生しない（デーモン常駐により
  ウォーム保持）。
