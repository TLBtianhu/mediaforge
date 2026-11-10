//! Cross-process runtime read/write lease.
//!
//! The in-process `RuntimeAccess` only serializes callers inside one app process.
//! MCP servers, a second Studio instance and CLI helpers can otherwise run while
//! a runtime directory is being replaced. This lease is a single file next to the
//! runtime-envs:
//!
//! * worker processes hold a shared lease for their whole lifetime;
//! * install / activate / delete hold an exclusive lease.
//!
//! Windows has no `flock`; opening the lock file with `share_mode(FILE_SHARE_READ)`
//! lets readers coexist and makes an exclusive writer (which requests write access
//! and shares nothing) fail while any reader is open. Unix uses `flock`.

use crate::error::{AppError, AppResult};
use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};

const LEASE_FILE: &str = ".runtime-lease.lock";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LeaseMode {
    Shared,
    Exclusive,
}

pub struct RuntimeLease {
    _file: File,
}

impl RuntimeLease {
    pub fn shared(envs: &Path) -> AppResult<Self> {
        Self::acquire(envs, LeaseMode::Shared)
    }

    pub fn exclusive(envs: &Path) -> AppResult<Self> {
        Self::acquire(envs, LeaseMode::Exclusive)
    }

    fn acquire(envs: &Path, mode: LeaseMode) -> AppResult<Self> {
        fs::create_dir_all(envs).map_err(|error| {
            AppError::Worker(format!("无法创建运行时租约目录：{error}"))
        })?;
        let path = lease_path(envs);
        ensure_plain(&path)?;
        ensure_file(&path)?;
        let mut options = OpenOptions::new();
        options.read(true);
        if mode == LeaseMode::Exclusive {
            options.write(true);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            // Readers share read access; the writer shares nothing and requests
            // write access, so Windows rejects it while a reader is open.
            options.share_mode(if mode == LeaseMode::Shared { 1 } else { 0 });
        }
        let file = options.open(&path).map_err(|error| {
            AppError::Worker(format!(
                "RUNTIME_BUSY: 运行时正在被另一进程使用，请等待当前操作完成后重试（{error}）"
            ))
        })?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            let operation = if mode == LeaseMode::Shared { libc::LOCK_SH } else { libc::LOCK_EX };
            if unsafe { libc::flock(file.as_raw_fd(), operation | libc::LOCK_NB) } != 0 {
                return Err(AppError::Worker(
                    "RUNTIME_BUSY: 运行时正在被另一进程使用，请等待当前操作完成后重试".into(),
                ));
            }
        }
        Ok(Self { _file: file })
    }
}

fn ensure_plain(path: &Path) -> AppResult<()> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(AppError::Worker(format!("无法读取运行时租约路径：{error}"))),
    };
    #[cfg(windows)]
    let reparse = {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    };
    #[cfg(not(windows))]
    let reparse = false;
    if metadata.file_type().is_symlink() || reparse {
        return Err(AppError::Worker("运行时租约文件不能是链接".into()));
    }
    Ok(())
}

fn lease_path(envs: &Path) -> PathBuf {
    envs.join(LEASE_FILE)
}

fn ensure_file(path: &Path) -> AppResult<()> {
    if !path.exists() {
        OpenOptions::new()
            .create(true)
            .write(true)
            .open(path)
            .map_err(|error| AppError::Worker(format!("无法创建运行时租约文件：{error}")))?;
    }
    Ok(())
}

#[cfg(unix)]
impl Drop for RuntimeLease {
    fn drop(&mut self) {
        use std::os::fd::AsRawFd;
        unsafe {
            libc::flock(self._file.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_leases_coexist_and_block_a_writer() {
        let root = tempfile::tempdir().unwrap();
        let first = RuntimeLease::shared(root.path()).unwrap();
        let second = RuntimeLease::shared(root.path()).unwrap();
        assert!(RuntimeLease::exclusive(root.path()).is_err());
        drop(first);
        drop(second);
        assert!(RuntimeLease::exclusive(root.path()).is_ok());
    }

    #[test]
    fn an_exclusive_lease_blocks_readers() {
        let root = tempfile::tempdir().unwrap();
        let exclusive = RuntimeLease::exclusive(root.path()).unwrap();
        assert!(RuntimeLease::shared(root.path()).is_err());
        drop(exclusive);
        assert!(RuntimeLease::shared(root.path()).is_ok());
    }
}
