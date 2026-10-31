//! 会话与存储命令：代理设置、应用路径、app store 与 workflow store。
//!
//! 从 app_cmd.rs 拆出。store 的写入仍然经过 storage，权限与原子性不变。

use crate::error::{AppError, AppResult};
use crate::python::worker::{run_worker_once, run_worker_with_payload};
use crate::session_log;
use crate::state::{AppState, ProxySettings};
use crate::storage;
use crate::workflow_editors::{self, WorkflowEditors};
use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Emitter, State};

use crate::commands::app_cmd::value_type;

#[tauri::command]
pub async fn update_proxy_settings(
    state: State<'_, AppState>,
    payload: Value,
) -> AppResult<Value> {
    let mode = payload
        .get("mode")
        .and_then(Value::as_str)
        .unwrap_or("system")
        .to_string();
    let url = payload
        .get("url")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let bypass = payload
        .get("bypass")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    validate_proxy_settings(&mode, &url)?;
    let next = ProxySettings { mode, url, bypass };
    {
        let mut guard = state
            .proxy_settings
            .lock()
            .map_err(|_| AppError::Worker("proxy_settings lock poisoned".into()))?;
        *guard = next.clone();
    }
    Ok(serde_json::json!({ "ok": true, "mode": next.mode }))
}

fn validate_proxy_settings(mode: &str, url: &str) -> AppResult<()> {
    if !matches!(mode, "system" | "none" | "custom") {
        return Err(AppError::Worker("INVALID_PROXY_MODE: unsupported proxy mode".into()));
    }
    if mode != "custom" {
        return Ok(());
    }
    let normalized = if url.contains("://") {
        url.trim().to_string()
    } else if url.trim().is_empty() {
        String::new()
    } else {
        format!("http://{}", url.trim())
    };
    let (scheme, authority) = normalized
        .split_once("://")
        .ok_or_else(|| AppError::Worker("INVALID_PROXY_URL: missing proxy scheme".into()))?;
    if !matches!(scheme.to_ascii_lowercase().as_str(), "http" | "https" | "socks5" | "socks5h") {
        return Err(AppError::Worker("UNSUPPORTED_PROXY_SCHEME: unsupported proxy scheme".into()));
    }
    let authority = authority.split('/').next().unwrap_or("");
    let host_port = authority.rsplit('@').next().unwrap_or("");
    let (host, port) = if host_port.starts_with('[') {
        let end = host_port
            .find(']')
            .ok_or_else(|| AppError::Worker("INVALID_PROXY_URL: invalid IPv6 host".into()))?;
        let port = host_port
            .get(end + 1..)
            .unwrap_or("")
            .strip_prefix(':')
            .unwrap_or("");
        (&host_port[1..end], port)
    } else {
        host_port
            .rsplit_once(':')
            .ok_or_else(|| AppError::Worker("INVALID_PROXY_PORT: proxy port is required".into()))?
    };
    if host.is_empty() {
        return Err(AppError::Worker("INVALID_PROXY_URL: proxy host is missing".into()));
    }
    let port = port
        .parse::<u16>()
        .map_err(|_| AppError::Worker("INVALID_PROXY_PORT: proxy port is invalid".into()))?;
    if port == 0 {
        return Err(AppError::Worker("INVALID_PROXY_PORT: proxy port is invalid".into()));
    }
    Ok(())
}

#[tauri::command]
pub async fn test_proxy_connection(app: AppHandle, payload: Value) -> AppResult<Value> {
    run_worker_with_payload(&app, "test_connection", Some(payload))
}

#[tauri::command]
pub async fn get_app_paths(app: AppHandle) -> AppResult<storage::AppPathsPayload> {
    session_log::append(&app, "DEBUG", "app.paths_requested", Vec::new());
    storage::app_paths_payload(&app)
}

#[tauri::command]
pub async fn load_app_store(app: AppHandle, name: String) -> AppResult<Value> {
    session_log::append(&app, "DEBUG", "app.store.load", vec![("name", name.clone())]);
    storage::read_app_store(&app, &name)
}

#[tauri::command]
pub async fn save_app_store(app: AppHandle, name: String, data: Value) -> AppResult<()> {
    if name == "workflow-state" {
        return Err(AppError::Worker("workflow-state requires mutate_workflow_store".into()));
    }
    session_log::append(
        &app,
        "DEBUG",
        "app.store.save",
        vec![("name", name.clone()), ("valueType", value_type(&data).to_string())],
    );
    storage::write_app_store(&app, &name, &data)?;
    if name == "app-settings" {
        session_log::set_developer_mode(
            data.get("developerMode").and_then(Value::as_bool) == Some(true),
        );
    }
    Ok(())
}

