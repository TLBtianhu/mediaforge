//! 两条版本轴（app / runtime）的检测与"接受运行时"。
//!
//! 这个模块只做四件事：
//!   1. 拿到一份 **签名过的** `versions.json`（本地缓存优先，否则从发布源拉）；
//!   2. 用它校验签名——没有签名的版本信息一律不采信；
//!   3. 与本地状态比较，给出"要不要更新"的结论；
//!   4. 接受运行时 = 把描述符写成 desired，再由 `runtime_assets` 整包下载替换。
//!
//! 明确不做：增量补丁、数据迁移。1.0.0 之前数据格式变了就直接拒绝。

use std::fs;
use std::path::PathBuf;

use minisign_verify::{PublicKey, Signature};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::AppHandle;

use crate::error::{AppError, AppResult};
use crate::runtime_assets::{self, RuntimeDescriptor};
use crate::storage;

/// 与 `tauri.conf.json` 的 `plugins.updater.pubkey` 是同一把 key（minisign 公钥，base64）。
const VERSIONS_PUBKEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDdGOTFCNEFFRTRGNUJBN0EKUldSNnV2WGtyclNSZjc4Y0Z4aDVGSUNTV0xlSkgwczdhSW9jenNkQXgrQmVZOTIrVVVhWEpLYW4K";

/// 发布源：版本清单与签名放在同一个目录下（`versions.json` + `versions.json.sig`）。
/// 环境变量可覆盖，测试与自建镜像都走这一个入口。
const VERSIONS_URLS: [&str; 2] = [
    "https://github.com/TLBtianhu/mediaforge/releases/download/versions/versions.json",
    "https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/versions/versions.json",
];

