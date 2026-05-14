from __future__ import annotations
from utils.formatters import strip_frontmatter, split_for_discord


async def send_chunked(
    sendable,
    text: str,
    suffix: str = "",
    view=None,
    prefix: str = "",
) -> None:
    """Send text as multiple Discord messages if it exceeds the 2000-char limit.

    - prefix: prepended only to the first chunk (e.g., "📺 **title**")
    - suffix: appended only to the last chunk (e.g., "💬 続けて話せます。")
    - view:   attached only to the last chunk so buttons appear at the bottom
    """
    body = strip_frontmatter(text)
    chunks = split_for_discord(body)

    for i, chunk in enumerate(chunks):
        is_first = i == 0
        is_last = i == len(chunks) - 1

        content = (f"{prefix}\n\n" if prefix and is_first else "") + chunk
        if is_last and suffix:
            content += f"\n\n{suffix}"

        kwargs = {}
        if is_last and view is not None:
            kwargs["view"] = view
        await sendable.send(content, **kwargs)
