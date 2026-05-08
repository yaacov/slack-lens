"""File attachment discovery and download helpers."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from slack_lens.models import ArchiveOptions, FileAttachment

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)


def extract_files(
    message_elem,
    page: Page,
    options: ArchiveOptions,
    channel_name: str,
    archives_dir: Path,
) -> list[FileAttachment]:
    """Extract file attachments and images from a message element."""
    files: list[FileAttachment] = []
    seen_urls: set[str] = set()

    # 1. Slack-kit file attachments
    for file_elem in message_elem.locator("[data-qa='slack_kit_attachment']").all():
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

    # 2. Inline / uploaded images
    img_selectors = [
        "[data-qa='message_file_image_thumbnail'] img",
        "[data-qa='file_image_thumbnail'] img",
        ".c-file__image img",
        ".p-file_image_thumbnail__image",
        "img[data-qa='image']",
        ".c-message_kit__file img",
    ]
    for selector in img_selectors:
        for img in message_elem.locator(selector).all():
            try:
                url = img.get_attribute("src") or ""
                if not url or url in seen_urls:
                    continue
                if "emoji" in url or "avatar" in url:
                    continue
                seen_urls.add(url)
                parsed = urlparse(url)
                name = Path(parsed.path).name or "image"
                files.append(FileAttachment(name=name, url=url, mimetype="image"))
            except Exception:
                continue

    # 3. Generic download links
    for link in message_elem.locator(
        "a[data-qa='file_download_button'], a[download]"
    ).all():
        try:
            url = link.get_attribute("href") or ""
            name = link.get_attribute("download") or link.inner_text() or "file"
            if url and url not in seen_urls:
                seen_urls.add(url)
                files.append(FileAttachment(name=name, url=url))
        except Exception:
            continue

    if options.file_pattern:
        files = [f for f in files if re.search(options.file_pattern, f.name)]

    if files:
        files = download_files(files, page, channel_name, archives_dir)

    return files


def download_files(
    files: list[FileAttachment],
    page: Page,
    channel_name: str,
    archives_dir: Path,
) -> list[FileAttachment]:
    """Download file attachments to local storage using the page's auth cookies."""
    download_dir = archives_dir / channel_name / "files"
    download_dir.mkdir(parents=True, exist_ok=True)

    for file_info in files:
        try:
            url = file_info.url
            if not url or not url.startswith("http"):
                continue

            response = page.request.get(url)
            if response.status != 200:
                logger.warning(
                    "Failed to download %s: HTTP %s", file_info.name, response.status
                )
                continue

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
            logger.debug("Downloaded: %s -> %s", file_info.name, local_path)

        except Exception as e:
            logger.warning("Failed to download %s: %s", file_info.name, e)
            continue

    return files