const CACHE_FILE: &str = "versions.json";
const CACHE_SIG_FILE: &str = "versions.json.sig";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct VersionsDoc {
    pub schema_version: u32,
    pub app: AppAxis,
    pub runtime: RuntimeDescriptor,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppAxis {
    pub version: String,
}

/// 比较结论。`*_available` 只说明"远端更新"，是否执行由用户决定。
fn compare(doc: &VersionsDoc, current_runtime: &str) -> Value {
    let local_app = env!("CARGO_PKG_VERSION");
    let app_remote = doc.app.version.trim();
    let app_available = match (
        semver::Version::parse(local_app),
        semver::Version::parse(app_remote),
    ) {
        (Ok(local), Ok(remote)) => remote > local,
        _ => app_remote != local_app,
    };
    json!({
        "local": { "app": local_app, "runtime": current_runtime },
        "remote": {
            "app": app_remote,
            "runtime": doc.runtime.asset_id,
        },
        "available": {
            "app": app_available,
            "runtime": doc.runtime.asset_id != current_runtime,
        },
        "schemaVersion": doc.schema_version,
    })
}

fn cache_dir(app: &AppHandle) -> AppResult<PathBuf> {
    Ok(storage::data_root_dir(app)?)
}

/// tauri.conf 里的 pubkey 是"整个公钥文件"的 base64；minisign 只接受里面那行 key。
fn public_key() -> AppResult<PublicKey> {
    use base64::Engine as _;

    let decoded = base64::engine::general_purpose::STANDARD
        .decode(VERSIONS_PUBKEY.trim())
        .map_err(|error| AppError::Worker(format!("versions 公钥不是合法 base64：{error}")))?;
    let text = String::from_utf8(decoded)
        .map_err(|error| AppError::Worker(format!("versions 公钥不是 UTF-8：{error}")))?;
    let key_line = text
        .lines()
        .map(str::trim)
        .find(|line| line.starts_with("RW"))
        .ok_or_else(|| AppError::Worker("versions 公钥文件里没有 key 行".into()))?;
    PublicKey::from_base64(key_line)
        .map_err(|error| AppError::Worker(format!("versions 公钥无效：{error}")))
}

fn verify(doc: &str, signature: &str) -> AppResult<()> {
    let key = public_key()?;
    let signature = Signature::decode(signature)
        .map_err(|error| AppError::Worker(format!("versions 签名格式错误：{error}")))?;
    key.verify(doc.as_bytes(), &signature, false)
        .map_err(|error| AppError::Worker(format!("versions 签名校验失败：{error}")))
}

fn parse_and_verify(doc: &str, signature: &str) -> AppResult<VersionsDoc> {
    verify(doc, signature)?;
    let parsed: VersionsDoc = serde_json::from_str(doc)
        .map_err(|error| AppError::Worker(format!("versions.json 解析失败：{error}")))?;
    if parsed.schema_version != 1 {
        return Err(AppError::Worker(format!(
            "versions.json schemaVersion={}，本程序支持 1",
            parsed.schema_version
        )));
    }
    if parsed.runtime.asset_id.trim().is_empty() || parsed.runtime.sha256.trim().is_empty() {
        return Err(AppError::Worker("versions.json 的 runtime 缺少 assetId/sha256".into()));
    }
    Ok(parsed)
}

fn read_cached(app: &AppHandle) -> Option<(String, String)> {
    let dir = cache_dir(app).ok()?;
    let doc = fs::read_to_string(dir.join(CACHE_FILE)).ok()?;
    let sig = fs::read_to_string(dir.join(CACHE_SIG_FILE)).ok()?;
    Some((doc, sig))
}

async fn fetch_remote() -> AppResult<(String, String)> {
    let override_url = std::env::var("MEDIAFORGE_VERSIONS_URL").ok().filter(|value| !value.trim().is_empty());
    let urls: Vec<String> = match override_url {
        Some(url) => vec![url],
        None => VERSIONS_URLS.iter().map(|item| item.to_string()).collect(),
    };
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .build()
        .map_err(|error| AppError::Worker(format!("HTTP 客户端初始化失败：{error}")))?;
    let mut last_error = String::from("没有可用的版本源");
    for url in urls {
        let doc = match client.get(&url).send().await {
            Ok(response) if response.status().is_success() => match response.text().await {
                Ok(text) => text,
                Err(error) => {
                    last_error = format!("{url}: 读取响应失败 {error}");
                    continue;
                }
            },
            Ok(response) => {
                last_error = format!("{url}: HTTP {}", response.status());
                continue;
            }
            Err(error) => {
                last_error = format!("{url}: {error}");
                continue;
            }
        };
        let signature = match client.get(format!("{url}.sig")).send().await {
            Ok(response) if response.status().is_success() => match response.text().await {
                Ok(text) => text,
                Err(error) => {
                    last_error = format!("{url}.sig: 读取响应失败 {error}");
                    continue;
                }
            },
            Ok(response) => {
                last_error = format!("{url}.sig: HTTP {}", response.status());
                continue;
            }
            Err(error) => {
                last_error = format!("{url}.sig: {error}");
                continue;
            }
        };
        return Ok((doc, signature));
    }
    Err(AppError::Worker(format!("无法获取 versions.json：{last_error}")))
}

/// 检测三条轴。缓存命中且签名有效时不联网；否则拉取并写回缓存。
#[tauri::command]
pub async fn versions_check(app: AppHandle) -> AppResult<Value> {
    let cached = read_cached(&app);
    let (doc, signature, source) = match cached {
        Some((doc, signature)) => (doc, signature, "cache"),
        None => {
            let (doc, signature) = fetch_remote().await?;
            if let Ok(dir) = cache_dir(&app) {
                let _ = fs::write(dir.join(CACHE_FILE), &doc);
                let _ = fs::write(dir.join(CACHE_SIG_FILE), &signature);
            }
            (doc, signature, "remote")
        }
    };
    let parsed = parse_and_verify(&doc, &signature)?;
    let current_runtime = runtime_assets::desired_runtime(&app)?
        .asset_id;
    let mut result = compare(&parsed, &current_runtime);
    result["source"] = json!(source);
    Ok(result)
}

/// 接受远端运行时：写成 desired，下一次 `runtime_asset_install` 会整包下载并切换指针。
#[tauri::command]
pub fn versions_accept_runtime(app: AppHandle) -> AppResult<Value> {
    let Some((doc, signature)) = read_cached(&app) else {
        return Err(AppError::Worker(
            "先调用 versions_check 拉取并校验版本信息".into(),
        ));
    };
    let parsed = parse_and_verify(&doc, &signature)?;
    runtime_assets::write_desired(&app, &parsed.runtime)?;
    Ok(json!({
        "accepted": true,
        "runtime": parsed.runtime.asset_id,
        "size": parsed.runtime.size,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(runtime: &str, app: &str) -> VersionsDoc {
        VersionsDoc {
            schema_version: 1,
            app: AppAxis { version: app.into() },
            runtime: RuntimeDescriptor {
                asset_id: runtime.into(),
                backend: "cuda".into(),
                sha256: "0".repeat(64),
                size: 1,
                urls: vec!["https://example.invalid/runtime.zip".into()],
            },
        }
    }

    #[test]
    fn newer_app_and_different_runtime_are_reported() {
        let result = compare(&doc("runtime-cuda-2026.11.1", "9.9.9"), "runtime-cuda-2026.10.2");
        assert_eq!(result["available"]["app"], true);
        assert_eq!(result["available"]["runtime"], true);
    }

    #[test]
    fn same_versions_report_nothing_to_do() {
        let local_app = env!("CARGO_PKG_VERSION");
        let result = compare(&doc("runtime-cuda-2026.10.2", local_app), "runtime-cuda-2026.10.2");
        assert_eq!(result["available"]["app"], false);
        assert_eq!(result["available"]["runtime"], false);
    }

    #[test]
    fn unsigned_document_is_rejected() {
        let error = parse_and_verify("{\"schemaVersion\":1}", "not-a-signature").unwrap_err();
        assert!(error.to_string().contains("签名"), "{error}");
    }
}
