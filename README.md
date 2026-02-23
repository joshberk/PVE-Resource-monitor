# PVE-Resource-Monitor

A Python-based telemetry tool for Proxmox Virtual Environments running on Supermicro hardware. This script provides daily resource utilization reports and real-time hardware power consumption stats via Slack.

## 🚀 Features
- **VM Utilization**: Tracks CPU and RAM usage for all active virtual machines.
- **Hardware Telemetry**: Pulls real-time power draw (Watts) directly from Supermicro BMC via IPMI.
- **Slack Integration**: Formats data into a clean, scannable report delivered to a dedicated Slack channel.
- **Lightweight**: Uses native Proxmox `pvesh` and `ipmitool` to avoid heavy API overhead.

## 🛠️ Requirements
- Proxmox VE 8.x+
- Python 3.x
- `ipmitool` installed on host
- A Slack Incoming Webhook URL

## 📈 Monitoring Logic
The script executes the following logic flow:
1. Queries the Proxmox API for the state of all QEMU guests.
2. Interfaces with the Supermicro SDR (Sensor Data Repository) for instantaneous wattage.
3. Calculates mean resource consumption across the node.
4. Pushes an automated payload to the Slack Webhook.



## 📅 Scheduling
To run this automatically every morning at 08:00, add the following to your crontab:
```bash
0 8 * * * /usr/bin/python3 /path/to/lab_report.py
