import asyncio
import discord
from discord import app_commands
import config
from services.github_client import GitHubClient
from services.claude_client import ClaudeClient
from services.knowledge_store import KnowledgeStore
from services.github_syncer import sync_once, run_sync_loop
from session.manager import SessionManager
from handlers.memo import register_memo_command, handle_memo_followup
from handlers.link import register_link_command, handle_link_followup
from handlers.research import register_research_command, handle_research_followup
from handlers.planning import register_planning_command, handle_planning_followup
from handlers.search import register_search_command
from handlers.chat import register_chat_command, handle_chat_followup


class SecondBrainBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)
        self.github = GitHubClient()
        self.claude = ClaudeClient(github=self.github)
        self.knowledge = KnowledgeStore()

    async def setup_hook(self) -> None:
        guild = discord.Object(id=config.DISCORD_GUILD_ID)

        register_memo_command(
            tree=self.tree,
            guild=guild,
            github=self.github,
            knowledge=self.knowledge,
            claude=self.claude,
        )
        register_link_command(
            tree=self.tree,
            guild=guild,
            github=self.github,
            knowledge=self.knowledge,
            claude=self.claude,
        )
        register_research_command(
            tree=self.tree,
            guild=guild,
            github=self.github,
            knowledge=self.knowledge,
            claude=self.claude,
        )
        register_planning_command(
            tree=self.tree,
            guild=guild,
            github=self.github,
            knowledge=self.knowledge,
            claude=self.claude,
        )
        register_search_command(
            tree=self.tree,
            guild=guild,
            knowledge=self.knowledge,
        )
        register_chat_command(
            tree=self.tree,
            guild=guild,
            github=self.github,
            knowledge=self.knowledge,
            claude=self.claude,
        )

        await self.tree.sync(guild=guild)
        print(f"[INFO] Slash commands synced to guild {config.DISCORD_GUILD_ID}")

        # 起動時フル同期（GitHub → ChromaDB）
        try:
            n = await asyncio.to_thread(sync_once, self.github, self.knowledge)
            print(f"[SYNC] startup sync done: {n} notes")
        except Exception as e:
            print(f"[SYNC] startup sync failed: {e}")

        # 定期同期バックグラウンドタスク（10分ごと）
        asyncio.create_task(run_sync_loop(self.github, self.knowledge))

    async def on_ready(self) -> None:
        print(f"[INFO] Logged in as {self.user} ({self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="your second brain 🧠",
            )
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        session = SessionManager.get(message.channel.id)
        if session is None:
            return

        try:
            if session.command == "chat":
                await handle_chat_followup(
                    message=message,
                    claude=self.claude,
                    github=self.github,
                    knowledge=self.knowledge,
                )
            elif session.command == "memo":
                await handle_memo_followup(
                    message=message,
                    claude=self.claude,
                    github=self.github,
                    knowledge=self.knowledge,
                )
            elif session.command == "link":
                await handle_link_followup(
                    message=message,
                    claude=self.claude,
                    github=self.github,
                    knowledge=self.knowledge,
                )
            elif session.command == "research":
                await handle_research_followup(
                    message=message,
                    claude=self.claude,
                    github=self.github,
                    knowledge=self.knowledge,
                )
            elif session.command == "planning":
                await handle_planning_followup(
                    message=message,
                    claude=self.claude,
                    github=self.github,
                    knowledge=self.knowledge,
                )
        except Exception as e:
            print(f"[ERROR] on_message handler failed: {e}")
            try:
                await message.channel.send(f"❌ エラーが発生しました: {e}")
            except Exception:
                pass

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        print(f"[ERROR] App command error: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"❌ エラーが発生しました: {error}", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ エラーが発生しました: {error}", ephemeral=True
            )


def main() -> None:
    bot = SecondBrainBot()
    bot.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()
