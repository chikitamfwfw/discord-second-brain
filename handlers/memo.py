from __future__ import annotations
import asyncio
import discord
from discord import app_commands, ui
from datetime import datetime

from session.manager import SessionManager
from services.claude_client import ClaudeClient
from services.github_client import GitHubClient
from services.knowledge_store import KnowledgeStore
from utils.formatters import (
    make_timestamp_filename,
    make_zk_filename,
    make_zk_id,
    sanitize_tags,
    discord_preview,
    inject_tags,
)
from utils.knowledge_ref import build_knowledge_context
import config


class MemoSaveView(ui.View):
    def __init__(
        self,
        github: GitHubClient,
        knowledge: KnowledgeStore,
        claude: ClaudeClient,
        channel_id: int,
    ) -> None:
        super().__init__(timeout=1800)
        self._github = github
        self._knowledge = knowledge
        self._claude = claude
        self._channel_id = channel_id

    async def on_timeout(self) -> None:
        SessionManager.close(self._channel_id)

    @ui.button(label="💾 保存", style=discord.ButtonStyle.primary)
    async def save_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer()
        session = SessionManager.get(interaction.channel_id)
        if session is None:
            await interaction.followup.send("セッションが見つかりません。", ephemeral=True)
            return

        try:
            dt = datetime.now()
            note_id = session.pending_note_id or make_zk_id(dt)
            date_str = dt.strftime("%Y-%m-%d")
            path = f"{config.FLEETING_PATH}/{session.pending_path}"

            try:
                template = await asyncio.to_thread(self._github.read_template, "fleeting-note")
            except FileNotFoundError:
                template = _default_fleeting_template()

            note_content = await self._claude.compile_to_note(
                history=session.history,
                template=template,
                note_id=note_id,
                date_str=date_str,
            )

            tags = await self._claude.generate_tags(note_content)
            tags = sanitize_tags(tags)
            note_content = inject_tags(note_content, tags)

            commit_sha = await asyncio.to_thread(
                self._github.commit_note, path, note_content, note_id, "memo"
            )
            await asyncio.to_thread(
                self._knowledge.add_note, note_id, note_content, "memo", path, tags
            )

            session.pending_content = note_content
            self.stop()
            permanent_view = MemoPermanentView(
                github=self._github,
                knowledge=self._knowledge,
                claude=self._claude,
                channel_id=interaction.channel_id,
            )
            await interaction.followup.send(
                f"✅ 保存: `{note_id}`\nCommit: `{commit_sha[:7]}`\nTags: {', '.join(tags)}\n\n"
                "Permanent Noteにする場合は下のボタンを押してください。",
                view=permanent_view,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 保存失敗: {e}", ephemeral=True)

    @ui.button(label="❌ 破棄", style=discord.ButtonStyle.danger)
    async def discard_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        SessionManager.close(interaction.channel_id)
        self.stop()
        await interaction.response.send_message("破棄しました。", ephemeral=True)


class MemoPermanentView(ui.View):
    def __init__(
        self,
        github: GitHubClient,
        knowledge: KnowledgeStore,
        claude: ClaudeClient,
        channel_id: int,
    ) -> None:
        super().__init__(timeout=600)
        self._github = github
        self._knowledge = knowledge
        self._claude = claude
        self._channel_id = channel_id

    async def on_timeout(self) -> None:
        SessionManager.close(self._channel_id)

    @ui.button(label="🌟 Permanent化", style=discord.ButtonStyle.success)
    async def permanent_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer()
        session = SessionManager.get(interaction.channel_id)
        if session is None:
            await interaction.followup.send("セッションが見つかりません。", ephemeral=True)
            return

        try:
            atomic_text, _ = await self._claude.chat(
                command="permanent",
                history=[],
                user_message=(
                    "以下のFleetingノートからAtomicなPermanentノートを抽出してください。"
                    "各ノートは独立した1つのアイデアを表します。\n\n"
                    f"{session.pending_content}"
                ),
            )

            dt = datetime.now()
            note_id = make_zk_id(dt)
            filename = make_zk_filename(dt)
            path = f"{config.PERMANENT_PATH}/{filename}"

            commit_sha = await asyncio.to_thread(
                self._github.commit_note, path, atomic_text, note_id, "permanent"
            )
            await asyncio.to_thread(
                self._knowledge.add_note, note_id, atomic_text, "permanent", path, []
            )

            SessionManager.close(interaction.channel_id)
            self.stop()
            await interaction.followup.send(
                f"🌟 Permanent Note作成: `{note_id}`\nCommit: `{commit_sha[:7]}`"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Permanent化失敗: {e}", ephemeral=True)


def register_memo_command(
    tree: app_commands.CommandTree,
    guild: discord.Object,
    github: GitHubClient,
    knowledge: KnowledgeStore,
    claude: ClaudeClient,
) -> None:
    @tree.command(name="memo", description="メモを記録してセカンドブレインに保存", guild=guild)
    @app_commands.describe(text="メモの内容")
    async def memo_command(interaction: discord.Interaction, text: str) -> None:
        await interaction.response.defer()

        dt = datetime.now()
        note_id = make_zk_id(dt)
        fleeting_filename = make_zk_filename(dt)
        inbox_filename = make_timestamp_filename(dt)
        inbox_path = f"{config.INBOX_PATH}/{inbox_filename}"

        try:
            await asyncio.to_thread(
                github.commit_inbox, inbox_path, f"# Inbox\n\n{text}\n", inbox_filename
            )
        except Exception as e:
            print(f"[WARN] Inbox commit failed: {e}")

        session = SessionManager.create(channel_id=interaction.channel_id, command="memo")
        session.pending_note_id = note_id
        session.pending_path = fleeting_filename

        related = await asyncio.to_thread(knowledge.search, text[:500], 3)
        knowledge_ctx = build_knowledge_context(related) if related else ""

        assistant_text, _ = await claude.chat_with_tools(
            command="memo",
            history=session.history,
            user_message=f"以下のメモについて話しましょう。\n\n{text}",
            extra_system=knowledge_ctx,
        )

        session.pending_content = assistant_text
        view = MemoSaveView(
            github=github, knowledge=knowledge, claude=claude, channel_id=interaction.channel_id
        )
        await interaction.followup.send(
            f"{discord_preview(assistant_text)}\n\n"
            "💬 続けて話しかけられます。[💾 保存] で会話全体をノートに整理します。",
            view=view,
        )


async def handle_memo_followup(
    message: discord.Message,
    claude: ClaudeClient,
    github: GitHubClient,
    knowledge: KnowledgeStore,
) -> None:
    session = SessionManager.get(message.channel.id)
    if session is None:
        return

    async with message.channel.typing():
        related = await asyncio.to_thread(knowledge.search, message.content[:500], 3)
        knowledge_ctx = build_knowledge_context(related) if related else ""

        assistant_text, _ = await claude.chat_with_tools(
            command="memo",
            history=session.history,
            user_message=message.content,
            extra_system=knowledge_ctx,
        )

    session.pending_content = assistant_text
    view = MemoSaveView(
        github=github, knowledge=knowledge, claude=claude, channel_id=message.channel.id
    )
    await message.channel.send(
        f"{discord_preview(assistant_text)}\n\n"
        "💬 続けて話せます。[💾 保存] で整理します。",
        view=view,
    )


def _default_fleeting_template() -> str:
    return (
        "---\n"
        "id: {{note_id}}\n"
        "date: {{date}}\n"
        "type: fleeting\n"
        "source: discord/memo\n"
        "tags: []\n"
        "---\n\n"
        "# {{title}}\n\n"
        "## 要約\n\n{{summary}}\n\n"
        "## キーポイント\n\n{{key_points}}\n\n"
        "## アクションアイテム\n\n{{action_items}}\n\n"
        "## 原文\n\n> {{raw_input}}\n"
    )
