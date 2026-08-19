#!/usr/bin/env python3
import csv
import http.client
import json
import platform
import shutil
import socket
import ssl
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
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"JSON request failed for {url}")


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
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Download failed for {url}")


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


def prepare_clean_ip_file(raw_ip_file):
    if not raw_ip_file.exists():
        raise SystemExit(f"IP list file not found: {raw_ip_file}")

    clean_path = DATA_DIR / "clean_ip.txt"
    lines = raw_ip_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    clean_lines = []
    for line in lines:
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#") or cleaned.startswith("//") or cleaned.startswith(";"):
            continue
        if "#" in cleaned:
            cleaned = cleaned.split("#", 1)[0].strip()
        if cleaned:
            clean_lines.append(cleaned)

    if not clean_lines:
        raise SystemExit(f"No valid IP/CIDR found in {raw_ip_file}")

    clean_path.parent.mkdir(parents=True, exist_ok=True)
    clean_path.write_text("\n".join(clean_lines) + "\n", encoding="utf-8")
    return clean_path


def parse_float(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def read_speedtest_rows(output_csv):
    if not output_csv.exists():
        return []

    with output_csv.open("r", encoding="utf-8-sig", newline="") as f:
        raw_rows = list(csv.reader(f))

    if len(raw_rows) <= 1:
        return []

    parsed = []
    seen = set()
    for row in raw_rows[1:]:
        if len(row) < 5:
            continue
        ip = row[0].strip()
        if not ip or ip in seen:
            continue
        latency = parse_float(row[4], 9999.0)
        speed = parse_float(row[5], 0.0) if len(row) > 5 else 0.0
        colo = row[6].strip() if len(row) > 6 else ""
        parsed.append({
            "ip": ip,
            "latency_ms": latency,
            "speed_mb": speed,
            "colo": colo,
        })
        seen.add(ip)

    return parsed


def check_ip_sni_connectivity(ip, sni, port=443, path="/", timeout=4, allow_insecure=False):
    """
    通过连接指定 IP 的 TLS 端口，并在 TLS 握手时发送 server_hostname=sni，
    发起 HTTP 请求验证该 IP 携带指定 SNI 时是否能够正常路由，排除 Cloudflare 边缘阻断（1000/1001/403/1034 等）。
    """
    if not sni:
        return True, "No SNI specified, skipped"

    if allow_insecure:
        ctx = ssl._create_unverified_context()
    else:
        ctx = ssl.create_default_context()

    sock = None
    tls_sock = None
    try:
        sock = socket.create_connection((ip, int(port)), timeout=timeout)
        sock.settimeout(timeout)
        tls_sock = ctx.wrap_socket(sock, server_hostname=sni)

        conn = http.client.HTTPSConnection(sni, port=int(port), timeout=timeout, context=ctx)
        conn.sock = tls_sock

        headers = {
            "Host": sni,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Connection": "close",
        }
        conn.request("GET", path if path else "/", headers=headers)
        resp = conn.getresponse()
        status = resp.status
        body_chunk = resp.read(2048).decode("utf-8", errors="replace").lower()
        conn.close()

        # 检查 Cloudflare 拦截特征
        cf_block_markers = [
            "direct ip access not allowed",
            "dns points to prohibited ip",
            "error code: 1000",
            "error code: 1001",
            "error code: 1002",
            "error code: 1003",
            "error code: 1016",
            "error code: 1020",
            "error code: 1034",
            "error 1000",
            "error 1001",
            "error 1002",
            "error 1034",
        ]
        for marker in cf_block_markers:
            if marker in body_chunk:
                return False, f"Cloudflare blocked (HTTP {status}: {marker})"

        if status in (1000, 1001, 1002, 1003, 1016, 1020, 1034):
            return False, f"Cloudflare blocked (HTTP {status})"

        if status == 403 and ("cloudflare" in body_chunk or "access denied" in body_chunk):
            return False, f"Cloudflare 403 Forbidden / Access Denied"

        # 200, 301, 302, 304, 307, 308, 400, 404, 405, 101, 426, 526 等均说明握手与路由到达了目标
        return True, f"HTTP {status} (OK)"

    except ssl.SSLCertVerificationError as e:
        return False, f"TLS Certificate Error: {e.reason if hasattr(e, 'reason') else e}"
    except ssl.SSLError as e:
        return False, f"TLS Handshake Error: {e}"
    except TimeoutError:
        return False, f"Connection Timeout (> {timeout}s)"
    except ConnectionRefusedError:
        return False, f"Connection Refused"
    except ConnectionResetError:
        return False, f"Connection Reset (likely GFW/Firewall RST)"
    except Exception as e:
        return False, f"Check failed: {e}"
    finally:
        try:
            if tls_sock:
                tls_sock.close()
            elif sock:
                sock.close()
        except Exception:
            pass


def run_latency_speedtest(config, cfst_path):
    """阶段一：快速延迟测速（TCP Ping）"""
    st = config.get("speedtest", {})
    latency_csv = ROOT / "data" / "latency_result.csv"
    latency_csv.parent.mkdir(parents=True, exist_ok=True)
    if latency_csv.exists():
        latency_csv.unlink()

    raw_ip_file = ROOT / st.get("ipv4_file", "ip.txt")
    clean_ip_file = prepare_clean_ip_file(raw_ip_file)

    cmd = [
        str(cfst_path),
        "-f", str(clean_ip_file),
        "-o", str(latency_csv),
        "-tp", str(st.get("test_port", 443)),
        "-n", str(st.get("latency_threads", 200)),
        "-t", str(st.get("latency_attempts", 4)),
        "-tl", str(st.get("max_latency_ms", 9999)),
        "-dn", "0",  # 不测下载，仅测延迟
        "-p", "0",
    ]

    log("Step 1: Running Fast Latency Test (TCP Ping)...")
    subprocess.run(cmd, cwd=ROOT, check=True)

    rows = read_speedtest_rows(latency_csv)
    if not rows:
        raise SystemExit("No available IPs from latency test.")
    # 按延迟从小到大排序
    rows.sort(key=lambda r: r["latency_ms"])
    log(f"Latency test completed. Available low-latency IPs: {len(rows)}")
    return rows


def verify_candidate_ips(latency_rows, config):
    """阶段二：在测速前对低延迟 IP 进行真实业务 SNI 握手验活"""
    verify_config = config.get("verify", {})
    st = config.get("speedtest", {})
    needed_verified_count = int(st.get("download_test_count", 15))
    max_check = int(verify_config.get("max_candidates_to_check", 35))

    sni = verify_config.get("sni", "").strip()
    if not sni:
        records = config.get("cloudflare", {}).get("record_names", [])
        if records:
            sni = records[0].strip()

    port = int(verify_config.get("port", st.get("test_port", 443)))
    path = str(verify_config.get("path", "/"))
    timeout = float(verify_config.get("timeout_seconds", 4))
    allow_insecure = bool(verify_config.get("allow_insecure_tls", False))

    log(f"Step 2: Pre-Verifying Real SNI Handshake (Target: {sni}, Port: {port}, Path: {path})...")
    candidates = latency_rows[:max_check]
    verified_ips = []

    for idx, row in enumerate(candidates, 1):
        ip = row["ip"]
        is_ok, msg = check_ip_sni_connectivity(
            ip=ip,
            sni=sni,
            port=port,
            path=path,
            timeout=timeout,
            allow_insecure=allow_insecure
        )
        status_tag = "PASS" if is_ok else "FAIL"
        log(f"  [Verify {idx:02d}/{len(candidates):02d}] {ip} (latency={row['latency_ms']:.1f}ms) -> [{status_tag}] {msg}")

        if is_ok:
            verified_ips.append(ip)
            if len(verified_ips) >= needed_verified_count:
                log(f"Collected {len(verified_ips)} verified alive IPs for targeted download testing.")
                break

    if not verified_ips:
        log("WARNING: None of the candidate IPs passed real SNI handshake verification!")
        return []

    verified_file = DATA_DIR / "verified_ips.txt"
    verified_file.write_text("\n".join(verified_ips) + "\n", encoding="utf-8")
    return verified_file


def run_download_speedtest(config, cfst_path, verified_file):
    """阶段三：仅对握手成功的真活 IP 做精准下载测速"""
    st = config.get("speedtest", {})
    output_csv = ROOT / st.get("output_csv", "data/result.csv")
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    if output_csv.exists():
        output_csv.unlink()

    cmd = [
        str(cfst_path),
        "-f", str(verified_file),
        "-o", str(output_csv),
        "-tp", str(st.get("test_port", 443)),
        "-n", "50",
        "-t", "4",
        "-tl", "9999",
        "-dn", str(st.get("download_test_count", 15)),
        "-dt", str(st.get("download_test_seconds", 5)),
        "-sl", str(st.get("min_download_speed_mb", 0)),
        "-p", "0",
    ]

    download_url = str(st.get("download_url", "")).strip()
    if download_url:
        cmd.extend(["-url", download_url])

    extra_args = st.get("extra_args", [])
    if extra_args:
        cmd.extend(str(arg) for arg in extra_args)

    log("Step 3: Running Targeted Download Speedtest on Verified Alive IPs...")
    subprocess.run(cmd, cwd=ROOT, check=True)

    if not output_csv.exists():
        raise SystemExit(f"Speedtest output not found: {output_csv}")

    return output_csv


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def print_speedtest_rows(rows, limit=30):
    visible = [row for row in rows if row["speed_mb"] > 0]
    if not visible:
        visible = rows[:limit]
    else:
        visible = visible[:limit]

    log(f"Download-tested rows: {len(visible)}")
    for idx, row in enumerate(visible, 1):
        log(
            f"  {idx:02d}. {row['ip']} "
            f"latency={row['latency_ms']:.2f}ms "
            f"speed={row['speed_mb']:.2f}MB/s "
            f"colo={row['colo']}"
        )


def select_final_ips(output_csv, config):
    """阶段四：从下载测速结果中根据策略选出最优 IP"""
    speedtest_config = config.get("speedtest", {})
    rows = read_speedtest_rows(output_csv)
    if not rows:
        return []

    if speedtest_config.get("print_all_results", True):
        print_speedtest_rows(rows, int(speedtest_config.get("print_results_limit", 30)))

    top_count = int(speedtest_config.get("top_count", 5))
    mode = str(speedtest_config.get("selection_mode", "speed")).lower()
    max_latency = parse_float(speedtest_config.get("max_selected_latency_ms"), 0.0)
    min_speed = parse_float(speedtest_config.get("min_selected_speed_mb"), 0.0)

    filtered = rows
    if max_latency > 0:
        filtered = [row for row in filtered if row["latency_ms"] <= max_latency]
    if min_speed > 0:
        filtered = [row for row in filtered if row["speed_mb"] >= min_speed]

    allow_partial = bool(speedtest_config.get("allow_partial_selection", True))
    if len(filtered) < top_count and not allow_partial:
        filtered = rows

    if mode == "latency":
        ranked = sorted(filtered, key=lambda row: (row["latency_ms"], -row["speed_mb"]))
    elif mode == "speed":
        ranked = sorted(filtered, key=lambda row: (-row["speed_mb"], row["latency_ms"]))
    else:
        max_speed = max((row["speed_mb"] for row in filtered), default=0.0) or 1.0
        latency_weight = parse_float(speedtest_config.get("latency_weight", 0.25), 0.25)
        speed_weight = parse_float(speedtest_config.get("speed_weight", 0.75), 0.75)
        latency_target = max_latency if max_latency > 0 else 200
        for row in filtered:
            speed_score = row["speed_mb"] / max_speed
            latency_score = 1 - clamp(row["latency_ms"] / latency_target)
            row["score"] = speed_score * speed_weight + latency_score * latency_weight
        ranked = sorted(filtered, key=lambda row: (-row["score"], row["latency_ms"], -row["speed_mb"]))

    selected = ranked[:top_count]
    for idx, row in enumerate(selected, 1):
        score = row.get("score")
        score_part = f", score={score:.3f}" if score is not None else ""
        log(f"Final Selected #{idx}: {row['ip']} latency={row['latency_ms']:.2f}ms speed={row['speed_mb']:.2f}MB/s colo={row['colo']}{score_part}")

    return [r["ip"] for r in selected]


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
    cf = config.get("cloudflare", {})
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


def write_outputs(ips):
    selected_path = DATA_DIR / "selected_ips.txt"
    selected_path.write_text("\n".join(ips) + ("\n" if ips else ""), encoding="utf-8")
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

    verify_enabled = config.get("verify", {}).get("enabled", True)

    if verify_enabled:
        # 1. 快速延迟初筛
        latency_rows = run_latency_speedtest(config, cfst)
        # 2. 真实 SNI 握手验活（秒级剔除 403/1034）
        verified_file = verify_candidate_ips(latency_rows, config)
        if not verified_file:
            log("No alive verified IPs found. Aborting DNS update to protect existing records.")
            return
        # 3. 仅对 100% 确认握手通过的真活 IP 跑下载测速
        output_csv = run_download_speedtest(config, cfst, verified_file)
    else:
        log("Real SNI verify is disabled; running standard speedtest.")
        # 旧流程
        raw_ip_file = ROOT / config.get("speedtest", {}).get("ipv4_file", "ip.txt")
        clean_ip_file = prepare_clean_ip_file(raw_ip_file)
        st = config.get("speedtest", {})
        output_csv = ROOT / st.get("output_csv", "data/result.csv")
        cmd = [
            str(cfst),
            "-f", str(clean_ip_file),
            "-o", str(output_csv),
            "-tp", str(st.get("test_port", 443)),
            "-n", str(st.get("latency_threads", 200)),
            "-t", str(st.get("latency_attempts", 4)),
            "-tl", str(st.get("max_latency_ms", 9999)),
            "-dn", str(st.get("download_test_count", 5)),
            "-dt", str(st.get("download_test_seconds", 5)),
            "-sl", str(st.get("min_download_speed_mb", 0)),
            "-p", "0",
        ]
        download_url = str(st.get("download_url", "")).strip()
        if download_url:
            cmd.extend(["-url", download_url])
        subprocess.run(cmd, cwd=ROOT, check=True)

    # 4. 挑选最优 IP 并更新 DNS
    final_ips = select_final_ips(output_csv, config)
    if final_ips:
        log("Selected Valid IPs: " + ", ".join(final_ips))
        write_outputs(final_ips)
        update_cloudflare_dns(config, final_ips)
        git_sync(config)
    else:
        log("No valid IPs selected after speedtest.")

    log("Finished.")


if __name__ == "__main__":
    main()
