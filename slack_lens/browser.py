"""Browser launch utility for Playwright-based Slack automation."""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError

from slack_lens.log import console


def launch_browser(pw, headless: bool = False):
    """Launch Chromium with a friendly error if not installed."""
    try:
        return pw.chromium.launch(headless=headless)
    except PlaywrightError as e:
        if "Executable doesn't exist" in str(e) or "browserType.launch" in str(e):
            console.print("[red]Chromium browser is not installed.[/red]")
            console.print("[yellow]Run: slack-lens setup[/yellow]")
            raise SystemExit(1) from e
        raise
