# VLESS Cloudflare Speed DNS

每天自动测速 Cloudflare 官方 CDN IP，并把合格 IP 更新到多个 Cloudflare DNS A 记录，供 VLESS CDN 加速入口使用。

本项目使用 [XIU2/CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest) 作为测速内核，不使用 EdgeTunnel/ProxyIP 节点池。

## 特性

- 自动下载并运行 CloudflareSpeedTest。
- 从 Cloudflare 官方 IPv4 段测速。
- 默认速度优先，延迟作为筛选条件。
- 支持多个域名轮换更新，避免只刷新前几个域名。
- 支持 Windows 计划任务和 Docker 定时运行。
- 本地真实配置和运行结果默认不提交到 Git。

## 工作方式

```text
CloudflareSpeedTest
  -> 延迟测速
  -> 对延迟靠前的 IP 做下载测速
  -> 筛出速度/延迟合格 IP
  -> 轮换更新 cf1/cf2/cf3/cf4/cf5
```

示例：

```text
cf1.example.com -> 172.64.x.x
cf2.example.com -> 162.159.x.x
cf3.example.com -> 保持上次记录
cf4.example.com -> 保持上次记录
cf5.example.com -> 下次从这里继续轮换
```

## 隐私与 Git

不要提交这些文件：

```text
config.json
data/*
vendor/*
logs/*
```

`config.json` 里包含 Cloudflare API Token、Zone ID、真实域名。`data/` 里会生成本次 DNS 分配结果，也可能包含你的真实域名。

仓库里只保留：

```text
config.example.json
main.py
ip.txt
Dockerfile
compose.yaml
scripts/
```

## 配置

复制配置模板：

```powershell
Copy-Item .\config.example.json .\config.json
```

编辑 `config.json`：

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
  }
}
```

建议保持 `proxied: false`，也就是 DNS only。否则外部解析到的是 Cloudflare 代理层地址，不是写入的优选 IP。

## 测速策略

默认配置偏向速度：

```json
{
  "top_count": 5,
  "download_test_count": 12,
  "download_test_seconds": 5,
  "selection_mode": "speed",
  "max_selected_latency_ms": 120,
  "min_selected_speed_mb": 8,
  "print_all_results": true,
  "allow_partial_selection": true
}
```

说明：

- `download_test_count`: 对多少个延迟靠前的 IP 做下载测速。
- `top_count`: 最多选出多少个 IP。
- `min_selected_speed_mb`: 低于该速度的 IP 不更新到 DNS。
- `allow_partial_selection`: 合格 IP 不足时，不强行凑满，未更新到的域名保持旧记录。
- `print_all_results`: 只打印参与下载测速且速度大于 0 的结果，不打印几千个延迟测速 IP。

## 轮换更新

默认启用：

```json
{
  "dns_update_strategy": "rotate",
  "state_file": "data/state.json"
}
```

如果本次只选出 2 个合格 IP，且上次更新到 `cf2`，则本次会从 `cf3` 开始更新，例如：

```text
本次: cf3, cf4
下次: cf5, cf1
再下次: cf2, cf3
```

这样每个域名都有机会被刷新到高速 IP，而不是永远只更新前几个。

## 手动运行

```powershell
python .\main.py
```

第一次运行会自动下载 CloudflareSpeedTest 到 `vendor/`。如果 GitHub 下载失败，可以手动下载 `cfst_windows_amd64.zip`，解压后把 `cfst.exe` 放到：

```text
vendor/cfst.exe
```

## Windows 定时任务

默认每天 03:00 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1
```

指定时间：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1 -At "04:30"
```

立即运行：

```powershell
Start-ScheduledTask -TaskName "VLESS Cloudflare Speed DNS"
```

卸载：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_windows_task.ps1
```

## Docker

启动后先运行一次，然后每 24 小时运行一次：

```powershell
docker compose up -d --build
```

查看日志：

```powershell
docker logs -f vless-cf-speed-dns
```

只运行一次：

```powershell
docker compose run --rm -e RUN_INTERVAL_SECONDS=0 vless-cf-speed-dns
```

## 验证 DNS

```powershell
nslookup cf1.example.com
nslookup cf2.example.com
nslookup cf3.example.com
```

请把示例域名替换为你自己的域名。
