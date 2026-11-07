//! Non-interactive subprocesses share one Windows console policy.
use std::ffi::OsStr;
use std::process::Command;

/// Hide the console without changing stdio, environment, process groups or lifetime ownership.
pub(crate) fn hide_console_window(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    #[cfg(not(windows))]
    let _ = command;
}

pub(crate) fn hidden_command(program: impl AsRef<OsStr>) -> Command {
    let mut command = Command::new(program);
    hide_console_window(&mut command);
    command
}

/// Test fixtures use the declared ABI and their own profile, never a Python
/// interpreter found accidentally on the developer's PATH.
#[cfg(test)]
pub(crate) fn isolated_test_python(profile: &std::path::Path) -> Command {
    let project = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let embedded = project.join(if cfg!(windows) { "python-runtime/python.exe" } else { "python-runtime/bin/python3" });
    let python = std::env::var_os("MEDIAFORGE_RUNTIME_PYTHON")
        .or_else(|| std::env::var_os("PYMSS_STUDIO_PYTHON"))
        .unwrap_or_else(|| {
            assert!(embedded.is_file(), "Rust Python fixtures require a prepared Python 3.12 bootstrap or MEDIAFORGE_RUNTIME_PYTHON");
            embedded.into_os_string()
        });
    let configure = |command: &mut Command| {
        command.current_dir(profile)
            .env("HOME", profile).env("USERPROFILE", profile)
            .env("APPDATA", profile).env("LOCALAPPDATA", profile)
            .env("TEMP", profile).env("TMP", profile)
            .env("MEDIAFORGE_DATA_ROOT", profile.join("data"))
            .env("MEDIAFORGE_DATA_DIR", profile.join("data"))
            .env("MEDIAFORGE_MODEL_DIR", profile.join("models"))
            .env("MEDIAFORGE_RUNTIME_ENVS_DIR", profile.join("runtime-envs"))
            .env("PYTHONNOUSERSITE", "1").env("PYTHONDONTWRITEBYTECODE", "1");
    };
    let mut probe = hidden_command(&python); configure(&mut probe);
    let output = probe.args(["-I", "-B", "-c", "import sys; assert sys.version_info[:2] == (3, 12), 'tests require Python 3.12'"]).output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let mut command = hidden_command(python); configure(&mut command); command
}

#[cfg(test)]
mod tests {
    use super::hidden_command;
    use std::io::{BufRead, Read, Write};
    use std::process::Stdio;

    const CHILD_MODE: &str = "MEDIAFORGE_NO_WINDOW_TEST_CHILD";

    // Run only as an explicitly selected child of the parent tests below.
    #[test]
    fn no_console_child_probe() {
        let Ok(mode) = std::env::var(CHILD_MODE) else { return; };
        #[cfg(windows)]
        unsafe {
            #[link(name = "kernel32")]
            extern "system" { fn GetConsoleWindow() -> *mut std::ffi::c_void; }
            assert!(GetConsoleWindow().is_null(), "background child acquired a console");
        }
        if mode == "cancel" {
            println!("ready");
            std::io::stdout().flush().unwrap();
        }
        let mut input = String::new();
        std::io::stdin().read_to_string(&mut input).unwrap();
        assert_eq!(input, "round trip");
        println!("probe:{input}");
        eprintln!("probe:stderr");
        std::process::exit(23);
    }

    fn probe(mode: &str) -> std::process::Command {
        let mut command = hidden_command(std::env::current_exe().unwrap());
        command.args(["--exact", "process_command::tests::no_console_child_probe", "--nocapture"])
            .env(CHILD_MODE, mode)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        command
    }

    #[test]
    fn background_process_preserves_pipes_and_exit_status_without_a_console() {
        let mut child = probe("io").spawn().unwrap();
        child.stdin.take().unwrap().write_all(b"round trip").unwrap();
        let output = child.wait_with_output().unwrap();
        assert_eq!(output.status.code(), Some(23), "{}", String::from_utf8_lossy(&output.stderr));
        assert!(String::from_utf8_lossy(&output.stdout).contains("probe:round trip"));
        assert!(String::from_utf8_lossy(&output.stderr).contains("probe:stderr"));
    }

    #[test]
    fn background_process_can_still_be_cancelled_and_reaped() {
        let mut child = probe("cancel").spawn().unwrap();
        let mut output = std::io::BufReader::new(child.stdout.take().unwrap());
        let mut line = String::new();
        while !line.contains("ready") {
            line.clear();
            assert!(output.read_line(&mut line).unwrap() > 0, "child exited before becoming ready");
        }
        child.kill().unwrap();
        assert!(!child.wait().unwrap().success());
    }
}
