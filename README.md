# VLESS Cloudflare Speed DNS

每天自动测速 Cloudflare 官方 CDN IP，并采用 **“延迟初筛 -> 真实业务 SNI 握手验活 -> 仅真活 IP 下载测速 -> 自动更新 DNS”** 的四阶段管道，确保写入 DNS 的每一个 IP 都是 100% 真实可用、不被 Cloudflare 拦截的高速 IP。

本项目使用 [XIU2/CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest) 作为测速内核。

---

## 核心工作流程（先验活，再测速）

```text
[Step 1: TCP Ping 快速延迟初筛]
   │
   ▼
[Step 2: 真实业务 SNI / 路径 提前握手验活] ──(剔除 403/1034/超时等假活 IP)
   │
   ▼
[Step 3: 仅对 100% 确认通畅的真活 IP 做下载测速]
   │
   ▼
[Step 4: 选出最优 IP，轮换更新 cf1/cf2/cf3/cf4/cf5 DNS 记录]
```

**优势**：
- **不浪费带宽与时间**：无法握手、报 403 / 1034 的 IP 在第 2 步就会被直接剔除，绝不参与下载测速。
- **杜绝客户端失联**：保证更新到 `cf1`~`cf5` 的每个 IP 都能在客户端即刻连通。

---

## 多服务器共用优选 IP 配置说明

如果有多台服务器（例如 `cdn1.example.com` 和 `cdn2.example.com`）：
- 两台服务器**共用同一批优选域名**（`cf1` ~ `cf5`）；
- 客户端中**地址 (Address)** 均填写 `cf1.example.com`（或 cf2~cf5）；
- 客户端中通过 **伪装域名 (Host / SNI)** 区分流量进入哪台服务器：
  - 服务器 1 节点：Host / SNI 填 `cdn1.example.com`
  - 服务器 2 节点：Host / SNI 填 `cdn2.example.com`

---

## 配置文件说明 (`config.json`)

```json
{
  "cloudflare": {
    "enabled": true,
    "api_token": "YOUR_CLOUDFLARE_API_TOKEN",
    "zone_id": "YOUR_CLOUDFLARE_ZONE_ID",
    "record_names": [
      "cf1.example.com",
      "cf2.example.com",
      "cf3.example.com",
      "cf4.example.com",
      "cf5.example.com"
    ],
    "ttl": 120,
    "proxied": false
  },
  "verify": {
    "enabled": true,
    "sni": "cdn1.example.com",
    "port": 443,
    "path": "/your-ws-path",
    "timeout_seconds": 4,
    "max_candidates_to_check": 25,
    "allow_insecure_tls": false
  },
  "speedtest": {
    "top_count": 5,
    "download_test_count": 15,
    "test_port": 443,
    "latency_threads": 200,
    "latency_attempts": 4,
    "max_latency_ms": 9999,
    "min_download_speed_mb": 0,
    "download_test_seconds": 5,
    "download_url": "https://speed.cloudflare.com/__down?bytes=20000000",
    "selection_mode": "speed",
    "latency_weight": 0.25,
    "speed_weight": 0.75,
    "max_selected_latency_ms": 0,
    "min_selected_speed_mb": 0,
    "print_all_results": true,
    "allow_partial_selection": true,
    "ipv4_file": "ip.txt"
  },
  "runtime": {
    "dns_update_strategy": "rotate",
    "state_file": "data/state.json"
  }
}
```

---

## 运行方法

### 1. 手动运行

```powershell
python .\main.py
```

### 2. Windows 定时任务（每天自动更新）

```powershell
# 安装计划任务（默认每天 03:00 执行）
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1

# 立即触发测试运行
Start-ScheduledTask -TaskName "VLESS Cloudflare Speed DNS"

# 卸载计划任务
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_windows_task.ps1
```

### 3. Docker 运行

```powershell
docker compose up -d --build
```
