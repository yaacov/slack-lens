"""Channel archival orchestrator -- scroll loop and incremental flush."""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from typing import TYPE_CHECKING

from playwright.sync_api import sync_playwright

from slack_lens.archiver.extractor import (
    extract_visible_messages,
    find_scroller,
    scroll_step,
)
from slack_lens.browser import launch_browser
from slack_lens.config import Config
from slack_lens.log import console
from slack_lens.models import (
    ArchiveOptions,
    Channel,
    ChannelArchive,
    Message,
    format_timestamp,
)
from slack_lens.storage import Storage

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Browser, Page

    from slack_lens.client import SlackClient

logger = logging.getLogger(__name__)


class ChannelArchiver:
    """Archive Slack channels."""

    def __init__(
        self,
        client: SlackClient,
        config: Config | None = None,
    ):
        self.client = client
        self.config = config or Config()
        self.storage = Storage(config=self.config)

    # ------------------------------------------------------------------
    # Browser / page helpers
    # ------------------------------------------------------------------

    def _open_channel_page(self, pw, channel: Channel) -> tuple[Browser, Page]:
        """Launch the browser, authenticate, and navigate to *channel*.

        Returns the ``(browser, page)`` pair so the caller can close the
        browser when finished.
        """
        browser = launch_browser(pw, headless=self.config.headless)
        context = browser.new_context(
            storage_state=str(self.config.auth_file),
        )
        page = context.new_page()

        client_url = self.config.get_client_url() or self.client.app_url
        team_base = client_url.rstrip("/").rsplit("/", 1)[0]
        channel_url = f"{team_base}/{channel.id}"
        page.goto(channel_url, timeout=self.config.browser_timeout)

        page.wait_for_selector(
            "[data-qa='message_container']", timeout=30000,
        )
        return browser, page

    def _should_stop_scrolling(self, page: Page, since: datetime) -> bool:
        """Return *True* when every visible message predates *since*.

        Reads ``data-ts`` attributes from the DOM so we can stop the
        scroll loop early instead of walking the entire channel history.
        """
        dom_timestamps: list[float] = page.evaluate("""() => {
            const els = document.querySelectorAll(
                "[data-qa='message_container'] [data-ts]"
            );
            const ts = [];
            for (const el of els) {
                const v = parseFloat(el.getAttribute('data-ts') || '');
                if (v) ts.push(v);
            }
            return ts;
        }""")

        if not dom_timestamps:
            return False

        try:
            newest_dt = datetime.fromtimestamp(max(dom_timestamps))
            if newest_dt < since:
                oldest = format_timestamp(str(min(dom_timestamps)))
                newest = format_timestamp(str(max(dom_timestamps)))
                console.print(
                    f"  [yellow]All visible msgs ({oldest} - {newest}) "
                    f"are before --since, stopping scroll[/yellow]"
                )
                return True
        except (ValueError, TypeError, OSError):
            pass

        return False

    # ------------------------------------------------------------------
    # Main archive loop
    # ------------------------------------------------------------------

    def archive_channel(
        self,
        channel: Channel,
        options: ArchiveOptions | None = None,
        output_format: str = "json",
    ) -> Path:
        """Archive a channel incrementally.

        Messages are extracted in scroll-window batches and flushed to
        disk after every batch so progress is visible in real time.

        Args:
            channel: Channel to archive.
            options: Archival filtering options.
            output_format: ``"json"``, ``"txt"``, or ``"both"``.
        """
        options = options or ArchiveOptions()

        console.print(f"\n[bold cyan]Archiving #{channel.name}[/bold cyan]")

        started_at = datetime.now().isoformat()
        ts_safe = started_at.replace(":", "-").replace(" ", "_")
        json_path = self.config.archives_dir / f"{channel.name}_{ts_safe}.json"
        txt_path = self.config.archives_dir / f"{channel.name}_{ts_safe}.txt"
        self.config.ensure_dirs()

        seen_ids: set[str] = set()
        all_messages: list[Message] = []

        with sync_playwright() as pw:
            browser, page = self._open_channel_page(pw, channel)

            try:
                scroller_sel = find_scroller(page)

                scroll_attempts = 0
                max_attempts = 100
                stable_count = 0
                msgs_since_pause = 0

                while scroll_attempts < max_attempts:
                    batch = extract_visible_messages(
                        page, options, channel.name, seen_ids,
                        self.config.archives_dir,
                    )
                    if batch:
                        all_messages.extend(batch)
                        msgs_since_pause += len(batch)
                        self._flush(
                            output_format=output_format,
                            json_path=json_path,
                            txt_path=txt_path,
                            channel=channel,
                            messages=all_messages,
                            options=options,
                            started_at=started_at,
                        )
                        console.print(
                            f"  [green]+{len(batch)} msgs "
                            f"(total {len(all_messages)})[/green]  "
                            f"oldest visible: {format_timestamp(batch[0].timestamp)}  "
                            f"newest visible: {format_timestamp(batch[-1].timestamp)}"
                        )

                        if msgs_since_pause >= 10:
                            delay = random.uniform(1.5, 4.0)
                            logger.debug(
                                "Pausing %.1fs after %d messages",
                                delay,
                                msgs_since_pause,
                            )
                            time.sleep(delay)
                            msgs_since_pause = 0

                    if options.since and self._should_stop_scrolling(
                        page, options.since,
                    ):
                        break

                    if not scroller_sel:
                        break

                    scroll_info = scroll_step(
                        page, scroller_sel, direction="up",
                    )
                    time.sleep(self.config.page_scroll_delay)

                    if not scroll_info:
                        break

                    logger.debug(
                        "scroll #%d  scrollTop=%d->%d  "
                        "scrollHeight=%d  clientHeight=%d  "
                        "msgs_in_dom=%d  total_collected=%d",
                        scroll_attempts,
                        scroll_info["scrollTopBefore"],
                        scroll_info["scrollTop"],
                        scroll_info["scrollHeight"],
                        scroll_info["clientHeight"],
                        page.locator("[data-qa='message_container']").count(),
                        len(all_messages),
                    )

                    if scroll_info["scrollTop"] == scroll_info["scrollTopBefore"]:
                        stable_count += 1
                        if stable_count >= 3:
                            break
                    else:
                        stable_count = 0

                    scroll_attempts += 1

                # Final extraction after the last scroll
                final_batch = extract_visible_messages(
                    page, options, channel.name, seen_ids,
                    self.config.archives_dir,
                )
                if final_batch:
                    all_messages.extend(final_batch)

                all_messages.sort(key=lambda m: m.timestamp)

                self._flush(
                    output_format=output_format,
                    json_path=json_path,
                    txt_path=txt_path,
                    channel=channel,
                    messages=all_messages,
                    options=options,
                    started_at=started_at,
                )

                saved: list[Path] = []
                if output_format in ("json", "both"):
                    saved.append(json_path)
                if output_format in ("txt", "both"):
                    saved.append(txt_path)

                for path in saved:
                    console.print(
                        f"\n[green]Archived {len(all_messages)} messages "
                        f"to {path}[/green]"
                    )
                if all_messages:
                    console.print(
                        f"  [dim]Date range: "
                        f"{format_timestamp(all_messages[0].timestamp)} -> "
                        f"{format_timestamp(all_messages[-1].timestamp)}[/dim]"
                    )

                browser.close()
                return saved[0]

            except Exception as e:
                logger.error("Failed to archive channel: %s", e)
                console.print(f"[red]Failed to archive channel: {e}[/red]")
                if all_messages:
                    console.print(
                        f"[yellow]Partial archive saved with "
                        f"{len(all_messages)} messages to "
                        f"{json_path}[/yellow]"
                    )
                browser.close()
                raise

    def _build_archive(
        self,
        *,
        channel: Channel,
        messages: list[Message],
        options: ArchiveOptions,
        started_at: str,
    ) -> ChannelArchive:
        """Construct a ChannelArchive from collected messages."""
        date_range = {}
        if messages:
            sorted_ts = sorted(messages, key=lambda m: m.timestamp)
            date_range = {
                "oldest_message": format_timestamp(sorted_ts[0].timestamp),
                "newest_message": format_timestamp(sorted_ts[-1].timestamp),
            }

        total_replies = sum(len(m.replies) for m in messages)
        total_files = sum(
            len(m.files) + sum(len(r.files) for r in m.replies)
            for m in messages
        )

        return ChannelArchive(
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
                    "include_threads": options.include_threads,
                    "include_files": options.include_files,
                    "file_pattern": options.file_pattern,
                },
                "total_messages": len(messages),
                "total_replies": total_replies,
                "total_files": total_files,
                **date_range,
            },
        )

    def _flush(
        self,
        *,
        output_format: str,
        json_path: Path,
        txt_path: Path,
        channel: Channel,
        messages: list[Message],
        options: ArchiveOptions,
        started_at: str,
    ) -> None:
        """Build a ChannelArchive and flush to disk in the requested formats."""
        archive = self._build_archive(
            channel=channel,
            messages=messages,
            options=options,
            started_at=started_at,
        )
        if output_format in ("json", "both"):
            self.storage.save_channel(archive, filepath=json_path)
        if output_format in ("txt", "both"):
            self.storage.save_channel_txt(archive, filepath=txt_path)
