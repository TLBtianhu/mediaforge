"""Agent 契约：MediaForge 每一个可执行功能，一份机器可读的描述。

为什么需要它：这个应用有**三套执行面**——45 个 worker 命令、8 个音频 operation、
以及 `agent_bridge` 里手写的 12 个 MCP 工具。三套各说各话：
新增一个功能，bridge 不知道、agent 用不上；改掉一个参数名，schema 还停在旧处。

这份契约把它们收成一张表，每条描述：

* `name`        稳定 id（agent 用它调用；改名等于破坏契约，要升 version）
* `command` + `operation`  真正落到 worker 的哪个命令（执行器只需要这两件事）
* `inputSchema` 输入（JSON Schema 的子集，够 MCP 用）
* `approval`    需要什么级别的确认：`none` 只读 / `confirm` 会写文件或耗时 / `explicit` 下载·安装·删除
* `cost`        预期耗时档位，供 agent 决定"直接等"还是"提交作业再轮询"
* `longRunning` 是否走作业台账（有 jobId / 可取消）
* `produces`    产物形状（file / report / json），供调用方知道去哪儿取结果
* `evaluate`    自评价钩子（跑完之后怎么判断"做好了没有"）

`audit_contract()` 会把**worker 里有、契约里没有**的命令列出来——覆盖率不是靠人记的，
是从 `worker.py` 源码里数出来的。新增命令忘了登记，测试会直接红。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

CONTRACT_VERSION = 1

APPROVAL_LEVELS: dict[str, str] = {
    "none": "只读或纯计算，不需要确认。",
    "confirm": "会写文件/占用较长时间；直接执行前应让用户知道。",
    "explicit": "会下载（大文件）、安装/卸载运行时、或删除文件；必须显式确认。",
}

COST_LEVELS: dict[str, str] = {
    "instant": "毫秒到秒级",
    "seconds": "几秒到几十秒",
    "minutes": "分钟级（建议提交作业并轮询）",
    "download": "取决于网络/体积（建议提交作业并轮询）",
}

_WORKER = Path(__file__).with_name("worker.py")


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _tool(
    name: str,
    title: str,
    description: str,
    category: str,
    command: str,
    *,
    operation: str | None = None,
    approval: str = "none",
    cost: str = "instant",
    long_running: bool = False,
    input_schema: dict[str, Any] | None = None,
    produces: str | None = None,
    evaluate: str | None = None,
    available: bool = True,
) -> dict[str, Any]:
    if approval not in APPROVAL_LEVELS:
        raise ValueError(f"unknown approval level {approval!r} for {name}")
    if cost not in COST_LEVELS:
        raise ValueError(f"unknown cost level {cost!r} for {name}")
    return {
        "name": name,
        "title": title,
        "description": description,
        "category": category,
        "command": command,
        "operation": operation,
        "approval": approval,
        "cost": cost,
        "longRunning": bool(long_running),
        "inputSchema": input_schema or _schema(),
        "produces": produces,
        "evaluate": evaluate,
        "available": bool(available),
    }


# ── 媒体能力：从 capabilities.json 派生，不在这里写死 model id ──────────────────
_MEDIA_STEPS_SCHEMA = _schema(
    {
        "modality": {"type": "string", "enum": ["image", "video"]},
        "inputs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "outputDir": {"type": "string"},
        "modelDir": {"type": "string", "description": "默认用应用的模型根"},
        "steps": {
            "type": "array",
            "description": "能力步骤：{capability, bindings, params}",
            "items": {"type": "object"},
            "minItems": 1,
        },
        "crf": {"type": "integer"},
        "preset": {"type": "string"},
        "previewSeconds": {"type": "number"},
    },
    ["modality", "inputs", "outputDir", "steps"],
)


def _media_tools() -> list[dict[str, Any]]:
    from media_ops import capabilities

    tools: list[dict[str, Any]] = []
    # 契约本身不能因为"某个可选依赖没装"就生成不出来：
    # 拿不到适配器/默认绑定时降级为"未就绪"，而不是抛异常。
    try:
        implemented = set(capabilities.adapter_capability_ids())
    except Exception:  # noqa: BLE001
        implemented = set()
    for capability in capabilities.CAPABILITIES:
        try:
            bindings = capabilities.default_bindings(capability["id"])
        except Exception:  # noqa: BLE001
            bindings = {}
        is_available = capability["id"] in implemented
        for modality in capability["modalities"]:
            name = f"media.{modality}.{capability['id']}"
            description = (
                f"{capability['label']}（{capability['labelEn']}）。"
                f"默认模型：{', '.join(f'{role}={model}' for role, model in bindings.items()) or '未声明'}。"
            )
            tools.append(
                _tool(
                    name,
                    capability["label"],
                    description,
                    "media",
                    "audio_tools",
                    operation="media_image" if modality == "image" else "media_workflow",
                    approval="confirm",
                    cost="seconds" if modality == "image" else "minutes",
                    long_running=modality == "video",
                    input_schema=_MEDIA_STEPS_SCHEMA,
                    produces="file",
                    evaluate="media.evaluate",
                    available=is_available,
                )
            )
    return tools


# ── 音频：8 个 native operation（handler 表本身就是真源）──────────────────────
_AUDIO_TOOL_DESCRIPTIONS = {
    "convert": ("音频转码", "转成目标格式/采样率/声道。"),
    "merge": ("音频合并", "按顺序拼接多个音频。"),
    "loudness": ("响度测量/标准化", "测量 LUFS 或按目标响度标准化。"),
    "sdr": ("SDR 评估", "对比参考音频计算 SDR 分数。"),
    "midi": ("人声转 MIDI", "从音频提取音高生成 MIDI。"),
    "inspect": ("音频检查", "读取时长/采样率/声道/峰值等。"),
    "slicer": ("音频切片", "按静音或时间切片。"),
    "asr": ("语音识别", "转写音频为文本/字幕。"),
}


def _audio_tool_tools() -> list[dict[str, Any]]:
    try:
        from audio_tools.registry import _HANDLERS
    except Exception:  # pragma: no cover - 依赖缺失时契约仍要能生成
        return []
    tools: list[dict[str, Any]] = []
    for operation in sorted(_HANDLERS):
        title, description = _AUDIO_TOOL_DESCRIPTIONS.get(operation, (operation, ""))
        tools.append(
            _tool(
                f"audio.{operation}",
                title,
                description,
                "audio",
                "audio_tools",
                operation=operation,
                approval="confirm",
                cost="seconds",
                long_running=operation in {"asr", "merge", "slicer"},
                input_schema=_schema(
                    {
                        "operation": {"type": "string", "const": operation},
                        "inputs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "outputDir": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    ["inputs"],
                ),
                produces="file",
                evaluate="media.evaluate",
            )
        )
    return tools


# ── 其余功能：手写但集中（每条都标了 approval / cost）──────────────────────────
def _native_tools() -> list[dict[str, Any]]:
    inputs_array = {"type": "array", "items": {"type": "string"}, "minItems": 1}
    tools = [
        # 模型库
        _tool("model.list", "列出模型", "统一模型库（音频 + 图像 + 视频）及下载状态。", "model", "list_models",
              input_schema=_schema({"query": {"type": "string"}, "modality": {"type": "string"}})),
        _tool("model.inspect", "模型详情", "单个模型的元数据与本地状态。", "model", "model_info",
              input_schema=_schema({"model": {"type": "string"}}, ["model"])),
        _tool("model.download", "下载模型", "下载一个模型（可能几百 MB 到数 GB）。", "model", "download_model",
              approval="explicit", cost="download", long_running=True,
              input_schema=_schema({"modelId": {"type": "string"}, "source": {"type": "string"}}, ["modelId"]),
              produces="file"),
        _tool("model.delete", "删除模型", "删除本地权重文件。", "model", "delete_model",
              approval="explicit", input_schema=_schema({"model": {"type": "string"}}, ["model"])),
        _tool("model.import_custom", "导入自定义模型", "把外部权重注册进分离模型库。", "model", "import_custom_model",
              approval="explicit", cost="seconds", input_schema=_schema({"paths": inputs_array}, ["paths"])),
        _tool("model.inspect_custom", "检查自定义模型", "在不注册的情况下检查权重是否可加载。", "model",
              "inspect_custom_model", cost="seconds", input_schema=_schema({"paths": inputs_array}, ["paths"])),
        _tool("model.storage", "模型存储概览", "各目录占用与可回收空间。", "model", "model_storage_summary"),
        _tool("model.hub", "模型库快照", "统一模型库快照（含环境可用性与下载计数）。", "model", "model_hub",
              input_schema=_schema({"query": {"type": "string"}, "modality": {"type": "string"}})),
        _tool("model.download_file", "按文件下载", "按 URL/路径下载单个模型文件（低层入口）。", "model",
              "model_download", approval="explicit", cost="download", long_running=True,
              input_schema=_schema({"url": {"type": "string"}, "target": {"type": "string"}}, ["url"])),
        _tool("model.cleanup", "清理残留文件", "清理未完成下载留下的临时文件。", "model", "cleanup_model_residual_files",
              approval="confirm"),
        # 运行时
        _tool("runtime.info", "运行时信息", "当前后端、解释器版本、已装环境。", "runtime", "runtime_info"),
        _tool("runtime.install", "安装运行时", "安装指定后端的 Python 运行时（可能数 GB）。", "runtime", "install_runtime",
              approval="explicit", cost="download", long_running=True,
              input_schema=_schema({"backend": {"type": "string", "enum": ["cpu", "cuda", "rocm", "mlx"]}}, ["backend"])),
        _tool("runtime.activate", "切换后端", "激活一个已安装的运行时后端。", "runtime", "activate_runtime",
              approval="confirm", input_schema=_schema({"backend": {"type": "string"}}, ["backend"])),
        _tool("runtime.versions", "运行时版本", "各后端的核心包版本。", "runtime", "runtime_core_versions"),
        _tool("runtime.sizes", "运行时占用", "每个环境的磁盘占用。", "runtime", "runtime_env_sizes"),
        _tool("runtime.optional_status", "可选组件状态", "可选包的安装状态。", "runtime", "optional_package_status"),
        _tool("runtime.optional_install", "安装可选组件", "安装/移除一个可选包（可能下载）。", "runtime",
              "manage_optional_package", approval="explicit", cost="download", long_running=True,
              input_schema=_schema({"action": {"type": "string"}, "name": {"type": "string"}}, ["action"])),
        _tool("runtime.delete", "删除运行时", "删除一个已安装的后端环境。", "runtime", "delete_runtime",
              approval="explicit", input_schema=_schema({"backend": {"type": "string"}}, ["backend"])),
        _tool("runtime.update_core", "更新运行时核心", "更新已安装环境里的核心包（torch 等）。", "runtime",
              "update_runtime_core", approval="explicit", cost="download", long_running=True,
              input_schema=_schema({"backend": {"type": "string"}}, ["backend"])),
        # 插件与图
        _tool("plugin.marketplace", "插件市场", "列出可安装插件。", "plugin", "plugin_marketplace"),
        _tool("plugin.manage", "管理插件", "安装/卸载/更新一个插件。", "plugin", "manage_plugin",
              approval="explicit", cost="seconds", long_running=True,
              input_schema=_schema({"action": {"type": "string"}, "name": {"type": "string"}}, ["action"])),
        _tool("graph.nodes", "图节点列表", "节点编辑器的可用节点。", "graph", "list_nodes"),
        _tool("graph.operations", "图操作列表", "可用 operation 及其描述。", "graph", "list_operations"),
        _tool("graph.operation_spec", "图操作参数", "单个 operation 的输入/输出规格。", "graph", "describe_operation",
              input_schema=_schema({"operation": {"type": "string"}}, ["operation"])),
        _tool("graph.run", "运行图工作流", "执行一个节点图并导出结果。", "graph", "infer_workflow",
              approval="confirm", cost="minutes", long_running=True,
              input_schema=_schema({"workflow": {"type": "object"}, "inputs": inputs_array}, ["inputs"])),
        _tool("graph.run_operation", "执行图操作", "直接执行一个 pymss operation（文件引用输入）。", "graph",
              "run_operation", approval="confirm", cost="minutes", long_running=True,
              input_schema=_schema(
                  {"operation": {"type": "string"}, "inputs": {"type": "object"}, "outputDir": {"type": "string"}},
                  ["operation"],
              ),
              produces="file"),
        # 分离
        _tool("audio.separate", "音频分离", "用一个分离模型把人声/伴奏等拆开。", "audio", "infer",
              approval="confirm", cost="minutes", long_running=True,
              input_schema=_schema({"model": {"type": "string"}, "inputs": inputs_array}, ["model", "inputs"]),
              produces="file"),
        # 编辑器
        _tool("editor.export_mix", "导出混音", "把编辑器工程导出为音频文件。", "editor", "export_editor_mix",
              approval="confirm", cost="seconds", input_schema=_schema({"project": {"type": "object"}}, ["project"]),
              produces="file"),
        _tool("editor.peaks", "波形峰值", "计算波形显示用峰值。", "editor", "waveform_peaks",
              input_schema=_schema({"inputs": inputs_array}, ["inputs"])),
        _tool("media.metadata", "媒体元数据", "读取音频/视频/图片的元数据。", "media", "audio_metadata",
              input_schema=_schema({"inputs": inputs_array}, ["inputs"])),
        _tool("media.library", "媒体库", "列出/刷新媒体库。", "media", "media_library"),
        # 自评价
        _tool("media.evaluate", "评估产物质量", "对产物做客观评估（尺寸/锐度/是否真的变化），供 agent 自检与重试。",
              "media", "evaluate_media", approval="none", cost="seconds",
              input_schema=_schema({"inputs": inputs_array, "reference": {"type": "string"}}, ["inputs"])),
        # 诊断
        _tool("system.env", "环境信息", "解释器/torch/CUDA/运行时与产品声明的版本对账。", "system", "env_info"),
        _tool("system.contract", "Agent 契约", "本契约全文（工具、批准等级、覆盖率审计）。", "system", "agent_contract"),
        _tool("system.health", "健康检查", "worker 版本探活。", "system", "health"),
    ]
    return tools


# 明确不暴露给 agent 的命令（内部/调试用）。每一条都要写清理由。
INTERNAL_COMMANDS: dict[str, str] = {
    "debug_catalog_info": "调试面板专用，读写本地调试目录。",
    "debug_catalog_save": "会覆盖本地调试目录，面向开发者。",
    "debug_catalog_reset": "删除本地调试目录，面向开发者。",
    "debug_model_config": "读取调试配置，面向开发者。",
    "delete_tool_model": "内部工具模型清理，没有稳定语义。",
    "relink_custom_model": "修复注册表路径，属于维护操作。",
    "remap_custom_model_paths": "批量重映射路径，属于维护操作。",
    "unregister_custom_model": "移除注册表条目，属于维护操作。",
    "test_connection": "诊断网络连通性。",
    "health": "由 system.health 覆盖。",
    "list_models": "由 model.list 覆盖。",
    "model_info": "由 model.inspect 覆盖。",
    "download_model": "由 model.download 覆盖。",
    "delete_model": "由 model.delete 覆盖。",
    "import_custom_model": "由 model.import_custom 覆盖。",
    "inspect_custom_model": "由 model.inspect_custom 覆盖。",
    "model_storage_summary": "由 model.storage 覆盖。",
    "cleanup_model_residual_files": "由 model.cleanup 覆盖。",
    "runtime_info": "由 runtime.info 覆盖。",
    "install_runtime": "由 runtime.install 覆盖。",
    "activate_runtime": "由 runtime.activate 覆盖。",
    "runtime_core_versions": "由 runtime.versions 覆盖。",
    "runtime_env_sizes": "由 runtime.sizes 覆盖。",
    "optional_package_status": "由 runtime.optional_status 覆盖。",
    "plugin_marketplace": "由 plugin.marketplace 覆盖。",
    "manage_plugin": "由 plugin.manage 覆盖。",
    "list_nodes": "由 graph.nodes 覆盖。",
    "list_operations": "由 graph.operations 覆盖。",
    "describe_operation": "由 graph.operation_spec 覆盖。",
    "infer_workflow": "由 graph.run 覆盖。",
    "infer": "由 audio.separate 覆盖。",
    "export_editor_mix": "由 editor.export_mix 覆盖。",
    "waveform_peaks": "由 editor.peaks 覆盖。",
    "audio_metadata": "由 media.metadata 覆盖。",
    "media_library": "由 media.library 覆盖。",
    "env_info": "由 system.env 覆盖。",
    "agent_contract": "由 system.contract 覆盖。",
    "evaluate_media": "由 media.evaluate 覆盖。",
    "audio_tools": "由 media.* / audio.* 覆盖（同一个命令，多种 operation）。",
    "media_capabilities": "由契约的 media.* 条目覆盖。",
}


def worker_commands() -> list[str]:
    """从 `worker.py` 源码里数出全部命令名（覆盖率必须来自源码，不能靠人维护）。"""
    text = _WORKER.read_text(encoding="utf-8")
    return sorted(set(re.findall(r'command == "([a-z_]+)"', text)))


def build_contract() -> dict[str, Any]:
    tools = _native_tools() + _media_tools() + _audio_tool_tools()
    names = [tool["name"] for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate tool names in contract: {duplicates}")
    return {
        "version": CONTRACT_VERSION,
        "approvalLevels": APPROVAL_LEVELS,
        "costLevels": COST_LEVELS,
        "tools": tools,
        "audit": audit_contract(tools),
    }


def audit_contract(tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """覆盖率审计：worker 的每个命令要么被某个工具执行，要么在 INTERNAL_COMMANDS 里说明理由。"""
    tools = tools if tools is not None else (_native_tools() + _media_tools() + _audio_tool_tools())
    commands = worker_commands()
    covered = {tool["command"] for tool in tools}
    internal = set(INTERNAL_COMMANDS)
    missing = [command for command in commands if command not in covered and command not in internal]
    unknown_internal = sorted(internal - set(commands))
    return {
        "workerCommands": len(commands),
        "tools": len(tools),
        "available": sum(1 for tool in tools if tool["available"]),
        "missing": missing,
        "internal": {name: INTERNAL_COMMANDS[name] for name in sorted(internal)},
        "staleInternal": unknown_internal,
    }


def cmd_agent_contract(payload: dict[str, Any]) -> int:
    """worker 命令：把整份契约交给调用方（含覆盖率审计）。"""
    from worker_protocol import emit, emit_error

    try:
        emit("agent_contract", build_contract())
        return 0
    except Exception as exc:  # noqa: BLE001
        return emit_error("CONTRACT_FAILED", str(exc))


def find_tool(tool_name: str) -> dict[str, Any]:
    for tool in _native_tools() + _media_tools() + _audio_tool_tools():
        if tool["name"] == tool_name:
            return tool
    raise KeyError(f"unknown tool: {tool_name}")


def dispatch(tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """**进程内**执行一个工具：不起子进程、不走管道、不需要端口。

    这就是"内嵌 agent 与外部 agent 共用同一个执行器"的那一层：
    外部走 MCP → bridge → 作业台账；内嵌走 dispatch → 同一个 worker 命令。
    两边用的是同一份契约里的 `command` + `operation`。

    长任务（`longRunning`）**不在这里执行**：它们需要作业台账、进度与取消，
    必须走 `pipeline_start` / `workflow_start`。这里直接拒绝，避免"内嵌调用
    卡住三十分钟且无法取消"。
    """
    tool = find_tool(tool_name)
    if tool["longRunning"]:
        raise ValueError(
            f"{tool_name} is long-running; submit it through the job ledger instead of dispatch()"
        )
    payload = dict(arguments or {})
    if tool["operation"]:
        payload.setdefault("operation", tool["operation"])

    events: list[dict[str, Any]] = []
    import worker
    import worker_protocol

    def sink(event_type: str, data: dict[str, Any], request_id: str | None, task_id: str | None) -> None:
        events.append({"type": event_type, "payload": data})

    worker_protocol.set_event_sink(sink)
    try:
        code = worker.run_command(tool["command"], payload)
    finally:
        worker_protocol.set_event_sink(None)

    terminal = events[-1] if events else None
    return {
        "tool": tool_name,
        "code": code,
        "events": events,
        "ok": bool(terminal and terminal["type"] != "error"),
        "result": terminal,
    }
