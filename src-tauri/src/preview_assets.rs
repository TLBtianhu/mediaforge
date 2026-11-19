//! File reads for desktop media previews. Only native producers can grant access.

use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::RwLock;
use std::time::SystemTime;

use tauri::http::{header, Method, Request, Response, StatusCode};
use tauri::{AppHandle, Manager};

const MAX_RANGE: u64 = 1024 * 1024;
// Protocol responses own their bytes. Large media must use Range instead of
// allocating the entire file; HEAD remains valid for discovery.
const MAX_FULL_GET: u64 = 16 * 1024 * 1024;
const MAX_GRANTS: usize = 2048;

#[derive(Default)]
pub(crate) struct PreviewAssets {
    grants: RwLock<HashMap<PathBuf, Grant>>,
    clock: AtomicU64,
}

#[derive(Clone, PartialEq, Eq)]
struct Stamp {
    identity: [u64; 4],
    len: u64,
    modified: SystemTime,
}

#[derive(Clone)]
struct Grant {
    stamp: Stamp,
    mime: &'static str,
    used: u64,
}

impl PreviewAssets {
    fn remember(&self, path: &Path, mime: &'static str) {
        self.remember_checked(path, mime, |_| true);
    }

    fn remember_checked(&self, path: &Path, mime: &'static str, verify: impl FnOnce(&mut File) -> bool) {
        let Some(key) = path_key(path) else { return };
        let Ok(mut file) = open_exact(path) else { return };
        let Ok(before) = stamp(&file) else { return };
        if !verify(&mut file) || !stamp(&file).is_ok_and(|after| after == before) {
            return;
        }
        let used = self.clock.fetch_add(1, Ordering::Relaxed);
        let mut grants = self.grants.write().unwrap_or_else(|error| error.into_inner());
        grants.insert(key, Grant { stamp: before, mime, used });
        while grants.len() > MAX_GRANTS {
            let oldest = grants.iter().min_by_key(|(_, grant)| grant.used).map(|(key, _)| key.clone());
            if let Some(oldest) = oldest { grants.remove(&oldest); } else { break; }
        }
    }

    fn authorized_file(&self, path: &Path) -> Option<(File, Grant)> {
        let key = path_key(path)?;
        let used = self.clock.fetch_add(1, Ordering::Relaxed);
        let mut grants = self.grants.write().unwrap_or_else(|error| error.into_inner());
        let grant = grants.get_mut(&key)?;
        grant.used = used;
        let grant = grant.clone();
        drop(grants);
        let file = open_exact(path).ok()?;
        (stamp(&file).ok()? == grant.stamp).then_some((file, grant))
    }
}

pub(crate) fn remember(app: &AppHandle, path: &Path) {
    if let (Some(registry), Some(mime)) = (app.try_state::<PreviewAssets>(), media_type(path)) {
        registry.remember(path, mime);
    }
}

pub(crate) fn remember_image(app: &AppHandle, path: &Path) {
    if let (Some(registry), Some(mime)) = (app.try_state::<PreviewAssets>(), media_type(path)) {
        if mime.starts_with("image/") {
            registry.remember(path, mime);
        }
    }
}

/// The caller owns validation of the exact project cache file. JSON is never a
/// general media type and cannot enter the registry through a file picker.
pub(crate) fn remember_waveform(app: &AppHandle, path: &Path, verify: impl FnOnce(&mut File) -> bool) {
    if path.extension().is_some_and(|ext| ext.eq_ignore_ascii_case("json")) {
        if let Some(registry) = app.try_state::<PreviewAssets>() {
            registry.remember_checked(path, "application/json", verify);
        }
    }
}

pub(crate) fn is_allowed(app: &AppHandle, path: &Path) -> bool {
    app.try_state::<PreviewAssets>()
        .is_some_and(|registry| registry.authorized_file(path).is_some())
}

