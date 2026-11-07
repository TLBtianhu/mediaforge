//! Runtime publication: serialize writers and publish only fully verified directories.
use super::{marker_matches, RuntimeDescriptor, ASSET_MARKER};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::Path;

pub(super) struct InstallLock {
    _file: File,
}

impl InstallLock {
    pub(super) fn acquire(envs: &Path) -> Result<Self, String> {
        ensure_plain(envs)?;
        fs::create_dir_all(envs).map_err(|error| error.to_string())?;
        let path = envs.join(".runtime-asset-install.lock");
        ensure_plain(&path)?;
        let mut options = OpenOptions::new();
        options.create(true).truncate(false).read(true).write(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            options.share_mode(0);
        }
        let file = options.open(path).map_err(|error| {
            if matches!(error.raw_os_error(), Some(32 | 33)) {
                "运行时正在安装或激活，请等待当前操作完成后重试".to_string()
            } else {
                format!("无法取得运行时安装锁：{error}")
            }
        })?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            // Keep the file open and never unlink it: all processes must lock one inode.
            if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
                return Err("运行时正在安装或激活，请等待当前操作完成后重试".into());
            }
        }
        Ok(Self { _file: file })
    }
}

#[cfg(unix)]
impl Drop for InstallLock {
    fn drop(&mut self) {
        use std::os::fd::AsRawFd;
        unsafe {
            libc::flock(self._file.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

pub(super) fn validate_descriptor(descriptor: &RuntimeDescriptor) -> Result<(), String> {
    if descriptor.python != super::expected_python()? {
        return Err("运行时 Python 版本必须与目标 3.12 一致，不能使用旧 ABI".into());
    }
    let id = descriptor.asset_id.as_bytes();
    if id.is_empty()
        || id.len() > 160
        || !id[0].is_ascii_alphanumeric()
        || !id[id.len() - 1].is_ascii_alphanumeric()
        || !id
            .iter()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_' | b'.'))
    {
        return Err("运行时 assetId 必须是安全的单一目录名".into());
    }
    if descriptor.sha256.len() != 64 || !descriptor.sha256.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("运行时 SHA-256 格式无效".into());
    }
    if descriptor.size == 0
        || !matches!(descriptor.backend.as_str(), "cpu" | "cuda" | "rocm" | "mlx")
        || descriptor.urls.is_empty()
    {
        return Err("运行时描述符缺少大小、后端或下载源".into());
    }
    for source in &descriptor.urls {
        let url =
            url::Url::parse(source).map_err(|error| format!("运行时下载地址无效：{error}"))?;
        if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
            return Err("运行时下载地址必须使用 HTTP 或 HTTPS".into());
        }
    }
    Ok(())
}

pub(super) fn ready(target: &Path, descriptor: &RuntimeDescriptor) -> bool {
    ensure_plain(target).is_ok()
        && validate_descriptor(descriptor).is_ok()
        && super::validate_asset_layout(target, descriptor).is_ok()
        && marker_matches(target, descriptor)
}

/// Refuse links before deleting or replacing anything owned by the installer.
pub(super) fn ensure_plain(path: &Path) -> Result<(), String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error.to_string()),
    };
    #[cfg(windows)]
    let reparse = {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    };
    #[cfg(not(windows))]
    let reparse = false;
    if metadata.file_type().is_symlink() || reparse {
        return Err(format!("运行时资产路径不能是链接：{}", path.display()));
    }
    Ok(())
}

fn ensure_plain_tree(root: &Path) -> Result<(), String> {
    ensure_plain(root)?;
    if root.is_dir() {
        for entry in fs::read_dir(root).map_err(|error| error.to_string())? {
            ensure_plain_tree(&entry.map_err(|error| error.to_string())?.path())?;
        }
    }
    Ok(())
}

pub(super) fn cleanup_staging(staging: &Path) -> Result<(), String> {
    ensure_plain_tree(staging)?;
    if staging.exists() {
        fs::remove_dir_all(staging).map_err(|error| error.to_string())?;
    }
    Ok(())
}

pub(super) fn recover_previous(staging: &Path, target: &Path) -> Result<(), String> {
    ensure_plain(staging)?;
    ensure_plain(target)?;
    let previous = staging.join("previous-runtime");
    ensure_plain(&previous)?;
    if previous.exists() && !target.exists() {
        fs::rename(previous, target).map_err(|error| format!("恢复旧运行时失败：{error}"))?;
    }
    Ok(())
}

const POINTER_RECOVERY: &str = "active-runtime.rollback.json";

