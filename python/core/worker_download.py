from __future__ import annotations

import contextlib
import inspect

from core.download_watchdog import DownloadStalled, ProgressWatchdog, stall_seconds
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from core.worker_models import (
    auxiliary_paths_for,
    config_path_for,
    effective_source_for,
    get_any_model_entry,
    is_user_model_entry,
    model_root,
    model_path_for,
    model_to_dict,
)
from shared.worker_protocol import emit, emit_error
from shared.worker_proxy import effective_proxy_url


_UNFETCHED_FILE_INDEX = object()


def _emit_download_log(task_id: str | None, level: str, message: str, **extra: Any) -> None:
    payload: dict[str, Any] = {"level": level, "message": message}
    payload.update(extra)
    emit("download_log", payload, task_id=task_id)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_download_method(value: Any) -> str:
    return "urllib" if str(value or '').strip().lower() == "urllib" else "aria2c"


def _should_fallback_from_aria2_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "aria2c" in message or "ossl_provider" in message or "provider_load" in message


def _require_index_for_cached_files(files: list[tuple[str, Path]], index: Any, *, force: bool) -> None:
    """Do not certify pre-existing model files when the metadata lookup failed."""
    if index is not None or force:
        return
    for _relpath, dest in files:
        if dest.is_file():
            raise RuntimeError(
                f"Cannot verify cached file {dest.name}: the file index is unavailable. "
                "Retry when metadata is available or force a fresh download."
            )


def _download_file_with_aria2_env(
    pymss_download: Any,
    download_file: Any,
    url: str,
    dest: Path,
    expected_size: Any,
    expected_sha256: Any,
    download_kwargs: dict[str, Any],
) -> None:
    overrides = {}
    if getattr(pymss_download, "ARIA2C_PATH", None):
        openssl_conf = os.environ.get("PYMSS_STUDIO_OPENSSL_CONF")
        openssl_modules = os.environ.get("PYMSS_STUDIO_OPENSSL_MODULES")
        if openssl_conf:
            overrides["OPENSSL_CONF"] = openssl_conf
        if openssl_modules:
            overrides["OPENSSL_MODULES"] = openssl_modules
    previous = {name: os.environ.get(name) for name in overrides}
    try:
        for name, value in overrides.items():
            os.environ[name] = value
        download_file(url, dest, expected_size, expected_sha256, **download_kwargs)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def files_for_studio_model(model_name: str, model_dir: str | None = None) -> tuple[Any, list[tuple[str, Path]]]:
    entry = get_any_model_entry(model_name)
    if is_user_model_entry(entry):
        raise ValueError(f"User-registered model {model_name!r} is local-only and cannot be downloaded")
    files = [(str(entry.relpath), model_path_for(entry, model_dir))]
    config_path = config_path_for(entry, model_dir)
    if entry.config_relpath and config_path is not None:
        files.append((str(entry.config_relpath), config_path))
    files.extend((str(relpath), path) for relpath, path in zip(entry.auxiliary_relpaths, auxiliary_paths_for(entry, model_dir)))
    return entry, files


