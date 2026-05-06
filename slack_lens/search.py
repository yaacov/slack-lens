"""Search and filter archived content."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from slack_lens.storage import ChannelArchive, Message, Storage

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Search result with context."""

    channel_name: str
    message: Message
    matches: list[str]


class SearchEngine:
    """Search engine for archived content."""

    def __init__(self, storage: Storage):
        """Initialize search engine.

        Args:
            storage: Storage manager
        """
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
        """Search for text in archived messages.

        Args:
            query: Search query (supports regex)
            channel_name: Limit to specific channel
            user_name: Filter by user
            since: Only messages after this date
            until: Only messages before this date
            with_files: Only messages with attachments
            threads_only: Only messages with replies

        Returns:
            List of search results
        """
        results = []
        pattern = re.compile(query, re.IGNORECASE)

        # Load archives
        archives = self._load_archives(channel_name)

        for archive in archives:
            for message in archive.messages:
                # Apply filters
                if not self._matches_filters(
                    message,
                    user_name=user_name,
                    since=since,
                    until=until,
                    with_files=with_files,
                    threads_only=threads_only,
                ):
                    continue

                # Search in message text
                matches = pattern.findall(message.text)
                if matches:
                    results.append(
                        SearchResult(
                            channel_name=archive.channel_name,
                            message=message,
                            matches=matches,
                        )
                    )

                # Search in thread replies
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
        """Load archives for searching.

        Args:
            channel_name: Limit to specific channel

        Returns:
            List of channel archives
        """
        archives = []

        if channel_name:
            # Load specific channel
            archive = self.storage.load_channel(channel_name)
            if archive:
                archives.append(archive)
        else:
            # Load all archives
            for filepath in self.storage.list_archives():
                # Extract channel name from filename
                name = filepath.stem.rsplit("_", 1)[0]
                archive = self.storage.load_channel(name)
                if archive:
                    archives.append(archive)

        return archives

    def _matches_filters(
        self,
        message: Message,
        user_name: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        with_files: bool = False,
        threads_only: bool = False,
    ) -> bool:
        """Check if message matches filters.

        Args:
            message: Message to check
            user_name: Filter by user
            since: Only messages after this date
            until: Only messages before this date
            with_files: Only messages with attachments
            threads_only: Only messages with replies

        Returns:
            True if message matches all filters
        """
        # User filter
        if user_name and message.user_name != user_name:
            return False

        # Date filters
        if since or until:
            try:
                msg_time = datetime.fromtimestamp(float(message.timestamp))
                if since and msg_time < since:
                    return False
                if until and msg_time > until:
                    return False
            except (ValueError, TypeError):
                logger.warning(f"Invalid timestamp: {message.timestamp}")
                return False

        # Files filter
        if with_files and not message.files:
            return False

        # Threads filter
        if threads_only and not message.replies:
            return False

        return True
