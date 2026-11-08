//! The original library database is the only reference index. Readers never
//! create/repair it, replay journals, or copy its rows into another durable file.
use crate::error::{AppError, AppResult};
use serde::Deserialize;
use std::fs;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::time::Duration;
use super::task_journal::FileLock;

const CONTRACT: &str = include_str!("../../../manifest/library-schema.json");

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Schema {
    schema_version: u32,
    application_id: u32,
    database_file: String,
    lock_file: String,
    journal_mode: String,
    objects: Vec<SchemaObject>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SchemaObject {
    #[serde(rename = "type")] kind: String,
    name: String,
    table: String,
    sql: Option<String>,
    create: bool,
}

fn invalid(message: impl std::fmt::Display) -> AppError {
    AppError::Worker(format!("library reference index unavailable: {message}; original files preserved"))
}
fn schema() -> AppResult<Schema> {
    let value: Schema = serde_json::from_str(CONTRACT)?;
    let single = |name: &str| !name.is_empty() && Path::new(name).components().count() == 1
        && matches!(Path::new(name).components().next(), Some(Component::Normal(_)));
    let mut names = std::collections::BTreeSet::new();
    if value.schema_version != 1 || value.application_id != 0x4D464C42 || value.journal_mode != "delete"
        || value.database_file != "library.db" || value.lock_file != ".asset-references.lock"
        || !single(&value.database_file) || !single(&value.lock_file)
        || value.objects.is_empty() || value.objects.iter().any(|object| object.name.is_empty() || object.table.is_empty()
            || !names.insert(object.name.clone()) || !matches!(object.kind.as_str(), "table" | "index")
            || object.sql.as_ref().is_some_and(|sql| sql.is_empty()) || object.create && object.sql.is_none()) {
        return Err(invalid("unsupported library contract"));
    }
    Ok(value)
}

pub(crate) struct AssetLease { _guard: FileLock }
impl AssetLease {
    pub(crate) fn acquire(root: &Path, timeout: Duration) -> AppResult<Self> {
        let contract = schema()?;
        super::result_jobs::plain_path(root).map_err(invalid)?;
        let path = root.join(contract.lock_file);
        super::result_jobs::plain_path(&path).map_err(invalid)?;
        let guard = FileLock::acquire_plain(&path, timeout)?;
        super::result_jobs::plain_path(&path).map_err(invalid)?;
        Ok(Self { _guard: guard })
    }
}

pub(crate) struct LibrarySnapshot {
    // Field drop order closes the read transaction before releasing the OS lease.
    _database: Option<Database>,
    _lease: AssetLease,
    paths: Vec<PathBuf>,
}
impl LibrarySnapshot {
    pub(crate) fn paths(&self) -> &[PathBuf] { &self.paths }
}
#[cfg(windows)]
type Database = native::Connection;
#[cfg(not(windows))]
struct Database;

fn check_sidecars(path: &Path) -> AppResult<()> {
    for suffix in ["-wal", "-shm", "-journal"] {
        let sidecar = PathBuf::from(format!("{}{suffix}", path.as_os_str().to_string_lossy()));
        match fs::symlink_metadata(&sidecar) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(invalid(error)),
            Ok(_) => return Err(invalid("journal/WAL/SHM present; no recovery or checkpoint is permitted")),
        }
    }
    Ok(())
}

fn database(root: &Path, contract: &Schema) -> AppResult<Option<PathBuf>> {
    let path = root.join(&contract.database_file);
    super::result_jobs::plain_path(&path).map_err(invalid)?;
    check_sidecars(&path)?;
    let metadata = match fs::symlink_metadata(&path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(invalid(error)),
        Ok(metadata) => metadata,
    };
    if !metadata.is_file() { return Err(invalid("database is not a regular file")); }
    let mut header = [0u8; 100];
    fs::File::open(&path)?.read_exact(&mut header)?;
    if &header[..16] != b"SQLite format 3\0" || header[18] != 1 || header[19] != 1
        || u32::from_be_bytes(header[56..60].try_into().unwrap()) != 1
        || u32::from_be_bytes(header[60..64].try_into().unwrap()) != contract.schema_version
        || u32::from_be_bytes(header[68..72].try_into().unwrap()) != contract.application_id {
        return Err(invalid("unsupported database header, encoding, version, owner or journal mode"));
    }
    Ok(Some(path))
}

