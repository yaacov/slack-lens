"""Command-line interface for Slack Lens."""

from __future__ import annotations

import argparse
import sys

from slack_lens import __version__
from slack_lens.cli.commands import (
    cmd_archive,
    cmd_auth,
    cmd_channels,
    cmd_clean,
    cmd_search,
    cmd_setup,
)
from slack_lens.log import setup_logging


def main(argv: list[str] | None = None) -> None:
    """Main entry point."""
    # Shared parent so -v / -w work both before and after the subcommand
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (debug) logging",
    )
    shared.add_argument(
        "-w",
        "--workspace",
        help="Slack workspace name (e.g. 'my-company' for my-company.slack.com)",
    )

    parser = argparse.ArgumentParser(
        prog="slack-lens",
        description="Browser-based Slack channel archiver for research",
        parents=[shared],
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(dest="command")

    # -- auth ----------------------------------------------------------------
    p_auth = sub.add_parser(
        "auth", help="Authenticate with Slack workspace", parents=[shared],
    )
    p_auth.add_argument(
        "--force",
        action="store_true",
        help="Force re-authentication even if session exists",
    )
    p_auth.set_defaults(func=cmd_auth)

    # -- channels ------------------------------------------------------------
    sub.add_parser(
        "channels", help="List available channels", parents=[shared],
    ).set_defaults(func=cmd_channels)

    # -- archive -------------------------------------------------------------
    p_archive = sub.add_parser(
        "archive", help="Archive a channel", parents=[shared],
    )
    p_archive.add_argument("channel", help="Channel name (without # prefix)")
    p_archive.add_argument(
        "--since", help="Archive messages from this date (YYYY-MM-DD)"
    )
    p_archive.add_argument(
        "--until", help="Archive messages up to this date (YYYY-MM-DD)"
    )
    p_archive.add_argument(
        "--no-threads",
        action="store_true",
        help="Skip thread replies",
    )
    p_archive.add_argument(
        "--skip-files",
        action="store_true",
        help="Skip file downloads",
    )
    p_archive.add_argument(
        "--file-pattern", help="Only download files matching regex pattern"
    )
    p_archive.add_argument(
        "--format",
        choices=["json", "txt", "both"],
        default="json",
        dest="output_format",
        help="Output format: json, txt, or both (default: json)",
    )
    p_archive.set_defaults(func=cmd_archive)

    # -- search --------------------------------------------------------------
    p_search = sub.add_parser(
        "search", help="Search archived content", parents=[shared],
    )
    p_search.add_argument("query", help="Search query (supports regex)")
    p_search.add_argument("--channel", help="Limit search to specific channel")
    p_search.add_argument("--user", help="Filter by message author")
    p_search.add_argument(
        "--since", help="Only search messages after date (YYYY-MM-DD)"
    )
    p_search.add_argument(
        "--until", help="Only search messages before date (YYYY-MM-DD)"
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
    p_search.set_defaults(func=cmd_search)

    # -- clean ---------------------------------------------------------------
    p_clean = sub.add_parser(
        "clean", help="Remove cached auth and/or archived data",
        parents=[shared],
    )
    p_clean.add_argument(
        "target",
        nargs="?",
        default="all",
        choices=["auth", "archives", "all"],
        help="What to clean (default: all)",
    )
    p_clean.set_defaults(func=cmd_clean)

    # -- setup ---------------------------------------------------------------
    sub.add_parser(
        "setup", help="Install required browser (Chromium)",
        parents=[shared],
    ).set_defaults(func=cmd_setup)

    # Parse and dispatch
    args = parser.parse_args(argv)

    setup_logging(verbose=args.verbose)

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
