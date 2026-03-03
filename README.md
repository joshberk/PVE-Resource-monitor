# PVE-Resource-Monitor

A Python telemetry tool for Proxmox VE running on a **Dell PowerEdge R830**. Sends a daily Slack report with host node stats, VM resource usage, full hardware health, and historical temperature trends — plus proactive **email alerts** for high power draw and daily runtime limits.

## Features

- **Node stats**: host CPU usage, memory, load average, uptime via Proxmox API
- **VM utilization**: CPU % and RAM for all running QEMU guests
- **Power draw**: instantaneous wattage via IPMI DCMI
- **Hardware health snapshot**:
  - Inlet (ambient) and exhaust air temperatures
  - Per-socket CPU die temps (up to 4 CPUs)
  - All fan speeds
  - Dual PSU status
  - CMOS battery voltage
- **7-day inlet temperature trend chart** in every report
- **Threshold-based alerts** — warning and critical levels for inlet, exhaust, CPU temps, fans, and VBAT
- **Separate urgent Slack message** when critical thresholds are breached
- **Sensor anomaly detection** from IPMI SDR output
- **30-day reading history** stored locally in `health_history.json`
- **Built-in cron installers** — no manual crontab editing required
- **Proactive email alerts** (sent at most once per day):
  - **Runtime warning** — email when uptime hits 5h 30m (30 min before the 6-hour daily target)
  - **Power warning** — email when power draw reaches 300 W (before the 376 W limit)

## Requirements

- Proxmox VE host with `pvesh` and `ipmitool`
- Python 3
- Python package: `requests`
- Slack Incoming Webhook URL
- Gmail account with an [app password](https://myaccount.google.com/apppasswords) (for email alerts)

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
INLET_WARN_C=30
INLET_CRIT_C=40
EXHAUST_WARN_C=55
EXHAUST_CRIT_C=70
CPU_WARN_C=75
CPU_CRIT_C=85
FAN_MIN_RPM=1000
VBAT_WARN_V=2.7

# Email alerts (Gmail with an app password)
EMAIL_FROM=your@gmail.com
EMAIL_TO=recipient@email.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your_16char_app_password

# Power and runtime alert thresholds — override if needed
POWER_WARN_W=300
POWER_CRIT_W=376
DAILY_RUNTIME_WARN_H=5.5
DAILY_RUNTIME_LIMIT_H=6.0
```

### Configuration reference

| Variable | Default | Description |
|---|---|---|
| `SLACK_WEBHOOK_URL` | — | **Required.** Must start with `https://hooks.slack.com/` |
| `PVE_NODE_NAME` | `node1` | Proxmox node name — letters, numbers, hyphens, underscores only |
| `FAIL_ON_CRITICAL_ALERTS` | `false` | Exit with code `2` on critical alerts (useful for monitoring pipelines) |
| `INLET_WARN_C` | `30` | Inlet air temp warning threshold (°C) |
| `INLET_CRIT_C` | `40` | Inlet air temp critical threshold (°C) |
| `EXHAUST_WARN_C` | `55` | Exhaust air temp warning threshold (°C) |
| `EXHAUST_CRIT_C` | `70` | Exhaust air temp critical threshold (°C) |
| `CPU_WARN_C` | `75` | CPU die temp warning threshold (°C) |
| `CPU_CRIT_C` | `85` | CPU die temp critical threshold (°C) |
| `FAN_MIN_RPM` | `1000` | Fan speed below which a failure alert fires |
| `VBAT_WARN_V` | `2.7` | CMOS battery voltage below which a warning fires |
| `EMAIL_FROM` | — | Sender email address |
| `EMAIL_TO` | — | Recipient email address |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (STARTTLS) |
| `SMTP_USER` | — | SMTP login username |
| `SMTP_PASSWORD` | — | SMTP password or app password |
| `POWER_WARN_W` | `300` | Power draw (W) that triggers the email warning |
| `POWER_CRIT_W` | `376` | Hard limit reference shown in the warning email |
| `DAILY_RUNTIME_WARN_H` | `5.5` | Uptime (hours) that triggers the runtime warning email |
| `DAILY_RUNTIME_LIMIT_H` | `6.0` | Daily runtime target shown in the warning email |

## Scheduling (cron)

### Daily Slack report

Install the cron job directly from the script — no manual crontab editing needed:

```bash
python3 /path/to/lab_report.py --install-cron
```

This registers a daily 08:00 cron job, writes logs to `lab_report.log`, and creates `/etc/logrotate.d/pve-resource-monitor` (7-day rotation). Safe to run again — will not add duplicates.

### Email alert monitor (every 30 minutes)

The email alerts require a separate cron job that polls every 30 minutes:

```bash
python3 /path/to/lab_report.py --install-monitor-cron
```

This registers a `*/30 * * * *` cron entry that runs `--check-alerts` and logs to `alert_monitor.log`. Each alert fires **at most once per day** — state is tracked in `alert_state.json` (auto-resets at midnight).

To run an alert check manually:

```bash
python3 lab_report.py --check-alerts
```

## Run manually

```bash
python3 lab_report.py
```

## Slack report structure

Each daily report contains:

```
🖥️ Daily Lab Island Report - node1

⚡ Power Draw: 240 Watts

📊 Node Stats:
   • CPU: 12.3%
   • Memory: 45.2GB / 128.0GB (35.3%)
   • Load (1/5/15m): 1.20 / 0.85 / 0.60
   • Uptime: 7d 3h

📦 Active VM Resources:
   • win-dc01: CPU: 0.5% | RAM: 9.98GB

🌡️ Health Snapshot:
   • Inlet: 22 degrees C | Exhaust: 38 degrees C
   • CPU Temps: CPU1 Temp: 52°C | CPU2 Temp: 48°C | CPU3 Temp: 50°C | CPU4 Temp: 49°C
   • Fans: Fan1 RPM: 3480 | Fan2 RPM: 3360 | ...
   • PSU: PS1 Status: Presence detected | PS2 Status: Presence detected
   • VBAT: 3.0 Volts

📈 Trends:
   Inlet Temp — Last 7 Days:
   `02-18`   22°C  ████████░░░░░░░░░░░░
   ...

🚨 Sensor Alerts:
   No sensor anomalies detected.
```

If a critical threshold is breached, a **second urgent message** is sent immediately to the same Slack channel.

## Notes

- Power draw via DCMI (`ipmitool dcmi power reading`) works on the Dell PowerEdge R830.
- `.env` is excluded from git via `.gitignore` to prevent credential exposure.
- History is stored in `health_history.json` next to the script (last 30 days), created with `0600` permissions (owner-read/write only).
- Alert state is stored in `alert_state.json` next to the script. It tracks which email alerts have been sent today and resets automatically at midnight.
- For Gmail, generate a 16-character app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) — do not use your regular Gmail password.
