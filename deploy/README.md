# Nexus Scheduler Examples

Copy and adapt these files for your environment. Paths assume a
`pip install -e .` editable install; adjust `ExecStart`/command
paths for production virtualenv installs.

All Nexus processes for one runtime must use the same absolute, local-disk
`NEXUS_DATA_DIR` and the same OS user. Before installing a scheduler, create
private data and log directories. For the systemd example:

```bash
sudo install -d -m 0700 -o nexus -g nexus /var/lib/nexus
sudo install -d -m 0750 -o nexus -g nexus /var/log/nexus
```

For launchd, create and restrict `~/Library/Application Support/Nexus` with
`mkdir -p` followed by `chmod 700`, create `~/Library/Logs/Nexus`, then replace
`/Users/you` in the template. Do not use NFS, SMB, iCloud Drive, Dropbox, or a
directory shared by multiple users.

## Schedules

| Job | Frequency | When | What |
|-----|-----------|------|------|
| Sync | Daily | 06:00 | Single-flight source sync + aggregation + briefing |
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

The historical `nexus-pipeline.*` filenames are kept for compatibility, but
the scheduled command is `nexus sync`. Browser-based archive sync remains an
explicit opt-in and is not enabled by these examples.

## Launchd (macOS)

```bash
cp deploy/com.nexus.pipeline.plist ~/Library/LaunchAgents/
cp deploy/com.nexus.backup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.nexus.pipeline.plist
launchctl load ~/Library/LaunchAgents/com.nexus.backup.plist
```
