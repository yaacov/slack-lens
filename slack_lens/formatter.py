"""Compact text formatter for channel archives."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slack_lens.models import ChannelArchive, Message


def format_archive_txt(archive: ChannelArchive) -> str:
    """Render a ChannelArchive as compact, human-readable text."""
    lines: list[str] = []

    lines.append(
        f"# #{archive.channel_name}"
        f" \u2014 {archive.workspace}"
        f" \u2014 archived {archive.archived_at}"
    )
    lines.append("")

    for msg in archive.messages:
        first, cont = _format_message(msg)
        lines.append(first)
        lines.extend(f"  | {c}" for c in cont)
        lines.extend(_format_attachments(msg, indent="  "))
        lines.extend(_format_reactions(msg, indent="  "))

        if msg.replies:
            for i, reply in enumerate(msg.replies):
                is_last = i == len(msg.replies) - 1
                prefix = "\u2517" if is_last else "\u2523"
                r_first, r_cont = _format_message(reply)
                lines.append(f"  {prefix} {r_first}")
                lines.extend(f"    | {c}" for c in r_cont)
                lines.extend(
                    _format_attachments(reply, indent="    ")
                )

        lines.append("")

    return "\n".join(lines)


def _format_message(msg: Message) -> tuple[str, list[str]]:
    """Format a message, returning (first_line, continuation_lines)."""
    ts = msg.datetime or msg.timestamp
    user = msg.user_name or msg.user or "unknown"
    edited = " (edited)" if msg.edited else ""
    text_lines = msg.text.split("\n")
    first = f"[{ts}] {user}: {text_lines[0]}{edited}"
    cont = text_lines[1:] if len(text_lines) > 1 else []
    return first, cont


def _format_attachments(msg: Message, indent: str) -> list[str]:
    return [f"{indent}[file: {f.name}]" for f in msg.files]


def _format_reactions(msg: Message, indent: str) -> list[str]:
    if not msg.reactions:
        return []
    parts = []
    for r in msg.reactions:
        name = r.get("name", "?")
        count = r.get("count", len(r.get("users", [])))
        parts.append(f"[+{count} :{name}:]")
    return [f"{indent}{' '.join(parts)}"]