#[tauri::command]
pub async fn mutate_workflow_store(
    app: AppHandle,
    window: tauri::WebviewWindow,
    editors: State<'_, WorkflowEditors>,
    payload: Value,
) -> AppResult<Value> {
    session_log::append(
        &app,
        "DEBUG",
        "workflow.store.mutate",
        vec![(
            "action",
            payload
                .get("action")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
        )],
    );
    // Ownership validation, persistence, and first-save identity adoption are
    // one operation. Opening a second editor cannot slip between those steps.
    let (mut response, changed, snapshot) = {
        let mut registry = editors.registry();
        let (response, changed) = registry.mutate(window.label(), &payload, || {
            storage::mutate_workflow_store(&app, &payload)
        })?;
        (response, changed, registry.snapshot())
    };
    response["editorSnapshot"] = serde_json::to_value(&snapshot)?;
    if let Some(snapshot) = changed {
        workflow_editors::emit_snapshot(&app, &snapshot);
    }
    Ok(response)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BuildInfo {
    version: &'static str,
    git_commit: &'static str,
    git_tag: &'static str,
    git_ref: &'static str,
    run_id: &'static str,
    run_attempt: &'static str,
    repository: &'static str,
    repository_owner: &'static str,
    build_time: &'static str,
    target: &'static str,
    variant: &'static str,
    official: bool,
}


#[tauri::command]
pub fn get_build_info() -> BuildInfo {
    BuildInfo {
        version: env!("CARGO_PKG_VERSION"),
        git_commit: option_env!("PYMSS_BUILD_GIT_COMMIT").unwrap_or(""),
        git_tag: option_env!("PYMSS_BUILD_GIT_TAG").unwrap_or(""),
        git_ref: option_env!("PYMSS_BUILD_GIT_REF").unwrap_or(""),
        run_id: option_env!("PYMSS_BUILD_RUN_ID").unwrap_or(""),
        run_attempt: option_env!("PYMSS_BUILD_RUN_ATTEMPT").unwrap_or(""),
        repository: option_env!("PYMSS_BUILD_REPOSITORY").unwrap_or(""),
        repository_owner: option_env!("PYMSS_BUILD_REPOSITORY_OWNER").unwrap_or(""),
        build_time: option_env!("PYMSS_BUILD_TIME").unwrap_or(""),
        target: option_env!("PYMSS_BUILD_TARGET").unwrap_or("dev"),
        variant: option_env!("PYMSS_BUILD_VARIANT").unwrap_or("development"),
        official: option_env!("PYMSS_BUILD_OFFICIAL") == Some("true"),
    }
}

#[tauri::command]
pub fn write_frontend_log(app: AppHandle, window: tauri::WebviewWindow, record: Value) -> AppResult<()> {
    let mut record = crate::log_record::LogRecord::from_payload(&record)
        .ok_or_else(|| AppError::Worker("Invalid frontend log record".into()))?;
    record.source = "studio".into();
    record.context.insert("windowLabel".into(), Value::String(window.label().into()));
    session_log::append_record(&app, record);
    Ok(())
}




#[tauri::command]
pub async fn get_env_info(app: AppHandle) -> AppResult<Value> {
    run_worker_once(&app, "env_info")
}

#[tauri::command]
pub async fn start_env_check(app: AppHandle, request_id: String) -> AppResult<Value> {
    let handle = app.clone();
    std::thread::spawn(move || {
        let result = run_worker_once(&handle, "env_info");
        match result {
            Ok(payload) => {
                let _ = handle.emit(
                    "pymss://worker-event",
                    serde_json::json!({
                        "type": "env_info",
                        "requestId": request_id,
                        "taskId": Value::Null,
                        "timestamp": Value::Null,
                        "payload": payload,
                    }),
                );
            }
            Err(error) => {
                let _ = handle.emit(
                    "pymss://worker-event",
                    serde_json::json!({
                        "type": "error",
                        "requestId": request_id,
                        "taskId": Value::Null,
                        "timestamp": Value::Null,
                        "payload": {
                            "code": "ENV_CHECK_FAILED",
                            "message": error.to_string(),
                            "recoverable": true,
                        },
                    }),
                );
            }
        }
    });

    Ok(serde_json::json!({ "started": true }))
}
