# PVE-Resource-Monitor

A Python telemetry tool for Proxmox VE running on Supermicro hardware. It sends a daily Slack report with real-time host power draw plus per-VM CPU and RAM usage.

## Features
- VM utilization for running QEMU guests
- Instantaneous power draw via IPMI (`ipmitool dcmi power reading`)
- Sensor anomaly detection from IPMI SDR output
- Slack incoming webhook delivery
- Lightweight host-side monitoring using native `pvesh` and `ipmitool`

## Requirements
- Proxmox VE host with `pvesh`
- Python 3
- `ipmitool`
- Python package: `requests`
- Slack Incoming Webhook URL

## Script
Main script: `lab_report.py`

Install Python dependency:

```bash
python3 -m pip install -r requirements.txt
```

### Configuration
Use a local `.env` file (recommended) so secrets are not committed:

```bash
cp .env.example .env
chmod 600 .env
```

Then edit `.env`:

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ
PVE_NODE_NAME=node1
FAIL_ON_CRITICAL_ALERTS=false
```

Notes:
- `SLACK_WEBHOOK_URL` is required.
- `PVE_NODE_NAME` defaults to `node1` if not set.
- `FAIL_ON_CRITICAL_ALERTS=true` makes the script exit non-zero when critical sensor alerts are detected.
- `.env` is ignored by git via `.gitignore`.

### Run
```bash
python3 lab_report.py
```

## Scheduling (cron)
Run every day at 08:00:

```bash
0 8 * * * cd /path/to/PVE-Resource-monitor && /usr/bin/python3 /path/to/PVE-Resource-monitor/lab_report.py
```
