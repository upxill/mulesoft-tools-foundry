"""The `watch` loop: runs the sync engine (cli.sync_one_flow_file) forever,
triggered by changes to flow XML files, until Ctrl+C.

Two trigger mechanisms:

  1. mtime polling (default, always available, zero extra dependencies).
     Stats every ``<flow-dir>/*.xml`` file every ``poll_interval`` seconds
     and reacts to changed mtimes (including newly-created flow files).
     This is what this project's own live verification relies on, since
     it is guaranteed to work in any sandboxed environment.
  2. ``watchdog``-based OS-level filesystem events (``--use-watchdog``),
     for real-world responsiveness. Imported lazily, INSIDE the function
     that needs it, so the default poll mode never depends on ``watchdog``
     being installed or working -- if ``--use-watchdog`` is passed but the
     package isn't available (or fails for any reason), this falls back to
     the poll loop rather than crashing the whole watch session.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict

from .report import format_status_line
from .test_file_index import NamingConvention, find_flow_files


def _sync_and_report(flow_file: Path, project_dir: Path, convention: NamingConvention, enrich: bool, model: str, verbose: bool) -> None:
    # Imported lazily to avoid a circular import (cli imports watcher for `watch`).
    from .cli import sync_one_flow_file

    result = sync_one_flow_file(flow_file, project_dir, convention, enrich, model, dry_run=False)
    line = format_status_line(result, verbose=verbose)
    if line:
        print(line, flush=True)


def _poll_loop(
    project_dir: Path,
    convention: NamingConvention,
    enrich: bool,
    model: str,
    poll_interval: float,
    verbose: bool,
    _stop_event=None,
) -> None:
    """The default watch loop. ``_stop_event`` is a test-only hook (a
    ``threading.Event``) that lets the test suite run this loop in a
    background thread and stop it deterministically instead of relying on
    ``KeyboardInterrupt`` -- production callers never pass it, so real
    behavior (loop forever until Ctrl+C) is unchanged.
    """
    mtimes: Dict[Path, float] = {}

    while _stop_event is None or not _stop_event.is_set():
        try:
            flow_files = find_flow_files(project_dir, convention)
        except OSError:
            flow_files = []

        current: Dict[Path, float] = {}
        changed = []
        for f in flow_files:
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            current[f] = mtime
            if mtimes.get(f) != mtime:
                changed.append(f)

        mtimes = current

        for f in changed:
            _sync_and_report(f, project_dir, convention, enrich, model, verbose)

        if _stop_event is not None:
            if _stop_event.wait(poll_interval):
                break
        else:
            time.sleep(poll_interval)


def _watchdog_loop(
    project_dir: Path,
    convention: NamingConvention,
    enrich: bool,
    model: str,
    verbose: bool,
) -> bool:
    """Returns True if the watchdog-based loop ran (until interrupted);
    returns False if watchdog isn't usable, so the caller can fall back to
    the poll loop.
    """
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except Exception:  # noqa: BLE001 - any import/setup problem -> fall back to polling
        print("munit-shadow: --use-watchdog requested but the 'watchdog' package isn't available; falling back to mtime polling.")
        return False

    flow_dir = Path(project_dir) / convention.flow_dir

    class _Handler(FileSystemEventHandler):
        def _maybe_sync(self, path_str: str) -> None:
            path = Path(path_str)
            if path.suffix == ".xml" and path.parent == flow_dir:
                _sync_and_report(path, project_dir, convention, enrich, model, verbose)

        def on_modified(self, event):
            if not event.is_directory:
                self._maybe_sync(event.src_path)

        def on_created(self, event):
            if not event.is_directory:
                self._maybe_sync(event.src_path)

    try:
        observer = Observer()
        observer.schedule(_Handler(), str(flow_dir), recursive=False)
        observer.start()
    except Exception:  # noqa: BLE001 - e.g. inotify limits hit in a sandbox -> fall back
        print("munit-shadow: --use-watchdog failed to start; falling back to mtime polling.")
        return False

    try:
        while True:
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()

    return True  # pragma: no cover - unreachable (loop only exits via exception)


def watch(
    project_dir: Path,
    convention: NamingConvention,
    enrich: bool = False,
    model: str = "claude-opus-5",
    poll_interval: float = 2.0,
    use_watchdog: bool = False,
    verbose: bool = False,
    _stop_event=None,
) -> None:
    project_dir = Path(project_dir)

    # Initial full pass, same as running `sync` once, so a developer who
    # starts `watch` on a project that's already drifted gets caught up
    # immediately rather than waiting for the next edit.
    from .cli import run_sync

    run_sync(project_dir, convention, enrich=enrich, model=model, dry_run=False, verbose=verbose)

    mode = "watchdog" if use_watchdog else "poll"
    print(
        f"munit-shadow: watching {project_dir / convention.flow_dir} "
        f"({'OS filesystem events' if use_watchdog else f'polling every {poll_interval}s'}) -- Ctrl+C to stop.",
        flush=True,
    )

    if use_watchdog:
        ran = _watchdog_loop(project_dir, convention, enrich, model, verbose)
        if ran:
            return
        # fell through: watchdog unusable, use the always-available poll loop

    _poll_loop(project_dir, convention, enrich, model, poll_interval, verbose, _stop_event=_stop_event)
