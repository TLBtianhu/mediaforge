use crate::model_dir_migration::ModelDirMigrationSession;
use crate::python::worker::WorkerProcess;
use std::collections::{HashMap, HashSet};
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};

pub type SharedMigrationSession = Arc<ModelDirMigrationSession>;

#[derive(Clone, Debug, Default)]
pub struct ProxySettings {
    pub mode: String,
    pub url: String,
    pub bypass: String,
}

pub struct AppState {
    pub tasks: Mutex<HashMap<String, Arc<WorkerProcess>>>,
    /// In-process tasks (no worker child), cancelled by flipping the flag.
    pub native_tasks: Mutex<HashMap<String, Arc<AtomicBool>>>,
    pub cancelled_tasks: Mutex<HashSet<String>>,
    pub terminal_tasks: Mutex<HashSet<String>>,
    pub pending_jobs: Mutex<HashMap<String, crate::python::worker::PendingWorker>>,
    pub job_dispatcher_running: AtomicBool,
    pub migrations: Arc<Mutex<HashMap<String, SharedMigrationSession>>>,
    pub proxy_settings: Mutex<ProxySettings>,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            tasks: Mutex::new(HashMap::new()),
            native_tasks: Mutex::new(HashMap::new()),
            cancelled_tasks: Mutex::new(HashSet::new()),
            terminal_tasks: Mutex::new(HashSet::new()),
            pending_jobs: Mutex::new(HashMap::new()),
            job_dispatcher_running: AtomicBool::new(false),
            migrations: Arc::new(Mutex::new(HashMap::new())),
            proxy_settings: Mutex::new(ProxySettings {
                mode: "system".into(),
                ..ProxySettings::default()
            }),
        }
    }
}