fn persist_bytes(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path.parent().ok_or("激活指针没有父目录")?;
    let mut temporary =
        tempfile::NamedTempFile::new_in(parent).map_err(|error| error.to_string())?;
    temporary
        .write_all(bytes)
        .map_err(|error| error.to_string())?;
    temporary
        .as_file()
        .sync_all()
        .map_err(|error| error.to_string())?;
    temporary.persist(path).map_err(|error| error.to_string())?;
    Ok(())
}

pub(super) fn recover_pointer(envs: &Path) -> Result<(), String> {
    let pointer = envs.join("active-runtime.json");
    let recovery = envs.join(POINTER_RECOVERY);
    ensure_plain(&pointer)?;
    ensure_plain(&recovery)?;
    let pointer_valid = fs::read(&pointer)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
        .is_some();
    if !pointer_valid && recovery.exists() {
        if pointer.exists() {
            fs::remove_file(&pointer).map_err(|error| error.to_string())?;
        }
        fs::rename(&recovery, &pointer).map_err(|error| {
            format!(
                "恢复激活指针失败，备份保留在 {}：{error}",
                recovery.display()
            )
        })?;
    }
    Ok(())
}

pub(super) fn activate_with_rollback(
    envs: &Path,
    activate: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    let pointer = envs.join("active-runtime.json");
    let recovery = envs.join(POINTER_RECOVERY);
    ensure_plain(&pointer)?;
    ensure_plain(&recovery)?;
    let old = match fs::read(&pointer) {
        Ok(bytes) => Some(bytes),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error.to_string()),
    };
    if let Some(bytes) = &old {
        // ReplaceFileW may move the old destination before returning 1176/1177.
        // Keep an independent synced checkpoint until activation has succeeded.
        persist_bytes(&recovery, bytes)?;
    }
    if let Err(error) = activate() {
        let restored = match old {
            Some(bytes) if fs::read(&pointer).ok().as_ref() == Some(&bytes) => Ok(()),
            Some(_) => {
                // Reuse the synced checkpoint rather than allocating another copy:
                // the original error may have been caused by a full disk.
                match fs::remove_file(&pointer) {
                    Ok(()) => {}
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                    Err(restore) => {
                        return Err(format!(
                            "激活失败：{error}；指针恢复失败：{restore}；备份保留在 {}",
                            recovery.display()
                        ))
                    }
                }
                fs::rename(&recovery, &pointer).map_err(|error| error.to_string())
            }
            None => match fs::remove_file(&pointer) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
                Err(error) => Err(error.to_string()),
            },
        };
        if let Err(restore) = restored {
            return Err(format!(
                "激活失败：{error}；指针恢复失败，备份保留在 {}：{restore}",
                recovery.display()
            ));
        }
        let _ = fs::remove_file(&recovery);
        return Err(error);
    }
    let _ = fs::remove_file(&recovery);
    Ok(())
}

pub(super) fn publish_archive(
    envs: &Path,
    staging: &Path,
    descriptor: &RuntimeDescriptor,
    archive: &Path,
    verify: impl FnOnce(&Path) -> Result<(), String>,
    activate: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    validate_descriptor(descriptor)?;
    ensure_plain(staging)?;
    ensure_plain(archive)?;
    // A killed process can leave an extraction behind. Retry reclaims it rather
    // than accumulating another full copy of a multi-GB runtime.
    for entry in fs::read_dir(staging).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        if entry
            .file_name()
            .to_string_lossy()
            .starts_with(".unpacked-")
        {
            cleanup_staging(&entry.path())?;
        }
    }
    let prepared = tempfile::Builder::new()
        .prefix(".unpacked-")
        .tempdir_in(staging)
        .map_err(|error| error.to_string())?;
    super::extract_zip(archive, prepared.path()).map_err(|error| error.to_string())?;
    verify(prepared.path())?;
    super::validate_asset_layout(prepared.path(), descriptor)?;
    crate::storage::write_json_file(
        &prepared.path().join(ASSET_MARKER),
        &serde_json::json!({"schemaVersion": 2, "assetId": descriptor.asset_id, "sha256": descriptor.sha256, "python": descriptor.python}),
    )
    .map_err(|error| error.to_string())?;
    commit_prepared(envs, staging, descriptor, prepared.path(), activate)
}

