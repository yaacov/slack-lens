# Quick Start Guide

## Setup (One Time)

```bash
cd slack-lens

# Install as a uv tool (adds slack-lens to your PATH)
uv tool install -e .

# Install required browser
slack-lens setup

# Verify installation
slack-lens --version
```

## Usage Examples

### 1. Authenticate with Your Workspace

```bash
# Replace 'my-company' with your actual workspace name
# (e.g., if your Slack URL is acme.slack.com, use 'acme')
slack-lens -w my-company auth
```

This will:
- Open a browser window
- Wait for you to complete SSO login (auto-detects when done)
- Save your session to `~/.slack-lens/slack_auth.json`
- Remember the workspace as default for future commands
- You only need to do this once (until session expires)

### 2. List Available Channels

```bash
slack-lens channels
```

No `-w` needed — it uses the workspace from your last authentication.

### 3. Save a Channel

**Basic (all messages + images):**
```bash
slack-lens archive general
```

**With date range:**
```bash
slack-lens archive engineering --since 2026-01-01
```

**Without files/images:**
```bash
slack-lens archive random --skip-files
```

**Without thread replies:**
```bash
slack-lens archive support --no-threads
```

**Compact text format (smaller footprint):**
```bash
slack-lens archive general --format txt
```

**Both JSON and text:**
```bash
slack-lens archive general --format both
```

**Advanced filtering:**
```bash
slack-lens archive design \
  --since 2026-01-01 \
  --until 2026-03-31 \
  --no-threads \
  --file-pattern "\.pdf$"
```

### 4. Search Saved Content

**Simple search:**
```bash
slack-lens search "bug fix"
```

**Search specific channel:**
```bash
slack-lens search "migration" --channel engineering
```

**Search with filters:**
```bash
slack-lens search "error" \
  --channel backend \
  --user "John Doe" \
  --since 2026-04-01 \
  --with-files
```

**Search for threads only:**
```bash
slack-lens search "discussion" --threads-only
```

### 5. Clean Up

```bash
# Remove all cached data (auth + saved data)
slack-lens clean

# Remove only auth session (forces re-login next time)
slack-lens clean auth

# Remove only saved data
slack-lens clean archives
```

## Verbose Mode

Add `-v` before the subcommand to see detailed diagnostic output (DOM walks, scroll positions, etc.):

```bash
slack-lens -v archive general --since 2026-01-01
```

## Where is Data Stored?

- **Authentication:** `~/.slack-lens/slack_auth.json`
- **Workspace config:** `~/.slack-lens/workspace.json`
- **Saved data:** `./archives/` (in current directory)
- **Downloaded files:** `./archives/{channel_name}/files/`
- **Format:** JSON files named `{channel_name}_{timestamp}.json`

## Troubleshooting

**"Not authenticated" error:**
```bash
slack-lens -w my-company auth --force
```

**"No workspace specified and no default found":**
```bash
# You need to authenticate first
slack-lens -w my-company auth
```

**Channel not found:**
- Make sure you have access to the channel
- Use the channel name without the # prefix
- Check spelling — the tool will suggest close matches

**Slow operation:**
- Large channels take time
- Adjust `SLACK_LENS_PAGE_SCROLL_DELAY` env var (default: 1.5s)

## Configuration

Set environment variables to customize behavior:

```bash
export SLACK_LENS_HEADLESS=false  # Show browser during operations
export SLACK_LENS_ARCHIVES_DIR=~/my-data  # Change save location
```

Or create a `.env` file in the project directory:

```env
SLACK_LENS_HEADLESS=false
SLACK_LENS_ARCHIVES_DIR=/path/to/data
```
