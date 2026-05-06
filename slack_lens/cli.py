"""Command-line interface for Slack Lens."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from rich.console import Console
from rich.table import Table

from slack_lens import __version__
from slack_lens.archiver import ArchiveOptions, ChannelArchiver
from slack_lens.config import Config
from slack_lens.search import SearchEngine
from slack_lens.slack_client import SlackClient
from slack_lens.storage import Storage

console = Console()


def _similar(a: str, b: str) -> bool:
    """Check if two strings are similar (simple Levenshtein distance <= 2)."""
    if abs(len(a) - len(b)) > 2:
        return False
    # Simple character-level comparison
    mismatches = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
    return mismatches <= 2


def _resolve_workspace(args: argparse.Namespace, config: Config) -> str:
    """Resolve workspace from args or saved default."""
    workspace = getattr(args, "workspace", None)
    if workspace:
        return workspace
    default = config.get_default_workspace()
    if default:
        return default
    console.print("[red]No workspace specified and no default found.[/red]")
    console.print("[yellow]Run: slack-lens auth --workspace <name>[/yellow]")
    sys.exit(1)


def _cmd_auth(args: argparse.Namespace) -> None:
    """Authenticate with Slack workspace."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    config = Config()
    workspace = _resolve_workspace(args, config)
    client = SlackClient(workspace=workspace, config=config)

    success = client.authenticate(force=args.force)
    if not success:
        sys.exit(1)


def _cmd_list(args: argparse.Namespace) -> None:
    """List available channels."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    config = Config()
    workspace = _resolve_workspace(args, config)
    client = SlackClient(workspace=workspace, config=config)

    try:
        channels = client.list_channels()

        # Display in a table
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


def _cmd_archive(args: argparse.Namespace) -> None:
    """Archive a channel."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    config = Config()
    workspace = _resolve_workspace(args, config)
    client = SlackClient(workspace=workspace, config=config)

    try:
        # Get channel (list_channels is called once inside get_channel_by_name)
        all_channels = client.list_channels()
        channel_name_clean = args.channel.lstrip("#")
        channel = next((ch for ch in all_channels if ch.name == channel_name_clean), None)
        if not channel:
            console.print(f"[red]Channel '{args.channel}' not found[/red]")
            suggestions = [
                ch.name for ch in all_channels
                if args.channel.lower() in ch.name.lower()
                or ch.name.lower() in args.channel.lower()
                or _similar(args.channel.lower(), ch.name.lower())
            ]
            if suggestions:
                console.print(f"[yellow]Did you mean: {', '.join(suggestions)}?[/yellow]")
            sys.exit(1)

        # Parse date options
        since = None
        until = None
        if args.since:
            try:
                since = datetime.strptime(args.since, "%Y-%m-%d")
            except ValueError:
                console.print(f"[red]Invalid date format for --since: {args.since}[/red]")
                console.print("[yellow]Expected format: YYYY-MM-DD[/yellow]")
                sys.exit(1)

        if args.until:
            try:
                until = datetime.strptime(args.until, "%Y-%m-%d")
            except ValueError:
                console.print(f"[red]Invalid date format for --until: {args.until}[/red]")
                console.print("[yellow]Expected format: YYYY-MM-DD[/yellow]")
                sys.exit(1)

        # Create archive options
        options = ArchiveOptions(
            since=since,
            until=until,
            thread_depth=args.thread_depth,
            include_files=not args.no_files,
            file_pattern=args.file_pattern,
        )

        # Archive channel
        archiver = ChannelArchiver(client=client, config=config)
        archiver.archive_channel(channel=channel, options=options)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def _cmd_search(args: argparse.Namespace) -> None:
    """Search archived content."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    config = Config()
    storage = Storage(config=config)
    search_engine = SearchEngine(storage=storage)

    try:
        # Parse date options
        since = None
        until = None
        if args.since:
            try:
                since = datetime.strptime(args.since, "%Y-%m-%d")
            except ValueError:
                console.print(f"[red]Invalid date format for --since: {args.since}[/red]")
                sys.exit(1)

        if args.until:
            try:
                until = datetime.strptime(args.until, "%Y-%m-%d")
            except ValueError:
                console.print(f"[red]Invalid date format for --until: {args.until}[/red]")
                sys.exit(1)

        # Search
        results = search_engine.search_text(
            query=args.query,
            channel_name=args.channel,
            user_name=args.user,
            since=since,
            until=until,
            with_files=args.with_files,
            threads_only=args.threads_only,
        )

        # Display results
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
                console.print(f"   [yellow]Files: {len(result.message.files)}[/yellow]")
            console.print()

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def _cmd_clean(args: argparse.Namespace) -> None:
    """Clean cached data and/or authentication."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    config = Config()
    clean_all = args.all or (not args.auth and not args.archives)

    if args.auth or clean_all:
        removed = []
        if config.auth_file.exists():
            config.auth_file.unlink()
            removed.append(str(config.auth_file))
        if config._workspace_file.exists():
            config._workspace_file.unlink()
            removed.append(str(config._workspace_file))
        if removed:
            console.print(f"[green]✓ Removed auth data: {', '.join(removed)}[/green]")
        else:
            console.print("[yellow]No auth data to clean[/yellow]")

    if args.archives or clean_all:
        import shutil

        if config.archives_dir.exists() and any(config.archives_dir.iterdir()):
            shutil.rmtree(config.archives_dir)
            config.archives_dir.mkdir(parents=True, exist_ok=True)
            console.print(f"[green]✓ Removed archived data from {config.archives_dir}[/green]")
        else:
            console.print("[yellow]No archived data to clean[/yellow]")


