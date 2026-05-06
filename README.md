# Slack Lens

Browser-based Slack channel viewer for research, with SSO support.

## Installation

```bash
cd slack-lens

# Install as a uv tool (adds slack-lens to your PATH)
uv tool install -e .

# Install required browser
slack-lens setup
```

Alternatively, for development:

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
slack-lens setup
```

## Quick Start

### 1. Authenticate

```bash
slack-lens auth --workspace your-workspace
```

This opens a browser window where you can log in via SSO. The session and workspace are saved for future use — you won't need to specify `--workspace` again.

### 2. List Channels

```bash
slack-lens list
```

### 3. View/Save a Channel

```bash
# Save all messages from #general (including images)
slack-lens archive general

# Save with filters
slack-lens archive engineering --since 2026-01-01 --thread-depth 2 --no-files
```

### 4. Search Saved Content

```bash
slack-lens search "migration bug" --channel engineering --since 2026-04-01
```

### 5. Clean Cached Data

```bash
# Remove everything (auth + saved data)
slack-lens clean

# Remove only auth/session data
slack-lens clean --auth

# Remove only saved channel data
slack-lens clean --archives
```

## Commands

### `auth`

Authenticate with Slack workspace via browser.

```bash
slack-lens auth --workspace <workspace-name>
```

Options:
- `--workspace` - Slack workspace name (e.g., `my-company` for `my-company.slack.com`)
- `--force` - Force re-authentication even if session exists

After first authentication, the workspace becomes the default for all commands.

### `list`

List available channels in the workspace.

```bash
slack-lens list
```

Options:
- `--workspace` - Override workspace (default: last authenticated workspace)

### `archive`

Save a specific channel's messages locally.

```bash
slack-lens archive <channel-name> [options]
```

Options:
- `--workspace` - Override workspace (default: last authenticated workspace)
- `--since YYYY-MM-DD` - Messages from this date forward
- `--until YYYY-MM-DD` - Messages up to this date
- `--thread-depth N` - How deep to expand threads (0=no threads, -1=all, default: -1)
- `--no-files` - Skip file/image downloads
- `--file-pattern REGEX` - Only download files matching pattern

Images and files are downloaded to `archives/<channel-name>/files/`.

### `search`

Search saved content.

```bash
slack-lens search <query> [options]
```

Options:
- `--channel NAME` - Limit search to specific channel
- `--user NAME` - Filter by message author
- `--since YYYY-MM-DD` - Only search messages after date
- `--until YYYY-MM-DD` - Only search messages before date
- `--with-files` - Only show messages with attachments
- `--threads-only` - Only show messages with replies

### `clean`

Remove cached authentication and/or saved data.

```bash
slack-lens clean [options]
```

Options:
- `--auth` - Remove only authentication/session data
- `--archives` - Remove only saved channel data
- `--all` - Remove both (default if no flag given)

## Configuration

Configuration can be set via environment variables (prefix: `SLACK_LENS_`) or a `.env` file.

Available settings:
- `AUTH_FILE` - Path to authentication file (default: `~/.slack-lens/slack_auth.json`)
- `ARCHIVES_DIR` - Directory for saved data (default: `./archives`)
- `HEADLESS` - Run browser headless (default: `true`, except for auth)
- `DEFAULT_THREAD_DEPTH` - Default thread expansion depth (default: `-1`)

## Data Format

Channel data is stored as JSON files in the `archives/` directory, one file per channel. Images and files are downloaded to `archives/<channel>/files/`.

```json
{
  "channel_id": "C123456",
  "channel_name": "general",
  "archived_at": "2026-05-06T10:00:00Z",
  "workspace": "my-company",
  "messages": [
    {
      "id": "msg-123",
      "timestamp": "2026-05-06T09:30:00Z",
      "user": "U456",
      "user_name": "John Doe",
      "text": "Message content",
      "thread_ts": null,
      "replies": [],
      "files": [
        {
          "name": "screenshot.png",
          "url": "https://files.slack.com/...",
          "size": 45000,
          "mimetype": "image",
          "local_path": "archives/general/files/screenshot.png"
        }
      ],
      "reactions": [],
      "edited": false
    }
  ]
}
```

## Disclaimer — Personal Research Use Only

This tool is intended **solely for personal research and educational purposes**. It is **not** designed or intended for:

- Bulk data extraction or mass-downloading of Slack content
- Circumventing Slack's access controls, rate limits, or security measures
- Backing up workspaces or channels in violation of your organization's policies
- Competing with or replicating Slack's commercial features (e.g., Slack Enterprise Export)
- Any activity that violates [Slack's Terms of Service](https://slack.com/terms-of-service) or [Acceptable Use Policy](https://slack.com/acceptable-use-policy)

If you need official data export capabilities, use Slack's built-in [Export tools](https://slack.com/help/articles/201658943-Export-your-workspace-data) or their [Discovery API](https://api.slack.com/admins/discovery).

## License

Apache License 2.0
