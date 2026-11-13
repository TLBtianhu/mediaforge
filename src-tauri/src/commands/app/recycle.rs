//! Result deletion never falls back from recycling to permanent file deletion.
use std::path::Path;

#[cfg(windows)]
pub(crate) fn recycle_file(path: &Path) -> Result<(), String> {
    let path = path.to_path_buf();
    // IFileOperation requires STA. Never use an async runtime's MTA apartment.
    std::thread::Builder::new().name("result-recycle-sta".into()).spawn(move || recycle_on_sta(&path))
        .map_err(|error| error.to_string())?.join().map_err(|_| "Recycle worker failed".to_string())?
}

#[cfg(windows)]
fn recycle_on_sta(path: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows::{core::PCWSTR, Win32::System::Com::*, Win32::UI::Shell::*};
    unsafe {
        let initialized = CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
        if initialized.is_err() {
            return Err(initialized.to_string());
        }
        struct ComGuard(bool);
        impl Drop for ComGuard {
            fn drop(&mut self) { if self.0 { unsafe { CoUninitialize() } } }
        }
        let _guard = ComGuard(initialized.is_ok());
        let operation: IFileOperation = CoCreateInstance(&FileOperation, None, CLSCTX_INPROC_SERVER)
            .map_err(|error| error.to_string())?;
        operation.SetOperationFlags(FOF_NO_UI | FOFX_RECYCLEONDELETE | FOFX_EARLYFAILURE | FOFX_ADDUNDORECORD | FOF_NO_CONNECTED_ELEMENTS)
            .map_err(|error| error.to_string())?;
        let wide = path.as_os_str().encode_wide().chain(Some(0)).collect::<Vec<_>>();
        let item: IShellItem = SHCreateItemFromParsingName(PCWSTR(wide.as_ptr()), None)
            .map_err(|error| error.to_string())?;
        operation.DeleteItem(&item, None).map_err(|error| error.to_string())?;
        operation.PerformOperations().map_err(|error| error.to_string())?;
        if operation.GetAnyOperationsAborted().map_err(|error| error.to_string())?.as_bool() {
            return Err("Recycling was aborted; the job record was preserved".into());
        }
        match std::fs::symlink_metadata(path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Ok(_) => Err("Recycling did not remove the source; the job record was preserved".into()),
            Err(error) => Err(error.to_string()),
        }
    }
}

#[cfg(not(windows))]
pub(crate) fn recycle_file(path: &Path) -> Result<(), String> {
    // The Unix/macOS trash implementation moves into the platform trash and
    // returns an error when it cannot do so; there is no remove_file fallback.
    trash::delete(path).map_err(|error| error.to_string())
}