def _cmd_setup(args: argparse.Namespace) -> None:
    """Install Playwright browser."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    import subprocess

    console.print("[cyan]Installing Chromium browser for Playwright...[/cyan]")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )
    if result.returncode == 0:
        console.print("[green]✓ Browser installed successfully[/green]")
    else:
        console.print("[red]✗ Browser installation failed[/red]")
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="slack-lens",
        description="Browser-based Slack channel viewer for research",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(dest="command")

    # -- auth ----------------------------------------------------------------
    p_auth = sub.add_parser(
        "auth",
        help="Authenticate with Slack workspace",
    )
    p_auth.add_argument(
        "--workspace",
        help="Slack workspace name (e.g., 'my-company' for my-company.slack.com)",
    )
    p_auth.add_argument(
        "--force",
        action="store_true",
        help="Force re-authentication even if session exists",
    )
    p_auth.set_defaults(func=_cmd_auth)

    # -- list ----------------------------------------------------------------
    p_list = sub.add_parser(
        "list",
        help="List available channels",
    )
    p_list.add_argument(
        "--workspace",
        help="Slack workspace name (default: last authenticated workspace)",
    )
    p_list.set_defaults(func=_cmd_list)

    # -- archive -------------------------------------------------------------
    p_archive = sub.add_parser(
        "archive",
        help="Archive a channel",
    )
    p_archive.add_argument(
        "channel",
        help="Channel name (without # prefix)",
    )
    p_archive.add_argument(
        "--workspace",
        help="Slack workspace name (default: last authenticated workspace)",
    )
    p_archive.add_argument(
        "--since",
        help="Archive messages from this date (YYYY-MM-DD)",
    )
    p_archive.add_argument(
        "--until",
        help="Archive messages up to this date (YYYY-MM-DD)",
    )
    p_archive.add_argument(
        "--thread-depth",
        type=int,
        default=-1,
        help="Thread expansion depth (0=no threads, -1=all, default: -1)",
    )
    p_archive.add_argument(
        "--no-files",
        action="store_true",
        help="Skip file downloads",
    )
    p_archive.add_argument(
        "--file-pattern",
        help="Only download files matching regex pattern",
    )
    p_archive.set_defaults(func=_cmd_archive)

    # -- search --------------------------------------------------------------
    p_search = sub.add_parser(
        "search",
        help="Search archived content",
    )
    p_search.add_argument(
        "query",
        help="Search query (supports regex)",
    )
    p_search.add_argument(
        "--channel",
        help="Limit search to specific channel",
    )
    p_search.add_argument(
        "--user",
        help="Filter by message author",
    )
    p_search.add_argument(
        "--since",
        help="Only search messages after date (YYYY-MM-DD)",
    )
    p_search.add_argument(
        "--until",
        help="Only search messages before date (YYYY-MM-DD)",
    )
    p_search.add_argument(
        "--with-files",
        action="store_true",
        help="Only show messages with attachments",
    )
    p_search.add_argument(
        "--threads-only",
        action="store_true",
        help="Only show messages with replies",
    )
    p_search.set_defaults(func=_cmd_search)

    # -- clean ---------------------------------------------------------------
    p_clean = sub.add_parser(
        "clean",
        help="Remove cached auth and/or archived data",
    )
    p_clean.add_argument(
        "--auth",
        action="store_true",
        help="Remove only authentication/session data",
    )
    p_clean.add_argument(
        "--archives",
        action="store_true",
        help="Remove only archived channel data",
    )
    p_clean.add_argument(
        "--all",
        action="store_true",
        help="Remove both auth and archives (default if no flag given)",
    )
    p_clean.set_defaults(func=_cmd_clean)

    # -- setup ---------------------------------------------------------------
    p_setup = sub.add_parser(
        "setup",
        help="Install required browser (Chromium)",
    )
    p_setup.set_defaults(func=_cmd_setup)

    # Parse and dispatch
    args = parser.parse_args(argv)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
