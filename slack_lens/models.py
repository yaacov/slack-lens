"""Data models for Slack Lens."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def format_timestamp(ts: str) -> str:
    """Convert a Slack epoch timestamp to a human-readable string."""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return ts


@dataclass
class FileAttachment:
    """File attachment information."""

    name: str
    url: str
    size: int | None = None
    mimetype: str | None = None
    local_path: str | None = None


@dataclass
class Message:
    """Slack message."""

    id: str
    timestamp: str
    user: str
    user_name: str | None
    text: str
    thread_ts: str | None = None
    replies: list[Message] = field(default_factory=list)
    files: list[FileAttachment] = field(default_factory=list)
    reactions: list[dict] = field(default_factory=list)
    edited: bool = False
    datetime: str = ""

    def __post_init__(self) -> None:
        if not self.datetime and self.timestamp:
            self.datetime = format_timestamp(self.timestamp)


@dataclass
class ChannelArchive:
    """Archived channel data."""

    channel_id: str
    channel_name: str
    archived_at: str
    workspace: str
    messages: list[Message] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class Channel:
    """Slack channel information."""

    id: str
    name: str
    is_private: bool
    member_count: int | None = None


@dataclass
class ArchiveOptions:
    """Options for channel archival."""

    since: datetime | None = None
    until: datetime | None = None
    include_threads: bool = True
    include_files: bool = True
    file_pattern: str | None = None


@dataclass
class SearchResult:
    """Search result with context."""

    channel_name: str
    message: Message
    matches: list[str]
