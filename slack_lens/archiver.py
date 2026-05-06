"""Channel archival logic."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from slack_lens.config import Config
from slack_lens.slack_client import Channel, SlackClient, _launch_browser
from slack_lens.storage import ChannelArchive, FileAttachment, Message, Storage

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class ArchiveOptions:
    """Options for channel archival."""

    since: datetime | None = None
    until: datetime | None = None
    thread_depth: int = -1
    include_files: bool = True
    file_pattern: str | None = None


class ChannelArchiver:
    """Archive Slack channels."""

    def __init__(
        self,
        client: SlackClient,
        config: Config | None = None,
    ):
        """Initialize archiver.

        Args:
            client: Slack client
            config: Application configuration
        """
        self.client = client
        self.config = config or Config()
        self.storage = Storage(config=self.config)

    def archive_channel(
        self,
        channel: Channel,
        options: ArchiveOptions | None = None,
    ) -> Path:
        """Archive a channel.

        Args:
            channel: Channel to archive
            options: Archive options

        Returns:
            Path to archive file

        Raises:
            RuntimeError: If archival fails
        """
        options = options or ArchiveOptions()

        console.print(f"\n[bold cyan]Archiving #{channel.name}[/bold cyan]")

        with sync_playwright() as p:
            browser = _launch_browser(p, headless=self.config.headless)
            context = browser.new_context(
                storage_state=str(self.config.auth_file)
            )
            page = context.new_page()

            try:
                # Navigate to channel
                # Client URL is like https://app.slack.com/client/TEAM_ID/CHANNEL_ID
                # Extract the team portion and navigate to the target channel
                client_url = self.config.get_client_url() or self.client.app_url
                parts = client_url.rstrip("/").rsplit("/", 1)
                team_base = parts[0]  # https://app.slack.com/client/TEAM_ID
                channel_url = f"{team_base}/{channel.id}"
                page.goto(channel_url, timeout=self.config.browser_timeout)

                # Wait for messages to load
                page.wait_for_selector("[data-qa='message_container']", timeout=30000)

                # Scroll to load all messages
                console.print("[yellow]Loading messages...[/yellow]")
                self._scroll_to_load_all(page)

                # Extract messages
                console.print("[yellow]Extracting messages...[/yellow]")
                messages = self._extract_messages(page, options, channel.name)

                # Create archive
                archive = ChannelArchive(
                    channel_id=channel.id,
                    channel_name=channel.name,
                    archived_at=datetime.now().isoformat(),
                    workspace=self.client.workspace,
                    messages=messages,
                    metadata={
                        "options": {
                            "since": options.since.isoformat() if options.since else None,
                            "until": options.until.isoformat() if options.until else None,
                            "thread_depth": options.thread_depth,
                            "include_files": options.include_files,
                        },
                        "total_messages": len(messages),
                    },
                )

                # Save archive
                filepath = self.storage.save_channel(archive)
                console.print(f"[green]✓ Archived {len(messages)} messages to {filepath}[/green]")

                browser.close()
                return filepath

            except Exception as e:
                logger.error(f"Failed to archive channel: {e}")
                console.print(f"[red]✗ Failed to archive channel: {e}[/red]")
                browser.close()
                raise

    def _scroll_to_load_all(self, page: Page) -> None:
        """Scroll to load all messages in the channel.

        Args:
            page: Playwright page
        """
        last_height = 0
        scroll_attempts = 0
        max_attempts = 100

        while scroll_attempts < max_attempts:
            # Scroll to top
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(self.config.page_scroll_delay)

            # Get current scroll height
            current_height = page.evaluate("document.body.scrollHeight")

            # If height hasn't changed, we've loaded everything
            if current_height == last_height:
                break

            last_height = current_height
            scroll_attempts += 1

        logger.info(f"Loaded messages after {scroll_attempts} scroll attempts")

    def _extract_messages(
        self,
        page: Page,
        options: ArchiveOptions,
        channel_name: str = "",
    ) -> list[Message]:
        """Extract messages from the page.

        Args:
            page: Playwright page
            options: Archive options
            channel_name: Channel name for file download organization

        Returns:
            List of messages
        """
        messages = []
        message_elements = page.locator("[data-qa='message_container']").all()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"Extracting {len(message_elements)} messages...",
                total=len(message_elements),
            )

            for elem in message_elements:
                try:
                    # Extract basic message info
                    msg_id = elem.get_attribute("data-ts") or ""

                    # Get timestamp
                    timestamp_elem = elem.locator("[data-ts]").first
                    timestamp = timestamp_elem.get_attribute("data-ts") or ""

                    # Get user info
                    user_elem = elem.locator("[data-qa='message_sender_name']").first
                    user_name = user_elem.inner_text() if user_elem.count() > 0 else "Unknown"

                    # Get message text
                    text_elem = elem.locator("[data-qa='message-text']").first
                    text = text_elem.inner_text() if text_elem.count() > 0 else ""

                    # Check if message is in thread
                    thread_ts = None
                    if elem.locator("[data-qa='message_thread']").count() > 0:
                        thread_ts = msg_id

                    # Extract file attachments and images
                    files = self._extract_files(elem, page, options, channel_name) if options.include_files else []

                    # Create message
                    message = Message(
                        id=msg_id,
                        timestamp=timestamp,
                        user="",  # User ID not easily accessible via DOM
                        user_name=user_name,
                        text=text,
                        thread_ts=thread_ts,
                        files=files,
                    )

                    # Apply date filters
                    if options.since or options.until:
                        try:
                            msg_time = datetime.fromtimestamp(float(timestamp))
                            if options.since and msg_time < options.since:
                                continue
                            if options.until and msg_time > options.until:
                                continue
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid timestamp: {timestamp}")

                    messages.append(message)
                    progress.update(task, advance=1)

                except Exception as e:
                    logger.warning(f"Failed to extract message: {e}")
                    progress.update(task, advance=1)
                    continue

        return messages

    def _extract_files(
        self,
        message_elem,
        page: Page,
        options: ArchiveOptions,
        channel_name: str,
    ) -> list[FileAttachment]:
        """Extract file attachments and images from a message.

        Args:
            message_elem: Message element
            page: Playwright page (for downloading)
            options: Archive options
            channel_name: Channel name for organizing downloads

        Returns:
            List of file attachments
        """
        files = []
        seen_urls: set[str] = set()

        # 1. Look for file attachments
        file_elements = message_elem.locator("[data-qa='slack_kit_attachment']").all()
        for file_elem in file_elements:
            try:
                file_link = file_elem.locator("a[href]").first
                if file_link.count() == 0:
                    continue
                url = file_link.get_attribute("href") or ""
                name = file_link.inner_text() or "unknown"
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    files.append(FileAttachment(name=name, url=url))
            except Exception:
                continue

        # 2. Look for images (inline images, uploaded images, image attachments)
        img_selectors = [
            "[data-qa='message_file_image_thumbnail'] img",
            "[data-qa='file_image_thumbnail'] img",
            ".c-file__image img",
            ".p-file_image_thumbnail__image",
            "img[data-qa='image']",
            ".c-message_kit__file img",
        ]
        for selector in img_selectors:
            imgs = message_elem.locator(selector).all()
            for img in imgs:
                try:
                    url = img.get_attribute("src") or ""
                    if not url or url in seen_urls:
                        continue
                    # Skip tiny icons / emoji
                    if "emoji" in url or "avatar" in url:
                        continue
                    seen_urls.add(url)
                    # Derive filename from URL
                    parsed = urlparse(url)
                    name = Path(parsed.path).name or "image"
                    files.append(FileAttachment(name=name, url=url, mimetype="image"))
                except Exception:
                    continue

        # 3. Also check for generic file download links within the message
        download_links = message_elem.locator(
            "a[data-qa='file_download_button'], a[download]"
        ).all()
        for link in download_links:
            try:
                url = link.get_attribute("href") or ""
                name = link.get_attribute("download") or link.inner_text() or "file"
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    files.append(FileAttachment(name=name, url=url))
            except Exception:
                continue

        # Apply file pattern filter
        if options.file_pattern:
            files = [f for f in files if re.search(options.file_pattern, f.name)]

        # Download files
        if files:
            files = self._download_files(files, page, channel_name)

        return files

    def _download_files(
        self,
        files: list[FileAttachment],
        page: Page,
        channel_name: str,
    ) -> list[FileAttachment]:
        """Download file attachments to local storage.

        Args:
            files: List of file attachments to download
            page: Playwright page (for authenticated downloads)
            channel_name: Channel name for directory organization

        Returns:
            Updated list with local_path set
        """
        download_dir = self.config.archives_dir / channel_name / "files"
        download_dir.mkdir(parents=True, exist_ok=True)

        for file_info in files:
            try:
                url = file_info.url
                if not url or not url.startswith("http"):
                    continue

                # Use the page's request context to download (preserves auth cookies)
                response = page.request.get(url)
                if response.status != 200:
                    logger.warning(f"Failed to download {file_info.name}: HTTP {response.status}")
                    continue

                # Ensure unique filename
                local_name = file_info.name.replace("/", "_")
                local_path = download_dir / local_name
                counter = 1
                while local_path.exists():
                    stem = Path(local_name).stem
                    suffix = Path(local_name).suffix
                    local_path = download_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                local_path.write_bytes(response.body())
                file_info.local_path = str(local_path)
                file_info.size = len(response.body())
                logger.info(f"Downloaded: {file_info.name} -> {local_path}")

            except Exception as e:
                logger.warning(f"Failed to download {file_info.name}: {e}")
                continue

        return files
