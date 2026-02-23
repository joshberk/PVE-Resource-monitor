#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

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
    try:
        # Supermicro specific IPMI command for real-time power.
        cmd = "ipmitool dcmi power reading | grep 'Instantaneous' | awk '{print $4}'"
        watts = run_command(cmd)
        return f"{watts} Watts" if watts else "N/A (No reading returned)"
    except subprocess.CalledProcessError:
        return "N/A (Check IPMI connection)"


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


def main() -> int:
    if not SLACK_WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL is not set.", file=sys.stderr)
        return 1

    power = get_power_usage()
    vm_report = get_vm_stats()

    payload = {
        "text": (
            f"🖥️ *Daily Lab Island Report - {NODE_NAME}*\n\n"
            f"⚡ *Power Draw*: {power}\n"
            f"📦 *Active VM Resources*:\n{vm_report}"
        )
    }

    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Failed to post to Slack: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
