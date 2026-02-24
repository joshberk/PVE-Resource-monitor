#!/usr/bin/env python3
import datetime
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Tuple

import requests


def _safe_float(s: str) -> "float | None":
    """Parse a float from an IPMI string, rejecting NaN and Infinity."""
    try:
        val = float(s.split()[0])
        return val if math.isfinite(val) else None
    except (ValueError, IndexError):
        return None


def _slack_escape(text: str) -> str:
    """Escape Slack mrkdwn special characters in user-supplied strings."""
    for ch in ("&", "<", ">", "*", "_", "`", "~"):
        text = text.replace(ch, f"\\{ch}")
    return text


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # Strip inline comments from unquoted values (e.g. KEY=value # comment)
        if value and value[0] not in ("'", '"'):
            value = value.split(" #")[0].rstrip()
        else:
            value = value.strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv(Path(__file__).with_name(".env"))

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
NODE_NAME = os.getenv("PVE_NODE_NAME", "node1").strip()
FAIL_ON_CRITICAL_ALERTS = os.getenv("FAIL_ON_CRITICAL_ALERTS", "false").strip().lower() in {
    "1", "true", "yes", "on",
}

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        print(f"Warning: {name} has an invalid value; using default {default}.", file=sys.stderr)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        print(f"Warning: {name} has an invalid value; using default {default}.", file=sys.stderr)
        return default


# Health alert thresholds (override via .env)
TEMP_WARN_C = _env_int("TEMP_WARN_C", 40)
TEMP_CRIT_C = _env_int("TEMP_CRIT_C", 50)
FAN_MIN_RPM = _env_int("FAN_MIN_RPM", 500)
VBAT_WARN_V = _env_float("VBAT_WARN_V", 2.7)

if not re.fullmatch(r"[a-zA-Z0-9_-]+", NODE_NAME):
    print(
        f"Invalid PVE_NODE_NAME '{NODE_NAME}': only letters, numbers, hyphens, and underscores are allowed.",
        file=sys.stderr,
    )
    sys.exit(1)

if SLACK_WEBHOOK_URL and not SLACK_WEBHOOK_URL.startswith("https://hooks.slack.com/"):
    print("SLACK_WEBHOOK_URL does not look like a Slack webhook URL.", file=sys.stderr)
    sys.exit(1)

HISTORY_FILE = Path(__file__).with_name("health_history.json")

# Minimal allowlisted environment for subprocesses — never inherit LD_PRELOAD,
# LD_LIBRARY_PATH, BASH_ENV, or other dangerous vars from the caller.
_CMD_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": os.environ.get("HOME", "/root"),
    "LANG": os.environ.get("LANG", "C"),
}


