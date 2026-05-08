"""DOM extraction helpers for Slack messages and threads."""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from typing import TYPE_CHECKING

from slack_lens.archiver.debug import (
    dump_flexpane_debug,
    dump_message_dom,
    dump_page_html,
)
from slack_lens.archiver.files import extract_files
from slack_lens.models import (
    ArchiveOptions,
    FileAttachment,
    Message,
    format_timestamp,
)

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Locator, Page

logger = logging.getLogger(__name__)

THREAD_PANEL_SELECTOR = (
    "[data-qa='thread_view'], "
    "[data-qa='threads_flexpane'], "
    "[data-qa='thread_flexpane'], "
    ".p-flexpane__inside_body--thread, "
    ".p-thread_view"
)

_THREAD_CLICK_TARGETS = [
    "[data-qa='reply_bar_count']",
    "[data-qa='reply_bar_view_thread']",
]


# ---------------------------------------------------------------------------
# Shared primitives: find scroller, extract, collect, scroll
# ---------------------------------------------------------------------------

def find_scroller(
    page: Page,
    root_selector: str | None = None,
    tag: str = "data-slack-lens-scroller",
) -> str | None:
    """Locate the scrollable ancestor of the message list and tag it.

    Works for both the main channel (``root_selector=None``) and the
    thread panel (pass a panel selector as *root_selector*).

    Returns:
        CSS selector ``[<tag>='1']`` for the tagged element, or *None*.
    """
    info = page.evaluate("""(opts) => {
        // Clear any stale tag from a previous call
        const old = document.querySelector('[' + opts.tag + ']');
        if (old) old.removeAttribute(opts.tag);

        const root = opts.rootSel
            ? document.querySelector(opts.rootSel)
            : document;
        if (!root) return {found: false, path: []};
        const msg = root.querySelector("[data-qa='message_container']");
        if (!msg) return null;
        const path = [];
        let el = msg.parentElement;
        while (el && el !== document.documentElement) {
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
                el.setAttribute(opts.tag, '1');
                return {found: true, path};
            }
            el = el.parentElement;
        }
        return {found: false, path};
    }""", {"rootSel": root_selector, "tag": tag})

    if info is None:
        logger.warning("No message_container elements in DOM")
        return None

    logger.debug("DOM ancestor walk (root=%s):", root_selector or "page")
    for i, node in enumerate(info.get("path", [])):
        logger.debug(
            "  %d. <%s class='%s'> overflowY=%s "
            "scrollHeight=%s clientHeight=%s",
            i,
            node["tag"],
            str(node["classes"])[:80],
            node["overflowY"],
            node["scrollH"],
            node["clientH"],
        )

    if info.get("found"):
        selector = f"[{tag}='1']"
        logger.debug("Scrollable container tagged: %s", selector)
        return selector

    logger.warning("Could not find scrollable container (root=%s)", root_selector)
    return None


def extract_message_data(
    elem: Locator,
    options: ArchiveOptions,
    page: Page,
    channel_name: str,
    archives_dir: Path | str,
) -> dict | None:
    """Extract data from a single ``message_container`` element.

    Returns a dict with keys ``ts``, ``msg_id``, ``user``, ``text``,
    ``files``, ``has_thread``, ``thread_ts``, or *None* on failure.
    """
    try:
        ts_elem = elem.locator("[data-ts]").first
        ts = ts_elem.get_attribute("data-ts") or ""
        if not ts:
            return None

        msg_id = elem.get_attribute("data-ts") or ts

        user_elem = elem.locator("[data-qa='message_sender_name']").first
        user = user_elem.inner_text() if user_elem.count() > 0 else "Unknown"

        text_elem = elem.locator("[data-qa='message-text']").first
        text = text_elem.inner_text() if text_elem.count() > 0 else ""

        files: list[FileAttachment] = (
            extract_files(elem, page, options, channel_name, archives_dir)
            if options.include_files
            else []
        )

        thread_btn = elem.locator("[data-qa='reply_bar_count']")
        has_thread = thread_btn.count() > 0

        return {
            "ts": ts,
            "msg_id": msg_id,
            "user": user,
            "text": text,
            "files": files,
            "has_thread": has_thread,
            "thread_ts": msg_id if has_thread else None,
        }
    except Exception as e:
        logger.warning("Failed to extract message data: %s", e)
        return None


def collect_visible(
    scope: Locator | Page,
    options: ArchiveOptions,
    page: Page,
    channel_name: str,
    archives_dir: Path | str,
    collected: dict[str, dict],
    *,
    skip_first: bool = False,
) -> int:
    """Extract visible ``message_container`` elements into *collected*.

    Args:
        scope: Playwright locator (thread panel) or page to search within.
        skip_first: If *True*, skip the first element (thread parent).
        collected: Dict keyed by timestamp; new items are merged in.

    Returns:
        Number of newly added items.
    """
    added = 0
    elements = scope.locator("[data-qa='message_container']").all()
    for i, elem in enumerate(elements):
        if skip_first and i == 0:
            continue
        data = extract_message_data(
            elem, options, page, channel_name, archives_dir,
        )
        if data and data["ts"] not in collected:
            collected[data["ts"]] = data
            added += 1
    return added


