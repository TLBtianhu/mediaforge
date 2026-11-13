//! All project deletion entry points inspect the same current reference snapshot.
//! No persistent reference index and no inference from the result page's visible IDs.
use super::{editor_project_id_for_task_id, validate_editor_project_id, EditorProjectCleanupResult, OrphanedEditorProject};
use crate::error::{AppError, AppResult};
use crate::{python::{library_references, result_jobs, task_journal}, storage};
use serde_json::Value;
use std::{collections::BTreeSet, fs, path::{Path, PathBuf}};

fn invalid(message: &str) -> AppError { AppError::Worker(format!("{message}; original project files preserved")) }

struct Project {
    id: String,
    directory: PathBuf,
    value: Value,
    references: result_jobs::PathReferences,
}

struct Snapshot<'a> {
    root: PathBuf,
    records: &'a [Value],
    projects: Vec<Project>,
    jobs: result_jobs::PathReferences,
    models: result_jobs::PathReferences,
    library: result_jobs::PathReferences,
    protected: result_jobs::PathReferences,
}

impl Snapshot<'_> {
    fn referenced(&self, project: &Project) -> bool {
        self.jobs.overlaps_directory(&project.directory)
            || self.models.overlaps_directory(&project.directory)
            || self.library.overlaps_directory(&project.directory)
            || self.protected.overlaps_directory(&project.directory)
            || self.records.iter().any(|row| row["command"] == "export_editor_mix"
                && row.pointer("/payload/project/id").and_then(Value::as_str) == Some(project.id.as_str()))
            || self.projects.iter().any(|other| other.id != project.id
                && other.references.overlaps_directory(&project.directory))
    }

    fn orphaned(&self) -> Vec<OrphanedEditorProject> {
        let mut projects = self.projects.iter().filter_map(|project| {
            let source = project.value["sourceTaskId"].as_str().filter(|source| task_journal::valid_id(source))?;
            if editor_project_id_for_task_id(source) != project.id || self.referenced(project)
                || self.records.iter().any(|row| row["jobId"] == source) { return None; }
            let name = project.value["name"].as_str().filter(|name| !name.trim().is_empty()).unwrap_or(&project.id).to_string();
            Some(OrphanedEditorProject { project_id: project.id.clone(), source_task_id: source.into(), name })
        }).collect::<Vec<_>>();
        projects.sort_by(|a,b| a.name.cmp(&b.name).then(a.project_id.cmp(&b.project_id)));projects
    }
}

fn with_snapshot<T>(data_root: &Path, protected: &[PathBuf], action: impl FnOnce(&Snapshot<'_>) -> AppResult<T>) -> AppResult<T> {
    result_jobs::plain_path(data_root).map_err(invalid)?;
    task_journal::with_catalog_records(data_root, |records| storage::with_editor_project_guard(|| {
        // Same ordering as result recycling: catalog -> JSON -> model -> asset.
        let model_guard = storage::model_reference_snapshot_guard();
        if *model_guard != 0 { return Err(invalid("model registry is busy")); }
        let library = library_references::snapshot(data_root)?;
        if result_jobs::snapshot_has_unresolved_paths(records) { return Err(invalid("job file references cannot be resolved safely")); }
        let models = result_jobs::model_path_references(data_root)?;
        let root = data_root.join("editor-projects");
        result_jobs::plain_path(&root).map_err(invalid)?;
        let mut projects = Vec::new();
        match fs::symlink_metadata(&root) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(error.into()),
            Ok(metadata) => {
                if !metadata.is_dir() { return Err(invalid("editor project root is not a directory")); }
                for entry in fs::read_dir(&root)? {
                    let entry = entry?;let directory = entry.path();
                    result_jobs::plain_path(&directory).map_err(invalid)?;
                    if !entry.file_type()?.is_dir() { continue; }
                    let id = entry.file_name().into_string().map_err(|_| invalid("invalid editor project filename"))?;
                    validate_editor_project_id(&id)?;
                    let path = directory.join("project.json");
                    result_jobs::plain_path(&path).map_err(invalid)?;
                    let text = match fs::read_to_string(&path) {
                        Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                        Err(error) => return Err(error.into()),
                        Ok(text) => text,
                    };
                    let value = result_jobs::parse_reference_json(&text)?;
                    if value["id"] != id { return Err(invalid("editor project identity does not match its directory")); }
                    let references = result_jobs::project_path_references(&value)?;
                    projects.push(Project { id, directory, value, references });
                }
            }
        }
        let mut roots = vec![data_root.join("models"), data_root.join("runtime-envs"), data_root.join("python-runtime")];
        roots.extend_from_slice(protected);
        let snapshot = Snapshot { root, records, projects, jobs: result_jobs::snapshot_path_references(records), models,
            library: result_jobs::PathReferences::from_paths(library.paths()), protected: result_jobs::PathReferences::from_paths(&roots) };
        // Keep both the library read transaction and its OS lease until action completes.
        action(&snapshot)
    }))
}