/// Lock order: catalog -> owner/write -> JSON -> model -> this asset lease.
/// Even an absent database keeps its lease until the caller finishes recycling.
pub(crate) fn snapshot(root: &Path) -> AppResult<LibrarySnapshot> {
    let contract = schema()?;
    let lease = AssetLease::acquire(root, Duration::ZERO)?;
    let Some(path) = database(root, &contract)? else { return Ok(LibrarySnapshot { _database: None, _lease: lease, paths: vec![] }); };
    #[cfg(windows)]
    {
        let connection = native::Connection::read_only(&path)?;
        connection.query("BEGIN")?;
        let paths = native::validate_and_read(&connection, &contract)?;
        check_sidecars(&path)?;
        Ok(LibrarySnapshot { _database: Some(connection), _lease: lease, paths })
    }
    #[cfg(not(windows))]
    { let _ = path; Err(invalid("precise SQLite reference reading is currently unsupported on this platform")) }
}

#[cfg(windows)]
mod native {
    use super::*;
    use std::ffi::{c_char, c_int, c_void, CStr, CString};
    use std::sync::OnceLock;
    use windows_sys::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryExW, LOAD_LIBRARY_SEARCH_SYSTEM32};

    // WinSDK winsqlite3.h declares these with SQLITE_APICALL (__stdcall).
    type Open = unsafe extern "system" fn(*const c_char, *mut *mut c_void, c_int, *const c_char) -> c_int;
    type Close = unsafe extern "system" fn(*mut c_void) -> c_int;
    type Prepare = unsafe extern "system" fn(*mut c_void, *const c_char, c_int, *mut *mut c_void, *mut *const c_char) -> c_int;
    type Step = unsafe extern "system" fn(*mut c_void) -> c_int;
    type Finalize = unsafe extern "system" fn(*mut c_void) -> c_int;
    type ColumnCount = unsafe extern "system" fn(*mut c_void) -> c_int;
    type ColumnType = unsafe extern "system" fn(*mut c_void, c_int) -> c_int;
    type ColumnText = unsafe extern "system" fn(*mut c_void, c_int) -> *const u8;
    type ColumnBytes = unsafe extern "system" fn(*mut c_void, c_int) -> c_int;
    type ColumnInt = unsafe extern "system" fn(*mut c_void, c_int) -> i64;
    type ColumnReal = unsafe extern "system" fn(*mut c_void, c_int) -> f64;
    type ErrorMessage = unsafe extern "system" fn(*mut c_void) -> *const c_char;
    type ReadOnly = unsafe extern "system" fn(*mut c_void, *const c_char) -> c_int;

    struct Api {
        _module: usize, open: Open, close: Close, prepare: Prepare, step: Step, finalize: Finalize,
        count: ColumnCount, kind: ColumnType, text: ColumnText, bytes: ColumnBytes,
        integer: ColumnInt, real: ColumnReal, error: ErrorMessage, readonly: ReadOnly,
    }
    fn api() -> AppResult<&'static Api> {
        static API: OnceLock<Result<Api, String>> = OnceLock::new();
        API.get_or_init(|| unsafe {
            let wide = "winsqlite3.dll\0".encode_utf16().collect::<Vec<_>>();
            let module = LoadLibraryExW(wide.as_ptr(), std::ptr::null_mut(), LOAD_LIBRARY_SEARCH_SYSTEM32);
            if module.is_null() { return Err(std::io::Error::last_os_error().to_string()); }
            macro_rules! symbol { ($name:literal, $kind:ty) => {{
                let address = GetProcAddress(module, concat!($name, "\0").as_ptr()).ok_or_else(|| format!("System SQLite is missing {}", $name))?;
                std::mem::transmute::<unsafe extern "system" fn() -> isize, $kind>(address)
            }}; }
            Ok(Api { _module: module as usize, open: symbol!("sqlite3_open_v2", Open), close: symbol!("sqlite3_close_v2", Close),
                prepare: symbol!("sqlite3_prepare_v2", Prepare), step: symbol!("sqlite3_step", Step), finalize: symbol!("sqlite3_finalize", Finalize),
                count: symbol!("sqlite3_column_count", ColumnCount), kind: symbol!("sqlite3_column_type", ColumnType), text: symbol!("sqlite3_column_text", ColumnText),
                bytes: symbol!("sqlite3_column_bytes", ColumnBytes), integer: symbol!("sqlite3_column_int64", ColumnInt), real: symbol!("sqlite3_column_double", ColumnReal),
                error: symbol!("sqlite3_errmsg", ErrorMessage), readonly: symbol!("sqlite3_db_readonly", ReadOnly) })
        }).as_ref().map_err(invalid)
    }

    #[derive(Debug, PartialEq)]
    pub(super) enum Cell { Null, Integer(i64), Real(f64), Text(String), Blob }
    pub(super) struct Connection { handle: *mut c_void, api: &'static Api }
    impl Drop for Connection { fn drop(&mut self) { unsafe { (self.api.close)(self.handle); } } }
    struct Statement<'a> { handle: *mut c_void, connection: &'a Connection }
    impl Drop for Statement<'_> { fn drop(&mut self) { unsafe { (self.connection.api.finalize)(self.handle); } } }
    impl Connection {
        fn open(path: &Path, flags: i32) -> AppResult<Self> {
            let api = api()?;
            let name = CString::new(path.to_str().ok_or_else(|| invalid("non-Unicode database path"))?).map_err(invalid)?;
            let mut handle = std::ptr::null_mut();
            let status = unsafe { (api.open)(name.as_ptr(), &mut handle, flags, std::ptr::null()) };
            if status != 0 {
                let error = if handle.is_null() { invalid(format!("SQLite open failed ({status})")) }
                    else { let connection = Self { handle, api }; connection.error(status) };
                return Err(error);
            }
            if handle.is_null() { return Err(invalid("SQLite returned no database handle")); }
            Ok(Self { handle, api })
        }
        pub(super) fn read_only(path: &Path) -> AppResult<Self> {
            let connection = Self::open(path, 1 | 0x8000)?; // READONLY | NOMUTEX; no URI/immutable/recovery.
            if unsafe { (connection.api.readonly)(connection.handle, c"main".as_ptr()) } != 1 { return Err(invalid("SQLite connection is not read-only")); }
            Ok(connection)
        }
        fn error(&self, status: i32) -> AppError {
            let message = unsafe { (self.api.error)(self.handle) };
            let detail = if message.is_null() { String::new() } else { unsafe { CStr::from_ptr(message) }.to_string_lossy().into_owned() };
            invalid(format!("SQLite error {status}: {detail}"))
        }
        pub(super) fn each(&self, sql: &str, mut row: impl FnMut(Vec<Cell>) -> AppResult<()>) -> AppResult<()> {
            let sql = CString::new(sql).map_err(invalid)?;
            let mut handle = std::ptr::null_mut();
            let status = unsafe { (self.api.prepare)(self.handle, sql.as_ptr(), -1, &mut handle, std::ptr::null_mut()) };
            let statement = Statement { handle, connection: self };
            if status != 0 { return Err(self.error(status)); }
            if statement.handle.is_null() { return Err(invalid("SQLite produced no statement")); }
            loop {
                let status = unsafe { (self.api.step)(statement.handle) };
                if status == 101 { return Ok(()); } // SQLITE_DONE
                if status != 100 { return Err(self.error(status)); } // SQLITE_ROW
                let mut cells = Vec::new();
                for index in 0..unsafe { (self.api.count)(statement.handle) } {
                    cells.push(match unsafe { (self.api.kind)(statement.handle, index) } {
                        1 => Cell::Integer(unsafe { (self.api.integer)(statement.handle, index) }),
                        2 => Cell::Real(unsafe { (self.api.real)(statement.handle, index) }),
                        3 => {
                            let text = unsafe { (self.api.text)(statement.handle, index) };
                            let count = unsafe { (self.api.bytes)(statement.handle, index) };
                            if !(0..=16_777_216).contains(&count) || text.is_null() && count != 0 { return Err(invalid("invalid or excessive SQLite text")); }
                            let bytes = if count == 0 { &[] } else { unsafe { std::slice::from_raw_parts(text, count as usize) } };
                            Cell::Text(std::str::from_utf8(bytes).map_err(invalid)?.to_string())
                        },
                        4 => Cell::Blob,
                        5 => Cell::Null,
                        _ => return Err(invalid("unknown SQLite value type")),
                    });
                }
                row(cells)?;
            }
        }
        pub(super) fn query(&self, sql: &str) -> AppResult<Vec<Vec<Cell>>> {
            let mut rows = Vec::new();self.each(sql, |row| { rows.push(row);Ok(()) })?;Ok(rows)
        }
    }

    fn text(cell: &Cell) -> AppResult<&str> { if let Cell::Text(text) = cell { Ok(text) } else { Err(invalid("expected SQLite text")) } }
    fn normalized_sql(value: &str) -> String { value.split_whitespace().collect::<Vec<_>>().join(" ") }
    fn scalar(connection: &Connection, sql: &str, expected: Cell) -> AppResult<()> {
        if connection.query(sql)? != vec![vec![expected]] { return Err(invalid(format!("unsupported {sql}"))); }Ok(())
    }
    pub(super) fn validate_and_read(connection: &Connection, contract: &Schema) -> AppResult<Vec<PathBuf>> {
        scalar(connection, "PRAGMA user_version", Cell::Integer(contract.schema_version.into()))?;
        scalar(connection, "PRAGMA application_id", Cell::Integer(contract.application_id.into()))?;
        scalar(connection, "PRAGMA journal_mode", Cell::Text(contract.journal_mode.clone()))?;
        scalar(connection, "PRAGMA encoding", Cell::Text("UTF-8".into()))?;
        let mut actual = Vec::new();
        connection.each("SELECT type,name,tbl_name,sql FROM sqlite_master", |row| {
            if row.len() != 4 { return Err(invalid("invalid SQLite schema row")); }
            let sql = match &row[3] { Cell::Null => None, Cell::Text(sql) => Some(normalized_sql(sql)), _ => return Err(invalid("invalid SQLite schema SQL")) };
            actual.push((text(&row[0])?.to_string(),text(&row[1])?.to_string(),text(&row[2])?.to_string(),sql));Ok(())
        })?;
        let mut expected = contract.objects.iter().map(|object| (object.kind.clone(),object.name.clone(),object.table.clone(),object.sql.as_deref().map(normalized_sql))).collect::<Vec<_>>();
        expected.sort();actual.sort();if actual != expected { return Err(invalid("unsupported SQLite objects/DDL; no schema repair is permitted")); }
        scalar(connection, "PRAGMA quick_check", Cell::Text("ok".into()))?;
        let mut paths = Vec::new();
        connection.each("SELECT id,path,name,kind,format,size,duration,width,height,fps,thumbnail,tags,created_at,updated_at FROM assets", |row| {
            if row.len() != 14 || !matches!(row[0],Cell::Integer(id) if id>0) { return Err(invalid("invalid asset row identity")); }
            for index in [2,3,12,13] { if text(&row[index])?.is_empty() { return Err(invalid("empty required asset field")); } }
            if !matches!(text(&row[3])?,"audio"|"video"|"image") { return Err(invalid("unknown media kind")); }
            for index in [4,11] { if !matches!(row[index],Cell::Null|Cell::Text(_)) { return Err(invalid("invalid optional asset text")); } }
            for index in [5,7,8] { if !matches!(row[index],Cell::Null|Cell::Integer(0..)) { return Err(invalid("invalid asset integer")); } }
            for index in [6,9] { if !matches!(row[index],Cell::Null|Cell::Integer(0..)) && !matches!(row[index],Cell::Real(value) if value.is_finite() && value>=0.0) { return Err(invalid("invalid asset number")); } }
            for index in [1,10] {
                let value=text(&row[index])?;
                if index==10 && value.is_empty() { continue; }
                if value.is_empty() || value.contains('\0') || value.starts_with('\u{feff}') || !Path::new(value).is_absolute() { return Err(invalid("unresolved or invalid asset/thumbnail path")); }
                paths.push(PathBuf::from(value));
            }
            Ok(())
        })?;
        paths.sort();paths.dedup();Ok(paths)
    }

    #[cfg(test)]
    pub(super) fn fixture_connection(path: &Path, create: bool) -> AppResult<Connection> { Connection::open(path, if create { 2|4 } else { 2 }) }
}

