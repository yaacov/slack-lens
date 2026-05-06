"""Slack client using Playwright browser automation."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright
from rich.console import Console

from slack_lens.config import Config

logger = logging.getLogger(__name__)
console = Console()


def _launch_browser(pw, headless: bool = False):
    """Launch Chromium with a friendly error if not installed."""
    try:
        return pw.chromium.launch(headless=headless)
    except PlaywrightError as e:
        if "Executable doesn't exist" in str(e) or "browserType.launch" in str(e):
            console.print("[red]✗ Chromium browser is not installed.[/red]")
            console.print("[yellow]Run: slack-lens setup[/yellow]")
            raise SystemExit(1) from e
        raise


@dataclass
class Channel:
    """Slack channel information."""

    id: str
    name: str
    is_private: bool
    member_count: int | None = None


class SlackClient:
    """Browser-based Slack client."""

    def __init__(self, workspace: str, config: Config | None = None):
        """Initialize Slack client.

        Args:
            workspace: Slack workspace name (e.g., 'my-company' for my-company.slack.com)
            config: Application configuration
        """
        self.workspace = workspace
        self.config = config or Config()
        self.config.ensure_dirs()
        self.login_url = f"https://{workspace}.slack.com"
        self.app_url = "https://app.slack.com"

    def authenticate(self, force: bool = False) -> bool:
        """Authenticate with Slack via browser.

        Opens a browser window for the user to log in via SSO.
        Saves the session state for future use.

        Args:
            force: Force re-authentication even if session exists

        Returns:
            True if authentication successful
        """
        if not force and self.is_authenticated():
            self.config.save_workspace(self.workspace)
            console.print("[green]Already authenticated. Use --force to re-authenticate.[/green]")
            return True

        console.print(f"[yellow]Opening browser for {self.workspace}.slack.com...[/yellow]")
        console.print("[yellow]Please log in via SSO. The browser will stay open until login is detected.[/yellow]")

        with sync_playwright() as p:
            browser = _launch_browser(p, headless=False)
            context = browser.new_context()
            page = context.new_page()

            try:
                page.goto(self.login_url, timeout=self.config.browser_timeout)

                console.print("\n[bold cyan]Waiting for login to complete (up to 5 minutes)...[/bold cyan]")
                console.print("[dim]The browser will close automatically once you're logged in.[/dim]")

                # Poll all pages in the context for the logged-in state.
                # SSO flows may open new tabs or redirect the original page.
                auth_timeout_ms = 300_000  # 5 minutes
                poll_interval_ms = 2_000
                elapsed = 0
                client_url = None

                # Selectors that indicate Slack is fully loaded after login
                logged_in_selectors = [
                    "[data-qa='channel-sidebar']",
                    ".p-channel_sidebar",
                    "[data-qa='slack_kit_list']",
                    ".p-workspace__primary_view",
                    "[data-qa='message_input']",
                    ".c-message_kit__background",
                ]

                while elapsed < auth_timeout_ms:
                    try:
                        pages = context.pages
                        if not pages:
                            break

                        for pg in pages:
                            url = pg.url
                            if "slack.com" not in url:
                                continue
                            if any(x in url for x in ["/signin", "/ssb/redirect", "/oauth"]):
                                continue

                            # Slack redirects to app.slack.com/client after login
                            if "app.slack.com/client" in url or (
                                self.workspace in url and "/client" in url
                            ):
                                pg.wait_for_timeout(2000)
                                client_url = pg.url
                                break

                            # Try multiple selectors as fallback
                            for selector in logged_in_selectors:
                                try:
                                    pg.wait_for_selector(selector, timeout=1000)
                                    client_url = pg.url
                                    break
                                except Exception:
                                    continue
                            if client_url:
                                break

                        if client_url:
                            break
                    except Exception:
                        # Browser was closed by user
                        break

                    page.wait_for_timeout(poll_interval_ms)
                    elapsed += poll_interval_ms

                if not client_url:
                    console.print("[red]✗ Login was not detected. Please try again.[/red]")
                    console.print(
                        "[yellow]Tip: Make sure you complete the SSO flow and the Slack "
                        "workspace loads with channels visible before the timeout.[/yellow]"
                    )
                    try:
                        browser.close()
                    except Exception:
                        pass
                    return False

                # Save authentication state, workspace, and client URL
                context.storage_state(path=str(self.config.auth_file))
                self.config.save_workspace(self.workspace, client_url=client_url)
                console.print(f"[green]✓ Authentication saved to {self.config.auth_file}[/green]")
                console.print(f"[green]✓ Client URL: {client_url}[/green]")

                browser.close()
                return True

            except Exception as e:
                logger.error(f"Authentication failed: {e}")
                console.print(f"[red]✗ Authentication failed: {e}[/red]")
                try:
                    browser.close()
                except Exception:
                    pass
                return False

    def is_authenticated(self) -> bool:
        """Check if valid authentication session exists.

        Returns:
            True if session file exists
        """
        return self.config.auth_file.exists()

    def list_channels(self) -> list[Channel]:
        """List all available channels in the workspace.

        Returns:
            List of Channel objects

        Raises:
            RuntimeError: If not authenticated
        """
        if not self.is_authenticated():
            raise RuntimeError("Not authenticated. Run 'slack-lens auth' first.")

        console.print("[cyan]Loading channels...[/cyan]")

        with sync_playwright() as p:
            browser = _launch_browser(p, headless=self.config.headless)
            context = browser.new_context(
                storage_state=str(self.config.auth_file)
            )
            page = context.new_page()

            try:
                target_url = self.config.get_client_url() or self.app_url
                page.goto(target_url, timeout=self.config.browser_timeout)

                # Wait for the page to settle — use domcontentloaded, not networkidle
                # (Slack never goes network-idle due to websockets)
                page.wait_for_load_state("domcontentloaded")

                # Wait for any sidebar element to appear
                sidebar_selectors = [
                    "[data-qa='channel-sidebar']",
                    ".p-channel_sidebar",
                    "[data-qa='slack_kit_list']",
                    ".p-channel_sidebar__static_list",
                ]
                sidebar_found = False
                for selector in sidebar_selectors:
                    try:
                        page.wait_for_selector(selector, timeout=30000)
                        sidebar_found = True
                        break
                    except Exception:
                        continue

                if not sidebar_found:
                    console.print("[red]✗ Could not find channel sidebar[/red]")
                    browser.close()
                    return []

                # Let the sidebar items render
                page.wait_for_timeout(3000)

                # Extract channels from sidebar links
                channels = []
                # Slack sidebar items are links with channel info
                items = page.locator(
                    "[data-qa='channel_sidebar_name_button'], "
                    "[data-qa-channel-sidebar-channel-type='channel'], "
                    "a.p-channel_sidebar__channel"
                ).all()

                if not items:
                    # Broader fallback: any clickable sidebar item with a channel name
                    items = page.locator(
                        ".p-channel_sidebar__static_list__item "
                        "button, .p-channel_sidebar__static_list__item a"
                    ).all()

                for elem in items:
                    try:
                        name = (
                            elem.get_attribute("data-qa-channel-sidebar-channel-name")
                            or elem.get_attribute("aria-label")
                            or elem.inner_text().strip()
                        )
                        channel_id = (
                            elem.get_attribute("data-qa-channel-sidebar-channel-id")
                            or ""
                        )
                        if name:
                            name = name.lstrip("#").strip()
                            is_private = elem.get_attribute(
                                "data-qa-channel-sidebar-channel-type"
                            ) == "im"
                            channels.append(
                                Channel(id=channel_id, name=name, is_private=is_private)
                            )
                    except Exception:
                        continue

                browser.close()
                console.print(f"[green]✓ Found {len(channels)} channels[/green]")
                return channels

            except Exception as e:
                logger.error(f"Failed to list channels: {e}")
                console.print(f"[red]✗ Failed to list channels: {e}[/red]")
                browser.close()
                raise

    def get_channel_by_name(self, channel_name: str) -> Channel | None:
        """Get channel information by name.

        Args:
            channel_name: Channel name (without # prefix)

        Returns:
            Channel object or None if not found
        """
        channels = self.list_channels()
        channel_name_clean = channel_name.lstrip("#")

        for channel in channels:
            if channel.name == channel_name_clean:
                return channel

        return None