def run_command(args: list, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            args,
            shell=False,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_CMD_ENV,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise subprocess.CalledProcessError(1, args) from exc
    return result.stdout.strip()


def get_power_usage() -> str:
    try:
        output = run_command(["ipmitool", "-I", "open", "dcmi", "power", "reading"])
        for line in output.splitlines():
            if "Instantaneous power reading" in line and ":" in line:
                reading = line.split(":", 1)[1].strip()
                if reading:
                    return reading
        if output:
            return "N/A (DCMI supported, but no instantaneous reading returned)"
    except subprocess.CalledProcessError:
        pass

    try:
        sensor_output = run_command(["ipmitool", "-I", "open", "sensor"])
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

    return "N/A (DCMI unsupported; no watt-based sensor found)"


def get_vm_stats() -> str:
    try:
        output = run_command(["pvesh", "get", f"/nodes/{NODE_NAME}/qemu", "--output-format", "json"])
        vms = json.loads(output)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return "Unable to read VM stats (check pvesh/node name)."

    report_lines = []
    for vm in vms:
        if vm.get("status") == "running":
            try:
                cpu = round(float(vm.get("cpu", 0)) * 100, 1)
                ram = round(float(vm.get("mem", 0)) / 1024**3, 2)
            except (ValueError, TypeError):
                continue
            name = vm.get("name") or f"vm-{vm.get('vmid', 'unknown')}"
            report_lines.append(f"• *{_slack_escape(str(name))}*: CPU: {cpu}% | RAM: {ram}GB")

    return "\n".join(report_lines) if report_lines else "No VMs running."


# ---------------------------------------------------------------------------
# SDR parsing — run ipmitool once and share output across functions
# ---------------------------------------------------------------------------

def _parse_sdr(sdr_output: str) -> dict:
    """Extract system temp, fans, and VBAT from raw sdr list output."""
    data: dict = {"system_temp": None, "fans": {}, "vbat": None}
    for line in sdr_output.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        name, reading = parts[0], parts[1]
        name_lower = name.lower()
        if "system temp" in name_lower:
            data["system_temp"] = reading
        elif name_lower.startswith("fan"):
            data["fans"][name] = reading
        elif "vbat" in name_lower:
            data["vbat"] = reading
    return data


def get_health_snapshot(sdr_data: dict) -> Tuple[str, dict]:
    """
    Build the snapshot display string and parse numeric values.
    Returns (display_str, numeric_dict).
    """
    temp_str = sdr_data["system_temp"]
    fans = sdr_data["fans"]
    vbat_str = sdr_data["vbat"]

    lines = []
    lines.append(f"• *System Temp*: {temp_str or 'N/A'}")
    if fans:
        fan_str = " | ".join(f"{k}: {v}" for k, v in sorted(fans.items()))
        lines.append(f"• *Fans*: {fan_str}")
    lines.append(f"• *VBAT*: {vbat_str or 'N/A'}")

    # Parse floats for threshold checking and history.
    # _safe_float rejects NaN/Infinity to prevent history file corruption.
    numeric: dict = {}
    if temp_str:
        val = _safe_float(temp_str)
        if val is not None:
            numeric["system_temp"] = val
    for k, v in fans.items():
        val = _safe_float(v)
        if val is not None:
            numeric[k.lower().replace(" ", "_")] = val
    if vbat_str:
        val = _safe_float(vbat_str)
        if val is not None:
            numeric["vbat"] = val

    return "\n".join(lines), numeric


def get_sensor_alerts(sdr_output: str) -> Tuple[str, bool]:
    alerts = []
    critical_detected = False
    ok_statuses = {"ok", "ns", "na", "disabled"}

    for line in sdr_output.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue

        name, reading, status = parts[0], parts[1], parts[2]
        status_lower = status.lower()
        reading_lower = reading.lower()
        name_lower = name.lower()

        if status_lower in ok_statuses:
            continue

        alerts.append(f"• *{_slack_escape(name)}*: {_slack_escape(status)} ({_slack_escape(reading)})")

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


# ---------------------------------------------------------------------------
# History & trend chart
# ---------------------------------------------------------------------------

def save_to_history(numeric: dict) -> None:
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            history = []

    entry = {"date": datetime.date.today().isoformat(), **numeric}
    history.append(entry)
    history = history[-30:]  # keep last 30 days

    # Write atomically via a temp file then rename, and lock to 0o600 (owner-only).
    # This prevents a race condition if two instances run concurrently and prevents
    # other local users from reading historical sensor/VM data.
    import tempfile
    try:
        fd, tmp_str = tempfile.mkstemp(dir=HISTORY_FILE.parent, suffix=".tmp")
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(history, f, indent=2)
            os.chmod(tmp_path, 0o600)
            tmp_path.rename(HISTORY_FILE)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise
    except OSError as exc:
        print(f"Warning: could not write history file: {exc}", file=sys.stderr)


def get_trend_chart() -> str:
    if not HISTORY_FILE.exists():
        return "No history yet — trend will appear after the first run."

    try:
        history = json.loads(HISTORY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return "Unable to read history file."

    recent = [
        e for e in history
        if isinstance(e.get("system_temp"), (int, float)) and math.isfinite(e["system_temp"])
    ][-7:]
    if not recent:
        return "No temperature history available yet."

    max_temp = max(e["system_temp"] for e in recent)
    bar_scale = 20

    lines = ["*System Temp — Last 7 Days:*"]
    for i, entry in enumerate(recent):
        date = entry["date"][5:]  # MM-DD
        temp = entry["system_temp"]
        bar_len = int((temp / max(max_temp, 1)) * bar_scale)
        bar = "█" * bar_len + "░" * (bar_scale - bar_len)
        marker = " ← today" if i == len(recent) - 1 else ""
        lines.append(f"`{date}`  {temp:>4.0f}°C  {bar}{marker}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Threshold alerts
# ---------------------------------------------------------------------------

def check_health_thresholds(numeric: dict) -> Tuple[list, bool]:
    """
    Compare numeric sensor values against configured thresholds.
    Returns (alert_lines, is_critical).
    """
    alerts = []
    is_critical = False

    temp = numeric.get("system_temp")
    if temp is not None:
        if temp >= TEMP_CRIT_C:
            alerts.append(f"🔥 *CRITICAL*: System Temp {temp:.0f}°C ≥ {TEMP_CRIT_C}°C threshold")
            is_critical = True
        elif temp >= TEMP_WARN_C:
            alerts.append(f"⚠️ *WARNING*: System Temp {temp:.0f}°C ≥ {TEMP_WARN_C}°C threshold")

    for key, val in numeric.items():
        if key.startswith("fan_") and val < FAN_MIN_RPM:
            label = key.replace("fan_", "FAN ").upper()
            alerts.append(f"🔥 *CRITICAL*: {label} at {val:.0f} RPM — possible fan failure")
            is_critical = True

    vbat = numeric.get("vbat")
    if vbat is not None and vbat < VBAT_WARN_V:
        alerts.append(f"⚠️ *WARNING*: VBAT {vbat:.2f}V is low (replace CMOS battery below 2.5V)")

    return alerts, is_critical


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------

def send_slack(text: str) -> bool:
    try:
        response = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f"Failed to post to Slack: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not SLACK_WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL is not set.", file=sys.stderr)
        return 1

    # Run ipmitool sdr once and share the output
    try:
        sdr_output = run_command(["ipmitool", "-I", "open", "sdr", "list"])
    except subprocess.CalledProcessError:
        sdr_output = ""

    power = get_power_usage()
    vm_report = get_vm_stats()

    if sdr_output:
        sdr_data = _parse_sdr(sdr_output)
        health_snapshot, numeric = get_health_snapshot(sdr_data)
        sensor_report, critical_alert_detected = get_sensor_alerts(sdr_output)
        save_to_history(numeric)
        threshold_alerts, threshold_critical = check_health_thresholds(numeric)
    else:
        health_snapshot = "Unable to read sensors (check IPMI connection)."
        numeric = {}
        sensor_report = "Unable to read sensors (check IPMI connection)."
        critical_alert_detected = False
        threshold_alerts = []
        threshold_critical = False

    trend_chart = get_trend_chart()

    # Build daily report
    report = (
        f"🖥️ *Daily Lab Island Report - {NODE_NAME}*\n\n"
        f"⚡ *Power Draw*: {power}\n"
        f"📦 *Active VM Resources*:\n{vm_report}\n\n"
        f"🌡️ *Health Snapshot*:\n{health_snapshot}\n\n"
        f"📈 *Trends*:\n{trend_chart}\n\n"
        f"🚨 *Sensor Alerts*:\n{sensor_report}"
    )

    if threshold_alerts:
        report += "\n\n⚠️ *Threshold Alerts*:\n" + "\n".join(threshold_alerts)

    if not send_slack(report):
        print("Warning: daily report was not delivered to Slack.", file=sys.stderr)

    # Send a separate urgent message for critical threshold breaches
    if threshold_critical:
        urgent = (
            f"🚨 *URGENT — {NODE_NAME} needs attention* 🚨\n\n"
            + "\n".join(threshold_alerts)
            + "\n\nCheck the server immediately."
        )
        if not send_slack(urgent):
            print("Warning: urgent alert was not delivered to Slack.", file=sys.stderr)

    if (critical_alert_detected or threshold_critical) and FAIL_ON_CRITICAL_ALERTS:
        print("Critical alert detected.", file=sys.stderr)
        return 2

    return 0


def install_cron(hour: int = 8) -> int:
    if not 0 <= hour <= 23:
        print(f"Invalid hour '{hour}': must be 0–23.", file=sys.stderr)
        return 1

    script_path = Path(__file__).resolve()
    python = sys.executable
    log_path = script_path.with_suffix(".log")
    cron_entry = f"0 {hour} * * * {python} {script_path} >> {log_path} 2>&1"

    # Read existing crontab (crontab -l exits non-zero when empty, so don't check=True)
    result = subprocess.run(
        ["crontab", "-l"],
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    existing = result.stdout if result.returncode == 0 else ""

    if str(script_path) in existing:
        print(f"Cron job already installed for {script_path}")
        return 0

    new_crontab = existing.rstrip("\n") + "\n" + cron_entry + "\n"

    install = subprocess.run(
        ["crontab", "-"],
        shell=False,
        input=new_crontab,
        text=True,
    )

    if install.returncode != 0:
        print("Failed to install cron job.", file=sys.stderr)
        return 1

    print("Cron job installed successfully.")
    print(f"  Schedule : daily at {hour:02d}:00")
    print(f"  Script   : {script_path}")
    print(f"  Log file : {log_path}")

    # Write a logrotate config to prevent unbounded log growth.
    logrotate_conf = (
        f"{log_path} {{\n"
        f"    daily\n"
        f"    rotate 7\n"
        f"    compress\n"
        f"    missingok\n"
        f"    notifempty\n"
        f"}}\n"
    )
    logrotate_path = Path("/etc/logrotate.d/pve-resource-monitor")
    try:
        logrotate_path.write_text(logrotate_conf)
        logrotate_path.chmod(0o644)
        print(f"  Log rotate: {logrotate_path} (7-day rotation, compressed)")
    except OSError:
        print("  Note: could not write logrotate config — set up log rotation manually.")

    return 0


if __name__ == "__main__":
    if "--install-cron" in sys.argv:
        raise SystemExit(install_cron())
    raise SystemExit(main())
