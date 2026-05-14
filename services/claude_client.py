from __future__ import annotations
import asyncio
import anthropic
from services.github_client import GitHubClient
import config

# Anthropic公式のサーバーサイドWeb検索ツール（Claude.aiと同じ検索エンジン）
_WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 10,
}


class ClaudeClient:
    def __init__(self, github: GitHubClient) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        self._github = github

    async def chat(
        self,
        command: str,
        history: list[dict],
        user_message: str,
        extra_system: str = "",
    ) -> tuple[str, list[dict]]:
        system = await self._build_system(command, extra_system)
        new_history = history + [{"role": "user", "content": user_message}]

        response = await self._client.messages.create(
            model=config.MODEL_MAIN,
            max_tokens=4096,
            system=system,
            messages=new_history,
        )
        assistant_text = response.content[0].text
        new_history.append({"role": "assistant", "content": assistant_text})

        history.clear()
        history.extend(new_history)
        return assistant_text, history

    async def generate_tags(self, content: str) -> list[str]:
        prompt = (
            "Extract 3 to 7 concise tags for the following note. "
            "Reply ONLY with a comma-separated list of tags, no explanations.\n\n"
            f"{content[:3000]}"
        )
        response = await self._client.messages.create(
            model=config.MODEL_TAGGING,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        return [t.strip() for t in raw.split(",") if t.strip()]

    async def chat_with_tools(
        self,
        command: str,
        history: list[dict],
        user_message: str,
        extra_system: str = "",
    ) -> tuple[str, list[dict]]:
        """Chat with Anthropic web_search_20250305 (server-side, same as Claude.ai)."""
        system = await self._build_system(command, extra_system)
        api_messages: list[dict] = list(history) + [{"role": "user", "content": user_message}]

        text = ""
        for _ in range(10):
            response = await self._client.messages.create(
                model=config.MODEL_MAIN,
                max_tokens=8096,
                system=system,
                tools=[_WEB_SEARCH_TOOL],
                messages=api_messages,
            )

            # Extract any text blocks in this response
            text = "\n".join(
                b.text for b in response.content if hasattr(b, "text") and b.text
            ).strip()

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason == "tool_use":
                # web_search_20250305 is server-side: Anthropic executed the search.
                # We must append the assistant turn and continue so Claude can use the results.
                assistant_content = [
                    b.model_dump() if hasattr(b, "model_dump") else b
                    for b in response.content
                ]
                api_messages.append({"role": "assistant", "content": assistant_content})

                # Build tool_result blocks for each tool_use block.
                # For web_search_20250305, the results are already embedded by Anthropic —
                # we pass an empty acknowledgment so the API can continue.
                tool_results = []
                for block in response.content:
                    block_type = getattr(block, "type", None)
                    if block_type == "tool_use":
                        query = getattr(block, "input", {}).get("query", "")
                        print(f"[SEARCH] {query}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "",  # server-side: results already in model context
                        })
                    elif block_type == "server_tool_use":
                        # Newer API format for server-managed tools
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "",
                        })

                if tool_results:
                    api_messages.append({"role": "user", "content": tool_results})
                else:
                    break
            else:
                break

        new_history = list(history) + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": text},
        ]
        history.clear()
        history.extend(new_history)
        return text or "応答を生成できませんでした。", history

    async def compile_to_note(
        self,
        history: list[dict],
        template: str,
        note_id: str,
        date_str: str,
        extra_context: str = "",
    ) -> str:
        """Compile a conversation history into a structured note using a template."""
        conversation_lines = []
        for m in history:
            role = m["role"].upper()
            content = m["content"] if isinstance(m["content"], str) else ""
            if content:
                conversation_lines.append(f"[{role}]\n{content}")
        conversation_text = "\n\n".join(conversation_lines)

        prompt = (
            f"以下の会話内容を、指定されたテンプレートに従って構造化されたMarkdownノートにまとめてください。\n\n"
            f"**ノートID (YAMLのidフィールドに使用):** {note_id}\n"
            f"**日付 (YAMLのdateフィールドに使用):** {date_str}\n"
            + (f"**追加情報:** {extra_context}\n" if extra_context else "")
            + f"\n## 会話内容\n\n{conversation_text}\n\n"
            f"## テンプレート\n\n{template}\n\n"
            "テンプレートの{{}}プレースホルダーを会話の内容で埋めてください。"
            "tagsフィールドは空のまま（[]）にしてください。"
        )

        response = await self._client.messages.create(
            model=config.MODEL_MAIN,
            max_tokens=8096,
            system=(
                "あなたはノート整理AIです。提供された会話をテンプレートに従って"
                "構造化されたMarkdownノートにまとめてください。"
                "会話に登場した情報のみ使用し、創作は加えないでください。"
                "テンプレートのプレースホルダーに対応する情報が会話中に見つからない場合は「不明」と記入してください。"
                "参照（references）は「- [タイトル](URL) — 概要一行」の形式で列挙してください。"
                "出力は必ず '---' で始まるYAMLフロントマターから開始し、前置きや説明文は一切つけないでください。"
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        # Claudeが前置き文を追加した場合、最初の --- から始まるよう切り詰める
        idx = text.find("---")
        if idx > 0:
            text = text[idx:]
        return text

    async def _build_system(self, command: str, extra: str) -> str:
        parts = []
        try:
            parts.append(await asyncio.to_thread(self._github.read_system_prompt))
        except FileNotFoundError:
            parts.append("You are a helpful knowledge management assistant.")

        try:
            profile = await asyncio.to_thread(self._github.read_user_profile)
            if profile.strip():
                parts.append(f"## ユーザープロファイル\n\n{profile}")
        except FileNotFoundError:
            pass

        try:
            parts.append(await asyncio.to_thread(self._github.read_prompt, command))
        except FileNotFoundError:
            pass

        if extra:
            parts.append(extra)

        return "\n\n---\n\n".join(parts)