fn media_type(path: &Path) -> Option<&'static str> {
    Some(match path.extension()?.to_str()?.to_ascii_lowercase().as_str() {
        "wav" | "wave" => "audio/wav",
        "mp3" => "audio/mpeg",
        "aac" => "audio/aac",
        "flac" => "audio/flac",
        "aif" | "aiff" => "audio/aiff",
        "m4a" => "audio/mp4",
        "ogg" | "oga" | "opus" => "audio/ogg",
        "wma" => "audio/x-ms-wma",
        "mp4" | "m4v" => "video/mp4",
        "webm" => "video/webm",
        "mov" => "video/quicktime",
        "mkv" => "video/x-matroska",
        "avi" => "video/x-msvideo",
        "flv" => "video/x-flv",
        "wmv" => "video/x-ms-wmv",
        "ts" | "mts" | "m2ts" => "video/mp2t",
        "mpg" | "mpeg" => "video/mpeg",
        "ogv" => "video/ogg",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "bmp" => "image/bmp",
        "tif" | "tiff" => "image/tiff",
        "avif" => "image/avif",
        _ => return None,
    })
}

pub(crate) fn path_key(path: &Path) -> Option<PathBuf> {
    if !path.is_absolute()
        || path.components().any(|part| matches!(part, Component::ParentDir | Component::CurDir))
    {
        return None;
    }
    #[cfg(windows)]
    {
        use std::path::Prefix;
        if !matches!(
            path.components().next(),
            Some(Component::Prefix(prefix))
                if matches!(prefix.kind(), Prefix::Disk(_) | Prefix::UNC(..) | Prefix::VerbatimDisk(_) | Prefix::VerbatimUNC(..))
        ) || path.components().any(|part| {
            matches!(part, Component::Normal(name) if name.to_string_lossy().contains(':'))
        }) {
            return None;
        }
        let normalized: PathBuf = path.components().collect();
        let text = normalized.to_str()?;
        let text = match text.strip_prefix(r"\\?\UNC\") {
            Some(unc) => format!(r"\\{unc}"),
            None => text.strip_prefix(r"\\?\").unwrap_or(text).to_string(),
        };
        Some(PathBuf::from(text.to_lowercase()))
    }
    #[cfg(not(windows))]
    {
        Some(path.components().collect())
    }
}

fn denied() -> io::Error {
    io::ErrorKind::PermissionDenied.into()
}

pub(crate) fn open_exact(path: &Path) -> io::Result<File> {
    let expected = path_key(path).ok_or_else(denied)?;
    let file = open_platform_exact(path, &expected)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() {
        return Err(denied());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(denied());
        }
    }
    Ok(file)
}

#[cfg(unix)]
fn open_platform_exact(path: &Path, _expected: &Path) -> io::Result<File> {
    use std::ffi::CString;
    use std::os::fd::{AsRawFd, FromRawFd};
    use std::os::unix::ffi::OsStrExt;

    let mut parts = path.components().peekable();
    if parts.next() != Some(Component::RootDir) {
        return Err(denied());
    }
    let mut directory = File::open("/")?;
    while let Some(Component::Normal(name)) = parts.next() {
        let name = CString::new(name.as_bytes()).map_err(|_| denied())?;
        let last = parts.peek().is_none();
        let flags = libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC
            | if last { libc::O_NONBLOCK } else { libc::O_DIRECTORY };
        // Each parent descriptor is already bound to a directory without links.
        let fd = unsafe { libc::openat(directory.as_raw_fd(), name.as_ptr(), flags) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let file = unsafe { File::from_raw_fd(fd) };
        if last {
            return Ok(file);
        }
        directory = file;
    }
    Err(denied())
}

#[cfg(windows)]
fn open_platform_exact(path: &Path, expected: &Path) -> io::Result<File> {
    use std::ffi::OsString;
    use std::fs::{self, OpenOptions};
    use std::os::windows::ffi::OsStringExt;
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFinalPathNameByHandleW, FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_OPEN_REPARSE_POINT,
    };

    for item in path.ancestors() {
        let info = fs::symlink_metadata(item)?;
        if info.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(denied());
        }
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)?;
    let mut final_path = vec![0_u16; 512];
    loop {
        let count = unsafe {
            GetFinalPathNameByHandleW(
                file.as_raw_handle(),
                final_path.as_mut_ptr(),
                final_path.len() as u32,
                0,
            )
        } as usize;
        if count == 0 {
            return Err(io::Error::last_os_error());
        }
        if count < final_path.len() {
            let actual = PathBuf::from(OsString::from_wide(&final_path[..count]));
            if path_key(&actual).as_deref() != Some(expected) {
                return Err(denied());
            }
            return Ok(file);
        }
        final_path.resize(count + 1, 0);
    }
}

