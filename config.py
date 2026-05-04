import os
from dotenv import load_dotenv

load_dotenv()

# Discord
DISCORD_TOKEN: str = os.environ["DISCORD_TOKEN"]
DISCORD_GUILD_ID: int = int(os.environ["DISCORD_GUILD_ID"])

# Anthropic
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]

MODEL_MAIN: str = "claude-sonnet-4-6"
MODEL_TAGGING: str = "claude-haiku-4-5-20251001"

# GitHub
GITHUB_TOKEN: str = os.environ["GITHUB_TOKEN"]
GITHUB_REPO: str = os.environ["GITHUB_REPO"]

# ChromaDB
CHROMA_DB_PATH: str = os.getenv("CHROMA_DB_PATH", "./chroma_db")

# Tavily (optional)
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

# GitHub repo フォルダパス
INBOX_PATH: str = "00-inbox"
FLEETING_PATH: str = "10-notes/fleeting"
ARTICLES_PATH: str = "10-notes/literature/articles"
YOUTUBE_PATH: str = "10-notes/literature/youtube"
PERMANENT_PATH: str = "10-notes/permanent"
RESEARCH_PATH: str = "20-research"
PLANNING_PATH: str = "30-planning"
COOKIES_FILE: str = os.getenv("COOKIES_FILE", "")

SYSTEM_PROMPT_PATH: str = "_config/system-prompt.md"
PROMPTS_PATH: str = "_config/prompts"
TEMPLATES_PATH: str = "_templates"