/// Test fixtures use the same manifest DDL with a test-only writable connection.
#[cfg(test)]
pub(crate) fn create_fixture(root: &Path, entries: &[(PathBuf, PathBuf)]) -> AppResult<()> {
    #[cfg(windows)]
    {
        let contract=schema()?;fs::create_dir_all(root)?;let path=root.join(&contract.database_file);
        if path.try_exists()? { return Err(invalid("fixture destination must be fresh")); }
        let connection=native::fixture_connection(&path,true)?;
        connection.query("BEGIN IMMEDIATE")?;
        for object in &contract.objects { if object.create { connection.query(object.sql.as_deref().ok_or_else(|| invalid("missing fixture DDL"))?)?; } }
        connection.query(&format!("PRAGMA user_version={}",contract.schema_version))?;
        connection.query(&format!("PRAGMA application_id={}",contract.application_id))?;
        let quote=|path:&Path| path.to_string_lossy().replace('\'',"''");
        for (path,thumbnail) in entries {
            connection.query(&format!("INSERT INTO assets(path,name,kind,thumbnail,created_at,updated_at) VALUES('{}','fixture','audio','{}','2026-11-08T00:00:00+00:00','2026-11-08T00:00:00+00:00')",quote(path),quote(thumbnail)))?;
        }
        connection.query("COMMIT")?;
        Ok(())
    }
    #[cfg(not(windows))]
    { let _=(root,entries);Err(invalid("SQLite fixture requires Windows system SQLite")) }
}