def scroll_step(
    page: Page,
    scroller_sel: str,
    direction: str = "down",
) -> dict | None:
    """Scroll *scroller_sel* by one viewport height.

    Args:
        direction: ``"up"`` (older messages) or ``"down"`` (newer).

    Returns:
        Dict with ``scrollTop``, ``scrollTopBefore``, ``scrollHeight``,
        ``clientHeight``, or *None* if the element wasn't found.
    """
    js = """(opts) => {
        const el = document.querySelector(opts.sel);
        if (!el) return null;
        const before = el.scrollTop;
        if (opts.dir === "up") {
            el.scrollTop = Math.max(0, el.scrollTop - el.clientHeight);
        } else {
            el.scrollTop = el.scrollTop + el.clientHeight;
        }
        return {
            scrollTop: el.scrollTop,
            scrollTopBefore: before,
            scrollHeight: el.scrollHeight,
            clientHeight: el.clientHeight,
        };
    }"""
    return page.evaluate(js, {"sel": scroller_sel, "dir": direction})


def scroll_and_collect(
    page: Page,
    scroller_sel: str | None,
    scope: Locator | Page,
    options: ArchiveOptions,
    channel_name: str,
    archives_dir: Path | str,
    *,
    direction: str = "down",
    skip_first: bool = False,
    scroll_delay: float = 0.6,
    max_scrolls: int = 30,
    stable_threshold: int = 2,
) -> dict[str, dict]:
    """Scroll-and-accumulate: extract visible items at each scroll position.

    This is the core loop shared by both channel scrolling and thread
    panel scrolling.  Slack virtualises long lists, so we must extract
    at each position before scrolling further.

    Returns:
        Dict keyed by message timestamp with extracted data dicts.
    """
    collected: dict[str, dict] = {}

    collect_visible(
        scope, options, page, channel_name, archives_dir,
        collected, skip_first=skip_first,
    )

    if not scroller_sel:
        return collected

    stable = 0
    for attempt in range(max_scrolls):
        scroll_step(page, scroller_sel, direction)
        time.sleep(scroll_delay + random.uniform(0, 0.3))

        added = collect_visible(
            scope, options, page, channel_name, archives_dir,
            collected, skip_first=skip_first,
        )
        logger.debug(
            "Scroll #%d (%s): +%d new items (total %d)",
            attempt, direction, added, len(collected),
        )

        if added == 0:
            stable += 1
            if stable >= stable_threshold:
                break
        else:
            stable = 0

    return collected


# ---------------------------------------------------------------------------
# Main channel extraction (two-phase: scan visible, then process threads)
# ---------------------------------------------------------------------------

def extract_visible_messages(
    page: Page,
    options: ArchiveOptions,
    channel_name: str,
    seen_ids: set[str],
    archives_dir: Path | str,
) -> list[Message]:
    """Extract messages currently rendered in the DOM, skipping duplicates.

    Uses a two-phase approach: first scan all visible messages (read-only),
    then process threads one at a time.  This avoids stale-locator bugs
    caused by Slack re-rendering the channel when a thread panel opens.
    """
    new_messages: list[Message] = []
    dump_page_html(page, archives_dir, "messages")

    # --- Phase 1: scan visible messages (no thread interaction) -----------
    collected: dict[str, dict] = {}
    collect_visible(
        page, options, page, channel_name, archives_dir,
        collected,
    )

    scanned: list[dict] = []
    for ts in sorted(collected):
        data = collected[ts]
        if ts in seen_ids:
            continue

        if options.since or options.until:
            try:
                msg_time = datetime.fromtimestamp(float(ts))
                if options.since and msg_time < options.since:
                    continue
                if options.until and msg_time > options.until:
                    continue
            except (ValueError, TypeError):
                logger.warning("Unparseable timestamp: %s", ts)

        if logger.isEnabledFor(logging.DEBUG):
            container = page.locator(
                f"[data-qa='message_container']:has([data-ts='{ts}'])"
            ).first
            if container.count() > 0:
                dump_message_dom(container, ts)

        scanned.append(data)
        seen_ids.add(ts)

    # --- Phase 2: extract threads (re-find each element by timestamp) -----
    for item in scanned:
        replies: list[Message] = []
        ts = item["ts"]

        if item["has_thread"] and options.include_threads:
            container = page.locator(
                f"[data-qa='message_container']:has([data-ts='{ts}'])"
            ).first
            if container.count() > 0:
                replies = extract_thread_replies(
                    page, container, options, channel_name, archives_dir,
                )
            else:
                logger.debug(
                    "Message %s left viewport after a thread interaction; "
                    "its thread will be captured on a later scroll pass",
                    format_timestamp(ts),
                )

        message = Message(
            id=item["msg_id"],
            timestamp=ts,
            user="",
            user_name=item["user"],
            text=item["text"],
            thread_ts=item["thread_ts"],
            replies=replies,
            files=item["files"],
        )
        new_messages.append(message)

        logger.debug(
            "  msg %s  %s  %s%s: %s",
            format_timestamp(ts),
            item["user"],
            f"{len(item['files'])} file(s) " if item["files"] else "",
            f"{len(replies)} replies " if replies else "",
            item["text"][:60],
        )

    return new_messages