fn stamp(file: &File) -> io::Result<Stamp> {
    let metadata = file.metadata()?;
    if !metadata.is_file() {
        return Err(denied());
    }
    #[cfg(unix)]
    let identity = {
        use std::os::unix::fs::MetadataExt;
        [metadata.dev(), metadata.ino(), metadata.ctime() as u64, metadata.ctime_nsec() as u64]
    };
    #[cfg(windows)]
    let identity = {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::{
            GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION, FILE_ATTRIBUTE_REPARSE_POINT,
        };
        let mut info = BY_HANDLE_FILE_INFORMATION::default();
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
            return Err(io::Error::last_os_error());
        }
        if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(denied());
        }
        [
            u64::from(info.dwVolumeSerialNumber),
            (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
            (u64::from(info.ftCreationTime.dwHighDateTime) << 32)
                | u64::from(info.ftCreationTime.dwLowDateTime),
            0,
        ]
    };
    Ok(Stamp { identity, len: metadata.len(), modified: metadata.modified()? })
}

fn permitted_origin(origin: &str, development: bool) -> bool {
    #[cfg(any(windows, target_os = "android"))]
    let local = matches!(origin, "http://tauri.localhost" | "https://tauri.localhost");
    #[cfg(not(any(windows, target_os = "android")))]
    let local = origin == "tauri://localhost";
    local || development && origin == "http://localhost:1420"
}

fn response(origin: Option<&str>) -> tauri::http::response::Builder {
    let builder = Response::builder()
        .header(header::CACHE_CONTROL, "no-store")
        .header(header::VARY, "Origin")
        .header("X-Content-Type-Options", "nosniff")
        .header("Content-Security-Policy", "default-src 'none'; sandbox");
    if let Some(origin) = origin {
        builder
            .header(header::ACCESS_CONTROL_ALLOW_ORIGIN, origin)
            .header(header::ACCESS_CONTROL_EXPOSE_HEADERS, "Content-Range, Content-Length, Accept-Ranges")
    } else {
        builder
    }
}

fn failure(status: StatusCode, origin: Option<&str>) -> Response<Vec<u8>> {
    response(origin).status(status).body(Vec::new()).expect("static response headers")
}

/// A single inclusive byte range is sufficient for the desktop audio and video
/// players. Limit open-ended reads to a small chunk, like Tauri's asset protocol.
fn byte_range(value: &str, len: u64) -> Option<(u64, u64)> {
    if len == 0 {
        return None;
    }
    let (start, end) = value.strip_prefix("bytes=")?.split_once('-')?;
    let number = |digits: &str| {
        if !digits.bytes().all(|byte| byte.is_ascii_digit()) {
            return None;
        }
        digits.parse::<u64>().ok()
    };
    let (start, end) = if start.is_empty() {
        let suffix = number(end)?;
        (len.saturating_sub(suffix), len - 1)
    } else {
        let start = number(start)?;
        let end = if end.is_empty() { len - 1 } else { number(end)?.min(len - 1) };
        (start, end)
    };
    (start <= end).then(|| (start, end.min(start.saturating_add(MAX_RANGE - 1))))
}