#[cfg(test)]
pub(super) fn with_current_orphaned_projects<T>(data_root: &Path, action: impl FnOnce(&Path, Vec<OrphanedEditorProject>) -> AppResult<T>) -> AppResult<T> {
    with_snapshot(data_root, &[], |snapshot| action(&snapshot.root, snapshot.orphaned()))
}

pub(super) fn list_orphaned_projects(data_root: &Path, protected: &[PathBuf]) -> AppResult<Vec<OrphanedEditorProject>> {
    with_snapshot(data_root, protected, |snapshot| Ok(snapshot.orphaned()))
}

#[derive(Clone, Copy)]
pub(super) enum Selection<'a> { Explicit, Tasks(&'a [String]), Orphans }

/// Inspect the entire selected trees before the first recursive removal. Reparse
/// points are rejected at every depth, including junctions hidden in recordings.
fn preflight_tree(directory: &Path) -> AppResult<()> {
    result_jobs::plain_path(directory).map_err(invalid)?;
    let metadata = fs::symlink_metadata(directory)?;
    if !metadata.is_dir() { return Err(invalid("editor project is not a directory")); }
    for entry in fs::read_dir(directory)? {
        let entry = entry?;result_jobs::plain_path(&entry.path()).map_err(invalid)?;
        let metadata = fs::symlink_metadata(entry.path())?;
        if metadata.is_dir() { preflight_tree(&entry.path())?; }
        else if !metadata.is_file() { return Err(invalid("editor project contains a special file")); }
    }
    Ok(())
}

pub(super) fn delete_projects(data_root: &Path, selected: &[String], selection: Selection<'_>, protected: &[PathBuf],
    open: impl FnOnce(&[String]) -> Vec<String>) -> AppResult<EditorProjectCleanupResult> {
    if selected.is_empty() { return Ok(EditorProjectCleanupResult::default()); }
    with_snapshot(data_root, protected, |snapshot| {
        let selected = selected.iter().cloned().collect::<BTreeSet<_>>();
        let orphans = snapshot.orphaned().into_iter().map(|project|project.project_id).collect::<BTreeSet<_>>();
        let mut result = EditorProjectCleanupResult::default();let mut candidates = Vec::new();
        for id in selected {
            if validate_editor_project_id(&id).is_err() { result.failed_project_ids.push(id);continue; }
            let Some(project) = snapshot.projects.iter().find(|project| project.id == id) else {
                match fs::symlink_metadata(snapshot.root.join(&id)) {
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => result.missing_project_ids.push(id),
                    Err(_) => result.failed_project_ids.push(id),
                    Ok(_) => result.blocked_project_ids.push(id),
                }
                continue;
            };
            let owned = match selection {
                Selection::Explicit => true,
                Selection::Orphans => orphans.contains(&id),
                Selection::Tasks(tasks) => project.value["sourceTaskId"].as_str().is_some_and(|source|
                    tasks.iter().any(|task| task.trim() == source) && editor_project_id_for_task_id(source) == id),
            };
            if !owned || snapshot.referenced(project) { result.blocked_project_ids.push(id);continue; }
            if preflight_tree(&project.directory).is_err() { result.failed_project_ids.push(id);continue; }
            candidates.push(project);
        }
        let ids = candidates.iter().map(|project| project.id.clone()).collect::<Vec<_>>();
        result.blocked_project_ids.extend(open(&ids).into_iter().filter(|id| ids.contains(id)));
        result.blocked_project_ids.sort();result.blocked_project_ids.dedup();
        // A blocked member prevents partial execution of an otherwise valid batch.
        if !result.blocked_project_ids.is_empty() || !result.failed_project_ids.is_empty() { return Ok(result); }
        for project in candidates {
            match fs::remove_dir_all(&project.directory) {
                Ok(()) => result.deleted_project_ids.push(project.id.clone()),
                Err(_) => result.failed_project_ids.push(project.id.clone()),
            }
        }
        Ok(result)
    })
}

#[cfg(test)]
pub(super) fn delete_current_orphaned_projects(data_root: &Path, selected: &[String], open: impl FnOnce(&[String]) -> Vec<String>) -> AppResult<EditorProjectCleanupResult> {
    delete_projects(data_root, selected, Selection::Orphans, &[], open)
}

#[cfg(test)]
mod tests;
