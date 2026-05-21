"""既存ノートの形式を正規化する（一回限りのデータ移行スクリプト）。

過去の `compile_to_note` / `inject_tags` のバグにより、一部ノートが
「外側フロントマター（tags のみ）+ ```markdown コードフェンス + 内側フロントマター」
という二重構造で保存されている。本スクリプトはそれを検出し、
単一のフロントマター + 本文の正常形式へ書き換える。

正常なノート（id を持つフロントマターが 1 つだけ、コードフェンス無し）は変更しない。

使い方:
    python scripts/normalize_notes.py <vault_path> [--apply]

  --apply を付けない場合は変更内容のプレビューのみ（dry-run）。
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

NOTE_DIRS = [
    "00-inbox",
    "10-notes/fleeting",
    "10-notes/literature/articles",
    "10-notes/literature/youtube",
    "10-notes/permanent",
    "20-research",
    "30-planning",
]

# 先頭に現れるフロントマターブロック（\r\n / \n 両対応）
_FM_AT_START = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.DOTALL)
_FENCE_LINE = re.compile(r"\A[ \t]*```[a-zA-Z]*[ \t]*\r?\n")
_CLOSING_FENCE = re.compile(r"\r?\n[ \t]*```[ \t]*(?=\r?\n|\Z)")


def _fm_fields(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in block.splitlines():
        m = re.match(r"^([\w_-]+):\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def _parse_tags(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]


def normalize(text: str) -> tuple[str, bool]:
    """ノート本文を正規化する。戻り値は (新テキスト, 変更されたか)。"""
    original = text
    text = text.lstrip("﻿")

    # 1つ目のフロントマターブロックを取得
    m1 = _FM_AT_START.match(text)
    if not m1:
        return original, False

    outer_fields = _fm_fields(m1.group(1))
    rest = text[m1.end():]

    # 1つ目に id があり、後続にコードフェンスや2つ目のフロントマターが無ければ正常
    has_fence = bool(_FENCE_LINE.match(rest.lstrip("\r\n")))
    if "id" in outer_fields and not has_fence and not _FM_AT_START.match(rest.lstrip("\r\n")):
        return original, False

    # 壊れた構造: 外側ブロックを捨てる。コードフェンスがあれば剥がす
    inner = rest.lstrip("\r\n")
    fence = _FENCE_LINE.match(inner)
    if fence:
        inner = inner[fence.end():]

    # 内側フロントマターを取得
    m2 = _FM_AT_START.match(inner)
    if not m2:
        # フェンスは無いが二重フロントマター、というケースを再判定
        return original, False

    inner_fields = _fm_fields(m2.group(1))
    if "id" not in inner_fields:
        return original, False

    body = inner[m2.end():]

    # 閉じコードフェンスを除去（以降は trailing として保持）
    cm = _CLOSING_FENCE.search(body)
    if cm:
        body = body[:cm.start()] + body[cm.end():]

    # tags のマージ: 内側が空なら外側を採用、両方あれば和集合
    inner_tags = _parse_tags(inner_fields.get("tags", ""))
    outer_tags = _parse_tags(outer_fields.get("tags", ""))
    merged = inner_tags or outer_tags
    if inner_tags and outer_tags:
        merged = inner_tags + [t for t in outer_tags if t not in inner_tags]

    # 内側フロントマターを再構築（tags 行を差し替え）
    fm_lines = m2.group(1).splitlines()
    tags_line = f"tags: [{', '.join(merged)}]"
    replaced = False
    for i, line in enumerate(fm_lines):
        if re.match(r"^tags:\s*", line):
            fm_lines[i] = tags_line
            replaced = True
            break
    if not replaced:
        fm_lines.append(tags_line)

    new_fm = "\n".join(fm_lines)
    new_text = f"---\n{new_fm}\n---\n\n{body.lstrip()}"
    if not new_text.endswith("\n"):
        new_text += "\n"

    return (new_text, new_text != original)


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    vault = Path(args[0]).expanduser().resolve()
    if not vault.is_dir():
        print(f"vault not found: {vault}")
        return 1

    changed = 0
    scanned = 0
    for d in NOTE_DIRS:
        directory = vault / d
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            scanned += 1
            text = path.read_text(encoding="utf-8")
            new_text, did = normalize(text)
            if did:
                changed += 1
                print(f"[{'FIX' if apply else 'DRY'}] {path.relative_to(vault)}")
                if apply:
                    path.write_text(new_text, encoding="utf-8")

    verb = "normalized" if apply else "would normalize"
    print(f"\nScanned {scanned} note(s); {verb} {changed}.")
    if not apply and changed:
        print("Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
