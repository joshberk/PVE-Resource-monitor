# PVE-Resource-Monitor

A Python telemetry tool for Proxmox VE running on Supermicro hardware. Sends a daily Slack report with VM resource usage, hardware health, historical temperature trends, and threshold-based alerts.

## Features

- VM utilization (CPU %, RAM) for all running QEMU guests
- Instantaneous power draw via IPMI DCMI (with sensor fallback)
- Hardware health snapshot: system temperature, fan speeds, CMOS battery voltage
- 7-day temperature trend chart in every report
- Threshold-based alerts — warning and critical levels for temp, fans, and VBAT
- Separate urgent Slack message when critical thresholds are breached
- Sensor anomaly detection from IPMI SDR output
- 30-day reading history stored locally in `health_history.json`
- Slack incoming webhook delivery
- Built-in cron installer — no manual crontab editing required

## Requirements

- Proxmox VE host with `pvesh` and `ipmitool`
- Python 3
- Python package: `requests`
- Slack Incoming Webhook URL

## Setup

**1. Install Python dependency:**

```bash
python3 -m pip install -r requirements.txt
```

**2. Configure `.env`:**

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` with your values:

```bash
# Required
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ

# Optional — defaults shown
PVE_NODE_NAME=node1
FAIL_ON_CRITICAL_ALERTS=false

# Health alert thresholds — override if needed
TEMP_WARN_C=40
TEMP_CRIT_C=50
FAN_MIN_RPM=500
VBAT_WARN_V=2.7
```

| Variable | Default | Description |
|---|---|---|
| `SLACK_WEBHOOK_URL` | — | **Required.** Must start with `https://hooks.slack.com/` |
| `PVE_NODE_NAME` | `node1` | Proxmox node name — letters, numbers, hyphens, underscores only |
| `FAIL_ON_CRITICAL_ALERTS` | `false` | Exit with code `2` on critical alerts (useful for monitoring pipelines) |
| `TEMP_WARN_C` | `40` | System temp warning threshold (°C) |
| `TEMP_CRIT_C` | `50` | System temp critical threshold (°C) |
| `FAN_MIN_RPM` | `500` | Fan speed below which a failure alert fires |
| `VBAT_WARN_V` | `2.7` | CMOS battery voltage below which a warning fires |

## Scheduling (cron)

Install the cron job directly from the script — no manual crontab editing needed:

```bash
python3 /path/to/lab_report.py --install-cron
```

This registers a daily 08:00 cron job, writes logs to `lab_report.log` in the same directory, and creates `/etc/logrotate.d/pve-resource-monitor` to rotate logs automatically (7-day retention). Running it again is safe — it will not add a duplicate entry.

## Run manually

```bash
python3 lab_report.py
```

## Slack report structure

Each daily report contains:

```
🖥️ Daily Lab Island Report - node1

⚡ Power Draw: ...
📦 Active VM Resources:
   • vm-name: CPU: 0.5% | RAM: 4.20GB

🌡️ Health Snapshot:
   • System Temp: 27 degrees C
   • Fans: FAN 1: 5929 RPM | FAN 2: 5929 RPM
   • VBAT: 2.62 Volts

📈 Trends:
   System Temp — Last 7 Days:
   `02-18`   24°C  ████████░░░░░░░░░░░░
   ...

🚨 Sensor Alerts:
   No sensor anomalies detected.
```

If a critical threshold is breached, a **second urgent message** is sent immediately to the same Slack channel.

## Notes

- Power draw via DCMI (`ipmitool dcmi power reading`) is hardware-dependent. Boards without an inline power meter (e.g. Supermicro H8DGT) will report `N/A`.
- `.env` is excluded from git via `.gitignore` to prevent credential exposure.
- History is stored in `health_history.json` next to the script (last 30 days), created with `0600` permissions (owner-read/write only).
