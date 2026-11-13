//! Current editor reference contracts: saved projects use sources, worker
//! exports use assets. Relink hints and opaque plugin state are never paths.
use crate::error::{AppError, AppResult};
use serde_json::Value;
use std::path::{Path, PathBuf};
use super::result_jobs::{plain_path, PathReferences};

fn invalid(message: &str) -> AppError {
    AppError::Worker(format!("invalid editor references: {message}; original files preserved"))
}

fn add_path(value: &Value, optional: bool, paths: &mut Vec<PathBuf>) -> AppResult<()> {
    if optional && value.is_null() { return Ok(()); }
    let text = value.as_str().ok_or_else(|| invalid("file reference must be text"))?;
    if optional && text.is_empty() { return Ok(()); }
    let path = Path::new(text);
    if text.trim().is_empty() || text.contains('\0') || !path.is_absolute() {
        return Err(invalid("file reference must be an explicit absolute path"));
    }
    plain_path(path).map_err(invalid)?;
    paths.push(path.to_path_buf());
    Ok(())
}

fn plugin_paths(value: Option<&Value>, paths: &mut Vec<PathBuf>) -> AppResult<()> {
    let Some(value) = value else { return Ok(()); };
    let chain = value.as_array().ok_or_else(|| invalid("plugin chain must be an array"))?;
    for slot in chain {
        if !slot.is_object() { return Err(invalid("plugin slot must be an object")); }
        // AU identities may not have a filesystem binary. State is opaque data.
        add_path(&slot["path"], true, paths)?;
    }
    Ok(())
}

fn project_paths(project: &Value, export: bool) -> AppResult<Vec<PathBuf>> {
    if !project.is_object() || project["version"].as_u64() != Some(4)
        || !project["id"].as_str().is_some_and(|id| !id.trim().is_empty()) {
        return Err(invalid("unsupported current project identity/version"));
    }
    let collection = if export {
        if project.get("sources").is_some() { return Err(invalid("export projection must use assets, not sources")); }
        "assets"
    } else {
        crate::storage::validate_editor_project(project)?;
        if project.get("assets").is_some() { return Err(invalid("saved projects must use sources, not assets")); }
        "sources"
    };
    let sources = project[collection].as_array().ok_or_else(|| invalid("invalid source collection"))?;
    let tracks = project["tracks"].as_array().ok_or_else(|| invalid("tracks must be an array"))?;
    let mut paths = Vec::new();
    for source in sources {
        if !source.is_object() { return Err(invalid("source must be an object")); }
        add_path(&source["path"], false, &mut paths)?;
        add_path(&source["peaksPath"], true, &mut paths)?;
    }
    add_path(&project["sourceResultDir"], true, &mut paths)?;
    plugin_paths(project.get("pluginChain"), &mut paths)?;
    for track in tracks {
        if !track.is_object() || track.get("effects").is_some() {
            return Err(invalid("invalid current track"));
        }
        let clips = track["clips"].as_array().ok_or_else(|| invalid("clips must be an array"))?;
        if clips.iter().any(|clip| !clip.is_object()
            || clip.get("assetId").is_some_and(|id| !id.is_string())) {
            return Err(invalid("invalid current clip"));
        }
        plugin_paths(track.get("pluginChain"), &mut paths)?;
    }
    paths.sort();paths.dedup();Ok(paths)
}

pub(crate) fn persisted_project_references(project: &Value) -> AppResult<PathReferences> {
    Ok(PathReferences::from_paths(&project_paths(project, false)?))
}

pub(crate) fn export_project_references(project: &Value) -> AppResult<PathReferences> {
    Ok(PathReferences::from_paths(&project_paths(project, true)?))
}

/// Resolve existing ancestors so Windows case/short-name aliases do not escape
/// the managed-project boundary even when the final referenced file is missing.
fn reference_key(path: &Path) -> AppResult<String> {
    let mut cursor = path.to_path_buf();
    let mut suffix = Vec::new();
    let mut resolved = loop {
        match std::fs::canonicalize(&cursor) {
            Ok(path) => break path,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                suffix.push(cursor.file_name().ok_or_else(|| invalid("cannot resolve reference boundary"))?.to_os_string());
                if !cursor.pop() { return Err(invalid("cannot resolve reference boundary")); }
            }
            Err(error) => return Err(error.into()),
        }
    };
    for name in suffix.into_iter().rev() { resolved.push(name); }
    let value = resolved.to_str().ok_or_else(|| invalid("reference path is not Unicode"))?;
    #[cfg(windows)]
    {
        let normalized = value.replace('\\', "/");
        Ok(normalized.strip_prefix("//?/").unwrap_or(&normalized).trim_end_matches('/').to_lowercase())
    }
    #[cfg(not(windows))]
    { Ok(value.trim_end_matches('/').to_string()) }
}

