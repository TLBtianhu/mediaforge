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
