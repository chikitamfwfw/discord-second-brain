from __future__ import annotations
import asyncio
import discord
from discord import app_commands, ui
from datetime import datetime
import re

from session.manager import SessionManager
from services.claude_client import ClaudeClient
from services.github_client import GitHubClient
from services.knowledge_store import KnowledgeStore
from services.scraper import fetch_article, ScrapeResult
from services.youtube_client import get_transcript, TranscriptResult
from utils.formatters import make_zk_filename, make_zk_id, sanitize_tags, discord_preview, inject_tags
from utils.knowledge_ref import build_knowledge_context
import config

YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
    r"[A-Za-z0-9_-]{11}"
)


class PaywallView(ui.View):
    def __init__(
        self,
        github: GitHubClient,
        knowledge: KnowledgeStore,
        url: str,
        title: str,
        channel_id: int,
    ) -> None:
        super().__init__(timeout=300)
        self._github = github
        self._knowledge = knowledge
        self._url = url
        self._title = title
        self._channel_id = channel_id

    async def on_timeout(self) -> None:
        SessionManager.close(self._channel_id)

    @ui.button(label="✅ 保存 (URL only)", style=discord.ButtonStyle.primary)
    async def save_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer()
        dt = datetime.now()
        note_id = make_zk_id(dt)
        filename = make_zk_filename(dt)
        path = f"{config.ARTICLES_PATH}/{filename}"
        content = (
            f"---\nid: {note_id}\ndate: {dt.strftime('%Y-%m-%d')}\n"
            f"type: literature/article\nsource: {self._url}\ntags: [paywall]\n---\n\n"
            f"# {self._title}\n\n**URL:** {self._url}\n\n> [!note] ペイウォールのため本文取得不可\n"
        )
        try:
            commit_sha = await asyncio.to_thread(
                self._github.commit_note, path, content, note_id, "link"
            )
            await asyncio.to_thread(
                self._knowledge.add_note, note_id, f"{self._title} {self._url}", "link", path, ["paywall"]
            )
            SessionManager.close(self._channel_id)
            self.stop()
            await interaction.followup.send(f"✅ URL保存: `{note_id}`\nCommit: `{commit_sha[:7]}`")
        except Exception as e:
            await interaction.followup.send(f"❌ 保存失敗: {e}", ephemeral=True)

    @ui.button(label="❌ スキップ", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        SessionManager.close(interaction.channel_id)
        self.stop()
        await interaction.response.send_message("スキップしました。", ephemeral=True)


class LinkSaveView(ui.View):
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
            note_id = session.pending_note_id
            date_str = dt.strftime("%Y-%m-%d")
            url = session.references[0] if session.references else ""

            template_name = "literature-youtube" if session.topic == "youtube" else "literature-article"
            try:
                template = await asyncio.to_thread(self._github.read_template, template_name)
            except FileNotFoundError:
                template = _default_youtube_template() if session.topic == "youtube" else _default_article_template()

            note_content = await self._claude.compile_to_note(
                history=session.history,
                template=template,
                note_id=note_id,
                date_str=date_str,
                extra_context=f"URL: {url}" if url else "",
            )

            if session.topic == "youtube" and session.raw_content:
                note_content += f"\n\n---\n\n## 書き起こし全文\n\n{session.raw_content}\n"

            tags = await self._claude.generate_tags(note_content)
            tags = sanitize_tags(tags)
            note_content = inject_tags(note_content, tags)

            commit_sha = await asyncio.to_thread(
                self._github.commit_note, session.pending_path, note_content, note_id, "link"
            )
            await asyncio.to_thread(
                self._knowledge.add_note, note_id, note_content, "link", session.pending_path, tags
            )
            SessionManager.close(interaction.channel_id)
            self.stop()
            await interaction.followup.send(
                f"✅ 保存: `{note_id}`\nCommit: `{commit_sha[:7]}`\nTags: {', '.join(tags)}"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 保存失敗: {e}", ephemeral=True)

    @ui.button(label="❌ 破棄", style=discord.ButtonStyle.danger)
    async def discard_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        SessionManager.close(interaction.channel_id)
        self.stop()
        await interaction.response.send_message("破棄しました。", ephemeral=True)


def register_link_command(
    tree: app_commands.CommandTree,
    guild: discord.Object,
    github: GitHubClient,
    knowledge: KnowledgeStore,
    claude: ClaudeClient,
) -> None:
    @tree.command(name="link", description="URLを保存してセカンドブレインに追加", guild=guild)
    @app_commands.describe(url="保存するURL (YouTube or 記事)")
    async def link_command(interaction: discord.Interaction, url: str) -> None:
        await interaction.response.defer()
        session = SessionManager.create(channel_id=interaction.channel_id, command="link")
        if YOUTUBE_RE.search(url):
            await _handle_youtube(interaction, url, session, github, knowledge, claude)
        else:
            await _handle_article(interaction, url, session, github, knowledge, claude)


async def _handle_youtube(interaction, url, session, github, knowledge, claude):
    await interaction.followup.send("⏳ 字幕/書き起こし取得中...", ephemeral=True)

    result: TranscriptResult = await get_transcript(url)
    if result.method == "unavailable" or not result.transcript:
        await interaction.followup.send(
            f"⚠️ 字幕・書き起こし取得失敗。\n**URL:** {url}"
        )
        SessionManager.close(interaction.channel_id)
        return

    dt = datetime.now()
    note_id = make_zk_id(dt)
    session.topic = "youtube"
    session.pending_note_id = note_id
    session.pending_path = f"{config.YOUTUBE_PATH}/{make_zk_filename(dt)}"
    session.references.append(url)
    session.raw_content = result.transcript

    related = await asyncio.to_thread(
        knowledge.search, f"{result.title} {result.transcript[:300]}", 3
    )
    knowledge_ctx = build_knowledge_context(related) if related else ""

    method_label = "字幕" if result.method == "api" else "Whisper書き起こし"
    user_message = (
        f"以下のYouTube動画の書き起こしを共有します。\n\n"
        f"**タイトル:** {result.title}\n"
        f"**URL:** {url}\n"
        f"**言語:** {result.language} ({method_label})\n\n"
        f"**書き起こし全文:**\n{result.transcript}"
    )
    assistant_text, _ = await claude.chat_with_tools(
        command="link",
        history=session.history,
        user_message=user_message,
        extra_system=knowledge_ctx,
    )

    session.pending_content = assistant_text
    view = LinkSaveView(github=github, knowledge=knowledge, claude=claude, channel_id=interaction.channel_id)
    await interaction.followup.send(
        f"📺 **{result.title}**\n\n{discord_preview(assistant_text)}\n\n"
        "💬 続けて話しかけられます。[💾 保存] で会話をノートに整理します。",
        view=view,
    )


async def _handle_article(interaction, url, session, github, knowledge, claude):
    await interaction.followup.send("⏳ 記事を取得中...", ephemeral=True)

    scrape: ScrapeResult = await fetch_article(url)

    if scrape.is_paywall:
        await interaction.followup.send(
            f"⚠️ 本文取得不可 (ペイウォール?)。\n**タイトル:** {scrape.title}\n**URL:** {url}\n\nURLのみ保存しますか?",
            view=PaywallView(
                github=github, knowledge=knowledge, url=url, title=scrape.title, channel_id=interaction.channel_id
            ),
        )
        return

    dt = datetime.now()
    note_id = make_zk_id(dt)
    session.topic = "article"
    session.pending_note_id = note_id
    session.pending_path = f"{config.ARTICLES_PATH}/{make_zk_filename(dt)}"
    session.references.append(url)

    related = await asyncio.to_thread(
        knowledge.search, f"{scrape.title} {scrape.text[:300]}", 3
    )
    knowledge_ctx = build_knowledge_context(related) if related else ""

    page_info = f"（{scrape.page_count}ページ分取得）" if scrape.page_count > 1 else ""
    user_message = (
        f"以下の記事を読みました。{page_info}\n\n"
        f"**タイトル:** {scrape.title}\n"
        f"**URL:** {url}\n\n"
        f"**本文:**\n{scrape.text[:8000]}"
    )
    assistant_text, _ = await claude.chat_with_tools(
        command="link",
        history=session.history,
        user_message=user_message,
        extra_system=knowledge_ctx,
    )

    session.pending_content = assistant_text
    view = LinkSaveView(github=github, knowledge=knowledge, claude=claude, channel_id=interaction.channel_id)
    await interaction.followup.send(
        f"📰 **{scrape.title}**{page_info}\n\n{discord_preview(assistant_text)}\n\n"
        "💬 続けて話しかけられます。[💾 保存] で会話をノートに整理します。",
        view=view,
    )


async def handle_link_followup(
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
            command="link",
            history=session.history,
            user_message=message.content,
            extra_system=knowledge_ctx,
        )

    session.pending_content = assistant_text
    view = LinkSaveView(github=github, knowledge=knowledge, claude=claude, channel_id=message.channel.id)
    await message.channel.send(
        f"{discord_preview(assistant_text)}\n\n"
        "💬 続けて話せます。[💾 保存] で整理します。",
        view=view,
    )


def _default_article_template() -> str:
    return (
        "---\nid: {{note_id}}\ndate: {{date}}\ntype: literature/article\n"
        "source: {{url}}\nauthor: {{author}}\ntags: []\n---\n\n"
        "# {{title}}\n\n## 要約\n\n{{summary}}\n\n"
        "## キーポイント\n\n{{key_points}}\n\n"
        "## 重要な引用\n\n{{quotes}}\n\n"
        "## 個人的洞察\n\n{{insights}}\n\n"
        "## ソース\n\n- URL: {{url}}\n- 取得日: {{date}}\n"
    )


def _default_youtube_template() -> str:
    return (
        "---\nid: {{note_id}}\ndate: {{date}}\ntype: literature/youtube\n"
        "source: {{url}}\nchannel: {{channel}}\ntags: []\n---\n\n"
        "# {{title}}\n\n## 要約\n\n{{summary}}\n\n"
        "## キーポイント\n\n{{key_points}}\n\n"
        "## 書き起こし抜粋\n\n{{excerpt}}\n\n"
        "## 個人的洞察\n\n{{insights}}\n\n"
        "## ソース\n\n- URL: {{url}}\n- 言語: {{language}}\n"
    )