/// Caller holds JSON -> asset through validation and the eventual replacement.
/// Existing missing references remain editable for relinking. A newly added
/// reference cannot point at project assets another process just deleted.
pub(crate) fn validate_managed_reference_targets(data_root: &Path, current: &Value, previous: Option<&Value>) -> AppResult<()> {
    plain_path(data_root).map_err(invalid)?;
    let boundary = reference_key(&data_root.join("editor-projects"))?;
    let prior = previous.map(|project| project_paths(project, false)).transpose()?.unwrap_or_default()
        .iter().map(|path| reference_key(path)).collect::<AppResult<std::collections::BTreeSet<_>>>()?;
    for path in project_paths(current, false)? {
        let key = reference_key(&path)?;
        if (key == boundary || key.strip_prefix(&boundary).is_some_and(|rest| rest.starts_with('/')))
            && !prior.contains(&key) {
            match std::fs::metadata(&path) {
                Ok(_) => (),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Err(invalid("new managed project reference no longer exists")),
                Err(error) => return Err(error.into()),
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn saved(path: &Path) -> Value {
        json!({"id":"edit_saved","version":4,"sources":[{"id":"source","path":path,"peaksPath":null,
            "originRoot":"relink hint only","relativePath":"recordings/take.wav","missing":true}],
            "tracks":[{"id":"track","sourceId":"source","autoName":false,"clips":[],"pluginChain":[]}]})
    }
    fn exported(path: &Path) -> Value {
        json!({"id":"edit_saved","version":4,"name":"Mix","masterVolume":1,"masterPan":0,
            "assets":[{"id":"source","path":path,"name":"take.wav","duration":1,"sampleRate":44100,"channels":2,"peaksPath":null,"peaks":[]}],
            "tracks":[{"id":"track","sourceId":"source","name":"Stem","type":"stem","volume":1,"pan":0,"muted":false,"solo":false,
                "clips":[{"id":"clip","assetId":"source","start":0,"offset":0,"duration":1,"volume":1,"fadeIn":0,"fadeOut":0,"muted":false,"locked":false}]}]})
    }

    #[test]
    fn current_saved_and_real_export_projections_have_distinct_reference_contracts() {
        let root=tempfile::tempdir().unwrap();let path=root.path().join("takes/source.wav");
        let saved=saved(&path);let exported=exported(&path);
        assert!(persisted_project_references(&saved).unwrap().includes_directory(&root.path().join("takes")));
        assert!(export_project_references(&exported).unwrap().includes_directory(&root.path().join("takes")));
        assert!(persisted_project_references(&exported).is_err());
        assert!(export_project_references(&saved).is_err());
        let mut hybrid=exported.clone();hybrid["sources"]=json!([]);
        assert!(export_project_references(&hybrid).is_err());
    }

    #[test]
    fn only_explicit_source_cache_and_master_or_track_plugin_paths_are_references() {
        let root=tempfile::tempdir().unwrap();let mut project=saved(&root.path().join("source/take.wav"));
        project["sources"][0]["peaksPath"]=json!(root.path().join("cache/wave.json"));
        project["sources"][0]["originRoot"]=json!(root.path().join("hint-only"));
        project["pluginChain"]=json!([{"path":root.path().join("master/plugin.vst3"),"state":"opaque state"}]);
        project["tracks"][0]["pluginChain"]=json!([{"path":root.path().join("track/plugin.vst3"),"state":root.path().join("opaque-not-a-path")}]);
        let refs=persisted_project_references(&project).unwrap();
        for dir in ["source","cache","master","track"] {assert!(refs.includes_directory(&root.path().join(dir)));}
        for dir in ["hint-only","opaque-not-a-path"] {assert!(!refs.includes_directory(&root.path().join(dir)));}
    }

    #[test]
    fn old_future_missing_versions_relative_paths_and_bad_shapes_fail_closed() {
        let root=tempfile::tempdir().unwrap();let base=exported(&root.path().join("source.wav"));
        for version in [Value::Null,json!(3),json!(5)] {let mut value=base.clone();value["version"]=version;assert!(export_project_references(&value).is_err());}
        for path in ["relative.wav","file:///tmp/source.wav",""] {let mut value=base.clone();value["assets"][0]["path"]=json!(path);assert!(export_project_references(&value).is_err());}
        for (field,value) in [("assets",json!([null])),("assets",json!({})),("tracks",json!([{"clips":null}])),("tracks",json!([{"clips":[false]}]))] {
            let mut changed=base.clone();changed[field]=value;assert!(export_project_references(&changed).is_err());
        }
    }

    #[test]
    fn new_missing_managed_refs_are_rejected_but_existing_or_external_missing_refs_stay_relinkable() {
        let root=tempfile::tempdir().unwrap();let path=root.path().join("editor-projects/other/recordings/take.wav");
        let project=saved(&path);
        assert!(validate_managed_reference_targets(root.path(),&project,None).is_err());
        assert!(validate_managed_reference_targets(root.path(),&project,Some(&project)).is_ok());
        let external=tempfile::tempdir().unwrap();
        assert!(validate_managed_reference_targets(root.path(),&saved(&external.path().join("missing.wav")),None).is_ok());
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();std::fs::write(&path,b"fixture audio").unwrap();
        assert!(validate_managed_reference_targets(root.path(),&project,None).is_ok());
    }

    #[cfg(windows)]
    #[test]
    fn managed_reference_comparison_recognizes_windows_case_aliases() {
        let root=tempfile::tempdir().unwrap();let path=root.path().join("editor-projects/other/missing.wav");
        let previous=saved(&path);let alias=saved(Path::new(&path.to_string_lossy().to_uppercase()));
        assert!(validate_managed_reference_targets(root.path(),&alias,None).is_err());
        assert!(validate_managed_reference_targets(root.path(),&alias,Some(&previous)).is_ok());
    }
}
