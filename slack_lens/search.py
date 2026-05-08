"""Search and filter archived content."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from slack_lens.models import ChannelArchive, Message, SearchResult

if TYPE_CHECKING:
    from slack_lens.storage import Storage

logger = logging.getLogger(__name__)


class SearchEngine:
    """Search engine for archived content."""

    def __init__(self, storage: Storage):
        self.storage = storage

    def search_text(
        self,
        query: str,
        channel_name: str | None = None,
        user_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        with_files: bool = False,
        threads_only: bool = False,
    ) -> list[SearchResult]:
        """Search for text in archived messages."""
        results: list[SearchResult] = []
        pattern = re.compile(query, re.IGNORECASE)

        archives = self._load_archives(channel_name)

        for archive in archives:
            for message in archive.messages:
                if not self._matches_filters(
                    message,
                    user_name=user_name,
                    since=since,
                    until=until,
                    with_files=with_files,
                    threads_only=threads_only,
                ):
                    continue

                matches = pattern.findall(message.text)
                if matches:
                    results.append(
                        SearchResult(
                            channel_name=archive.channel_name,
                            message=message,
                            matches=matches,
                        )
                    )

                for reply in message.replies:
                    if not self._matches_filters(
                        reply,
                        user_name=user_name,
                        since=since,
                        until=until,
                        with_files=with_files,
                    ):
                        continue

                    matches = pattern.findall(reply.text)
                    if matches:
                        results.append(
                            SearchResult(
                                channel_name=archive.channel_name,
                                message=reply,
                                matches=matches,
                            )
                        )

        return results

    def _load_archives(
        self,
        channel_name: str | None = None,
    ) -> list[ChannelArchive]:
        """Load archives for searching."""
        archives: list[ChannelArchive] = []

        if channel_name:
            archive = self.storage.load_channel(channel_name)
            if archive:
                archives.append(archive)
        else:
            for filepath in self.storage.list_archives():
                name = filepath.stem.rsplit("_", 1)[0]
                archive = self.storage.load_channel(name)
                if archive:
                    archives.append(archive)

        return archives

    @staticmethod
    def _matches_filters(
        message: Message,
        user_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        with_files: bool = False,
        threads_only: bool = False,
    ) -> bool:
        """Check if message matches filters."""
        if user_name and message.user_name != user_name:
            return False

        if since or until:
            try:
                msg_time = datetime.fromtimestamp(float(message.timestamp))
                if since and msg_time < since:
                    return False
                if until and msg_time > until:
                    return False
            except (ValueError, TypeError):
                logger.warning("Invalid timestamp: %s", message.timestamp)
                return False

        if with_files and not message.files:
            return False

        return not (threads_only and not message.replies)