def _file_size_probe(paths: Any) -> int:
    total = 0
    for path in paths:
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _download_with_stall_guard(label: str, call: Any, watchdog: Any = None, probe_path: Any = None) -> Any:
    """把一次阻塞下载放进 daemon 线程，主线程盯着"有没有进展"。

    为什么不用回调里抛异常：pymss 会隔离 progress_callback 的异常（它怕回调把下载搞挂），
    所以只能在外面等——超时就抛错，让上层重试/换源，而不是像 2026-10-30 那次
    `legacy_models/demucs.th.part` 一样 0 字节挂十分钟。
    """
    if watchdog is None and probe_path is None:
        # 既没有进度回调、也没有可探测的落盘文件：只能原样调用
        return call()

    import threading

    result: dict[str, Any] = {}
    failure: dict[str, BaseException] = {}
    done = threading.Event()

    def target() -> None:
        try:
            result["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - 原样转交给调用方
            failure["value"] = exc
        finally:
            done.set()

    timeout = watchdog.timeout if watchdog is not None else stall_seconds()
    worker = threading.Thread(target=target, name="download-guard", daemon=True)
    probe_paths = [probe_path, probe_path.with_suffix(probe_path.suffix + ".part")] if probe_path else []
    last_size = _file_size_probe(probe_paths)
    last_change = time.monotonic()
    with (watchdog if watchdog is not None else contextlib.nullcontext()):
        worker.start()
        while not done.wait(0.2):
            if watchdog is not None:
                if watchdog.stalled():
                    raise DownloadStalled(label, watchdog.timeout, watchdog.bytes_done)
                continue
            size = _file_size_probe(probe_paths)
            if size != last_size:
                last_size = size
                last_change = time.monotonic()
                continue
            if time.monotonic() - last_change >= timeout:
                raise DownloadStalled(label, timeout, size)
    if "value" in failure:
        raise failure["value"]
    return result.get("value")


def download_studio_model(
    pymss_download: Any,
    model_name: str,
    files: list[tuple[str, Path]],
    *,
    source: str = "modelscope",
    endpoint: str | None = None,
    verify: bool = True,
    force: bool = False,
    timeout: int = 30,
    progress_callback: Any = None,
    proxy: str | None = None,
    task_id: str | None = None,
    file_index: Any = _UNFETCHED_FILE_INDEX,
) -> dict[str, Any]:
    """Download studio-resolved files through pymss's downloader.

    pymss 2.1.1+ takes a resolved ``proxy`` URL and routes both its aria2c and
    urllib downloaders through it (SOCKS forces the urllib path inside pymss),
    so the worker passes the studio proxy setting straight through instead of
    patching aria2c command lines or the socket layer.
    """
    remote_url = pymss_download.remote_url
    download_file = pymss_download._download_file
    already_valid = pymss_download._already_valid
    expected_size_and_hash = pymss_download._expected_size_and_hash
    fetch_index = pymss_download.fetch_modelscope_file_index
    index = None
    if verify and endpoint is None:
        if file_index is not _UNFETCHED_FILE_INDEX:
            # Reuse the command's lookup, including an unavailable index, so progress
            # and validation use the same metadata without a second network request.
            index = file_index
        else:
            try:
                index = fetch_index(timeout=timeout, proxy=proxy)
            except Exception as exc:
                _emit_download_log(task_id, "warning", f"File index unavailable; published size/hash metadata cannot be used: {exc}")
        _require_index_for_cached_files(files, index, force=force)
    downloaded: list[str] = []
    skipped: list[str] = []

    for relpath, dest in files:
        expected_size, expected_sha256 = expected_size_and_hash(relpath, index)
        if not force and already_valid(dest, expected_size, expected_sha256):
            skipped.append(str(dest))
            continue
        download_kwargs: dict[str, Any] = {"timeout": timeout}
        # 每个文件一个看门狗：回调只要还在响就算活着（字节数只用于报错文案）
        guard_watchdog = ProgressWatchdog(relpath, timeout=stall_seconds())
        download_file_params = inspect.signature(download_file).parameters
        can_watch_progress = progress_callback is not None and "progress_callback" in download_file_params
        if "progress_callback" in download_file_params:
            download_kwargs["progress_callback"] = progress_callback
            if progress_callback is not None:
                file_done = 0

                def on_file_progress(done: int, total: int, message: str) -> None:
                    nonlocal file_done
                    # Retrying this file must not look like a new file to the model adapter.
                    file_done = max(file_done, done)
                    guard_watchdog.beat(1)
                    try:
                        progress_callback(file_done, total, message)
                    except Exception:
                        # Match pymss's callback isolation; BaseException cancellation still propagates.
                        pass

                download_kwargs["progress_callback"] = on_file_progress
                # This is the actual file boundary, even if the downloader's first
                # callback already contains bytes. Retrying below keeps this wrapper.
                on_file_progress(0, int(expected_size or 0), relpath)
        if "proxy" in download_file_params:
            download_kwargs["proxy"] = proxy
        url = remote_url(relpath, source=source, endpoint=endpoint)
        try:
            _download_with_stall_guard(
                relpath,
                lambda: _download_file_with_aria2_env(
                    pymss_download, download_file, url, dest, expected_size, expected_sha256, download_kwargs
                ),
                guard_watchdog if can_watch_progress else None,
                probe_path=dest,
            )
        except Exception as exc:
            if not getattr(pymss_download, "ARIA2C_PATH", None) or not _should_fallback_from_aria2_error(exc):
                raise
            pymss_download.ARIA2C_PATH = None
            _emit_download_log(task_id, "warning", f"aria2c failed; retrying with urllib: {exc}")
            _download_file_with_aria2_env(pymss_download, download_file, url, dest, expected_size, expected_sha256, download_kwargs)
        downloaded.append(str(dest))

    return {"downloaded": downloaded, "skipped": skipped}


def _pymss_reports_progress(download_model: Any) -> bool:
    """Whether the installed pymss can report download progress to its caller.

    The callback landed in pymss after this app shipped, so the version actually installed in a
    runtime environment may predate it. Passing an argument it does not accept would raise
    TypeError and fail the download outright, which is a much worse outcome than a coarse bar.
    """
    try:
        return "progress_callback" in inspect.signature(download_model).parameters
    except (TypeError, ValueError):
        return False


def prepare_pymss_download(
    pymss_download: Any,
    task_id: str | None,
    download_model: Any | None = None,
    download_method: str = "aria2c",
) -> None:
    """Prepare pymss's downloader for this worker process.

    This is shared by the model-library download command and the automatic download path used
    before separation. Proxy routing lives in pymss itself (2.1.1+: an explicit ``proxy=``
    argument on the download calls), so what remains here is the downloader selection and the
    stdout-protocol guard for aria2c progress reporting.
    """
    if _normalize_download_method(download_method) == "urllib":
        pymss_download.ARIA2C_PATH = None
        _emit_download_log(task_id, "info", "Downloading via urllib by user setting")
        return

    legacy_aria2 = (
        download_model is not None
        and not _pymss_reports_progress(download_model)
        and getattr(pymss_download, "ARIA2C_PATH", None)
    )
    if legacy_aria2:
        pymss_download.ARIA2C_PATH = None
        _emit_download_log(
            task_id,
            "warning",
            "Installed pymss cannot report aria2c progress safely; downloading via urllib instead",
        )


def _watch_download_progress(
    *,
    files: list[tuple[str, Path]],
    expected_sizes: dict[str, int],
    already_done: set[str],
    stop: threading.Event,
    emit_progress: Any,
    count_partial_bytes: bool,
) -> None:
    """Report progress by watching the files pymss is writing.

    Used when the installed pymss cannot report progress itself. What it does guarantee is where
    the bytes land — files_for_studio_model names every destination, and a partial download sits
    beside it as `<dest>.part`. Sampling those sizes gives a percentage without reaching into how
    the transfer is done.

    `count_partial_bytes` says whether the partial file's size means anything yet. It only does
    when the downloader grows the file as bytes arrive, which urllib does and aria2 does not:
    aria2 preallocates, so the file reaches its full size in the first second and a bar built on
    it would jump to 100% and sit there for the rest of the download. Counting whole files is
    coarse, but it never claims to be finished when it is not.
    """
    poll_interval = 0.4
    # Rate over a short trailing window: an average over the whole transfer barely moves once a
    # large file is underway, which is useless for telling a slow connection from a stalled one.
    window_started_at = time.monotonic()
    window_start_bytes = -1
    # Held across iterations: the window only closes every few polls, and a rate recomputed from
    # scratch each time would report zero on every poll in between.
    speed = 0.0
    sizes = [expected_sizes.get(str(dest), 0) for _relpath, dest in files]
    total_sum = sum(sizes) if all(size > 0 for size in sizes) else 0

    while not stop.is_set():
        downloaded_by_file: dict[str, int] = {}
        completed = 0
        for _relpath, dest in files:
            key = str(dest)
            if key in already_done:
                size = expected_sizes.get(key, 0) or (dest.stat().st_size if dest.is_file() else 0)
                downloaded_by_file[key] = size
                completed += 1
                continue
            if dest.is_file():
                downloaded_by_file[key] = dest.stat().st_size
                completed += 1
                continue
            if not count_partial_bytes:
                downloaded_by_file[key] = 0
                continue
            partial = dest.with_name(dest.name + ".part")
            try:
                downloaded_by_file[key] = partial.stat().st_size if partial.is_file() else 0
            except OSError:
                downloaded_by_file[key] = 0

        downloaded_sum = sum(downloaded_by_file.values())
        now = time.monotonic()
        elapsed = now - window_started_at
        if window_start_bytes < 0:
            window_start_bytes = downloaded_sum
        elif elapsed >= 0.8:
            speed = max(0.0, (downloaded_sum - window_start_bytes) / elapsed)
            window_started_at = now
            window_start_bytes = downloaded_sum

        emit_progress(
            downloaded_bytes=downloaded_sum,
            total_bytes=total_sum,
            completed_files=completed,
            speed_bytes_per_second=speed,
        )
        stop.wait(poll_interval)


def _make_pymss_progress_adapter(
    *,
    skipped_bytes: int,
    skipped_files: int,
    total_bytes: int,
    emit_progress: Any,
) -> Any:
    """Turn pymss's per-file callback into one figure for the whole model.

    pymss reports bytes for the file it is currently working on and does not say which file that
    is. It does work through them one at a time, so a count that drops is the signal that one
    finished and the next began — enough to keep a running total without matching names. Files
    that were already valid never produce a callback at all, so their bytes seed the total.
    """
    finished_bytes = skipped_bytes
    finished_files = skipped_files
    current_done = 0
    # Rate over a short trailing window: an average over the whole transfer barely moves once a
    # large file is underway, which is useless for telling a slow connection from a stalled one.
    window_started_at = time.monotonic()
    window_start_bytes = skipped_bytes
    speed = 0.0

    def on_progress(done: int, _total: int, _message: str) -> None:
        nonlocal finished_bytes, finished_files, current_done
        nonlocal window_started_at, window_start_bytes, speed

        if done < current_done:
            finished_bytes += current_done
            finished_files += 1
        current_done = done
        downloaded = finished_bytes + current_done

        now = time.monotonic()
        elapsed = now - window_started_at
        if elapsed >= 0.8:
            speed = max(0.0, (downloaded - window_start_bytes) / elapsed)
            window_started_at = now
            window_start_bytes = downloaded

        emit_progress(
            downloaded_bytes=downloaded,
            total_bytes=total_bytes,
            completed_files=finished_files,
            speed_bytes_per_second=speed,
        )

    return on_progress


def cmd_download_model(payload: dict[str, Any]) -> int:
    """Download a model.

    Studio resolves the files so local Debug catalog entries download to the same paths that the
    model page and inference use. The transfer itself still uses pymss's downloader helpers.
    """
    model_name = payload.get("model")
    if not model_name:
        return emit_error("MODEL_NOT_FOUND", "Missing model name")

    task_id = payload.get("taskId") or f"download_{model_name}"
    model_dir = payload.get("modelDir") or None
    source = payload.get("source") or "modelscope"
    download_method = _normalize_download_method(payload.get("downloadMethod"))
    endpoint = payload.get("endpoint") or None
    force = bool(payload.get("force", False))
    timeout = _safe_int(payload.get("timeout"), 30)

    try:
        from pymss import model_download as pymss_model_download  # type: ignore
        from pymss.model_download import (  # type: ignore
            _already_valid,
            _expected_size_and_hash,
            download_model,
            fetch_modelscope_file_index,
        )
    except Exception as exc:
        return emit_error("PYMSS_IMPORT_FAILED", str(exc), traceback.format_exc(), task_id=task_id)

    stop_watching = threading.Event()
    watcher: threading.Thread | None = None
    try:
        prepare_pymss_download(pymss_model_download, task_id, download_model, download_method)

        entry, files = files_for_studio_model(model_name, model_dir)
        total_files = max(1, len(files))
        emit("download_started", {
            "model": entry.name,
            "source": source,
            "force": force,
            "totalFiles": total_files,
            "completedFiles": 0,
            "progress": 0,
        }, task_id=task_id)
        emit("download_stage", {"stage": "resolving_files", "progress": 0}, task_id=task_id)

        # Fresh downloads can fall back to file counts when index metadata is unavailable.
        # Existing cache files require a successful lookup unless a fresh download is forced.
        index = None
        try:
            index = fetch_modelscope_file_index(timeout=timeout, proxy=effective_proxy_url()) if endpoint is None else None
        except Exception as exc:
            _emit_download_log(task_id, "warning", f"File index unavailable; published progress and validation metadata cannot be used: {exc}")
        if endpoint is None:
            _require_index_for_cached_files(files, index, force=force)

        expected_sizes: dict[str, int] = {}
        already_done: set[str] = set()
        for relpath, dest in files:
            expected_size, expected_sha256 = _expected_size_and_hash(relpath, index)
            expected_sizes[str(dest)] = int(expected_size or 0)
            if not force and _already_valid(dest, expected_size, expected_sha256):
                already_done.add(str(dest))
                if not expected_sizes[str(dest)]:
                    expected_sizes[str(dest)] = dest.stat().st_size
            _emit_download_log(
                task_id,
                "info",
                f"[{len(expected_sizes)}/{total_files}] {relpath}"
                + (f", {expected_size / 1048576:.1f} MB" if expected_size else ""),
            )

        last_emitted_progress = -1
        last_emitted_files = -1
        last_emitted_metrics: tuple[int, int, float] | None = None
        last_emitted_at = 0.0

        def emit_progress(
            *,
            downloaded_bytes: int,
            total_bytes: int,
            completed_files: int,
            speed_bytes_per_second: float,
            force: bool = False,
        ) -> None:
            nonlocal last_emitted_progress, last_emitted_files, last_emitted_metrics, last_emitted_at
            if total_bytes > 0:
                progress = min(95, int(downloaded_bytes / total_bytes * 95))
            else:
                progress = min(95, int((completed_files / total_files) * 95))
            metrics = (downloaded_bytes, total_bytes, speed_bytes_per_second)
            now = time.monotonic()
            # Without a known total, byte/speed labels change even when the bar does not.
            # Sample those changes once a second; bar and file transitions remain immediate.
            if progress == last_emitted_progress and completed_files == last_emitted_files:
                if metrics == last_emitted_metrics or (not force and now - last_emitted_at < 1.0):
                    return
            last_emitted_progress = progress
            last_emitted_files = completed_files
            last_emitted_metrics = metrics
            last_emitted_at = now
            emit("download_progress", {
                "model": entry.name,
                "completedFiles": completed_files,
                "totalFiles": total_files,
                "aggregateDownloadedBytes": downloaded_bytes,
                "aggregateTotalBytes": total_bytes,
                "speedBytesPerSecond": speed_bytes_per_second,
                "progress": progress,
            }, task_id=task_id)

        emit("download_stage", {
            "stage": "downloading_files",
            "progress": 0,
            "message": "Downloading model files",
        }, task_id=task_id)

        # Where the progress numbers come from depends on what the installed pymss can tell us.
        # Asking it directly is exact; watching the files it writes is a fallback, and how much
        # that fallback can say depends on which downloader pymss picks.
        progress_callback = None
        if _pymss_reports_progress(download_model):
            progress_callback = _make_pymss_progress_adapter(
                skipped_bytes=sum(expected_sizes.get(key, 0) for key in already_done),
                skipped_files=len(already_done),
                total_bytes=sum(expected_sizes.values()) if all(size > 0 for size in expected_sizes.values()) else 0,
                emit_progress=emit_progress,
            )
        else:
            uses_aria2 = bool(getattr(pymss_model_download, "ARIA2C_PATH", None))
            watcher = threading.Thread(
                target=_watch_download_progress,
                kwargs={
                    "files": files,
                    "expected_sizes": expected_sizes,
                    "already_done": already_done,
                    "stop": stop_watching,
                    "emit_progress": emit_progress,
                    "count_partial_bytes": not uses_aria2,
                },
                daemon=True,
            )
            watcher.start()

        result = download_studio_model(
            pymss_model_download,
            model_name,
            files,
            source=source,
            endpoint=endpoint,
            force=force,
            timeout=timeout,
            progress_callback=progress_callback,
            proxy=effective_proxy_url(),
            task_id=task_id,
            file_index=index,
        )
        settled_bytes = sum(expected_sizes.get(str(dest), 0) or dest.stat().st_size for _relpath, dest in files)
    except KeyError as exc:
        # An unknown model name is the caller's mistake, not a transfer failure, and the UI tells
        # the two apart by this code.
        return emit_error("MODEL_NOT_FOUND", str(exc), task_id=task_id)
    except Exception as exc:
        return emit_error("MODEL_DOWNLOAD_FAILED", str(exc), traceback.format_exc(), task_id=task_id)
    finally:
        stop_watching.set()
        if watcher is not None:
            watcher.join(timeout=2)

    # A file is only known to be finished once the next one starts, so the last file of the set
    # is still counted as in flight when the call returns. Settle the count before the UI reads
    # it for the last time.
    emit_progress(
        downloaded_bytes=settled_bytes,
        total_bytes=settled_bytes,
        completed_files=total_files,
        speed_bytes_per_second=0.0,
        force=True,
    )

    downloaded = [str(item) for item in (result.get("downloaded") or [])]
    skipped = [str(item) for item in (result.get("skipped") or [])]
    emit("download_stage", {
        "stage": "verifying",
        "progress": 97,
        "message": "Verifying downloaded files",
    }, task_id=task_id)
    emit("download_done", {
        "model": entry.name,
        "downloaded": downloaded,
        "skipped": skipped,
        "modelDir": str(model_root(model_dir)),
        "modelInfo": model_to_dict(entry, model_dir, include_local_state=True),
        "source": effective_source_for(entry),
        "progress": 100,
    }, task_id=task_id)
    return 0
