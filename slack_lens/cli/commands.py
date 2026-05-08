"""CLI command handlers for Slack Lens."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import TYPE_CHECKING

from rich.table import Table

from slack_lens.archiver import ArchiveOptions, ChannelArchiver
from slack_lens.client import SlackClient
from slack_lens.config import Config
from slack_lens.log import console
from slack_lens.search import SearchEngine
from slack_lens.storage import Storage

if TYPE_CHECKING:
    import argparse


def parse_date(value: str, flag_name: str) -> datetime:
    """Parse a YYYY-MM-DD date string, exiting on failure."""
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        console.print(f"[red]Invalid date format for {flag_name}: {value}[/red]")
        console.print("[yellow]Expected format: YYYY-MM-DD[/yellow]")
        sys.exit(1)


def similar(a: str, b: str) -> bool:
    """Check if two strings are similar (character-level distance <= 2)."""
    if abs(len(a) - len(b)) > 2:
        return False
    pairs = zip(a, b, strict=False)
    mismatches = sum(1 for x, y in pairs if x != y)
    mismatches += abs(len(a) - len(b))
    return mismatches <= 2


def resolve_workspace(args: argparse.Namespace, config: Config) -> str:
    """Resolve workspace from args or saved default."""
    workspace = getattr(args, "workspace", None)
    if workspace:
        return workspace
    default = config.get_default_workspace()
    if default:
        return default
    console.print("[red]No workspace specified and no default found.[/red]")
    console.print("[yellow]Run: slack-lens -w <name> auth[/yellow]")
    sys.exit(1)


def cmd_auth(args: argparse.Namespace) -> None:
    """Authenticate with Slack workspace."""
    config = Config()
    workspace = resolve_workspace(args, config)
    client = SlackClient(workspace=workspace, config=config)

    success = client.authenticate(force=args.force)
    if not success:
        sys.exit(1)


def cmd_channels(args: argparse.Namespace) -> None:
    """List available channels."""
    config = Config()
    workspace = resolve_workspace(args, config)
    client = SlackClient(workspace=workspace, config=config)

    try:
        channels = client.list_channels()

        table = Table(title=f"Channels in {workspace}.slack.com")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("ID", style="dim")

        for channel in channels:
            channel_type = "Private" if channel.is_private else "Public"
            table.add_row(f"#{channel.name}", channel_type, channel.id)

        console.print(table)
        console.print(f"\n[green]Total: {len(channels)} channels[/green]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def cmd_archive(args: argparse.Namespace) -> None:
    """Archive a channel."""
    config = Config()
    workspace = resolve_workspace(args, config)
    client = SlackClient(workspace=workspace, config=config)

    try:
        all_channels = client.list_channels()
        channel_name_clean = args.channel.lstrip("#")
        channel = next(
            (ch for ch in all_channels if ch.name == channel_name_clean), None
        )
        if not channel:
            console.print(f"[red]Channel '{args.channel}' not found[/red]")
            suggestions = [
                ch.name
                for ch in all_channels
                if args.channel.lower() in ch.name.lower()
                or ch.name.lower() in args.channel.lower()
                or similar(args.channel.lower(), ch.name.lower())
            ]
            if suggestions:
                console.print(
                    f"[yellow]Did you mean: {', '.join(suggestions)}?[/yellow]"
                )
            sys.exit(1)

        since = parse_date(args.since, "--since") if args.since else None
        until = parse_date(args.until, "--until") if args.until else None

        options = ArchiveOptions(
            since=since,
            until=until,
            include_threads=not args.no_threads,
            include_files=not args.skip_files,
            file_pattern=args.file_pattern,
        )

        archiver = ChannelArchiver(client=client, config=config)
        archiver.archive_channel(channel=channel, options=options)

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def cmd_search(args: argparse.Namespace) -> None:
    """Search archived content."""
    config = Config()
    storage = Storage(config=config)
    search_engine = SearchEngine(storage=storage)

    try:
        since = parse_date(args.since, "--since") if args.since else None
        until = parse_date(args.until, "--until") if args.until else None

        results = search_engine.search_text(
            query=args.query,
            channel_name=args.channel,
            user_name=args.user,
            since=since,
            until=until,
            with_files=args.with_files,
            threads_only=args.threads_only,
        )

        if not results:
            console.print("[yellow]No results found[/yellow]")
            return

        console.print(f"\n[green]Found {len(results)} results:[/green]\n")

        for i, result in enumerate(results, 1):
            console.print(f"[bold cyan]{i}. #{result.channel_name}[/bold cyan]")
            console.print(f"   [dim]User: {result.message.user_name}[/dim]")
            console.print(f"   [dim]Time: {result.message.timestamp}[/dim]")
            console.print(f"   {result.message.text[:200]}...")
            if result.message.files:
                console.print(
                    f"   [yellow]Files: {len(result.message.files)}[/yellow]"
                )
            console.print()

    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def cmd_clean(args: argparse.Namespace) -> None:
    """Clean cached data and/or authentication."""
    import shutil

    config = Config()
    target = getattr(args, "target", "all")

    if target in ("auth", "all"):
        removed = []
        if config.auth_file.exists():
            config.auth_file.unlink()
            removed.append(str(config.auth_file))
        if config._workspace_file.exists():
            config._workspace_file.unlink()
            removed.append(str(config._workspace_file))
        if removed:
            console.print(
                f"[green]Removed auth data: {', '.join(removed)}[/green]"
            )
        else:
            console.print("[yellow]No auth data to clean[/yellow]")

    if target in ("archives", "all"):
        if config.archives_dir.exists() and any(config.archives_dir.iterdir()):
            shutil.rmtree(config.archives_dir)
            config.archives_dir.mkdir(parents=True, exist_ok=True)
            console.print(
                f"[green]Removed archived data from {config.archives_dir}[/green]"
            )
        else:
            console.print("[yellow]No archived data to clean[/yellow]")


def cmd_setup(args: argparse.Namespace) -> None:
    """Install Playwright browser."""
    import subprocess

    console.print("[cyan]Installing Chromium browser for Playwright...[/cyan]")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )
    if result.returncode == 0:
        console.print("[green]Browser installed successfully[/green]")
    else:
        console.print("[red]Browser installation failed[/red]")
        sys.exit(1)