fn commit_prepared(
    envs: &Path,
    staging: &Path,
    descriptor: &RuntimeDescriptor,
    prepared: &Path,
    activate: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    let target = envs.join(&descriptor.asset_id);
    let previous = staging.join("previous-runtime");
    ensure_plain_tree(&target)?;
    ensure_plain(&previous)?;
    if previous.exists() {
        return Err(format!("已有待恢复运行时，请先检查 {}", previous.display()));
    }
    let replacing = target.exists();
    if replacing {
        fs::rename(&target, &previous)
            .map_err(|error| format!("旧运行时仍在使用或无法替换：{error}"))?;
    }
    let publish = fs::rename(prepared, &target).map_err(|error| error.to_string());
    if let Err(error) = publish {
        if replacing {
            fs::rename(&previous, &target).map_err(|restore| {
                format!(
                    "提交失败：{error}；旧运行时保留在 {}：{restore}",
                    previous.display()
                )
            })?;
        }
        return Err(error);
    }
    if let Err(error) = activate_with_rollback(envs, activate) {
        // Restore the old directory as well; a failed rollback retains the backup.
        fs::rename(&target, prepared).map_err(|restore| {
            format!(
                "激活失败：{error}；恢复目录失败，旧运行时保留在 {}：{restore}",
                previous.display()
            )
        })?;
        if replacing {
            fs::rename(&previous, &target).map_err(|restore| {
                format!(
                    "激活失败：{error}；旧运行时保留在 {}：{restore}",
                    previous.display()
                )
            })?;
        }
        return Err(error);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn descriptor() -> RuntimeDescriptor {
        RuntimeDescriptor {
            asset_id: "runtime-fixture".into(),
            backend: "cuda".into(),
            python: "3.12".into(),
            sha256: "0".repeat(64),
            size: 1,
            urls: vec!["http://127.0.0.1/fixture.zip".into()],
        }
    }

    fn archive(staging: &Path, name: &str) -> std::path::PathBuf {
        fs::create_dir_all(staging).unwrap();
        let path = staging.join("runtime.zip");
        let mut zip = zip::ZipWriter::new(File::create(&path).unwrap());
        zip.start_file(name, zip::write::SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"fixture-interpreter").unwrap();
        if name == "Scripts/python.exe" {
            for name in [
                "Scripts/python312.dll",
                "Lib/encodings/__init__.py",
                "Lib/site-packages/.keep",
            ] {
                zip.start_file(name, zip::write::SimpleFileOptions::default())
                    .unwrap();
                zip.write_all(b"tiny ABI layout fixture").unwrap();
            }
        }
        zip.finish().unwrap();
        path
    }

    fn seed_old(envs: &Path, descriptor: &RuntimeDescriptor) -> std::path::PathBuf {
        let target = envs.join(&descriptor.asset_id);
        fs::create_dir_all(target.join("Scripts")).unwrap();
        fs::write(target.join("Scripts/python.exe"), b"old-interpreter").unwrap();
        fs::write(envs.join("active-runtime.json"), b"old-pointer").unwrap();
        target
    }

    #[test]
    fn installation_lock_excludes_another_writer_and_releases_on_drop() {
        let root = tempfile::tempdir().unwrap();
        let first = InstallLock::acquire(root.path()).unwrap();
        assert!(InstallLock::acquire(root.path()).is_err());
        drop(first);
        InstallLock::acquire(root.path()).unwrap();
    }

    #[test]
    fn installation_lock_child() {
        let Some(root) = std::env::var_os("MEDIAFORGE_TEST_INSTALL_LOCK_ROOT") else {
            return;
        };
        let root = std::path::PathBuf::from(root);
        let _lock = InstallLock::acquire(&root).unwrap();
        fs::write(root.join("child-ready"), b"locked").unwrap();
        let mut input = String::new();
        std::io::stdin().read_line(&mut input).unwrap();
    }

    #[test]
    fn installation_lock_survives_process_contention_and_releases_after_crash() {
        use std::process::Stdio;
        let root = tempfile::tempdir().unwrap();
        let mut command = crate::process_command::hidden_command(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "runtime_assets::transaction::tests::installation_lock_child",
            ])
            .env("MEDIAFORGE_TEST_INSTALL_LOCK_ROOT", root.path())
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        let mut child = command.spawn().unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !root.path().join("child-ready").is_file() && std::time::Instant::now() < deadline {
            if child.try_wait().unwrap().is_some() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let ready = root.path().join("child-ready").is_file();
        let contended = InstallLock::acquire(root.path()).is_err();
        let _ = child.kill();
        child.wait().unwrap();
        assert!(ready, "child never acquired the installation lock");
        assert!(contended);
        InstallLock::acquire(root.path()).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn helper_job_terminates_download_owner_and_releases_its_lock() {
        use std::process::Stdio;
        let root = tempfile::tempdir().unwrap();
        let mut child = crate::process_command::hidden_command(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "runtime_assets::transaction::tests::installation_lock_child",
            ])
            .env("MEDIAFORGE_TEST_INSTALL_LOCK_ROOT", root.path())
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let job = crate::process_job::ProcessJob::attach(&child).unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !root.path().join("child-ready").is_file() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let ready = root.path().join("child-ready").is_file();
        drop(job); // Same kernel action occurs when the owning application exits/crashes.
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while child.try_wait().unwrap().is_none() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        let stopped = child.try_wait().unwrap().is_some();
        if !stopped {
            let _ = child.kill();
        }
        child.wait().unwrap();
        assert!(ready);
        assert!(stopped, "helper kept running after its job was closed");
        InstallLock::acquire(root.path()).unwrap();
    }

    #[test]
    fn invalid_descriptor_cannot_select_another_directory() {
        let mut value = descriptor();
        for id in ["../outside", "C:/outside", ".", "asset.", "", "a/b"] {
            value.asset_id = id.into();
            assert!(validate_descriptor(&value).is_err(), "{id}");
        }
    }

    #[test]
    fn verification_failure_preserves_existing_runtime_and_pointer() {
        let root = tempfile::tempdir().unwrap();
        let descriptor = descriptor();
        let target = seed_old(root.path(), &descriptor);
        let staging = root.path().join(".incoming");
        let zip = archive(&staging, "Scripts/python.exe");
        let error = publish_archive(
            root.path(),
            &staging,
            &descriptor,
            &zip,
            |_| Err("interpreter self-check failed".into()),
            || panic!("must not activate"),
        )
        .unwrap_err();
        assert!(error.contains("self-check"));
        assert_eq!(
            fs::read(target.join("Scripts/python.exe")).unwrap(),
            b"old-interpreter"
        );
        assert_eq!(
            fs::read(root.path().join("active-runtime.json")).unwrap(),
            b"old-pointer"
        );
        assert_eq!(fs::read_dir(staging).unwrap().count(), 1); // Keep only the resumable archive.
    }

    #[test]
    fn activation_failure_restores_old_runtime_and_keeps_pointer() {
        let root = tempfile::tempdir().unwrap();
        let descriptor = descriptor();
        let target = seed_old(root.path(), &descriptor);
        let staging = root.path().join(".incoming");
        let zip = archive(&staging, "Scripts/python.exe");
        let error = publish_archive(
            root.path(),
            &staging,
            &descriptor,
            &zip,
            |_| Ok(()),
            || Err("disk full".into()),
        )
        .unwrap_err();
        assert_eq!(error, "disk full");
        assert_eq!(
            fs::read(target.join("Scripts/python.exe")).unwrap(),
            b"old-interpreter"
        );
        assert_eq!(
            fs::read(root.path().join("active-runtime.json")).unwrap(),
            b"old-pointer"
        );
        assert!(!staging.join("previous-runtime").exists());
        assert_eq!(fs::read_dir(staging).unwrap().count(), 1);
    }

    #[test]
    fn partial_pointer_replacement_error_restores_both_directory_and_pointer() {
        for delete_pointer in [true, false] {
            let root = tempfile::tempdir().unwrap();
            let descriptor = descriptor();
            let target = seed_old(root.path(), &descriptor);
            let staging = root.path().join(".incoming");
            let zip = archive(&staging, "Scripts/python.exe");
            let error = publish_archive(
                root.path(),
                &staging,
                &descriptor,
                &zip,
                |_| Ok(()),
                || {
                    let pointer = root.path().join("active-runtime.json");
                    if delete_pointer {
                        fs::remove_file(pointer).unwrap();
                    } else {
                        fs::write(pointer, b"partial-pointer").unwrap();
                    }
                    Err("partial replacement failed".into())
                },
            )
            .unwrap_err();
            assert_eq!(error, "partial replacement failed");
            assert_eq!(
                fs::read(root.path().join("active-runtime.json")).unwrap(),
                b"old-pointer"
            );
            assert_eq!(
                fs::read(target.join("Scripts/python.exe")).unwrap(),
                b"old-interpreter"
            );
            assert!(!root.path().join(POINTER_RECOVERY).exists());
        }
    }

    #[test]
    fn first_activation_failure_does_not_leave_a_dangling_pointer() {
        let root = tempfile::tempdir().unwrap();
        assert!(activate_with_rollback(root.path(), || {
            fs::write(root.path().join("active-runtime.json"), b"new-pointer").unwrap();
            Err("failed".into())
        })
        .is_err());
        assert!(!root.path().join("active-runtime.json").exists());
    }

    #[test]
    fn interrupted_partial_pointer_replacement_recovers_checkpoint() {
        for partial in [false, true] {
            let root = tempfile::tempdir().unwrap();
            fs::write(root.path().join(POINTER_RECOVERY), b"old-pointer").unwrap();
            if partial {
                fs::write(root.path().join("active-runtime.json"), b"truncated-json").unwrap();
            }
            recover_pointer(root.path()).unwrap();
            assert_eq!(
                fs::read(root.path().join("active-runtime.json")).unwrap(),
                b"old-pointer"
            );
            assert!(!root.path().join(POINTER_RECOVERY).exists());
        }
    }

    #[test]
    fn marker_and_interpreter_are_complete_before_pointer_is_changed() {
        let root = tempfile::tempdir().unwrap();
        let descriptor = descriptor();
        let target = seed_old(root.path(), &descriptor);
        let staging = root.path().join(".incoming");
        let zip = archive(&staging, "Scripts/python.exe");
        publish_archive(
            root.path(),
            &staging,
            &descriptor,
            &zip,
            |prepared| {
                assert_eq!(
                    fs::read(target.join("Scripts/python.exe")).unwrap(),
                    b"old-interpreter"
                );
                assert_eq!(
                    fs::read(prepared.join("Scripts/python.exe")).unwrap(),
                    b"fixture-interpreter"
                );
                assert!(!prepared.join(ASSET_MARKER).exists());
                Ok(())
            },
            || {
                assert!(ready(&target, &descriptor));
                assert_eq!(
                    fs::read(staging.join("previous-runtime/Scripts/python.exe")).unwrap(),
                    b"old-interpreter"
                );
                super::super::write_active_pointer(root.path(), &descriptor)
            },
        )
        .unwrap();
        let pointer: serde_json::Value =
            serde_json::from_slice(&fs::read(root.path().join("active-runtime.json")).unwrap())
                .unwrap();
        assert_eq!(pointer["assetId"], descriptor.asset_id);
        cleanup_staging(&staging).unwrap();
        assert!(!staging.exists());
        assert!(ready(&target, &descriptor));
    }

    #[test]
    fn interrupted_directory_replacement_is_recovered_without_extraction() {
        let root = tempfile::tempdir().unwrap();
        let descriptor = descriptor();
        let target = seed_old(root.path(), &descriptor);
        let staging = root.path().join(".incoming");
        fs::create_dir_all(&staging).unwrap();
        fs::rename(&target, staging.join("previous-runtime")).unwrap();
        recover_previous(&staging, &target).unwrap();
        assert_eq!(
            fs::read(target.join("Scripts/python.exe")).unwrap(),
            b"old-interpreter"
        );
        assert_eq!(
            fs::read(root.path().join("active-runtime.json")).unwrap(),
            b"old-pointer"
        );
    }

    #[test]
    fn retry_reclaims_orphan_extraction_and_missing_interpreter_never_activates() {
        let root = tempfile::tempdir().unwrap();
        let descriptor = descriptor();
        let staging = root.path().join(".incoming");
        let zip = archive(&staging, "README.txt");
        fs::create_dir_all(staging.join(".unpacked-abandoned")).unwrap();
        fs::write(
            staging.join(".unpacked-abandoned/large-file"),
            b"small-fixture",
        )
        .unwrap();
        assert!(publish_archive(
            root.path(),
            &staging,
            &descriptor,
            &zip,
            |_| Ok(()),
            || panic!("must not activate")
        )
        .is_err());
        assert_eq!(fs::read_dir(staging).unwrap().count(), 1);
        assert!(!root.path().join(&descriptor.asset_id).exists());
    }

    #[test]
    fn archive_traversal_is_rejected_before_any_publication() {
        let root = tempfile::tempdir().unwrap();
        let staging = root.path().join(".incoming");
        let zip = archive(&staging, "../outside");
        assert!(publish_archive(
            root.path(),
            &staging,
            &descriptor(),
            &zip,
            |_| panic!("invalid archive"),
            || panic!("must not activate")
        )
        .is_err());
        assert!(!root.path().join("outside").exists());
        assert_eq!(fs::read_dir(staging).unwrap().count(), 1);
    }
}