fn serve_request(
    registry: &PreviewAssets,
    request: &Request<Vec<u8>>,
    development: bool,
) -> Response<Vec<u8>> {
    let origin = match request.headers().get(header::ORIGIN) {
        Some(origin) => match origin.to_str() {
            Ok(origin) if permitted_origin(origin, development) => Some(origin),
            _ => return failure(StatusCode::FORBIDDEN, None),
        },
        // Native media elements and some WebKit loads omit Origin. An opaque
        // iframe fetch sends "null", which is rejected above.
        None => None,
    };
    if !matches!(*request.method(), Method::GET | Method::HEAD) {
        return response(origin).status(StatusCode::METHOD_NOT_ALLOWED)
            .header(header::ALLOW, "GET, HEAD").body(Vec::new()).expect("static response headers");
    }
    let Some(path) = request.uri().path().strip_prefix('/').and_then(|encoded| {
        percent_encoding::percent_decode_str(encoded).decode_utf8().ok()
    }) else {
        return failure(StatusCode::NOT_FOUND, origin);
    };
    let Some((mut file, grant)) = registry.authorized_file(Path::new(path.as_ref())) else {
        return failure(StatusCode::NOT_FOUND, origin);
    };
    let len = grant.stamp.len;
    let mut builder = response(origin)
        .header(header::CONTENT_TYPE, grant.mime)
        .header(header::ACCEPT_RANGES, "bytes");
    let (start, count) = match request.headers().get(header::RANGE) {
        Some(value) => {
            let Some((start, end)) = value.to_str().ok().and_then(|value| byte_range(value, len)) else {
                return builder.status(StatusCode::RANGE_NOT_SATISFIABLE)
                    .header(header::CONTENT_RANGE, format!("bytes */{len}"))
                    .body(Vec::new()).expect("static response headers");
            };
            builder = builder.status(StatusCode::PARTIAL_CONTENT)
                .header(header::CONTENT_RANGE, format!("bytes {start}-{end}/{len}"));
            (start, end - start + 1)
        }
        None => {
            if request.method() == Method::GET && len > MAX_FULL_GET {
                return builder.status(StatusCode::PAYLOAD_TOO_LARGE)
                    .header(header::CONTENT_RANGE, format!("bytes */{len}"))
                    .header(header::CONTENT_LENGTH, 0)
                    .body(Vec::new()).expect("static response headers");
            }
            (0, len)
        }
    };
    let mut body = Vec::new();
    if request.method() == Method::GET {
        let Ok(capacity) = usize::try_from(count) else {
            return failure(StatusCode::PAYLOAD_TOO_LARGE, origin);
        };
        if body.try_reserve_exact(capacity).is_err() {
            return failure(StatusCode::PAYLOAD_TOO_LARGE, origin);
        }
        if file.seek(SeekFrom::Start(start)).is_err()
            || (&mut file).take(count).read_to_end(&mut body).is_err()
            || body.len() != capacity
            || !stamp(&file).is_ok_and(|stamp| stamp == grant.stamp)
        {
            return failure(StatusCode::NOT_FOUND, origin);
        }
    }
    builder.header(header::CONTENT_LENGTH, count).body(body).expect("static response headers")
}

pub(crate) fn serve(app: &AppHandle, request: &Request<Vec<u8>>) -> Response<Vec<u8>> {
    let Some(registry) = app.try_state::<PreviewAssets>() else {
        return failure(StatusCode::NOT_FOUND, None);
    };
    serve_request(&registry, request, cfg!(debug_assertions) && crate::storage::is_development_executable())
}

#[cfg(test)]
mod tests {
    use super::*;
    use percent_encoding::{utf8_percent_encode, NON_ALPHANUMERIC};
    use std::fs;

    fn request(path: &Path, method: Method) -> tauri::http::request::Builder {
        let path = path.to_str().unwrap();
        let encoded = utf8_percent_encode(path, NON_ALPHANUMERIC);
        Request::builder()
            .uri(format!("http://mediaforge-preview.localhost/{encoded}"))
            .method(method)
    }

    fn run(registry: &PreviewAssets, request: tauri::http::request::Builder) -> Response<Vec<u8>> {
        serve_request(registry, &request.body(Vec::new()).unwrap(), false)
    }

    #[test]
    fn only_exact_registered_regular_files_are_served() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().canonicalize().unwrap();
        let selected = root.join("测试[1] #.WAV");
        let sibling = root.join("测试1 #.WAV");
        let settings = root.join("settings.json");
        for file in [&selected, &sibling, &settings] {
            fs::write(file, b"sample").unwrap();
        }
        let registry = PreviewAssets::default();
        registry.remember(&selected, media_type(&selected).unwrap());