# ---------------------------------------------------------------------------
# Thread panel helpers
# ---------------------------------------------------------------------------

def close_thread_panel(page: Page) -> None:
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
        page.keyboard.press("Escape")
        time.sleep(0.5)
    except Exception:
        pass


def _open_thread_panel(page: Page, message_elem: Locator) -> Locator | None:
    """Click reply bar to open the thread panel, with fallback targets.

    Hovers over the message first so Slack attaches its event handlers
    and renders the action bar, then tries each click target in
    ``_THREAD_CLICK_TARGETS`` and waits for the thread panel to become
    visible.  Returns the panel locator on success, or *None* if the
    panel never appeared.
    """
    try:
        message_elem.hover()
        time.sleep(random.uniform(0.3, 0.6))
    except Exception:
        logger.debug("Hover on message failed, proceeding with click")

    for target_sel in _THREAD_CLICK_TARGETS:
        btn = message_elem.locator(target_sel).first
        if btn.count() == 0:
            continue

        try:
            btn.scroll_into_view_if_needed()
            btn.hover()
            time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            pass

        btn.click()
        logger.debug("Clicked %s", target_sel)

        panel = page.locator(THREAD_PANEL_SELECTOR)
        try:
            panel.wait_for(state="visible", timeout=8000)
            logger.debug(
                "Thread panel visible after clicking %s", target_sel,
            )
            return panel
        except Exception:
            logger.debug(
                "Thread panel not visible after clicking %s, "
                "trying next target",
                target_sel,
            )
            close_thread_panel(page)
            time.sleep(random.uniform(0.3, 0.6))

    return None


def extract_thread_replies(
    page: Page,
    message_elem: Locator,
    options: ArchiveOptions,
    channel_name: str,
    archives_dir: Path | str,
) -> list[Message]:
    """Open a thread panel, scroll to accumulate all replies, then close.

    Uses the shared :func:`scroll_and_collect` helper so that long
    virtualised threads are fully traversed.
    """
    replies: list[Message] = []
    _pts_el = message_elem.locator("[data-ts]").first
    parent_ts = _pts_el.get_attribute("data-ts") if _pts_el.count() > 0 else ""

    try:
        thread_panel = _open_thread_panel(page, message_elem)

        if not thread_panel:
            logger.warning(
                "Thread panel did not appear for message %s. "
                "Dumping flexpane DOM for debugging...",
                format_timestamp(parent_ts),
            )
            dump_flexpane_debug(page, parent_ts, archives_dir)
            close_thread_panel(page)
            return replies

        dump_page_html(page, archives_dir, "thread_open", parent_ts)
        time.sleep(random.uniform(0.8, 1.5))

        try:
            thread_panel.locator(
                "[data-qa='message_container']"
            ).first.wait_for(state="visible", timeout=8000)
        except Exception:
            logger.debug(
                "No message_containers appeared in thread panel for %s",
                format_timestamp(parent_ts),
            )

        thread_scroller = find_scroller(
            page,
            root_selector=THREAD_PANEL_SELECTOR,
            tag="data-slack-lens-tscroll",
        )

        collected = scroll_and_collect(
            page,
            scroller_sel=thread_scroller,
            scope=thread_panel,
            options=options,
            channel_name=channel_name,
            archives_dir=archives_dir,
            direction="down",
            skip_first=True,
            scroll_delay=0.5,
            max_scrolls=30,
        )

        panel_first_ts = ""
        first_elements = thread_panel.locator(
            "[data-qa='message_container']"
        ).all()
        if first_elements:
            try:
                _fe = first_elements[0].locator("[data-ts]").first
                panel_first_ts = _fe.get_attribute("data-ts") or ""
            except Exception:
                panel_first_ts = "ERROR"

        if parent_ts and panel_first_ts and parent_ts != panel_first_ts:
            logger.debug(
                "Thread panel parent differs (msg %s, panel %s) — "
                "message is a cross-posted reply; extracting thread as-is",
                format_timestamp(parent_ts),
                format_timestamp(panel_first_ts),
            )

        for ts in sorted(collected):
            data = collected[ts]
            replies.append(
                Message(
                    id=data["ts"],
                    timestamp=data["ts"],
                    user="",
                    user_name=data["user"],
                    text=data["text"],
                    files=data["files"],
                )
            )

        logger.debug(
            "Thread: extracted %d replies for message %s",
            len(replies),
            format_timestamp(parent_ts),
        )

    except Exception as e:
        logger.warning("Failed to load thread for %s: %s", parent_ts, e)

    close_thread_panel(page)
    return replies
