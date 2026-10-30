# 自建镜像源（第三源）接入说明

目标：在自己的服务器上再放一份发布包，作为 GitHub / 魔搭之外的第三个下载点。
服务器同时承载 Cutrans 的业务，所以**隔离**是本文的重点，不是事后补充。

## 1. 带宽现实：先算清楚再决定放什么

服务器上行 **3 MB/s**（约 24 Mbps），这是**整机总量**，不是每个用户。

| 文件 | 大小 | 单用户独占全部带宽耗时 | 结论 |
|---|---|---|---|
| 安装包 | 1.8 GB | ~10 分钟 | 勉强可用 |
| 便携包 | 3.2 GB | ~18 分钟 | 不建议放，会把 Cutrans 拖死 |
| 更新包（update.zip） | 视版本，通常 100–300 MB | 1–2 分钟 | **最合适** |

所以这台服务器的定位应该是：**更新包源**（自动更新用）＋ 安装包兜底，
不放便携包。便携包继续走魔搭。

## 2. 与 Cutrans 的隔离清单

### 2.1 网络与路径

```nginx
# 独立 vhost（推荐）：与 Cutrans 完全不共享 location
server {
    listen 443 ssl http2;
    server_name mf.example.com;          # 独立子域名，不要和 cutrans 共用

    # 只暴露发布包目录，app 不允许访问上级
    root /srv/mediaforge-dist/public;
    autoindex off;
    client_max_body_size 1m;             # 只读下载站，不接受上传

    location = /latest.json {
        default_type application/json;
        root /srv/mediaforge-dist/manifest;
    }

    location /releases/ {
        alias /srv/mediaforge-dist/releases/;
        add_header Cache-Control "public, max-age=86400";
        add_header Accept-Ranges bytes;

        # 限速：单连接 1 MB/s，并发 8 —— 保证 Cutrans 不被抢满
        limit_rate 1m;
        limit_conn  perip 8;
    }
}
```

如果只能用同一个域名，就用**路径前缀** `/mediaforge/`，并且单独写 `limit_rate`／`limit_conn`，
绝不要把发布包和 Cutrans 的接口放在同一段 `location /` 下。

### 2.2 系统层

| 项 | 做法 |
|---|---|
| 运行用户 | 独立系统用户 `mfdl`，只对 `/srv/mediaforge-dist` 有读权限 |
| 磁盘 | 单独配额或独立挂载点；定时清理旧版本（保留最近 3 个） |
| 带宽 | 若能做 cgroup/tc 限速，给该目录的出流量上限（例如 2 MB/s），**留 1 MB/s 给 Cutrans** |
| 日志 | 独立 `access_log`／`error_log`，不要混进 Cutrans 的日志文件 |
| 证书 | 独立子域名更省事；共用证书时确认证书覆盖新域名 |
| 反向代理/CDN | 不要复用 Cutrans 的 CDN 缓存规则（大文件会被缓存计费） |

### 2.3 权限与凭据

- 发布包**公开可读**，不含任何凭据；不要在这里放模型 token 或签名私钥
- 上传走 SFTP/rsync 专用账号，与 Cutrans 部署账号分开
- 服务器上**不要**存 `TAURI_SIGNING_PRIVATE_KEY`；签名在本地/CI 做

## 3. 客户端要改什么

现在 `src-tauri/src/update_sources.rs` 里只有两个源：

```rust
const MODELSCOPE_TREE / MODELSCOPE_ROOT   // 镜像（构建期开关）
const GITHUB_ROOT                         // 官方
```

接第三源有两种做法，**推荐第二种**：

### 做法 A：把自建站加成一个常量源

```rust
const SELFHOST_ROOT: &str = "https://mf.example.com/releases";
pub const SELFHOST_ENABLED: bool = option_env!("MEDIAFORGE_ENABLE_SELFHOST").is_some();
```

然后在 `download_candidates()` 里，当 `UpdateSource::Auto` 时把它的 URL 追加进去
（顺序：GitHub → 魔搭 → 自建）。代价：每加一个源就要发一次版本。

### 做法 B：清单里带源（不用改代码）

更新清单（`latest.json`）里已经有 `url` 字段；`UpdateProvider::Custom` 会**直接用清单里的 URL**。
所以只要清单写成自建站地址，老客户端也能从自建站更新：

```json
{
  "version": "0.0.14",
  "url": "https://mf.example.com/releases/MediaForge-0.0.14-windows-x64-update.zip",
  "signature": "..."
}
```

前提：**签名仍然由私钥签**，镜像只负责分发。这样换源不需要发新版，也不用重新编译。

## 4. 上线检查清单

- [ ] 用**未登录的浏览器**直接下 `https://mf.example.com/releases/<file>`，确认 200 且大小一致
- [ ] `curl -r 0-1023` 返回 206（断点续传可用）
- [ ] 限速生效：单连接应约 1 MB/s，不会把整机 3 MB/s 占满
- [ ] Cutrans 侧在下载压力下响应时间无明显劣化（用 `ab`/`hey` 并发压一下）
- [ ] 访问日志里能看到请求，且与 Cutrans 日志分开
- [ ] 旧版本清理策略生效（目录大小不随时间无限增长）

## 5. 分发时的推荐组合

| 渠道 | 放什么 | 面向 |
|---|---|---|
| GitHub Release | 安装包 + 便携包 + update.zip（官方、可追溯） | 开发者 / 有代理的用户 |
| 魔搭数据集 | 安装包 + 便携包（带宽好、匿名可下） | 国内普通用户 |
| 自建站 | **update.zip 优先**，安装包兜底 | 自动更新 / 魔搭异常时的备用 |

三处文件名必须一致：`MediaForge-<版本>-windows-x64-update.zip`，
因为客户端是按 `archive_name()` 拼路径去找的。
