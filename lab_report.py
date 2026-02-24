#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple

import requests


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv(Path(__file__).with_name(".env"))

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
NODE_NAME = os.getenv("PVE_NODE_NAME", "node1").strip()
FAIL_ON_CRITICAL_ALERTS = os.getenv("FAIL_ON_CRITICAL_ALERTS", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def run_command(command: str) -> str:
    result = subprocess.run(
        command,
        shell=True,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def get_power_usage() -> str:
    dcmi_cmd = "ipmitool -I open dcmi power reading"
    try:
        output = run_command(dcmi_cmd)
        for line in output.splitlines():
            if "Instantaneous power reading" in line and ":" in line:
                reading = line.split(":", 1)[1].strip()
                if reading:
                    return reading
        if output:
            return "N/A (DCMI supported, but no instantaneous reading returned)"
    except subprocess.CalledProcessError:
        pass

    # Some platforms do not support DCMI power reading; attempt sensor fallback.
    try:
        sensor_output = run_command("ipmitool -I open sensor")
        for line in sensor_output.splitlines():
            lowered = line.lower()
            if "watt" not in lowered and "power" not in lowered:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                continue
            value = parts[1]
            if value and value.lower() not in {"na", "n/a"}:
                if "watt" in value.lower():
                    return value
                return f"{value} Watts"
    except subprocess.CalledProcessError:
        return "N/A (Check IPMI connection)"

    return "N/A (DCMI unsupported and no power sensor found)"


def get_vm_stats() -> str:
    try:
        cmd = f"pvesh get /nodes/{NODE_NAME}/qemu --output-format json"
        output = run_command(cmd)
        vms = json.loads(output)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return "Unable to read VM stats (check pvesh/node name)."

    report_lines = []
    for vm in vms:
        if vm.get("status") == "running":
            cpu = round(float(vm.get("cpu", 0)) * 100, 1)
            ram = round(float(vm.get("mem", 0)) / 1024**3, 2)
            name = vm.get("name") or f"vm-{vm.get('vmid', 'unknown')}"
            report_lines.append(f"• *{name}*: CPU: {cpu}% | RAM: {ram}GB")

    return "\n".join(report_lines) if report_lines else "No VMs running."


def get_sensor_alerts() -> Tuple[str, bool]:
    try:
        output = run_command("ipmitool -I open sdr list")
    except subprocess.CalledProcessError:
        return "Unable to read sensors (check IPMI connection).", False

    alerts = []
    critical_detected = False
    ok_statuses = {"ok", "ns", "na", "disabled"}

    for line in output.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue

        name, reading, status = parts[0], parts[1], parts[2]
        status_lower = status.lower()
        reading_lower = reading.lower()
        name_lower = name.lower()

        if status_lower in ok_statuses:
            continue

        alerts.append(f"• *{name}*: {status} ({reading})")

        status_tokens = (
            status_lower.replace(",", " ").replace("/", " ").replace("|", " ").split()
        )
        critical_status_tokens = {"cr", "nr", "lnr", "unr", "lcr", "ucr"}
        is_critical = (
            any(token in status_tokens for token in critical_status_tokens)
            or ("critical" in status_lower and "non-critical" not in status_lower)
            or "failure" in reading_lower
            or ("ps" in name_lower and "fail" in reading_lower)
        )
        if is_critical:
            critical_detected = True

    if not alerts:
        return "No sensor anomalies detected.", False

    max_alerts = 10
    displayed_alerts = alerts[:max_alerts]
    remaining = len(alerts) - len(displayed_alerts)
    if remaining > 0:
        displayed_alerts.append(f"• ... and {remaining} more alert(s)")

    return "\n".join(displayed_alerts), critical_detected


def main() -> int:
    if not SLACK_WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL is not set.", file=sys.stderr)
        return 1

    power = get_power_usage()
    vm_report = get_vm_stats()
    sensor_report, critical_alert_detected = get_sensor_alerts()

    payload = {
        "text": (
            f"🖥️ *Daily Lab Island Report - {NODE_NAME}*\n\n"
            f"⚡ *Power Draw*: {power}\n"
            f"📦 *Active VM Resources*:\n{vm_report}\n\n"
            f"🚨 *Sensor Alerts*:\n{sensor_report}"
        )
    }

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Failed to post to Slack: {exc}", file=sys.stderr)
        return 1

    if critical_alert_detected and FAIL_ON_CRITICAL_ALERTS:
        print("Critical sensor alert detected.", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
