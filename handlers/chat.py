from __future__ import annotations
import asyncio
import discord
from discord import app_commands, ui
from datetime import datetime

from session.manager import SessionManager
from services.claude_client import ClaudeClient
from services.github_client import GitHubClient
from services.knowledge_store import KnowledgeStore
from utils.formatters import make_zk_id, make_zk_filename, sanitize_tags, discord_preview, inject_tags
from utils.knowledge_ref import build_knowledge_context
import config


class ChatView(ui.View):
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
            note_id = make_zk_id(dt)
            filename = make_zk_filename(dt)
            path = f"{config.FLEETING_PATH}/{filename}"
            date_str = dt.strftime("%Y-%m-%d")

            try:
                template = await asyncio.to_thread(self._github.read_template, "fleeting-note")
            except FileNotFoundError:
                template = _default_chat_template()

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
                self._github.commit_note, path, note_content, note_id, "chat"
            )
            await asyncio.to_thread(
                self._knowledge.add_note, note_id, note_content, "chat", path, tags
            )

            SessionManager.close(interaction.channel_id)
            self.stop()
            await interaction.followup.send(
                f"✅ 保存: `{note_id}`\nCommit: `{commit_sha[:7]}`\nTags: {', '.join(tags)}"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 保存失敗: {e}", ephemeral=True)

    @ui.button(label="❌ 終了", style=discord.ButtonStyle.secondary)
    async def end_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        SessionManager.close(interaction.channel_id)
        self.stop()
        await interaction.response.send_message("チャットセッションを終了しました。", ephemeral=True)


def register_chat_command(
    tree: app_commands.CommandTree,
    guild: discord.Object,
    github: GitHubClient,
    knowledge: KnowledgeStore,
    claude: ClaudeClient,
) -> None:
    @tree.command(
        name="chat",
        description="AIと自由に会話（Web検索・蓄積知識を自動参照）",
        guild=guild,
    )
    @app_commands.describe(message="メッセージ")
    async def chat_command(interaction: discord.Interaction, message: str) -> None:
        await interaction.response.defer()

        session = SessionManager.create(channel_id=interaction.channel_id, command="chat")

        related = await asyncio.to_thread(knowledge.search, message, 3)
        knowledge_ctx = build_knowledge_context(related) if related else ""

        assistant_text, _ = await claude.chat_with_tools(
            command="chat",
            history=session.history,
            user_message=message,
            extra_system=knowledge_ctx,
        )

        session.pending_content = assistant_text

        view = ChatView(github=github, knowledge=knowledge, claude=claude, channel_id=interaction.channel_id)
        await interaction.followup.send(
            f"{discord_preview(assistant_text)}\n\n"
            "💬 続けて話しかけると会話を続けられます。",
            view=view,
        )


async def handle_chat_followup(
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
            command="chat",
            history=session.history,
            user_message=message.content,
            extra_system=knowledge_ctx,
        )

    session.pending_content = assistant_text
    view = ChatView(github=github, knowledge=knowledge, claude=claude, channel_id=message.channel.id)
    await message.channel.send(
        f"{discord_preview(assistant_text)}\n\n"
        "💬 続けて話しかけると会話を続けられます。",
        view=view,
    )


def _default_chat_template() -> str:
    return (
        "---\nid: {{note_id}}\ndate: {{date}}\ntype: fleeting\nsource: discord/chat\ntags: []\n---\n\n"
        "# {{title}}\n\n## 要約\n\n{{summary}}\n\n## キーポイント\n\n{{key_points}}\n\n"
        "## アクションアイテム\n\n{{action_items}}\n"
    )
