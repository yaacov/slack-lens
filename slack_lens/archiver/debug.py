"""Debug and diagnostic helpers for DOM inspection.

These functions are only active when DEBUG logging is enabled
(i.e. when ``--verbose`` is passed).  They produce HTML dumps and
structured log output that help diagnose extraction issues offline.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from slack_lens.models import format_timestamp

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.sync_api import Locator, Page

logger = logging.getLogger(__name__)

_dump_counter = 0


def dump_page_html(
    page: Page, archives_dir: Path | str, label: str, ts: str = "",
) -> None:
    """Save the current page HTML to a file for offline analysis.

    Only writes when DEBUG logging is enabled.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    from pathlib import Path as _P

    global _dump_counter  # noqa: PLW0603
    _dump_counter += 1
    safe_ts = ts.replace(".", "_") if ts else ""
    name = f"dump_{_dump_counter:03d}_{label}"
    if safe_ts:
        name += f"_{safe_ts}"
    dump_dir = _P(archives_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)
    path = dump_dir / f"{name}.html"
    path.write_text(page.content(), encoding="utf-8")
    logger.debug("HTML dump saved: %s", path)


def dump_message_dom(elem: Locator, timestamp: str) -> None:
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


def dump_flexpane_debug(
    page: Page,
    parent_ts: str,
    archives_dir: Path | str,
) -> None:
    """Log flexpane candidates (WARNING) and optionally dump full HTML."""
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

    if logger.isEnabledFor(logging.DEBUG):
        from pathlib import Path as _Path

        dump_dir = _Path(archives_dir)
        dump_dir.mkdir(parents=True, exist_ok=True)
        safe_ts = parent_ts.replace(".", "_")
        dump_path = dump_dir / f"debug_thread_fail_{safe_ts}.html"
        dump_path.write_text(page.content(), encoding="utf-8")
        logger.debug("Full page HTML dumped to %s", dump_path)