#[cfg(test)]
pub(crate) fn fixture_execute(root: &Path, sql: &str) -> AppResult<()> {
    #[cfg(windows)]
    { native::fixture_connection(&root.join(schema()?.database_file),false)?.query(sql)?;Ok(()) }
    #[cfg(not(windows))]
    { let _=(root,sql);Err(invalid("SQLite fixture requires Windows system SQLite")) }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::process::{Child, Stdio};
    use std::sync::mpsc;

    fn entry(root: &Path) -> (PathBuf, PathBuf) { (root.join("source.wav"), root.join("thumbnail.jpg")) }
    fn preserved_failure(root: &Path) {
        let path = root.join("library.db");
        let bytes = fs::read(&path).unwrap();
        let names = fs::read_dir(root).unwrap().map(|entry| entry.unwrap().file_name()).collect::<std::collections::BTreeSet<_>>();
        assert!(snapshot(root).is_err());
        assert_eq!(fs::read(&path).unwrap(), bytes);
        // The zero-content lock is the only file a first reference check creates.
        let actual = fs::read_dir(root).unwrap().map(|entry| entry.unwrap().file_name())
            .filter(|name| name != ".asset-references.lock").collect::<std::collections::BTreeSet<_>>();
        let expected = names.into_iter().filter(|name| name != ".asset-references.lock").collect::<std::collections::BTreeSet<_>>();
        assert_eq!(actual, expected);
    }

    #[test]
    fn absent_database_stays_absent_and_lease_lasts_until_snapshot_drop() {
        let root = tempfile::tempdir().unwrap();
        let view = snapshot(root.path()).unwrap();
        assert!(view.paths().is_empty());
        assert!(!root.path().join("library.db").exists());
        assert!(AssetLease::acquire(root.path(), Duration::ZERO).is_err());
        let lock = root.path().join(".asset-references.lock");
        assert_eq!(fs::metadata(&lock).unwrap().len(), 0);
        drop(view);
        let lease = AssetLease::acquire(root.path(), Duration::ZERO).unwrap();drop(lease);
        assert!(lock.is_file());assert_eq!(fs::metadata(lock).unwrap().len(), 0);
    }

    #[test]
    fn complete_source_and_thumbnail_references_include_rows_beyond_2000_without_writes() {
        let root = tempfile::tempdir().unwrap();
        let entries = (0..2005).map(|index| (root.path().join(format!("source-{index}.wav")),
            root.path().join(format!("thumb-{index}.jpg")))).collect::<Vec<_>>();
        create_fixture(root.path(), &entries).unwrap();
        let path = root.path().join("library.db");let bytes = fs::read(&path).unwrap();
        let modified = fs::metadata(&path).unwrap().modified().unwrap();
        let view = snapshot(root.path()).unwrap();assert_eq!(view.paths().len(), 4010);
        assert!(view.paths().contains(&entries[2004].0));assert!(view.paths().contains(&entries[2004].1));
        assert!(!entries[2004].0.exists(), "stale absolute references still protect files");
        drop(view);assert_eq!(fs::read(&path).unwrap(), bytes);assert_eq!(fs::metadata(path).unwrap().modified().unwrap(), modified);
    }

    #[test]
    fn old_future_foreign_and_modified_schema_are_never_repaired() {
        for sql in ["PRAGMA user_version=0", "PRAGMA user_version=2", "PRAGMA application_id=0",
            "CREATE TABLE unexpected(value TEXT)", "CREATE VIEW unexpected AS SELECT path FROM assets",
            "CREATE TRIGGER idx_assets_kind AFTER INSERT ON assets BEGIN SELECT 1; END",
            "CREATE INDEX unexpected ON assets(name)", "DROP INDEX idx_assets_kind", "ALTER TABLE assets ADD COLUMN extra TEXT"] {
            let root = tempfile::tempdir().unwrap();create_fixture(root.path(), &[entry(root.path())]).unwrap();
            fixture_execute(root.path(), sql).unwrap();preserved_failure(root.path());
        }
    }

    #[test]
    fn malformed_rows_fail_closed_instead_of_returning_partial_references() {
        for patch in ["path='relative.wav'", "path=''", "path=X'FF'", "path=CAST(X'FF' AS TEXT)",
            "path='C:/valid'||char(0)||'.wav'", "thumbnail=NULL", "thumbnail=X'FF'", "thumbnail='relative.jpg'",
            "kind='unknown'", "name=''", "tags=X'FF'", "size=-1", "width=1.5", "duration='unknown'", "fps=-1"] {
            let root = tempfile::tempdir().unwrap();create_fixture(root.path(), &[entry(root.path())]).unwrap();
            fixture_execute(root.path(), &format!("UPDATE assets SET {patch}")).unwrap();preserved_failure(root.path());
        }
        let root = tempfile::tempdir().unwrap();
        let entries = (0..2005).map(|index| (root.path().join(format!("source-{index}.wav")),PathBuf::new())).collect::<Vec<_>>();
        create_fixture(root.path(), &entries).unwrap();fixture_execute(root.path(), "UPDATE assets SET thumbnail=NULL WHERE id=2005").unwrap();
        preserved_failure(root.path());
    }

    #[test]
    fn nullable_non_reference_metadata_and_empty_thumbnail_are_valid() {
        let root = tempfile::tempdir().unwrap();create_fixture(root.path(), &[(root.path().join("source.wav"),PathBuf::new())]).unwrap();
        fixture_execute(root.path(), "UPDATE assets SET format=NULL,tags=NULL,size=NULL,duration=NULL,width=NULL,height=NULL,fps=NULL").unwrap();
        assert_eq!(snapshot(root.path()).unwrap().paths(), &[root.path().join("source.wav")]);
    }

    #[test]
    fn wal_shm_journal_and_persistent_wal_headers_are_rejected_without_side_effects() {
        for suffix in ["-wal", "-shm", "-journal"] {
            for present in [true, false] {
                let root = tempfile::tempdir().unwrap();
                if present { create_fixture(root.path(), &[]).unwrap(); }
                let sidecar = root.path().join(format!("library.db{suffix}"));fs::write(&sidecar, []).unwrap();
                assert!(snapshot(root.path()).is_err());assert_eq!(fs::read(&sidecar).unwrap(), b"");
                if !present { assert!(!root.path().join("library.db").exists()); }
            }
        }
        let root = tempfile::tempdir().unwrap();create_fixture(root.path(), &[]).unwrap();
        fixture_execute(root.path(), "PRAGMA journal_mode=WAL").unwrap();
        assert!(!root.path().join("library.db-wal").exists());
        preserved_failure(root.path());
    }

    #[test]
    fn malformed_or_bom_database_is_not_opened_or_repaired() {
        for bytes in [b"not SQLite".to_vec(), [b"\xEF\xBB\xBF".as_slice(), &[0;100]].concat()] {
            let root = tempfile::tempdir().unwrap();fs::write(root.path().join("library.db"), bytes).unwrap();preserved_failure(root.path());
        }
    }

    #[test]
    fn database_busy_fails_closed_then_recovers_after_transaction_release() {
        let root = tempfile::tempdir().unwrap();create_fixture(root.path(), &[]).unwrap();
        let writer = native::fixture_connection(&root.path().join("library.db"),false).unwrap();
        writer.query("BEGIN EXCLUSIVE").unwrap();preserved_failure(root.path());drop(writer);
        assert!(snapshot(root.path()).unwrap().paths().is_empty());
    }

    #[test]
    fn reparse_root_is_rejected_without_following_or_creating_a_lock() {
        let parent = tempfile::tempdir().unwrap();let root = parent.path().join("real");fs::create_dir(&root).unwrap();
        let alias = parent.path().join("alias");
        let output = crate::process_command::hidden_command("cmd.exe").args(["/D","/C","mklink","/J"]).arg(&alias).arg(&root).output().unwrap();
        assert!(output.status.success(), "{}",String::from_utf8_lossy(&output.stderr));
        let outcome = snapshot(&alias);fs::remove_dir(alias).unwrap();
        assert!(outcome.is_err());assert!(!root.join(".asset-references.lock").exists());
    }

    struct OwnedChild { child: Child, lines: mpsc::Receiver<String>, reader: Option<std::thread::JoinHandle<()>> }
    impl OwnedChild {
        fn spawn(root: &Path, script: &str) -> Self {
            let project = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
            let mut command = crate::process_command::isolated_test_python(root);
            let mut child = command.args(["-I","-B","-c",script]).arg(project.join("python")).arg(root)
                .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
            let stdout=child.stdout.take().unwrap();let (sender,lines)=mpsc::channel();
            let reader=std::thread::spawn(move || { for line in BufReader::new(stdout).lines() {
                match line { Ok(line)=>{if sender.send(line).is_err(){break;}},Err(_)=>break }
            } });Self {child,lines,reader:Some(reader)}
        }
        fn line(&self) -> String { self.lines.recv_timeout(Duration::from_secs(15)).expect("owned Python fixture did not respond") }
        fn finish(&mut self) {
            let deadline=std::time::Instant::now()+Duration::from_secs(15);
            loop { if let Some(status)=self.child.try_wait().unwrap() { assert!(status.success(),"owned Python fixture failed: {status}");break; }
                assert!(std::time::Instant::now()<deadline,"owned Python fixture did not exit");std::thread::sleep(Duration::from_millis(10)); }
        }
    }
    impl Drop for OwnedChild { fn drop(&mut self) { let _=self.child.kill();let _=self.child.wait();if let Some(reader)=self.reader.take(){let _=reader.join();} } }

    #[test]
    fn python_writer_lease_excludes_native_reader_and_process_exit_releases_it() {
        let root = tempfile::tempdir().unwrap();
        let mut child=OwnedChild::spawn(root.path(),r#"import sys
assert sys.version_info[:2] == (3,12)
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from shared.asset_references import asset_reference_lock
with asset_reference_lock(Path(sys.argv[2])):
    print('held',flush=True)
    sys.stdin.buffer.read(1)
"#);
        assert_eq!(child.line(),"held");assert!(snapshot(root.path()).is_err());
        child.child.stdin.as_mut().unwrap().write_all(b"x").unwrap();child.finish();
        assert!(snapshot(root.path()).unwrap().paths().is_empty());
        assert_eq!(fs::metadata(root.path().join(".asset-references.lock")).unwrap().len(),0);
    }

    #[test]
    fn native_missing_database_snapshot_blocks_python_first_creation_until_drop() {
        let root = tempfile::tempdir().unwrap();let view=snapshot(root.path()).unwrap();
        let mut child=OwnedChild::spawn(root.path(),r#"import sys
assert sys.version_info[:2] == (3,12)
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from shared.asset_references import asset_reference_lock,AssetReferencesBusy
root=Path(sys.argv[2])
try:
    with asset_reference_lock(root,timeout=0): pass
except AssetReferencesBusy: print('blocked',flush=True)
else: raise AssertionError('native lease was not held')
from core import library
library.stats(data_root=root)
print('created',flush=True)
"#);
        assert_eq!(child.line(),"blocked");assert!(!root.path().join("library.db").exists());
        drop(view);assert_eq!(child.line(),"created");child.finish();
        assert!(root.path().join("library.db").is_file());assert!(snapshot(root.path()).unwrap().paths().is_empty());
    }
}
