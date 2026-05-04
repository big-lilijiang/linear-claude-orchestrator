---
name: linear-claude-orchestrator
description: Setup the Linear + Claude Code orchestrator. One-time setup wizard that configures automatic issue processing.
---

# Linear + Claude Code Orchestrator

This skill sets up a background service that automatically processes Linear issues with Claude Code. Runs once for setup, then the OS scheduler handles the rest.

## Setup

Run through these steps with the user. Detect the OS first (`uname -s` or `echo %OS%`).

### Step 1: Gather configuration

Ask the user for:

1. **Linear API key** — Get from Linear → Settings → Security & access → Personal API keys
2. **Linear project slug** — Right-click project → Copy URL, the last part is the slug

### Step 2: Create the installation directory

```bash
mkdir -p ~/.lco
```

### Step 3: Download the scripts

Download `dispatcher.py` and `post_result.py` from the GitHub repo:

```bash
curl -o ~/.lco/dispatcher.py https://raw.githubusercontent.com/big-lilijiang/linear-claude-orchestrator/main/dispatcher.py
curl -o ~/.lco/post_result.py https://raw.githubusercontent.com/big-lilijiang/linear-claude-orchestrator/main/post_result.py
```

Or if the repo is already cloned:

```bash
cp dispatcher.py post_result.py ~/.lco/
```

### Step 4: Configure

Write the env config to `~/.lco/env`:

```
LCO_LINEAR_API_KEY=<user's key>
LCO_LINEAR_PROJECT_SLUG=<user's project slug>
LCO_WORKSPACE_ROOT=~/.lco_workspaces
LCO_MAX_CONCURRENT=5
LCO_LANGUAGE=zh
```

### Step 5: Install OS scheduler

#### macOS (launchd)

Write `~/Library/LaunchAgents/com.linear.claude-dispatcher.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.linear.claude-dispatcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>source ~/.lco/env && python3 ~/.lco/dispatcher.py</string>
    </array>
    <key>StartInterval</key>
    <integer>15</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/lco-dispatcher.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/lco-dispatcher.err</string>
</dict>
</plist>
```

Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.linear.claude-dispatcher.plist
```

#### Linux (systemd timer)

Write `~/.config/systemd/user/lco-dispatcher.service`:

```ini
[Unit]
Description=LCO Dispatcher

[Service]
Type=oneshot
EnvironmentFile=%h/.lco/env
ExecStart=python3 %h/.lco/dispatcher.py
```

Write `~/.config/systemd/user/lco-dispatcher.timer`:

```ini
[Unit]
Description=LCO Dispatcher Timer

[Timer]
OnUnitActiveSec=15s
AccuracySec=1s

[Install]
WantedBy=timers.target
```

Enable and start:
```bash
systemctl --user daemon-reload
systemctl --user enable --now lco-dispatcher.timer
```

#### Windows (Task Scheduler)

```bash
schtasks /create /tn "LCO Dispatcher" /tr "cmd /c set /p LCO_LINEAR_API_KEY=< ~/.lco/env && python3 ~/.lco/dispatcher.py" /sc minute /mo 1 /f
```

### Step 6: Verify

```bash
python3 ~/.lco/dispatcher.py
```

Should print either "no issues" or dispatch messages. The scheduler will call this every 15 seconds.

### Step 7: Tell the user

"The orchestrator is installed. Create issues in your Linear project, drag them to Todo, and Claude Code will automatically pick them up within 15 seconds. Results will be posted as comments with 🤖 Claude Code prefix."

"To check logs: `tail -f /tmp/lco-dispatcher.log` (macOS) or `journalctl --user -u lco-dispatcher -f` (Linux)."

"To stop: `launchctl unload ~/Library/LaunchAgents/com.linear.claude-dispatcher.plist` (macOS) or `systemctl --user stop lco-dispatcher.timer` (Linux) or `schtasks /delete /tn "LCO Dispatcher" /f` (Windows)."
