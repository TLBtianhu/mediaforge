# 为什么这样设计（调研结论）

调研对象：`@deepseek-ai/dsh-mcp-client` 的 README（本机安装版）、
`cordis-plugin-development` 的 `mcp-bundle` / `practices` / `ui-plugin` 参考、
以及本 profile 里真实在用的四条 MCP 行（snowluma / project-kb / web-search-safe）。

## 1. 三条会改变设计的机制

### 1.1 stdio 会启动**两次**子进程

> "stdio negotiation starts a temporary probe process before the serving process."

所以启动器必须**幂等且便宜**：它只能做路径解析 + exec，不能有副作用（不能建目录、
不能写文件、不能下东西）。我们现在这份启动器符合；如果有人把它写成"首次运行安装
运行时"，每次连接都会触发一次安装。

### 1.2 DSH 会**清洗子进程环境**

> ambient names matching `/KEY|PASSWORD|SECRET|TOKEN/i` and ambient `DSH_*` are dropped

这直接影响配置写法：

* 不能指望用 `MEDIAFORGE_MCP_TOKEN` 这类环境变量传鉴权——名字里有 TOKEN，会被丢掉
* 想传秘密必须写进配置的 `env:`（configured env merges on top，显式值保留）
* 反过来，`MEDIAFORGE_HOME`、`MEDIAFORGE_PYTHON` 这类名字是安全的

### 1.3 stdio 与 HTTP 的语义完全不同

| | stdio（当前） | streamable-http |
|---|---|---|
| 谁启动 bridge | **DSH** 拉起子进程 | 应用自己（必须常驻） |
| 应用没开时能用吗 | **能**（bridge 独立跑 worker 命令） | 不能 |
| 多份安装/便携版 | 需要路径解析决定连哪个 | 连"正在跑的那个"，语义天然正确 |
| 重连 | DSH 管（500ms 起指数退避，10 次预算） | SDK 的 per-request 重试 |

结论：**stdio 更适合我们的产品形态**——用户不开 GUI 也应该能让 agent 下模型、跑分离。
HTTP 更适合"大量状态只在进程内"的应用（Blender 那种 socket 插件就是这一类）。

## 2. 所以问题不是"插件要不要两份"，而是"启动器怎么找应用"

插件包保持零路径（`command: cmd` + 固定启动器位置），一份包同时服务四种安装形态。
歧义全部收敛到启动器里的解析顺序：

```
1. MEDIAFORGE_HOME                                  显式覆盖（调试/多版本）
2. %LOCALAPPDATA%\MediaForge\home.txt               安装包写（单行路径）
3. HKCU\Software\MediaForge  InstallDir             安装包写（用户级，不要管理员）
4. %ProgramFiles%\MediaForge                        兜底（默认安装位置）
5. %ProgramFiles(x86)%\MediaForge
6. 失败 → 明确列出找过哪里 + 怎么修（stderr）
```

为什么是这五层而不是只看一个文件：

* **单行指针文件**坏掉了（用户手动删、被杀软清理）还有注册表兜底
* **注册表**在 HKCU 而不是 HKLM：写 HKCU 不需要提权，PerUser 安装也能写
* **Program Files** 兜底覆盖"安装包没写指针"的老版本
* 每一步都验证 `python\agent_bridge.py` 真的存在才采用——防止指到一个空目录

## 3. 认证的现实约束

* bridge 默认 `--token` 为空 = 本地不鉴权（只监听 stdio 时无所谓）
* 如果改成 HTTP，**必须**带 token 且 token 不能走环境变量（见 1.2），
  只能写在配置的 `env:` 或 `headers:` 里；而配置是明文的，所以更好的做法是
  bridge 启动时把 token 写进 `%LOCALAPPDATA%\MediaForge\bridge-token.txt`，
  插件用 `!!js` 读文件——DSH 支持 Loader 表达式读环境，也支持 `!!js` 任意 JS。
* 这条留给想改成 HTTP 的时候再实现，现在就保持"仅 stdio、不鉴权"。

## 4. 失败模式与用户看到什么

| 情况 | 现象 | 我们的处理 |
|---|---|---|
| 应用没装 | 无工具 | 启动器 stderr 说明找过哪里 |
| 装了但指针没写 | 无工具 | 靠注册表/Program Files 兜底 |
| 装了两份（Program Files + 便携） | 连到 Program Files（顺序决定） | 想换：`MEDIAFORGE_HOME` 指向便携版 |
| 运行时没装好 | 工具在，调用报 `No active runtime environment` | bridge 会把搜索过的目录打出来 |
| 同名 serverName 已存在 | 后加载的那行报错、工具消失 | 文档要求：手工配过 mediaforge 就先删掉旧行 |

## 5. 不做的事（明确记录，避免以后返工）

* **不做 iframe/HTML 宿主**：`practices.md` 明确禁止（iframe 拿不到主题、locale）
* **不在插件里写业务页面**：MediaForge 有自己的 GUI；DSH 侧只需要工具
  与一个状态说明面板（已做的 ModelScope 面板是独立例子）
* **不重复实现工具协议**：66 个工具全部由 `agent_contract.py` 生成，
  MCP 侧零硬编码（`test_bridge_tool_coverage.py` 盯着两侧一致）
