//! 两条版本轴（app / runtime）的检测与"接受运行时"。
//!
//! 这个模块只做四件事：
//!   1. 每次检查都从发布源重新拉取 `versions.json`，避免旧缓存冻结版本；
//!   2. 用它校验签名——没有签名的版本信息一律不采信；
//!   3. 与本地状态比较，给出"要不要更新"的结论；
//!   4. 接受运行时 = 把描述符写成 desired，再由 `runtime_assets` 整包下载替换。
//!
//! 明确不做：增量补丁、数据迁移。1.0.0 之前数据格式变了就直接拒绝。

use std::fs;
use std::path::{Path, PathBuf};

use minisign_verify::{PublicKey, Signature};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::AppHandle;

use crate::error::{AppError, AppResult};
use crate::runtime_assets::{self, RuntimeDescriptor};
use crate::storage;

/// 与 `tauri.conf.json` 的 `plugins.updater.pubkey` 是同一把 key（minisign 公钥，base64）。
const VERSIONS_PUBKEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDk3OTE1MTQyQjEyNzYxMzYKUldRMllTZXhRbEdSbHdKclg2RzBGbmo0ZGJZYzZMNzY1a1d3a3hFN3JXbWs3SkxOK21HTkI4RFMK";

/// 发布源：版本清单与签名放在同一个目录下（`versions.json` + `versions.json.sig`）。
/// 环境变量可覆盖，测试与自建镜像都走这一个入口。
const VERSIONS_URLS: [&str; 2] = [
    "https://github.com/TLBtianhu/mediaforge/releases/download/versions/versions.json",
    "https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist/resolve/master/versions/versions.json",
];

const SIGNED_CACHE_FILE: &str = "versions-cache.json";

/// Keep the original signed bytes together: replacing one file commits both halves.
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct SignedVersions {
    schema_version: u32,
    document: String,
    signature: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct VersionsDoc {
    pub schema_version: u32,
    pub app: AppAxis,
    pub runtime: RuntimeDescriptor,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
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
    parse_document(doc)
}

fn parse_document(doc: &str) -> AppResult<VersionsDoc> {
    let value: Value = serde_json::from_str(doc)
        .map_err(|error| AppError::Worker(format!("versions.json 解析失败：{error}")))?;
    if value.get("schemaVersion").and_then(Value::as_u64) != Some(2) {
        return Err(AppError::Worker(format!(
            "versions.json schemaVersion={}，本程序支持 2",
            value.get("schemaVersion").unwrap_or(&Value::Null)
        )));
    }
    let parsed: VersionsDoc = serde_json::from_value(value)
        .map_err(|error| AppError::Worker(format!("versions.json 解析失败：{error}")))?;
    semver::Version::parse(parsed.app.version.trim())
        .map_err(|error| AppError::Worker(format!("versions.json app.version 无效：{error}")))?;
    runtime_assets::validate_runtime_descriptor(&parsed.runtime).map_err(AppError::Worker)?;
    Ok(parsed)
}

fn read_cached(dir: &Path) -> Option<SignedVersions> {
    let cached: SignedVersions = fs::read_to_string(dir.join(SIGNED_CACHE_FILE))
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())?;
    if cached.schema_version != 1 || cached.document.trim().is_empty() || cached.signature.trim().is_empty() {
        return None;
    }
    Some(cached)
}

fn versions_urls() -> Vec<String> {
    let override_url = std::env::var("MEDIAFORGE_VERSIONS_URL")
        .ok()
        .filter(|value| !value.trim().is_empty());
    match override_url {
        Some(url) => vec![url],
        None => VERSIONS_URLS.iter().map(|item| item.to_string()).collect(),
    }
}

