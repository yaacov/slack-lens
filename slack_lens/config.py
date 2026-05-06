"""Configuration management for Slack Lens."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Application configuration."""

    model_config = SettingsConfigDict(
        env_prefix="SLACK_LENS_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Paths
    auth_file: Path = Field(
        default=Path.home() / ".slack-lens" / "slack_auth.json",
        description="Path to Slack authentication state file",
    )
    archives_dir: Path = Field(
        default=Path("archives"),
        description="Directory for archived channel data",
    )

    # Browser settings
    headless: bool = Field(
        default=True,
        description="Run browser in headless mode",
    )
    browser_timeout: int = Field(
        default=30000,
        description="Browser timeout in milliseconds",
    )

    # Archival settings
    default_thread_depth: int = Field(
        default=-1,
        description="Default thread depth (-1 for all threads)",
    )
    page_scroll_delay: float = Field(
        default=1.5,
        description="Delay between scrolls when loading messages (seconds)",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum retry attempts for failed operations",
    )

    @property
    def _workspace_file(self) -> Path:
        return self.auth_file.parent / "workspace.json"

    def ensure_dirs(self) -> None:
        """Create necessary directories if they don't exist."""
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.archives_dir.mkdir(parents=True, exist_ok=True)

    def save_workspace(self, workspace: str, client_url: str | None = None) -> None:
        """Save the workspace name and client URL."""
        self.ensure_dirs()
        data = {"workspace": workspace}
        if client_url:
            data["client_url"] = client_url
        self._workspace_file.write_text(json.dumps(data))

    def get_default_workspace(self) -> str | None:
        """Load the saved default workspace name."""
        if self._workspace_file.exists():
            data = json.loads(self._workspace_file.read_text())
            return data.get("workspace")
        return None

    def get_client_url(self) -> str | None:
        """Load the saved post-login client URL."""
        if self._workspace_file.exists():
            data = json.loads(self._workspace_file.read_text())
            return data.get("client_url")
        return None
