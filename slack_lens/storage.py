"""Persistence layer for archived Slack channels."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING

from slack_lens.config import Config
from slack_lens.models import ChannelArchive, FileAttachment, Message

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class Storage:
    """Storage manager for channel archives."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.config.ensure_dirs()

    def save_channel(
        self,
        archive: ChannelArchive,
        filepath: Path | None = None,
    ) -> Path:
        """Save channel archive to disk.

        Args:
            archive: Channel archive data.
            filepath: Explicit output path.  When *None* a filename is
                      derived from the archive metadata.

        Returns:
            Path to saved file.
        """
        if filepath is None:
            filename = (
                f"{archive.channel_name}_"
                f"{archive.archived_at.replace(':', '-').replace(' ', '_')}.json"
            )
            filepath = self.config.archives_dir / filename

        try:
            with filepath.open("w", encoding="utf-8") as f:
                json.dump(asdict(archive), f, indent=2, ensure_ascii=False)
            logger.info("Saved archive to %s", filepath)
            return filepath
        except Exception as e:
            logger.error("Failed to save archive: %s", e)
            raise

    def load_channel(self, channel_name: str) -> ChannelArchive | None:
        """Load most recent archive for a channel."""
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

            messages = [
                self._deserialize_message(msg) for msg in data["messages"]
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
            logger.error("Failed to load archive: %s", e)
            return None

    def list_archives(self) -> list[Path]:
        """List all archive files sorted by most-recent first."""
        return sorted(
            self.config.archives_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    @staticmethod
    def _deserialize_message(data: dict) -> Message:
        """Recursively reconstruct a Message from a dict."""
        return Message(
            id=data["id"],
            timestamp=data["timestamp"],
            user=data["user"],
            user_name=data.get("user_name"),
            text=data["text"],
            thread_ts=data.get("thread_ts"),
            replies=[
                Storage._deserialize_message(r) for r in data.get("replies", [])
            ],
            files=[FileAttachment(**f) for f in data.get("files", [])],
            reactions=data.get("reactions", []),
            edited=data.get("edited", False),
            datetime=data.get("datetime", ""),
        )
