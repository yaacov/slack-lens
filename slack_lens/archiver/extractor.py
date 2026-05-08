"""DOM extraction helpers for Slack messages and threads."""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime
from typing import TYPE_CHECKING

from slack_lens.archiver.files import extract_files
from slack_lens.models import (
    ArchiveOptions,
    FileAttachment,
    Message,
    format_timestamp,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)


def find_scroller(page: Page) -> str | None:
    """Locate the scrollable ancestor of the message list and tag it.

    Returns:
        CSS selector for the tagged element, or *None* if not found.
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


def debug_message_dom(elem, timestamp: str) -> None:
    """Dump data-qa attributes and thread/reply hints for a message element.

    Only called when DEBUG logging is enabled.
    """
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
        format_timestamp(timestamp),
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
            format_timestamp(timestamp),
            json.dumps(reply_hints, indent=2),
        )


def extract_visible_messages(
    page: Page,
    options: ArchiveOptions,
    channel_name: str,
    seen_ids: set[str],
    archives_dir,
) -> list[Message]:
    """Extract messages currently rendered in the DOM, skipping duplicates.

    Args:
        page: Playwright page.
        options: Archive options.
        channel_name: Channel name (for file downloads).
        seen_ids: Set of already-collected message timestamps.
        archives_dir: Base archive directory (for file downloads).

    Returns:
        Newly extracted messages (not previously seen).
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

            user_elem = elem.locator("[data-qa='message_sender_name']").first
            user_name = (
                user_elem.inner_text() if user_elem.count() > 0 else "Unknown"
            )

            text_elem = elem.locator("[data-qa='message-text']").first
            text = text_elem.inner_text() if text_elem.count() > 0 else ""

            thread_btn = elem.locator("[data-qa='reply_bar_count']")
            has_thread = thread_btn.count() > 0

            if logger.isEnabledFor(logging.DEBUG):
                debug_message_dom(elem, timestamp)

            thread_ts = msg_id if has_thread else None

            if options.since or options.until:
                try:
                    msg_time = datetime.fromtimestamp(float(timestamp))
                    if options.since and msg_time < options.since:
                        continue
                    if options.until and msg_time > options.until:
                        continue
                except (ValueError, TypeError):
                    logger.warning("Unparseable timestamp: %s", timestamp)

            files: list[FileAttachment] = (
                extract_files(elem, page, options, channel_name, archives_dir)
                if options.include_files
                else []
            )

            replies: list[Message] = []
            if has_thread and options.include_threads:
                replies = extract_thread_replies(
                    page, elem, options, channel_name, archives_dir
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
                format_timestamp(timestamp),
                user_name,
                f"{len(files)} file(s) " if files else "",
                f"{len(replies)} replies " if replies else "",
                text[:60],
            )

        except Exception as e:
            logger.warning("Failed to extract message: %s", e)
            continue

    return new_messages


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


def extract_thread_replies(
    page: Page,
    message_elem,
    options: ArchiveOptions,
    channel_name: str,
    archives_dir,
) -> list[Message]:
    """Open a thread panel, extract replies, then close it."""
    replies: list[Message] = []
    parent_ts = message_elem.get_attribute("data-ts") or ""

    try:
        reply_btn = message_elem.locator("[data-qa='reply_bar_count']").first
        reply_btn.click()
        logger.info(
            "Clicked reply_bar_count for message %s",
            format_timestamp(parent_ts),
        )

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
            close_thread_panel(page)
            return replies

        time.sleep(random.uniform(0.8, 1.5))

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

                r_files: list[FileAttachment] = (
                    extract_files(
                        reply_elem, page, options, channel_name, archives_dir
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
            format_timestamp(parent_ts),
        )

    except Exception as e:
        logger.warning("Failed to load thread for %s: %s", parent_ts, e)

    close_thread_panel(page)
    return replies
