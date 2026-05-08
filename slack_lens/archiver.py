"""Channel archival logic."""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright
from rich.console import Console

from slack_lens.config import Config
from slack_lens.slack_client import Channel, SlackClient, _launch_browser
from slack_lens.storage import ChannelArchive, FileAttachment, Message, Storage

logger = logging.getLogger(__name__)
console = Console()


def _format_ts(ts: str) -> str:
    """Convert a Slack epoch timestamp to a human-readable string."""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return ts


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
        """Archive a channel incrementally.

        Messages are extracted in scroll-window batches and flushed to
        a JSON file on disk after every batch so progress is visible in
        real time.

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

        # Prepare the output file path up front so we can write incrementally
        started_at = datetime.now().isoformat()
        filename = (
            f"{channel.name}_{started_at.replace(':', '-').replace(' ', '_')}.json"
        )
        filepath = self.config.archives_dir / filename
        self.config.ensure_dirs()

        seen_ids: set[str] = set()
        all_messages: list[Message] = []

        with sync_playwright() as p:
            browser = _launch_browser(p, headless=self.config.headless)
            context = browser.new_context(
                storage_state=str(self.config.auth_file)
            )
            page = context.new_page()

            try:
                client_url = self.config.get_client_url() or self.client.app_url
                parts = client_url.rstrip("/").rsplit("/", 1)
                team_base = parts[0]
                channel_url = f"{team_base}/{channel.id}"
                page.goto(channel_url, timeout=self.config.browser_timeout)

                page.wait_for_selector(
                    "[data-qa='message_container']", timeout=30000
                )

                scroller_sel = self._find_scroller(page)

                # --- Incremental scroll-and-extract loop ----------------------
                scroll_attempts = 0
                max_attempts = 100
                last_height = 0
                stable_count = 0
                msgs_since_pause = 0
                reached_since = False

                while scroll_attempts < max_attempts:
                    # Extract whatever is currently in the DOM
                    batch = self._extract_visible_messages(
                        page, options, channel.name, seen_ids
                    )
                    if batch:
                        all_messages.extend(batch)
                        msgs_since_pause += len(batch)
                        self._flush_archive(
                            filepath,
                            channel=channel,
                            messages=all_messages,
                            options=options,
                            started_at=started_at,
                        )
                        console.print(
                            f"  [green]+{len(batch)} msgs "
                            f"(total {len(all_messages)})[/green]  "
                            f"oldest visible: {_format_ts(batch[0].timestamp)}  "
                            f"newest visible: {_format_ts(batch[-1].timestamp)}"
                        )

                        # Random pause every ~10 messages to look human
                        if msgs_since_pause >= 10:
                            delay = random.uniform(1.5, 4.0)
                            logger.info(
                                "Pausing %.1fs after %d messages",
                                delay,
                                msgs_since_pause,
                            )
                            time.sleep(delay)
                            msgs_since_pause = 0

                    # After extracting, check if ALL visible messages
                    # are older than --since.  Only stop when the
                    # entire viewport is before the cutoff, because
                    # the virtualized list can show a mix of old and
                    # new timestamps within one viewport.
                    if options.since:
                        dom_timestamps = page.evaluate("""() => {
                            const els = document.querySelectorAll(
                                "[data-qa='message_container'] [data-ts]"
                            );
                            const ts = [];
                            for (const el of els) {
                                const v = parseFloat(
                                    el.getAttribute('data-ts') || ''
                                );
                                if (v) ts.push(v);
                            }
                            return ts;
                        }""")
                        if dom_timestamps:
                            try:
                                newest_dt = datetime.fromtimestamp(
                                    max(dom_timestamps)
                                )
                                oldest_dt = datetime.fromtimestamp(
                                    min(dom_timestamps)
                                )
                                if newest_dt < options.since:
                                    console.print(
                                        f"  [yellow]All visible msgs "
                                        f"({_format_ts(str(min(dom_timestamps)))} "
                                        f"– {_format_ts(str(max(dom_timestamps)))}) "
                                        f"are before --since, stopping "
                                        f"scroll[/yellow]"
                                    )
                                    reached_since = True
                                    break
                            except (ValueError, TypeError, OSError):
                                pass

                    if not scroller_sel:
                        break

                    # Scroll up by one viewport height so the
                    # virtualized list renders the next page of
                    # messages instead of jumping to the very top and
                    # skipping everything in between.
                    scroll_info = page.evaluate(f"""() => {{
                        const el = document.querySelector("{scroller_sel}");
                        if (!el) return null;
                        const before = el.scrollTop;
                        el.scrollTop = Math.max(0, el.scrollTop - el.clientHeight);
                        return {{
                            scrollTop: el.scrollTop,
                            scrollTopBefore: before,
                            scrollHeight: el.scrollHeight,
                            clientHeight: el.clientHeight,
                        }};
                    }}""")
                    time.sleep(self.config.page_scroll_delay)

                    if not scroll_info:
                        break

                    current_height = scroll_info["scrollHeight"]

                    logger.info(
                        "scroll #%d  scrollTop=%d→%d  "
                        "scrollHeight=%d  clientHeight=%d  "
                        "msgs_in_dom=%d  total_collected=%d",
                        scroll_attempts,
                        scroll_info["scrollTopBefore"],
                        scroll_info["scrollTop"],
                        current_height,
                        scroll_info["clientHeight"],
                        page.locator("[data-qa='message_container']").count(),
                        len(all_messages),
                    )

                    # If scrollTop didn't move we've hit the top
                    if scroll_info["scrollTop"] == scroll_info["scrollTopBefore"]:
                        stable_count += 1
                        if stable_count >= 3:
                            break
                    else:
                        stable_count = 0

                    last_height = current_height
                    scroll_attempts += 1

                # Final extraction after the last scroll
                final_batch = self._extract_visible_messages(
                    page, options, channel.name, seen_ids
                )
                if final_batch:
                    all_messages.extend(final_batch)

                # Sort by timestamp and do one final flush
                all_messages.sort(key=lambda m: m.timestamp)
                self._flush_archive(
                    filepath,
                    channel=channel,
                    messages=all_messages,
                    options=options,
                    started_at=started_at,
                )

                console.print(
                    f"\n[green]✓ Archived {len(all_messages)} messages "
                    f"to {filepath}[/green]"
                )
                if all_messages:
                    console.print(
                        f"  [dim]Date range: "
                        f"{_format_ts(all_messages[0].timestamp)} → "
                        f"{_format_ts(all_messages[-1].timestamp)}[/dim]"
                    )

                browser.close()
                return filepath

            except Exception as e:
                logger.error(f"Failed to archive channel: {e}")
                console.print(f"[red]✗ Failed to archive channel: {e}[/red]")
                if all_messages:
                    console.print(
                        f"[yellow]Partial archive saved with "
                        f"{len(all_messages)} messages to {filepath}[/yellow]"
                    )
                browser.close()
                raise

    # ------------------------------------------------------------------
    # Scrolling helpers
    # ------------------------------------------------------------------

    def _find_scroller(self, page: Page) -> str | None:
        """Locate the scrollable ancestor of the message list and tag it.

        Returns:
            CSS selector for the tagged element, or None if not found.
        """
        info = page.evaluate("""() => {
            const msg = document.querySelector("[data-qa='message_container']");
            if (!msg) return null;
            const path = [];
            let el = msg.parentElement;
            while (el) {
                const style = window.getComputedStyle(el);
                path.push({
                    tag: el.tagName,
                    classes: el.className,
                    overflowY: style.overflowY,
                    scrollH: el.scrollHeight,
                    clientH: el.clientHeight,
                });
                if ((style.overflowY === 'auto' || style.overflowY === 'scroll')
                    && el.scrollHeight > el.clientHeight) {
                    el.setAttribute('data-slack-lens-scroller', '1');
                    return {found: true, path};
                }
                el = el.parentElement;
            }
            return {found: false, path};
        }""")

        if info is None:
            logger.warning("No message_container elements in DOM")
            return None

        logger.info("DOM ancestor walk from message_container:")
        for i, node in enumerate(info.get("path", [])):
            logger.info(
                "  %d. <%s class='%s'> overflowY=%s "
                "scrollHeight=%s clientHeight=%s",
                i,
                node["tag"],
                node["classes"][:80],
                node["overflowY"],
                node["scrollH"],
                node["clientH"],
            )

        if info.get("found"):
            logger.info("Scrollable container found and tagged")
            return "[data-slack-lens-scroller='1']"

        logger.warning("Could not find scrollable message container")
        return None

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract_visible_messages(
        self,
        page: Page,
        options: ArchiveOptions,
        channel_name: str,
        seen_ids: set[str],
    ) -> list[Message]:
        """Extract messages currently rendered in the DOM, skipping duplicates.

        Args:
            page: Playwright page
            options: Archive options
            channel_name: Channel name (for file downloads)
            seen_ids: Set of already-collected message timestamps

        Returns:
            Newly extracted messages (not previously seen)
        """
        new_messages: list[Message] = []
        message_elements = page.locator("[data-qa='message_container']").all()

        for elem in message_elements:
            try:
                timestamp_elem = elem.locator("[data-ts]").first
                timestamp = timestamp_elem.get_attribute("data-ts") or ""

                if not timestamp or timestamp in seen_ids:
                    continue

                msg_id = elem.get_attribute("data-ts") or timestamp

                user_elem = elem.locator(
                    "[data-qa='message_sender_name']"
                ).first
                user_name = (
                    user_elem.inner_text()
                    if user_elem.count() > 0
                    else "Unknown"
                )

                text_elem = elem.locator("[data-qa='message-text']").first
                text = text_elem.inner_text() if text_elem.count() > 0 else ""

                # Detect thread indicator -- dump DOM info for debugging
                thread_btn = elem.locator("[data-qa='reply_bar_count']")
                has_thread = thread_btn.count() > 0

                # Debug: log all data-qa attributes and any "repl"
                # related elements inside this message container
                if logger.isEnabledFor(logging.DEBUG):
                    qa_attrs = elem.evaluate("""el => {
                        const items = el.querySelectorAll('[data-qa]');
                        return Array.from(items).map(n => ({
                            tag: n.tagName,
                            qa: n.getAttribute('data-qa'),
                            text: n.innerText?.slice(0, 60) || '',
                        }));
                    }""")
                    logger.debug(
                        "msg %s data-qa children: %s",
                        _format_ts(timestamp),
                        json.dumps(qa_attrs, indent=2),
                    )

                    reply_hints = elem.evaluate("""el => {
                        const all = el.querySelectorAll('*');
                        const hits = [];
                        for (const n of all) {
                            const attrs = Array.from(n.attributes || []);
                            const match = attrs.some(a =>
                                a.value.toLowerCase().includes('repl')
                                || a.value.toLowerCase().includes('thread')
                            );
                            const textMatch = (n.innerText || '')
                                .toLowerCase().includes('repl');
                            if (match || textMatch) {
                                hits.push({
                                    tag: n.tagName,
                                    classes: n.className?.slice?.(0, 80),
                                    attrs: attrs.map(a =>
                                        a.name + '=' + a.value.slice(0, 60)
                                    ),
                                    text: n.innerText?.slice(0, 60) || '',
                                });
                            }
                        }
                        return hits;
                    }""")
                    if reply_hints:
                        logger.debug(
                            "msg %s thread/reply hints: %s",
                            _format_ts(timestamp),
                            json.dumps(reply_hints, indent=2),
                        )

                thread_ts = msg_id if has_thread else None

                # Apply date filters early, before expensive file/thread work
                if options.since or options.until:
                    try:
                        msg_time = datetime.fromtimestamp(float(timestamp))
                        if options.since and msg_time < options.since:
                            continue
                        if options.until and msg_time > options.until:
                            continue
                    except (ValueError, TypeError):
                        logger.warning("Unparseable timestamp: %s", timestamp)

                files = (
                    self._extract_files(elem, page, options, channel_name)
                    if options.include_files
                    else []
                )

                # Fetch thread replies if enabled
                replies: list[Message] = []
                if has_thread and options.thread_depth != 0:
                    replies = self._extract_thread_replies(
                        page, elem, options, channel_name
                    )

                message = Message(
                    id=msg_id,
                    timestamp=timestamp,
                    user="",
                    user_name=user_name,
                    text=text,
                    thread_ts=thread_ts,
                    replies=replies,
                    files=files,
                )
                seen_ids.add(timestamp)
                new_messages.append(message)

                logger.debug(
                    "  msg %s  %s  %s%s: %s",
                    _format_ts(timestamp),
                    user_name,
                    f"{len(files)} file(s) " if files else "",
                    f"{len(replies)} replies " if replies else "",
                    text[:60],
                )

            except Exception as e:
                logger.warning("Failed to extract message: %s", e)
                continue

        return new_messages

    def _close_thread_panel(self, page: Page) -> None:
        """Try to close any open thread / flexpane panel."""
        try:
            close_btn = page.locator(
                "[data-qa='close_flexpane'], "
                "button[aria-label='Close'], "
                "button[aria-label='Close thread']"
            ).first
            if close_btn.count() > 0:
                close_btn.click()
                time.sleep(random.uniform(0.5, 1.0))
                return
            # Fallback: press Escape
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

    def _extract_thread_replies(
        self,
        page: Page,
        message_elem,
        options: ArchiveOptions,
        channel_name: str,
    ) -> list[Message]:
        """Open a thread panel, extract replies, then close it.

        Args:
            page: Playwright page
            message_elem: The parent message element
            options: Archive options
            channel_name: Channel name (for file downloads)

        Returns:
            List of reply messages (excludes the parent)
        """
        replies: list[Message] = []
        parent_ts = message_elem.get_attribute("data-ts") or ""

        try:
            # Click the reply count button to open the thread panel
            reply_btn = message_elem.locator(
                "[data-qa='reply_bar_count']"
            ).first
            reply_btn.click()
            logger.info(
                "Clicked reply_bar_count for message %s",
                _format_ts(parent_ts),
            )

            # Wait for the thread panel to appear -- try several selectors
            thread_panel = None
            for sel in [
                "[data-qa='thread_view']",
                "[data-qa='threads_flexpane']",
                ".p-flexpane__inside_body--thread",
                ".p-thread_view",
            ]:
                candidate = page.locator(sel)
                try:
                    candidate.wait_for(state="visible", timeout=5000)
                    thread_panel = candidate
                    logger.info("Thread panel matched selector: %s", sel)
                    break
                except Exception:
                    continue

            if not thread_panel:
                # Dump what we can see for debugging
                logger.warning(
                    "Thread panel did not appear. "
                    "Dumping flexpane DOM for debugging..."
                )
                flexpane_info = page.evaluate("""() => {
                    const panes = document.querySelectorAll(
                        '[class*=flexpane], [class*=thread], '
                        + '[data-qa*=thread]'
                    );
                    return Array.from(panes).map(el => ({
                        tag: el.tagName,
                        classes: el.className?.slice?.(0, 120),
                        qa: el.getAttribute('data-qa') || '',
                        visible: el.offsetParent !== null,
                        children: el.children.length,
                    }));
                }""")
                logger.warning(
                    "Flexpane candidates: %s",
                    json.dumps(flexpane_info, indent=2),
                )
                self._close_thread_panel(page)
                return replies

            time.sleep(random.uniform(0.8, 1.5))

            # Extract reply messages from the thread panel (skip the first
            # one which is the parent message itself)
            reply_elements = thread_panel.locator(
                "[data-qa='message_container']"
            ).all()
            logger.info(
                "Thread panel has %d message_container elements",
                len(reply_elements),
            )

            for i, reply_elem in enumerate(reply_elements):
                if i == 0:
                    continue

                try:
                    ts_elem = reply_elem.locator("[data-ts]").first
                    ts = ts_elem.get_attribute("data-ts") or ""

                    r_user_elem = reply_elem.locator(
                        "[data-qa='message_sender_name']"
                    ).first
                    r_user = (
                        r_user_elem.inner_text()
                        if r_user_elem.count() > 0
                        else "Unknown"
                    )

                    r_text_elem = reply_elem.locator(
                        "[data-qa='message-text']"
                    ).first
                    r_text = (
                        r_text_elem.inner_text()
                        if r_text_elem.count() > 0
                        else ""
                    )

                    r_files = (
                        self._extract_files(
                            reply_elem, page, options, channel_name
                        )
                        if options.include_files
                        else []
                    )

                    replies.append(
                        Message(
                            id=ts,
                            timestamp=ts,
                            user="",
                            user_name=r_user,
                            text=r_text,
                            files=r_files,
                        )
                    )
                except Exception as e:
                    logger.warning("Failed to extract reply: %s", e)
                    continue

            logger.info(
                "Thread: extracted %d replies for message %s",
                len(replies),
                _format_ts(parent_ts),
            )

        except Exception as e:
            logger.warning("Failed to load thread for %s: %s", parent_ts, e)

        self._close_thread_panel(page)
        return replies

    # ------------------------------------------------------------------
    # Incremental flush
    # ------------------------------------------------------------------

    def _flush_archive(
        self,
        filepath: Path,
        *,
        channel: Channel,
        messages: list[Message],
        options: ArchiveOptions,
        started_at: str,
    ) -> None:
        """Write the current archive state to disk (overwrite)."""
        date_range = {}
        if messages:
            sorted_ts = sorted(messages, key=lambda m: m.timestamp)
            date_range = {
                "oldest_message": _format_ts(sorted_ts[0].timestamp),
                "newest_message": _format_ts(sorted_ts[-1].timestamp),
            }

        total_replies = sum(len(m.replies) for m in messages)
        total_files = sum(
            len(m.files) + sum(len(r.files) for r in m.replies)
            for m in messages
        )

        archive = ChannelArchive(
            channel_id=channel.id,
            channel_name=channel.name,
            archived_at=started_at,
            workspace=self.client.workspace,
            messages=messages,
            metadata={
                "options": {
                    "since": (
                        options.since.isoformat() if options.since else None
                    ),
                    "until": (
                        options.until.isoformat() if options.until else None
                    ),
                    "thread_depth": options.thread_depth,
                    "include_files": options.include_files,
                    "file_pattern": options.file_pattern,
                },
                "total_messages": len(messages),
                "total_replies": total_replies,
                "total_files": total_files,
                **date_range,
            },
        )
        with filepath.open("w", encoding="utf-8") as f:
            json.dump(asdict(archive), f, indent=2, ensure_ascii=False)

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
