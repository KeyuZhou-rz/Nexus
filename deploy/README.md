# Nexus Scheduler Examples

Copy and adapt these files for your environment. Paths assume a
`pip install -e .` editable install; adjust `ExecStart`/command
paths for production virtualenv installs.

## Schedules

| Job | Frequency | When | What |
|-----|-----------|------|------|
| Pipeline | Daily | 06:00 | Aggregation + briefing |
| Session finalize | Hourly | — | Clean up expired sessions |
| Backup | Weekly | Sun 03:00 | Full database + Chroma backup |

## Cron (Linux/macOS)

```bash
# Install cron jobs (edit paths first)
crontab deploy/nexus-pipeline.cron
crontab deploy/nexus-backup.cron
crontab deploy/nexus-session-finalize.cron
```

## Systemd (Linux)

```bash
sudo cp deploy/nexus-pipeline.service deploy/nexus-pipeline.timer /etc/systemd/system/
sudo cp deploy/nexus-backup.service deploy/nexus-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nexus-pipeline.timer nexus-backup.timer
```

## Launchd (macOS)

```bash
cp deploy/com.nexus.pipeline.plist ~/Library/LaunchAgents/
cp deploy/com.nexus.backup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.nexus.pipeline.plist
launchctl load ~/Library/LaunchAgents/com.nexus.backup.plist
```
