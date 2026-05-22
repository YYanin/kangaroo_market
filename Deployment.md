# Kangaroo — Proxmox LXC Deployment Guide

This guide covers running Kangaroo continuously inside an unprivileged LXC container on a Proxmox cluster, including the dashboard, scheduler, database backups, and phone access via the home LAN.

---

## Table of Contents

- [Container specs](#container-specs)
- [Create the LXC container](#create-the-lxc-container)
- [Initial container setup](#initial-container-setup)
- [Deploy the application](#deploy-the-application)
- [Configure secrets](#configure-secrets)
- [Systemd services](#systemd-services)
- [Dashboard access on the home LAN](#dashboard-access-on-the-home-lan)
- [Tailscale (off-LAN and phone access)](#tailscale-off-lan-and-phone-access)
- [Database backups](#database-backups)
- [Log management](#log-management)
- [Updating the application](#updating-the-application)
- [Daily workflow](#daily-workflow)
- [Troubleshooting](#troubleshooting)

---

## Container specs

Kangaroo is a lightly loaded Python process. It makes a handful of API calls every 30 minutes and writes small rows to SQLite. It does not do any number-crunching.

| Resource | Minimum | Recommended |
|---|---|---|
| CPU cores | 1 | 2 |
| RAM | 256 MB | 512 MB |
| Swap | 256 MB | 512 MB |
| Root disk | 6 GB | 10 GB |
| OS template | Debian 12 | Debian 12 |
| Network | Bridged (LAN) | Bridged (LAN), static IP |

**Why Debian 12:** Ships Python 3.11, has a minimal footprint, and receives security updates until 2028. Python 3.11 is the minimum version required by the application.

**Why a static IP:** The nginx reverse proxy (for dashboard access from your phone) and any firewall rules are easier to manage when the container IP doesn't change. Assign it in Proxmox or via your router's DHCP reservation.

**Note on Tailscale:** If you want to access the dashboard from outside your home network, Tailscale must be installed inside the container. Tailscale requires access to the `/dev/tun` device — see the [Tailscale section](#tailscale-off-lan-and-phone-access) for the LXC config changes this requires.

---

## Create the LXC container

### 1. Download a Debian 12 template

In the Proxmox web UI: **node → local storage → CT Templates → Templates** and download `debian-12-standard`.

Or from the Proxmox shell:

```bash
pveam update
pveam download local debian-12-standard_12.7-1_amd64.tar.zst
```

### 2. Create the container

In the Proxmox web UI, click **Create CT** and fill in:

| Field | Value |
|---|---|
| CT ID | e.g. `200` |
| Hostname | `kangaroo` |
| Password | Set a strong root password |
| Template | `debian-12-standard` |
| Disk | 10 GB on your preferred storage |
| CPU | 2 cores |
| Memory | 512 MB |
| Swap | 512 MB |
| Network | Bridge: `vmbr0`, IPv4: Static (e.g. `192.168.1.200/24`), Gateway: your router |
| DNS | Your router or `1.1.1.1` |

Leave **Unprivileged container** checked (the default).

Or via the Proxmox shell:

```bash
pct create 200 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname kangaroo \
  --cores 2 \
  --memory 512 \
  --swap 512 \
  --rootfs local-lvm:10 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.1.200/24,gw=192.168.1.1 \
  --nameserver 1.1.1.1 \
  --unprivileged 1 \
  --start 1
```

### 3. Start and enter the container

```bash
pct start 200
pct enter 200
```

---

## Initial container setup

Run all of the following **inside the container**.

### Update and install system dependencies

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git curl nginx logrotate
```

Confirm you have Python 3.11 or later:

```bash
python3 --version   # must be >= 3.11
```

### Create a dedicated user

Running the application as root is unnecessary. Create a restricted user:

```bash
useradd -r -m -s /bin/bash -d /opt/kangaroo kangaroo
```

### Set the system timezone to UTC

Storing logs in UTC keeps them consistent with the database (which also stores timestamps in UTC). The application's market-hours logic uses `zoneinfo` internally and does not depend on the system timezone.

```bash
timedatectl set-timezone UTC
```

---

## Deploy the application

```bash
# Switch to the kangaroo user for all subsequent steps
su - kangaroo
```

### Copy the project into the container

**Option A — from a local machine:**
```bash
# Run this on your local machine, not in the container
rsync -av --exclude='.venv' --exclude='__pycache__' --exclude='*.db' \
  /path/to/retail_bot/ kangaroo@192.168.1.200:/opt/kangaroo/
```

**Option B — from a git remote** (if you push the repo to a private remote):
```bash
# Inside the container, as the kangaroo user
git clone https://your-remote/kangaroo.git /opt/kangaroo
cd /opt/kangaroo
```

### Create the virtual environment and install

```bash
cd /opt/kangaroo
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
```

The `[dev]` extras are included so you can run the test suite from the container if needed. You can use just `.` for a leaner production install.

### Initialize the database

```bash
cd /opt/kangaroo
.venv/bin/python -m kangaroo.db.init
```

This creates `kangaroo.db` in `/opt/kangaroo`. Run it any time you need to rebuild the schema — it is idempotent.

---

## Configure secrets

```bash
cd /opt/kangaroo
cp .env.example .env
chmod 600 .env      # owner-readable only
nano .env           # or your preferred editor
```

Fill in your API keys:

```dotenv
POLYGON_API_KEY=your_polygon_key
FINNHUB_API_KEY=your_finnhub_key

# Pushbullet (default provider):
PUSHBULLET_TOKEN=your_pushbullet_token

# Or Telegram — also set provider: "telegram" in config.yaml:
# TELEGRAM_BOT_TOKEN=your_bot_token
# TELEGRAM_CHAT_ID=your_chat_id

DB_PATH=/opt/kangaroo/kangaroo.db
```

**Never commit `.env`.** It is already in `.gitignore`.

### Smoke test with a manual pipeline run

Before starting the long-running services, confirm your API keys work:

```bash
cd /opt/kangaroo
.venv/bin/python -m kangaroo.jobs.pipeline_run
```

Watch for log output. A successful run will produce either `New alert:` lines or `filtered_out` writes (most tickers will be filtered on any given day). An auth error will appear as an `HTTP 401` or `HTTP 403` in the logs.

---

## Systemd services

Two services need to run continuously: the **scheduler** (pipeline + nightly job) and the **dashboard** (FastAPI/uvicorn). Create them as root.

### Scheduler service

```bash
cat > /etc/systemd/system/kangaroo-scheduler.service << 'EOF'
[Unit]
Description=Kangaroo Scheduler (pipeline + nightly job)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=kangaroo
WorkingDirectory=/opt/kangaroo
EnvironmentFile=/opt/kangaroo/.env
ExecStart=/opt/kangaroo/.venv/bin/python -m kangaroo.scheduler
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=kangaroo-scheduler

[Install]
WantedBy=multi-user.target
EOF
```

### Dashboard service

```bash
cat > /etc/systemd/system/kangaroo-dashboard.service << 'EOF'
[Unit]
Description=Kangaroo Dashboard (FastAPI/uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=kangaroo
WorkingDirectory=/opt/kangaroo
EnvironmentFile=/opt/kangaroo/.env
ExecStart=/opt/kangaroo/.venv/bin/uvicorn kangaroo.dashboard.app:app \
          --host 127.0.0.1 --port 8000 --no-access-log
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=kangaroo-dashboard

[Install]
WantedBy=multi-user.target
EOF
```

### Enable and start both services

```bash
systemctl daemon-reload
systemctl enable kangaroo-scheduler kangaroo-dashboard
systemctl start  kangaroo-scheduler kangaroo-dashboard
```

### Check they are running

```bash
systemctl status kangaroo-scheduler
systemctl status kangaroo-dashboard
journalctl -u kangaroo-scheduler -f    # follow live logs
```

---

## Dashboard access on the home LAN

The dashboard binds to `127.0.0.1:8000` inside the container. To reach it from your phone or laptop on the home network, set up a local nginx reverse proxy. This keeps the dashboard off the public internet while making it reachable at `http://192.168.1.200` (or a local DNS name like `kangaroo.local`).

```bash
cat > /etc/nginx/sites-available/kangaroo << 'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }
}
EOF

ln -s /etc/nginx/sites-available/kangaroo /etc/nginx/sites-enabled/kangaroo
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
systemctl enable nginx
```

Now open `http://192.168.1.200` from any device on your home network.

**Security note:** nginx listens on port 80 of the container's LAN IP — not on the public internet (your router is not forwarding this port). This matches the spirit of AGENTS.md's rule: "never bind to 0.0.0.0 or expose ports publicly."

### Optional: give it a memorable local hostname

On your router (or in `/etc/hosts` on each device), map `kangaroo.local` → `192.168.1.200`. On most home routers this is a DHCP static-lease hostname or a local DNS entry.

---

## Tailscale (off-LAN and phone access)

If you want to access the dashboard from your phone while away from home, install Tailscale inside the container. Tailscale requires `/dev/tun`, which needs a small extra configuration for unprivileged LXC containers.

### 1. Add TUN device access to the container config

On the **Proxmox host** (not inside the container), add the following to the container's config file. Substitute `200` for your container ID:

```bash
# On the Proxmox host
echo "lxc.cgroup2.devices.allow: c 10:200 rwm" >> /etc/pve/lxc/200.conf
echo "lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file" >> /etc/pve/lxc/200.conf

# Restart the container to apply
pct restart 200
```

### 2. Install Tailscale inside the container

```bash
# Inside the container
curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now tailscaled
tailscale up
```

Follow the login URL to authenticate. After connecting, your container gets a stable Tailscale IP (e.g. `100.x.x.x`). The dashboard is now reachable at `http://100.x.x.x` from any device on your Tailnet — your phone, your Mac, wherever.

---

## Database backups

SQLite is a single file. Back it up regularly so you don't lose your alert history.

### Proxmox-level backup (whole container)

In the Proxmox web UI, go to **Datacenter → Backup** and schedule a daily backup of container `200`. This captures the entire container including the database file. Configure 7-day retention.

This is the simplest and most complete option — it also lets you roll back to a previous container state if something goes wrong.

### Application-level database backup (finer granularity)

For a second layer, copy just the database file daily using SQLite's online backup mechanism (safe while the scheduler is writing to it):

```bash
# Create a backup script
cat > /opt/kangaroo/backup_db.sh << 'EOF'
#!/bin/bash
set -euo pipefail
DEST="/opt/kangaroo/backups"
mkdir -p "$DEST"
sqlite3 /opt/kangaroo/kangaroo.db ".backup $DEST/kangaroo_$(date +%Y%m%d).db"
# Keep the last 30 daily backups
find "$DEST" -name "kangaroo_*.db" -mtime +30 -delete
EOF

chmod +x /opt/kangaroo/backup_db.sh
chown kangaroo: /opt/kangaroo/backup_db.sh
```

Schedule it with cron (runs as the kangaroo user):

```bash
crontab -u kangaroo -e
# Add this line:
0 18 * * 1-5  /opt/kangaroo/backup_db.sh
```

This runs at 6pm UTC (1pm ET) on weekdays, one hour after the nightly job has finished.

### Offsite copy (optional)

If your Proxmox host has a NAS or shared storage mounted, add a `cp` to the backup script pointing at that path. For a cloud copy, `rclone` to an S3-compatible bucket is minimal to set up.

---

## Log management

The scheduler and dashboard write to systemd's journal. By default the journal is limited in size, but it is worth confirming log rotation is configured.

### Check journal disk usage

```bash
journalctl --disk-usage
```

### Cap the journal size

Edit `/etc/systemd/journald.conf` and set:

```ini
[Journal]
SystemMaxUse=200M
MaxRetentionSec=30day
```

Then restart journald:

```bash
systemctl restart systemd-journald
```

### Useful log commands

```bash
# Live scheduler output
journalctl -u kangaroo-scheduler -f

# Last 100 lines from the dashboard
journalctl -u kangaroo-dashboard -n 100

# All Kangaroo logs since yesterday
journalctl -u kangaroo-scheduler -u kangaroo-dashboard --since yesterday

# Filter to alerts only
journalctl -u kangaroo-scheduler --since today | grep "alert:"

# Filter to errors only
journalctl -u kangaroo-scheduler -p err --since today
```

---

## Updating the application

### Pull new code and restart

```bash
su - kangaroo
cd /opt/kangaroo
git pull                          # if using git
.venv/bin/pip install -e .        # pick up any new dependencies
exit

systemctl restart kangaroo-scheduler kangaroo-dashboard
```

### After a schema change

If `schema.sql` was modified, the simplest procedure is to re-run `init_db` (which is idempotent for new `CREATE TABLE IF NOT EXISTS` additions). For column additions or data migrations, stop the services first and handle it manually:

```bash
systemctl stop kangaroo-scheduler kangaroo-dashboard
# Make a backup first!
sqlite3 /opt/kangaroo/kangaroo.db ".backup /opt/kangaroo/backups/pre-migration.db"
# Apply the migration
sqlite3 /opt/kangaroo/kangaroo.db < migration.sql
systemctl start kangaroo-scheduler kangaroo-dashboard
```

### After a config.yaml change

Just restart the scheduler — it reloads settings on startup:

```bash
systemctl restart kangaroo-scheduler
```

No dashboard restart is needed unless dashboard-related settings changed.

---

## Daily workflow

### Morning (before open, ~9am ET)

1. Check your Pushbullet or Telegram for overnight `[CLOSED]` notifications. If any ladders closed as `thesis_broken`, review the reason before the day starts.
2. Open the **Ladders** tab: check what's still active, note any next-rung trigger prices you should watch.

### During the trading day (9:30am–4pm ET)

- Alerts arrive as push notifications every 30 minutes (if any ticker passes all filters).
- A `[NEW]` notification means a new candidate. Tap to open the dashboard Today tab and read the headlines.
- A `[RUNG N]` notification means an existing tracked ticker dropped further. Check cumulative drawdown and decide whether you want to add.
- A `[CLOSED]` notification during the day means a blocklist term appeared in new news. Stop — research before considering any action.

### After close (~4:30pm ET)

1. The nightly job runs at 5pm ET. It fills 1d/3d/5d/20d realized-return columns for past alerts.
2. Check the **Performance** tab after a few weeks to see whether the filter thresholds are producing actionable alerts.

### Useful one-liners from the container

```bash
# How many alerts fired today?
sqlite3 /opt/kangaroo/kangaroo.db \
  "SELECT count(*) FROM alerts WHERE timestamp_utc LIKE '$(date -u +%Y-%m-%d)%';"

# What's on active ladders right now?
sqlite3 /opt/kangaroo/kangaroo.db \
  "SELECT ticker, rung_count, last_alert_price, status FROM tracked_tickers WHERE status='active';"

# Which tickers were filtered out today and why?
sqlite3 /opt/kangaroo/kangaroo.db \
  "SELECT ticker, filter_name, filter_reason FROM filtered_out
   WHERE timestamp_utc LIKE '$(date -u +%Y-%m-%d)%'
   ORDER BY filter_name;"

# Run one pipeline cycle manually (e.g. after market open to test)
cd /opt/kangaroo && .venv/bin/python -m kangaroo.jobs.pipeline_run

# Run the test suite to confirm nothing is broken after an update
cd /opt/kangaroo && .venv/bin/pytest -q
```

---

## Troubleshooting

### Scheduler isn't firing alerts

1. **Check the service is running:** `systemctl status kangaroo-scheduler`
2. **Check API keys:** `journalctl -u kangaroo-scheduler --since today | grep -i "401\|403\|error"`
3. **Check market hours:** The scheduler skips runs outside 9:30am–4pm ET, weekdays. Run a manual pipeline to bypass this: `python -m kangaroo.jobs.pipeline_run`
4. **Check filters:** Most tickers are filtered out on most days. Query `filtered_out` to confirm the pipeline is running but no tickers are surviving.

### Dashboard not loading

1. **Dashboard service:** `systemctl status kangaroo-dashboard`
2. **nginx:** `systemctl status nginx` and `nginx -t`
3. **Port conflict:** `ss -tlnp | grep 8000` — something else may have taken port 8000.
4. **From inside the container:** `curl http://127.0.0.1:8000/` should return HTML. If it does, the problem is nginx config.

### Notifications not arriving

1. Verify the provider setting in `config.yaml` matches the keys in `.env`.
2. Check for HTTP errors in the logs: `journalctl -u kangaroo-scheduler | grep -i "pushbullet\|telegram\|notification"`
3. For Telegram, confirm the bot has been started (send `/start` to the bot from your Telegram account before the first message).
4. Notification failures are swallowed — a failed push never stops the pipeline. Check the dashboard Today tab to confirm alerts are being generated even if notifications aren't arriving.

### Container won't start / disk full

```bash
# On the Proxmox host
pct df 200              # check disk usage inside the container
du -sh /opt/kangaroo/   # check app directory size
journalctl --disk-usage # check journal size
```

The database grows slowly (a few KB per day at typical alert volumes). If disk is a concern, increase the container's root disk in Proxmox: **CT → Hardware → Root Disk → Resize**.

### High memory usage

Kangaroo should use under 100 MB of RSS in steady state. If you see high memory:

```bash
# Inside the container
ps aux --sort=-%mem | head -5
```

The most likely culprit is a large `aiohttp` request that didn't complete cleanly. Restarting the scheduler service clears it: `systemctl restart kangaroo-scheduler`.

---

## Security checklist

- [ ] `.env` has permissions `600` and is owned by the `kangaroo` user
- [ ] The container's firewall (or Proxmox network rules) does not forward port 80 or 8000 to the public internet
- [ ] Proxmox backups are scheduled and tested — restore at least once
- [ ] The Proxmox web UI is not accessible from the public internet (use Tailscale or a local-only management network)
- [ ] `DB_PATH` in `.env` points to an absolute path inside the container, not a relative one
- [ ] Logs are confirmed to contain no API key values (run `journalctl -u kangaroo-scheduler | grep -i "token\|key\|secret"` — should be empty)