async fn fetch_remote(
    client: &reqwest::Client,
    urls: &[String],
    validate: fn(&str, &str) -> AppResult<VersionsDoc>,
) -> AppResult<(SignedVersions, VersionsDoc)> {
    let mut last_error = String::from("没有可用的版本源");
    for url in urls {
        let doc = match client
            .get(url)
            .header("Cache-Control", "no-cache")
            .send()
            .await
        {
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
        let signature = match client
            .get(format!("{url}.sig"))
            .header("Cache-Control", "no-cache")
            .send()
            .await
        {
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
        // A reachable but invalid mirror must not poison the cache or prevent
        // another configured mirror from supplying a valid signed document.
        match validate(&doc, &signature) {
            Ok(parsed) => {
                return Ok((
                    SignedVersions {
                        schema_version: 1,
                        document: doc,
                        signature,
                    },
                    parsed,
                ))
            }
            Err(error) => last_error = format!("{url}: {error}"),
        }
    }
    Err(AppError::Worker(format!(
        "无法获取 versions.json：{last_error}"
    )))
}

async fn refresh_versions(
    dir: &Path,
    client: &reqwest::Client,
    urls: &[String],
) -> AppResult<VersionsDoc> {
    refresh_versions_validated(dir, client, urls, parse_and_verify).await
}

async fn refresh_versions_validated(
    dir: &Path,
    client: &reqwest::Client,
    urls: &[String],
    validate: fn(&str, &str) -> AppResult<VersionsDoc>,
) -> AppResult<VersionsDoc> {
    let (signed, parsed) = fetch_remote(client, urls, validate).await?;
    // Verification happens before persistence. Failed refreshes leave the last
    // verified pair available for acceptance, but never report it as up-to-date.
    // Surface persistence failures too: accepting must use the version just shown.
    storage::write_json_file(&dir.join(SIGNED_CACHE_FILE), &serde_json::to_value(signed)?)?;
    Ok(parsed)
}

/// 检测两条轴：包括手动检查在内，每次都联网；持久缓存只供后续接受运行时使用。
#[tauri::command]
pub async fn versions_check(app: AppHandle) -> AppResult<Value> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .build()
        .map_err(|error| AppError::Worker(format!("HTTP 客户端初始化失败：{error}")))?;
    let parsed = refresh_versions(&cache_dir(&app)?, &client, &versions_urls()).await?;
    let current_runtime = runtime_assets::desired_runtime(&app)?.asset_id;
    let mut result = compare(&parsed, &current_runtime);
    result["source"] = json!("remote");
    Ok(result)
}

/// 接受远端运行时：写成 desired，下一次 `runtime_asset_install` 会整包下载并切换指针。
#[tauri::command]
pub fn versions_accept_runtime(app: AppHandle) -> AppResult<Value> {
    let Some(cached) = read_cached(&cache_dir(&app)?) else {
        return Err(AppError::Worker(
            "先调用 versions_check 拉取并校验版本信息".into(),
        ));
    };
    let parsed = parse_and_verify(&cached.document, &cached.signature)?;
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
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    };
    use std::thread;
    use std::time::Duration;

    // Public release bytes and public signature only; no signing key, network,
    // existing installation or user profile is needed by these tests.
    fn signed_fixture() -> SignedVersions {
        use base64::Engine as _;
        let encoded = concat!(
            "ew0KICAic2NoZW1hVmVyc2lvbiI6IDEsDQogICJhcHAiOiB7DQogICAgInZlcnNpb24iOiAiMC4wLjMxIg0KICB9LA0KICAicnVudGltZSI6IHsNCiAgICAiYXNzZXRJZCI6ICJydW50aW1lLWN1ZGEtMjAyNi4xMS40IiwNCiAgICAiYmFja2VuZCI6ICJjdWRhIiwNCiAgICAic2hhMjU2IjogImNjYmViNjdkMDExMGRjMWI2YWJjMTkwNmQwNmU3NjM2Yzk5NDVlMzMzNjcyODc0YjQzNzM4ZjUzYjI5ZDAyMWYiLA0KICAgICJzaXplIjogMzYxODIwMDcxNiwNCiAgICAidXJscyI6IFsNCiAgICAgICJodHRwczovL2hmLW1pcnJvci5jb20vZGF0YXNldHMvVExCdGlhbmh1L01lZGlhRm9yZ2UtRGlzdC9yZXNvbHZlL21haW4vcnVudGltZS9ydW50aW1lLWN1ZGEtMjAyNi4xMS40LnppcCIsDQogICAgICAiaHR0cHM6Ly93d3cubW9kZWxzY29wZS5jbi9kYXRhc2V0cy9UTEJ0aWFuaHUvTWVkaWFGb3JnZS1EaXN0L3Jlc29sdmUvbWFzdGVyL3J1bnRpbWUvcnVudGltZS1jdWRhLTIwMjYuMTEuNC56aXAiDQogICAgXQ0KICB9DQp9DQo="
        );
        SignedVersions {
            schema_version: 1,
            document: String::from_utf8(base64::engine::general_purpose::STANDARD.decode(encoded).unwrap()).unwrap(),
            signature: concat!(
                "untrusted comment: signature from tauri secret key\n",
                "RUQ2YSexQlGRlycV4Cmm6MlN6cQS6IgTNrYMZ5agoTSZbplomXMmLL+/9faFq7/IxLwD4TGfoEaeSddfdiVfZVZc+d50BZNHrww=\n",
                "trusted comment: timestamp:1793891135\tfile:versions.json\n",
                "W6GrCBXPPGf5FkPMUNz1j7MziJYQyufLt+Fyu0AeRPwU3MDo75f39qxLNC1u02OBKsprrtyjJuPPf4L12wwvDw==\n",
            ).into(),
        }
    }

    struct TestServer {
        base: String,
        requests: Arc<Mutex<Vec<String>>>,
        stop: Arc<AtomicBool>,
        thread: Option<thread::JoinHandle<()>>,
    }

    impl TestServer {
        fn new(routes: Vec<(String, u16, String)>) -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let base = format!("http://{}", listener.local_addr().unwrap());
            listener.set_nonblocking(true).unwrap();
            let requests = Arc::new(Mutex::new(Vec::new()));
            let stop = Arc::new(AtomicBool::new(false));
            let recorded = Arc::clone(&requests);
            let stopping = Arc::clone(&stop);
            let thread = thread::spawn(move || {
                while !stopping.load(Ordering::Relaxed) {
                    let (mut stream, _) = match listener.accept() {
                        Ok(connection) => connection,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(2));
                            continue;
                        }
                        Err(error) => panic!("local HTTP accept: {error}"),
                    };
                    // Windows accepted sockets inherit the listener's nonblocking
                    // mode. Read timeouts only work after restoring blocking I/O;
                    // otherwise a client still sending headers yields WSAEWOULDBLOCK.
                    stream.set_nonblocking(false).unwrap();
                    stream
                        .set_read_timeout(Some(Duration::from_secs(2)))
                        .unwrap();
                    stream
                        .set_write_timeout(Some(Duration::from_secs(2)))
                        .unwrap();
                    let mut request = Vec::new();
                    let mut buffer = [0u8; 1024];
                    while !request.windows(4).any(|bytes| bytes == b"\r\n\r\n") {
                        let count = stream.read(&mut buffer).unwrap();
                        if count == 0 {
                            break;
                        }
                        request.extend_from_slice(&buffer[..count]);
                    }
                    let request = String::from_utf8_lossy(&request);
                    let path = request.split_whitespace().nth(1).unwrap_or("");
                    recorded.lock().unwrap().push(path.to_string());
                    let (status, body) = routes
                        .iter()
                        .find(|(route, _, _)| route == path)
                        .map(|(_, status, body)| (*status, body.as_str()))
                        .unwrap_or((404, "missing"));
                    write!(stream, "HTTP/1.1 {status} Fixture\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
                }
            });
            Self {
                base,
                requests,
                stop,
                thread: Some(thread),
            }
        }

        fn url(&self, prefix: &str) -> String {
            format!("{}{prefix}/versions.json", self.base)
        }
    }

    impl Drop for TestServer {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::Relaxed);
            if let Err(error) = self.thread.take().unwrap().join() {
                // Retain a fixture panic as a test failure, but do not turn an
                // existing HTTP/assertion failure into a double-panic process abort.
                if !thread::panicking() {
                    std::panic::resume_unwind(error);
                }
            }
        }
    }

    fn routes(prefix: &str, signed: &SignedVersions) -> Vec<(String, u16, String)> {
        vec![
            (
                format!("{prefix}/versions.json"),
                200,
                signed.document.clone(),
            ),
            (
                format!("{prefix}/versions.json.sig"),
                200,
                signed.signature.clone(),
            ),
        ]
    }

    fn client() -> reqwest::Client {
        reqwest::Client::builder()
            .no_proxy()
            .timeout(Duration::from_secs(3))
            .build()
            .unwrap()
    }

    #[test]
    fn fixture_cleanup_preserves_the_primary_failure_without_double_panicking() {
        let failure = std::panic::catch_unwind(|| {
            let _server = TestServer {
                base: String::new(),
                requests: Arc::new(Mutex::new(Vec::new())),
                stop: Arc::new(AtomicBool::new(false)),
                thread: Some(thread::spawn(|| panic!("fixture failure"))),
            };
            panic!("primary HTTP assertion failure");
        })
        .unwrap_err();
        assert_eq!(
            failure.downcast_ref::<&str>(),
            Some(&"primary HTTP assertion failure")
        );
    }

    fn doc(runtime: &str, app: &str) -> VersionsDoc {
        VersionsDoc {
            schema_version: 2,
            app: AppAxis {
                version: app.into(),
            },
            runtime: RuntimeDescriptor {
                asset_id: runtime.into(),
                backend: "cuda".into(),
                python: "3.12".into(),
                sha256: "0".repeat(64),
                size: 1,
                urls: vec!["https://example.invalid/runtime.zip".into()],
            },
        }
    }

    #[test]
    fn newer_app_and_different_runtime_are_reported() {
        let result = compare(
            &doc("runtime-cuda-2026.11.1", "9.9.9"),
            "runtime-cuda-2026.10.2",
        );
        assert_eq!(result["available"]["app"], true);
        assert_eq!(result["available"]["runtime"], true);
    }

    #[test]
    fn same_versions_report_nothing_to_do() {
        let local_app = env!("CARGO_PKG_VERSION");
        let result = compare(
            &doc("runtime-cuda-2026.10.2", local_app),
            "runtime-cuda-2026.10.2",
        );
        assert_eq!(result["available"]["app"], false);
        assert_eq!(result["available"]["runtime"], false);
    }

    #[test]
    fn unsigned_document_is_rejected() {
        let error = parse_and_verify("{\"schemaVersion\":1}", "not-a-signature").unwrap_err();
        assert!(error.to_string().contains("签名"), "{error}");
    }

    #[test]
    fn public_signature_verifies_but_previous_document_schema_is_rejected() {
        let signed = signed_fixture();
        verify(&signed.document, &signed.signature).unwrap();
        assert!(parse_and_verify(&signed.document, &signed.signature).unwrap_err().to_string().contains("schemaVersion"));
        assert!(verify(&signed.document.replace("0.0.31", "0.0.32"), &signed.signature).is_err());
    }

    // No test creates a signing key or signs new bytes. Public signature
    // verification and current-schema semantics are independent tests. The
    // transport/cache seam returns a separately validated current document;
    // production always calls parse_and_verify without this test validator.
    fn public_transport_fixture(document: &str, signature: &str) -> AppResult<VersionsDoc> {
        verify(document, signature)?;
        parse_document(&serde_json::to_string(&doc("runtime-cuda-2026.11.5", "0.0.32"))?)
    }

    async fn refresh_fixture(dir: &Path, client: &reqwest::Client, urls: &[String]) -> AppResult<VersionsDoc> {
        refresh_versions_validated(dir, client, urls, public_transport_fixture).await
    }

    #[test]
    fn current_document_requires_current_python_and_exact_schema() {
        let value = serde_json::to_value(doc("runtime-cuda-2026.11.5", "0.0.32")).unwrap();
        parse_document(&value.to_string()).unwrap();
        for field in ["python", "assetId", "sha256", "size", "urls"] {
            let mut invalid = value.clone();
            invalid["runtime"].as_object_mut().unwrap().remove(field);
            assert!(parse_document(&invalid.to_string()).is_err(), "missing {field}");
        }
        for invalid in [
            { let mut v = value.clone(); v["runtime"]["python"] = json!("3.11"); v },
            { let mut v = value.clone(); v["runtime"]["legacy"] = json!(true); v },
            { let mut v = value.clone(); v["legacy"] = json!(true); v },
            { let mut v = value.clone(); v["schemaVersion"] = json!(1); v },
            { let mut v = value.clone(); v["app"]["version"] = json!("not-semver"); v },
        ] { assert!(parse_document(&invalid.to_string()).is_err(), "{invalid}"); }
    }

    #[test]
    fn production_refresh_rejects_signed_previous_python_descriptor_without_caching_it() {
        let dir = tempfile::tempdir().unwrap();
        let server = TestServer::new(routes("", &signed_fixture()));
        assert!(tauri::async_runtime::block_on(refresh_versions(dir.path(), &client(), &[server.url("")])).is_err());
        assert!(!dir.path().join(SIGNED_CACHE_FILE).exists());
    }

    #[test]
    fn every_check_fetches_remote_even_after_a_valid_cache_was_written() {
        let dir = tempfile::tempdir().unwrap();
        let signed = signed_fixture();
        let server = TestServer::new(routes("", &signed));
        let client = client();
        for _ in 0..2 {
            tauri::async_runtime::block_on(refresh_fixture(
                dir.path(),
                &client,
                &[server.url("")],
            ))
            .unwrap();
        }
        assert_eq!(
            *server.requests.lock().unwrap(),
            vec![
                "/versions.json",
                "/versions.json.sig",
                "/versions.json",
                "/versions.json.sig"
            ]
        );
        let cached = read_cached(dir.path()).unwrap();
        assert_eq!(cached.document, signed.document);
        assert_eq!(cached.signature, signed.signature);
        assert!(!dir.path().join("versions.json").exists());
        assert!(!dir.path().join("versions.json.sig").exists());
    }

    #[test]
    fn corrupt_cached_pair_is_replaced_by_verified_remote_without_blocking_check() {
        for corrupt in ["not-json", r#"{"document":"bad","signature":"bad"}"#] {
            let dir = tempfile::tempdir().unwrap();
            fs::write(dir.path().join(SIGNED_CACHE_FILE), corrupt).unwrap();
            let signed = signed_fixture();
            let server = TestServer::new(routes("", &signed));
            tauri::async_runtime::block_on(refresh_fixture(
                dir.path(),
                &client(),
                &[server.url("")],
            ))
            .unwrap();
            let cached = read_cached(dir.path()).unwrap();
            verify(&cached.document, &cached.signature).unwrap();
        }
    }

    #[test]
    fn loose_cache_files_are_ignored_and_never_mixed_with_the_current_envelope() {
        let dir = tempfile::tempdir().unwrap();
        let signed = signed_fixture();
        fs::write(dir.path().join("versions.json"), &signed.document).unwrap();
        fs::write(dir.path().join("versions.json.sig"), &signed.signature).unwrap();
        assert!(read_cached(dir.path()).is_none());
        storage::write_json_file(&dir.path().join(SIGNED_CACHE_FILE), &serde_json::to_value(&signed).unwrap()).unwrap();
        let cached = read_cached(dir.path()).unwrap();
        assert_eq!(cached.document, signed.document);
        assert_eq!(cached.signature, signed.signature);
        for invalid in [
            json!({"document": signed.document, "signature": signed.signature}),
            json!({"schemaVersion": 2, "document": signed.document, "signature": signed.signature}),
            json!({"schemaVersion": 1, "document": signed.document, "signature": signed.signature, "legacy": true}),
            json!({"schemaVersion": 1, "document": "", "signature": signed.signature}),
            json!({"schemaVersion": 1, "document": signed.document, "signature": 123}),
            Value::Null,
        ] {
            fs::write(dir.path().join(SIGNED_CACHE_FILE), invalid.to_string()).unwrap();
            assert!(read_cached(dir.path()).is_none(), "{invalid}");
        }
        assert_eq!(fs::read_to_string(dir.path().join("versions.json")).unwrap(), signed.document);
        assert_eq!(fs::read_to_string(dir.path().join("versions.json.sig")).unwrap(), signed.signature);
    }

    #[test]
    fn invalid_signature_and_http_failure_preserve_last_verified_cache_but_return_errors() {
        let dir = tempfile::tempdir().unwrap();
        let signed = signed_fixture();
        let mut invalid = signed_fixture();
        invalid.document = invalid.document.replace("0.0.31", "0.0.32");
        let mut responses = routes("/good", &signed);
        responses.extend(routes("/bad", &invalid));
        responses.push(("/offline/versions.json".into(), 503, "offline".into()));
        let server = TestServer::new(responses);
        let client = client();
        tauri::async_runtime::block_on(refresh_fixture(
            dir.path(),
            &client,
            &[server.url("/good")],
        ))
        .unwrap();
        let before = fs::read(dir.path().join(SIGNED_CACHE_FILE)).unwrap();
        for prefix in ["/bad", "/offline"] {
            assert!(tauri::async_runtime::block_on(refresh_fixture(
                dir.path(),
                &client,
                &[server.url(prefix)]
            ))
            .is_err());
            assert_eq!(
                fs::read(dir.path().join(SIGNED_CACHE_FILE)).unwrap(),
                before
            );
        }
    }

    #[test]
    fn invalid_first_mirror_does_not_prevent_verified_second_mirror() {
        let dir = tempfile::tempdir().unwrap();
        let signed = signed_fixture();
        let mut invalid = signed_fixture();
        invalid.signature = "not-a-signature".into();
        let mut responses = routes("/invalid", &invalid);
        responses.extend(routes("/good", &signed));
        let server = TestServer::new(responses);
        tauri::async_runtime::block_on(refresh_fixture(
            dir.path(),
            &client(),
            &[server.url("/invalid"), server.url("/good")],
        ))
        .unwrap();
        assert_eq!(server.requests.lock().unwrap().len(), 4);
        let cached = read_cached(dir.path()).unwrap();
        verify(&cached.document, &cached.signature).unwrap();
    }

    #[test]
    fn persistence_failure_is_not_reported_as_a_successful_check() {
        let dir = tempfile::tempdir().unwrap();
        let blocked = dir.path().join("file-instead-of-directory");
        fs::write(&blocked, "keep").unwrap();
        let server = TestServer::new(routes("", &signed_fixture()));
        assert!(tauri::async_runtime::block_on(refresh_fixture(
            &blocked,
            &client(),
            &[server.url("")]
        ))
        .is_err());
        assert_eq!(fs::read_to_string(blocked).unwrap(), "keep");
    }
}