        let opened = run(&registry, request(&selected, Method::GET));
        assert_eq!(opened.status(), StatusCode::OK);
        assert_eq!(opened.body(), b"sample");
        assert_eq!(opened.headers()[header::CONTENT_TYPE], "audio/wav");
        assert_eq!(opened.headers()[header::CACHE_CONTROL], "no-store");
        assert_eq!(opened.headers()["X-Content-Type-Options"], "nosniff");
        assert!(media_type(&settings).is_none());

        let separator = std::path::MAIN_SEPARATOR;
        // Appending to a verbatim Windows PathBuf normalizes parent components.
        let traversal = PathBuf::from(format!("{}{separator}untrusted{separator}..{separator}测试[1] #.WAV", root.display()));
        for rejected in [&root, &sibling, &settings, &traversal] {
            let response = run(&registry, request(rejected, Method::GET));
            assert_eq!(response.status(), StatusCode::NOT_FOUND, "{}", rejected.display());
            assert!(response.body().is_empty());
        }
        assert_eq!(run(&registry, request(&selected, Method::POST)).status(), StatusCode::METHOD_NOT_ALLOWED);
    }

    #[test]
    fn successful_responses_preserve_length_seek_and_app_only_cors() {
        let temporary = tempfile::tempdir().unwrap();
        let file = temporary.path().canonicalize().unwrap().join("clip.mp4");
        fs::write(&file, b"0123456789").unwrap();
        let registry = PreviewAssets::default();
        registry.remember(&file, media_type(&file).unwrap());
        assert_eq!(media_type(Path::new("clip.MTS")), Some("video/mp2t"));
        let app_origin = if cfg!(any(windows, target_os = "android")) {
            "http://tauri.localhost"
        } else {
            "tauri://localhost"
        };

        let head = run(&registry, request(&file, Method::HEAD));
        assert_eq!(head.status(), StatusCode::OK);
        assert_eq!(head.headers()[header::CONTENT_LENGTH], "10");
        assert_eq!(head.headers()[header::ACCEPT_RANGES], "bytes");
        assert!(head.body().is_empty());

        for (value, data, bounds) in [
            ("bytes=2-4", b"234".as_slice(), "bytes 2-4/10"),
            ("bytes=7-", b"789".as_slice(), "bytes 7-9/10"),
            ("bytes=-2", b"89".as_slice(), "bytes 8-9/10"),
        ] {
            let partial = run(&registry, request(&file, Method::GET)
                .header(header::ORIGIN, app_origin).header(header::RANGE, value));
            assert_eq!(partial.status(), StatusCode::PARTIAL_CONTENT);
            assert_eq!(partial.headers()[header::CONTENT_RANGE], bounds);
            assert_eq!(partial.headers()[header::ACCESS_CONTROL_ALLOW_ORIGIN], app_origin);
            assert_eq!(partial.body(), data);
        }
        for value in ["bytes=10-", "bytes=4-3", "bytes=-0", "bytes=+1-2", "bytes=1-2,4-5", "items=0-1"] {
            let invalid = run(&registry, request(&file, Method::GET).header(header::RANGE, value));
            assert_eq!(invalid.status(), StatusCode::RANGE_NOT_SATISFIABLE, "{value}");
            assert_eq!(invalid.headers()[header::CONTENT_RANGE], "bytes */10");
            assert!(invalid.body().is_empty());
        }
        assert_eq!(byte_range("bytes=0-", MAX_RANGE + 20), Some((0, MAX_RANGE - 1)));

        for origin in ["null", "http://pymss-plugin.localhost", "http://localhost:1420", "https://tauri.localhost.example"] {
            let blocked = run(&registry, request(&file, Method::GET).header(header::ORIGIN, origin));
            assert_eq!(blocked.status(), StatusCode::FORBIDDEN);
            assert!(!blocked.headers().contains_key(header::ACCESS_CONTROL_ALLOW_ORIGIN));
            assert!(blocked.body().is_empty());
        }
        assert!(permitted_origin("http://localhost:1420", true));
    }

    #[test]
    fn full_get_is_bounded_while_head_and_ranges_remain_available() {
        let temporary = tempfile::tempdir().unwrap();
        let file = temporary.path().canonicalize().unwrap().join("large.mp4");
        fs::File::create(&file).unwrap().set_len(MAX_FULL_GET + 1).unwrap();
        let registry = PreviewAssets::default();
        registry.remember(&file, media_type(&file).unwrap());

        let full = run(&registry, request(&file, Method::GET));
        assert_eq!(full.status(), StatusCode::PAYLOAD_TOO_LARGE);
        assert!(full.body().is_empty());
        assert_eq!(full.headers()[header::CONTENT_RANGE], format!("bytes */{}", MAX_FULL_GET + 1));
        let head = run(&registry, request(&file, Method::HEAD));
        assert_eq!(head.status(), StatusCode::OK);
        assert_eq!(head.headers()[header::CONTENT_LENGTH], (MAX_FULL_GET + 1).to_string());
        assert!(head.body().is_empty());

        let range = run(&registry, request(&file, Method::GET).header(header::RANGE, "bytes=1024-2047"));
        assert_eq!(range.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(range.body().len(), 1024);
        assert_eq!(range.headers()[header::CONTENT_RANGE], format!("bytes 1024-2047/{}", MAX_FULL_GET + 1));
        let open = run(&registry, request(&file, Method::GET).header(header::RANGE, "bytes=0-"));
        assert_eq!(open.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(open.body().len(), MAX_RANGE as usize);
    }

    #[test]
    fn grants_form_a_bounded_lru_and_reads_refresh_recency() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().canonicalize().unwrap();
        let registry = PreviewAssets::default();
        for index in 0..MAX_GRANTS {
            let file = root.join(format!("{index}.wav"));
            fs::write(&file, [index as u8]).unwrap();
            registry.remember(&file, "audio/wav");
        }
        assert!(registry.authorized_file(&root.join("0.wav")).is_some());
        for index in MAX_GRANTS..MAX_GRANTS + 2 {
            let file = root.join(format!("{index}.wav"));
            fs::write(&file, [index as u8]).unwrap();
            registry.remember(&file, "audio/wav");
        }
        assert_eq!(registry.grants.read().unwrap().len(), MAX_GRANTS);
        assert!(registry.authorized_file(&root.join("0.wav")).is_some());
        assert!(registry.authorized_file(&root.join("1.wav")).is_none());
        assert!(registry.authorized_file(&root.join(format!("{}.wav", MAX_GRANTS + 1))).is_some());
    }

    #[test]
    fn stale_grants_reject_replaced_files_until_native_producer_grants_them_again() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().canonicalize().unwrap();
        let file = root.join("clip.wav");
        fs::write(&file, b"first bytes").unwrap();
        let registry = PreviewAssets::default();
        registry.remember(&file, media_type(&file).unwrap());
        fs::rename(&file, root.join("old.wav")).unwrap();
        fs::write(&file, b"other bytes").unwrap();

        let replaced = run(&registry, request(&file, Method::GET));
        assert_eq!(replaced.status(), StatusCode::NOT_FOUND);
        assert!(replaced.body().is_empty());
        registry.remember(&file, media_type(&file).unwrap());
        assert_eq!(run(&registry, request(&file, Method::GET)).body(), b"other bytes");
    }

    #[test]
    fn links_and_junctions_do_not_become_authorized_preview_paths() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().canonicalize().unwrap();
        let media = root.join("media");
        fs::create_dir(&media).unwrap();
        let file = media.join("clip.wav");
        fs::write(&file, b"sample").unwrap();
        let alias = root.join("alias");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&media, &alias).unwrap();
        #[cfg(windows)]
        {
            let output = crate::process_command::hidden_command("cmd.exe").args(["/D", "/C", "mklink", "/J"])
                .arg(temporary.path().join("alias")).arg(temporary.path().join("media")).output().unwrap();
            assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        }

        let registry = PreviewAssets::default();
        let alias_file = alias.join("clip.wav");
        registry.remember(&file, media_type(&file).unwrap());
        registry.remember(&alias_file, media_type(&alias_file).unwrap());
        let rejected = run(&registry, request(&alias_file, Method::GET));
        #[cfg(windows)]
        fs::remove_dir(&alias).unwrap();
        assert_eq!(rejected.status(), StatusCode::NOT_FOUND);
        assert!(rejected.body().is_empty());
        assert_eq!(run(&registry, request(&file, Method::GET)).body(), b"sample");
    }
}
