"""Data storage for archived Slack channels."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from slack_lens.config import Config

logger = logging.getLogger(__name__)


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


@dataclass
class ChannelArchive:
    """Archived channel data."""

    channel_id: str
    channel_name: str
    archived_at: str
    workspace: str
    messages: list[Message] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class Storage:
    """Storage manager for channel archives."""

    def __init__(self, config: Config | None = None):
        """Initialize storage manager.

        Args:
            config: Application configuration
        """
        self.config = config or Config()
        self.config.ensure_dirs()

    def save_channel(self, archive: ChannelArchive) -> Path:
        """Save channel archive to disk.

        Args:
            archive: Channel archive data

        Returns:
            Path to saved file
        """
        filename = f"{archive.channel_name}_{archive.archived_at.replace(':', '-').replace(' ', '_')}.json"
        filepath = self.config.archives_dir / filename

        try:
            with filepath.open("w", encoding="utf-8") as f:
                json.dump(
                    asdict(archive),
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            logger.info(f"Saved archive to {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"Failed to save archive: {e}")
            raise

    def load_channel(self, channel_name: str) -> ChannelArchive | None:
        """Load most recent archive for a channel.

        Args:
            channel_name: Channel name

        Returns:
            ChannelArchive or None if not found
        """
        # Find most recent archive file for this channel
        pattern = f"{channel_name}_*.json"
        files = sorted(
            self.config.archives_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not files:
            return None

        try:
            with files[0].open("r", encoding="utf-8") as f:
                data = json.load(f)
                # Reconstruct nested Message objects
                messages = [
                    Message(
                        id=msg["id"],
                        timestamp=msg["timestamp"],
                        user=msg["user"],
                        user_name=msg.get("user_name"),
                        text=msg["text"],
                        thread_ts=msg.get("thread_ts"),
                        replies=[
                            Message(
                                id=reply["id"],
                                timestamp=reply["timestamp"],
                                user=reply["user"],
                                user_name=reply.get("user_name"),
                                text=reply["text"],
                                thread_ts=reply.get("thread_ts"),
                                files=[FileAttachment(**f) for f in reply.get("files", [])],
                                reactions=reply.get("reactions", []),
                                edited=reply.get("edited", False),
                            )
                            for reply in msg.get("replies", [])
                        ],
                        files=[FileAttachment(**f) for f in msg.get("files", [])],
                        reactions=msg.get("reactions", []),
                        edited=msg.get("edited", False),
                    )
                    for msg in data["messages"]
                ]

                return ChannelArchive(
                    channel_id=data["channel_id"],
                    channel_name=data["channel_name"],
                    archived_at=data["archived_at"],
                    workspace=data.get("workspace", ""),
                    messages=messages,
                    metadata=data.get("metadata", {}),
                )

        except Exception as e:
            logger.error(f"Failed to load archive: {e}")
            return None

    def list_archives(self) -> list[Path]:
        """List all archive files.

        Returns:
            List of archive file paths
        """
        return sorted(
            self.config.archives_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
