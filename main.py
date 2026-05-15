#!/usr/bin/env python3
import csv
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
VENDOR_DIR = ROOT / "vendor"
LOG_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"
GITHUB_API_LATEST = "https://api.github.com/repos/XIU2/CloudflareSpeedTest/releases/latest"

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except AttributeError:
    pass


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def load_config():
    if not CONFIG_PATH.exists():
        raise SystemExit(f"Missing config file: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def ensure_dirs():
    VENDOR_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)


def request_json(url, timeout=20, retries=3, retry_delay=3):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vless-cf-speed-dns"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_error = e
            log(f"JSON request failed {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(retry_delay)
    raise last_error


def download_file(url, target, timeout=120, retries=3, retry_delay=5):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vless-cf-speed-dns"})
            with urllib.request.urlopen(req, timeout=timeout) as resp, target.open("wb") as f:
                shutil.copyfileobj(resp, f)
            return
        except Exception as e:
            last_error = e
            log(f"Download failed {attempt}/{retries}: {e}")
            try:
                target.unlink(missing_ok=True)
            except Exception:
                pass
            if attempt < retries:
                time.sleep(retry_delay)
    raise last_error


def current_asset_name():
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        os_name = "windows"
        ext = ".zip"
    elif system == "linux":
        os_name = "linux"
        ext = ".tar.gz"
    elif system == "darwin":
        os_name = "darwin"
        ext = ".zip"
    else:
        raise SystemExit(f"Unsupported OS for auto download: {platform.system()}")

    if machine in ("amd64", "x86_64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86", "i386", "i686"):
        arch = "386"
    else:
        raise SystemExit(f"Unsupported CPU architecture for auto download: {platform.machine()}")

    return f"cfst_{os_name}_{arch}{ext}"


def extract_archive(archive_path, target_dir):
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(target_dir)
        return

    if archive_path.name.endswith(".tar.gz"):
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(target_dir)
        return

    raise SystemExit(f"Unsupported archive format: {archive_path}")


def executable_name():
    return "cfst.exe" if platform.system().lower() == "windows" else "cfst"


def executable_candidates():
    if platform.system().lower() == "windows":
        return ["cfst.exe", "CloudflareST.exe"]
    return ["cfst", "CloudflareST"]


def find_cfst(config):
    configured = config.get("runtime", {}).get("cfst_path", "").strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            return path
        raise SystemExit(f"Configured cfst_path does not exist: {path}")

    for name in executable_candidates():
        local = VENDOR_DIR / name
        if local.exists():
            return local

    for name in executable_candidates():
        system_path = shutil.which(name)
        if system_path:
            return Path(system_path)

    if not config.get("runtime", {}).get("auto_download_cfst", True):
        raise SystemExit("CloudflareSpeedTest binary not found, and auto_download_cfst is false.")

    return download_cfst()


def download_cfst():
    asset_name = current_asset_name()
    log(f"Downloading CloudflareSpeedTest latest release asset: {asset_name}")
    release = request_json(GITHUB_API_LATEST)
    asset = next((a for a in release.get("assets", []) if a.get("name") == asset_name), None)
    if not asset:
        raise SystemExit(f"Could not find release asset {asset_name}")

    archive_path = VENDOR_DIR / asset_name
    download_file(asset["browser_download_url"], archive_path)
    try:
        extract_archive(archive_path, VENDOR_DIR)
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        archive_path.unlink(missing_ok=True)
        raise SystemExit(
            "Downloaded CloudflareSpeedTest archive is invalid. "
            "Your network may have returned an error page instead of the release file. "
            f"Please manually download {asset_name} from {asset['browser_download_url']} "
            f"and extract {executable_name()} into {VENDOR_DIR}."
        ) from e

    cfst = None
    for name in executable_candidates():
        candidate = VENDOR_DIR / name
        if candidate.exists():
            cfst = candidate
            break
    if cfst is None:
        for name in executable_candidates():
            matches = list(VENDOR_DIR.rglob(name))
            if matches:
                cfst = matches[0]
                break
    if cfst is None or not cfst.exists():
        raise SystemExit("CloudflareSpeedTest downloaded, but executable was not found after extraction.")

    if platform.system().lower() != "windows":
        cfst.chmod(cfst.stat().st_mode | 0o111)
    return cfst


def run_speedtest(config, cfst_path):
    st = config["speedtest"]
    output_csv = ROOT / st.get("output_csv", "data/result.csv")
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    if output_csv.exists():
        output_csv.unlink()

    ip_file = ROOT / st.get("ipv4_file", "ip.txt")

    cmd = [
        str(cfst_path),
        "-f", str(ip_file),
        "-o", str(output_csv),
        "-tp", str(st.get("test_port", 443)),
        "-n", str(st.get("latency_threads", 200)),
        "-t", str(st.get("latency_attempts", 4)),
        "-tl", str(st.get("max_latency_ms", 9999)),
        "-dn", str(st.get("download_test_count", 5)),
        "-dt", str(st.get("download_test_seconds", 5)),
        "-sl", str(st.get("min_download_speed_mb", 0)),
        "-p", str(st.get("print_result_count", 0)),
    ]

    extra_args = st.get("extra_args", [])
    if extra_args:
        cmd.extend(str(arg) for arg in extra_args)

    log("Running CloudflareSpeedTest")
    log("Command: " + " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)

    if not output_csv.exists():
        raise SystemExit(f"Speedtest output not found: {output_csv}")

    return output_csv


def parse_float(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def find_column(fieldnames, candidates, fallback_index=None):
    normalized = {name.strip().lower(): name for name in fieldnames}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in normalized:
            return normalized[key]
    if fallback_index is not None and len(fieldnames) > fallback_index:
        return fieldnames[fallback_index]
    return None


def read_speedtest_rows(output_csv):
    with output_csv.open("r", encoding="utf-8-sig", newline="") as f:
        raw_rows = list(csv.reader(f))

    if len(raw_rows) <= 1:
        raise SystemExit("Speedtest CSV is empty.")

    parsed = []
    seen = set()
    for row in raw_rows[1:]:
        if len(row) < 6:
            continue
        ip = row[0].strip()
        if not ip or ip in seen:
            continue
        parsed.append({
            "ip": ip,
            "latency_ms": parse_float(row[4], 9999.0),
            "speed_mb": parse_float(row[5], 0.0),
            "colo": row[6].strip() if len(row) > 6 else "",
        })
        seen.add(ip)

    if not parsed:
        raise SystemExit("No IPs parsed from speedtest CSV.")
    return parsed


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def print_speedtest_rows(rows, limit=30):
    visible = [row for row in rows if row["speed_mb"] > 0]
    if not visible:
        visible = rows[:limit]
    else:
        visible = visible[:limit]

    log(f"Download-tested rows shown: {len(visible)}")
    for idx, row in enumerate(visible, 1):
        log(
            f"  {idx:02d}. {row['ip']} "
            f"latency={row['latency_ms']:.2f}ms "
            f"speed={row['speed_mb']:.2f}MB/s "
            f"colo={row['colo']}"
        )


def select_ips(output_csv, speedtest_config):
    rows = read_speedtest_rows(output_csv)
    if speedtest_config.get("print_all_results", False):
        print_speedtest_rows(rows, int(speedtest_config.get("print_results_limit", 30)))

    top_count = int(speedtest_config.get("top_count", 3))
    mode = str(speedtest_config.get("selection_mode", "balanced")).lower()
    max_latency = parse_float(speedtest_config.get("max_selected_latency_ms"), 0.0)
    min_speed = parse_float(speedtest_config.get("min_selected_speed_mb"), 0.0)

    filtered = rows
    if max_latency > 0:
        filtered = [row for row in filtered if row["latency_ms"] <= max_latency]
    if min_speed > 0:
        filtered = [row for row in filtered if row["speed_mb"] >= min_speed]

    allow_partial = bool(speedtest_config.get("allow_partial_selection", True))
    if len(filtered) < top_count and not allow_partial:
        log("Not enough rows matched strict latency/speed filters; falling back to all speedtest rows.")
        filtered = rows
    elif len(filtered) < top_count:
        log(f"Only {len(filtered)} rows matched latency/speed filters; remaining DNS records will keep previous values.")

    if mode == "latency":
        ranked = sorted(filtered, key=lambda row: (row["latency_ms"], -row["speed_mb"]))
    elif mode == "speed":
        ranked = sorted(filtered, key=lambda row: (-row["speed_mb"], row["latency_ms"]))
    else:
        max_speed = max((row["speed_mb"] for row in filtered), default=0.0) or 1.0
        latency_weight = parse_float(speedtest_config.get("latency_weight", 0.55), 0.55)
        speed_weight = parse_float(speedtest_config.get("speed_weight", 0.45), 0.45)
        latency_target = max_latency if max_latency > 0 else parse_float(speedtest_config.get("max_latency_ms", 200), 200)
        latency_target = latency_target or 200
        for row in filtered:
            speed_score = row["speed_mb"] / max_speed
            latency_score = 1 - clamp(row["latency_ms"] / latency_target)
            row["score"] = speed_score * speed_weight + latency_score * latency_weight
        ranked = sorted(filtered, key=lambda row: (-row["score"], row["latency_ms"], -row["speed_mb"]))

    selected = ranked[:top_count]
    for idx, row in enumerate(selected, 1):
        score = row.get("score")
        score_part = f", score={score:.3f}" if score is not None else ""
        log(f"Selected #{idx}: {row['ip']} latency={row['latency_ms']:.2f}ms speed={row['speed_mb']:.2f}MB/s colo={row['colo']}{score_part}")

    ips = []
    seen = set()
    for row in selected:
        ip = row["ip"]
        if ip not in seen:
            ips.append(ip)
            seen.add(ip)
        if len(ips) >= top_count:
            break

    if not ips:
        raise SystemExit("No IPs selected from speedtest CSV.")
    return ips


def cf_api(config, method, path, payload=None):
    cf = config["cloudflare"]
    url = f"https://api.cloudflare.com/client/v4{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {cf['api_token']}",
        "Content-Type": "application/json",
        "User-Agent": "vless-cf-speed-dns",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    timeout = max(int(cf.get("connect_timeout", 5)), int(cf.get("read_timeout", 10)))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloudflare API HTTP {e.code}: {body}") from e

    result = json.loads(body)
    if not result.get("success"):
        raise RuntimeError(f"Cloudflare API failed: {result.get('errors')}")
    return result


def replace_dns_record(config, record_name, ip):
    cf = config["cloudflare"]
    zone_id = cf["zone_id"]
    list_path = f"/zones/{zone_id}/dns_records?type=A&name={record_name}"
    existing = cf_api(config, "GET", list_path).get("result", [])
    deletes = [{"id": rec["id"]} for rec in existing]
    posts = [{
        "type": "A",
        "name": record_name,
        "content": ip,
        "ttl": int(cf.get("ttl", 120)),
        "proxied": bool(cf.get("proxied", False)),
    }]
    cf_api(config, "POST", f"/zones/{zone_id}/dns_records/batch", {"deletes": deletes, "posts": posts})


def state_path(config):
    configured = config.get("runtime", {}).get("state_file", "data/state.json")
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    return path


def load_state(config):
    path = state_path(config)
    if not path.exists():
        return {"next_record_index": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"next_record_index": 0}


def save_state(config, state):
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def build_dns_assignments(config, record_names, ips):
    runtime = config.get("runtime", {})
    strategy = str(runtime.get("dns_update_strategy", "fixed")).lower()
    usable_ips = ips[:len(record_names)]

    if strategy != "rotate":
        assignments = [{"record": record, "ip": ip} for record, ip in zip(record_names, usable_ips)]
        skipped = record_names[len(assignments):]
        return assignments, skipped, None

    state = load_state(config)
    start = int(state.get("next_record_index", 0)) % len(record_names)
    assignments = []
    updated_records = set()
    for offset, ip in enumerate(usable_ips):
        record = record_names[(start + offset) % len(record_names)]
        assignments.append({"record": record, "ip": ip})
        updated_records.add(record)

    skipped = [record for record in record_names if record not in updated_records]
    next_index = (start + len(assignments)) % len(record_names)
    return assignments, skipped, next_index


def write_assignments(assignments, skipped):
    assignments_path = DATA_DIR / "assignments.json"
    payload = {
        "updated": assignments,
        "kept_previous": skipped,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    assignments_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Wrote {assignments_path}")


def update_cloudflare_dns(config, ips):
    cf = config["cloudflare"]
    if not cf.get("enabled", True):
        log("Cloudflare DNS update disabled.")
        return

    token = str(cf.get("api_token", "")).strip()
    zone_id = str(cf.get("zone_id", "")).strip()
    if not token or token == "YOUR_CLOUDFLARE_API_TOKEN" or not zone_id or zone_id == "YOUR_CLOUDFLARE_ZONE_ID":
        log("Cloudflare api_token/zone_id is not configured; skipping DNS update.")
        return

    record_names = [str(name).strip() for name in cf.get("record_names", []) if str(name).strip()]
    if not record_names:
        raise SystemExit("cloudflare.record_names is empty.")

    assignments, skipped, next_index = build_dns_assignments(config, record_names, ips)
    if not assignments:
        log("No DNS assignments to update.")
        return

    strategy = str(config.get("runtime", {}).get("dns_update_strategy", "fixed")).lower()
    if strategy == "rotate":
        log("DNS update strategy: rotate")
    else:
        log("DNS update strategy: fixed")

    max_retries = int(cf.get("max_retries", 3))
    retry_delay = int(cf.get("retry_delay_seconds", 5))
    for attempt in range(1, max_retries + 1):
        try:
            for assignment in assignments:
                log(f"Updating {assignment['record']} -> {assignment['ip']}")
                replace_dns_record(config, assignment["record"], assignment["ip"])
            if skipped:
                log("Kept previous DNS records unchanged for: " + ", ".join(skipped))
            write_assignments(assignments, skipped)
            if next_index is not None:
                save_state(config, {"next_record_index": next_index})
                log(f"Next rotated DNS update will start at index {next_index}.")
            return
        except Exception as e:
            log(f"DNS update failed on attempt {attempt}/{max_retries}: {e}")
            if attempt >= max_retries:
                raise
            time.sleep(retry_delay)


def write_outputs(ips, config):
    selected_path = DATA_DIR / "selected_ips.txt"
    selected_path.write_text("\n".join(ips) + "\n", encoding="utf-8")
    log(f"Wrote {selected_path}")


def git_sync(config):
    sync = config.get("github_sync", {})
    if not sync.get("enabled", False):
        return

    subprocess.run(["git", "add", "data/result.csv", "data/selected_ips.txt", "data/assignments.json", "data/state.json"], cwd=ROOT, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if diff.returncode == 0:
        log("No Git changes to commit.")
        return

    msg = sync.get("commit_message", "Update Cloudflare speed IPs")
    subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "origin", sync.get("branch", "main")], cwd=ROOT, check=True)


def main():
    ensure_dirs()
    config = load_config()
    cfst = find_cfst(config)
    output_csv = run_speedtest(config, cfst)
    ips = select_ips(output_csv, config.get("speedtest", {}))
    log("Selected IPs: " + ", ".join(ips))
    write_outputs(ips, config)
    update_cloudflare_dns(config, ips)
    git_sync(config)
    log("Finished.")


if __name__ == "__main__":
    main()
