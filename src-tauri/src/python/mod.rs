pub mod protocol;
pub mod worker;
pub(crate) mod task_journal;
pub(crate) mod audio_jobs;
pub(crate) mod result_jobs;
pub(crate) mod library_references;
pub(crate) mod editor_references;
mod job_schema;
mod job_removal;
mod job_runtime;
mod execution_quota;
#[cfg(any(target_os = "linux", test))]
mod bootstrap_cache;

/// The compute-worker limit contract is also the settings range for
/// `maxConcurrentSeparations`, so both gates read one manifest.
pub(crate) use execution_quota::max_slots as compute_worker_slots;
