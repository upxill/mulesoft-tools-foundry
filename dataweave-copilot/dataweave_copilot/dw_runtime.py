"""Real DataWeave 2.0 execution -- Option A.

Research (2026-09-13): the official ``mulesoft/data-weave`` engine repo is
built from Scala/sbt and has no bundled portable pre-built CLI in its own
release notes reachable from this environment. However, MuleSoft also
publishes **mulesoft/data-weave-cli** (https://github.com/mulesoft/data-weave-cli),
which wraps the same engine and ships **self-contained, JVM-free native
binaries** (built with GraalVM native-image) on its GitHub Releases page --
for example ``dw-cli-2.12.0-macos-arm64.zip`` -- for macOS (arm64), Linux
(x86_64), and Windows (x86_64). No Java/Maven install was needed: the
binary was downloaded directly and confirmed to execute real ``.dwl``
scripts against real JSON/CSV input from the command line, e.g.::

    dw run -s -f script.dwl -i payload=input.json

This module downloads (once, cached) and shells out to that binary, so
``generate`` can actually run a generated script against a real sample
input file and compare its real output to an expected sample output,
instead of only checking that the script looks well-formed.

If the current platform has no published binary, or the one-time download
fails (e.g. no network), callers should fall back to static validation
only (``dw_validator.py``) -- this module reports that unavailability via
``is_available()`` / ``DataWeaveRuntimeUnavailable`` rather than crashing.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from pydantic import BaseModel

DW_CLI_VERSION = "2.12.0"
_RELEASE_BASE = f"https://github.com/mulesoft/data-weave-cli/releases/download/v{DW_CLI_VERSION}"

# (system, arch) -> (release asset zip name, path to the executable inside it)
_ASSETS: dict[tuple[str, str], tuple[str, str]] = {
    ("darwin", "arm64"): (f"dw-cli-{DW_CLI_VERSION}-macos-arm64.zip", "bin/dw"),
    ("linux", "x86_64"): (f"dw-cli-{DW_CLI_VERSION}-linux-x86_64.zip", "bin/dw"),
    ("windows", "amd64"): (f"dw-cli-{DW_CLI_VERSION}-windows-x86_64.zip", "bin/dw.exe"),
}

CACHE_DIR = Path(os.environ.get("DATAWEAVE_COPILOT_CACHE", str(Path.home() / ".cache" / "dataweave-copilot")))


class DataWeaveRuntimeUnavailable(RuntimeError):
    """Raised when real DataWeave execution can't be provided on this
    platform/environment (unsupported platform, or download failed)."""


class ExecutionResult(BaseModel):
    success: bool
    stdout: str
    stderr: str
    returncode: int
    command: list[str]


def _platform_key() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        return ("darwin", "arm64" if machine in ("arm64", "aarch64") else "x86_64")
    if system == "linux":
        return ("linux", "x86_64" if machine in ("x86_64", "amd64") else machine)
    if system == "windows":
        return ("windows", "amd64")
    return (system, machine)


def is_platform_supported() -> bool:
    return _platform_key() in _ASSETS


def _binary_path() -> Path:
    key = _platform_key()
    system, arch = key
    inner_path = _ASSETS[key][1] if key in _ASSETS else ("dw.exe" if system == "windows" else "dw")
    return CACHE_DIR / f"dw-cli-{DW_CLI_VERSION}-{system}-{arch}" / inner_path


def ensure_binary(download: bool = True) -> Path:
    """Return a path to a working ``dw`` native CLI binary, downloading it
    from GitHub Releases into a local cache directory if needed."""
    key = _platform_key()
    if key not in _ASSETS:
        raise DataWeaveRuntimeUnavailable(
            f"No prebuilt DataWeave CLI binary is published for platform {key}. "
            f"Supported: {sorted(_ASSETS)}."
        )

    exe_path = _binary_path()
    if exe_path.exists():
        return exe_path

    if not download:
        raise DataWeaveRuntimeUnavailable(f"DataWeave CLI binary not found at {exe_path} and download=False.")

    asset_name, _inner_path = _ASSETS[key]
    url = f"{_RELEASE_BASE}/{asset_name}"
    system, arch = key
    # Extract at the version/platform root, not exe_path.parent -- the
    # release zip already contains the nested path (e.g. "bin/dw"), so
    # extracting into a directory already named "bin" would double it up.
    dest_dir = CACHE_DIR / f"dw-cli-{DW_CLI_VERSION}-{system}-{arch}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / asset_name
        try:
            req = Request(url, headers={"User-Agent": "dataweave-copilot"})
            with urlopen(req, timeout=120) as resp, open(archive_path, "wb") as out:
                shutil.copyfileobj(resp, out)
        except Exception as exc:  # noqa: BLE001 -- surfaced as a clean, typed error
            raise DataWeaveRuntimeUnavailable(
                f"Failed to download DataWeave CLI from {url}: {exc}"
            ) from exc

        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest_dir)

    if not exe_path.exists():
        raise DataWeaveRuntimeUnavailable(f"Downloaded archive did not contain expected binary at {exe_path}.")

    mode = exe_path.stat().st_mode
    exe_path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return exe_path


def is_available(download: bool = False) -> bool:
    """Cheap availability check. With download=False this never touches
    the network -- it only reports whether a binary is already cached or
    whether the platform is one we know how to fetch a binary for. Pass
    download=True to actually attempt the (cached, one-time) download."""
    try:
        if _binary_path().exists():
            return True
        if not download:
            return is_platform_supported()
        ensure_binary(download=True)
        return True
    except DataWeaveRuntimeUnavailable:
        return False


_NOISE_MARKERS = ("WARNING:", "sun.misc.Unsafe")


def _clean_stderr(raw_stderr: str) -> str:
    """Strip the native-image JVM-shim deprecation warnings that the
    binary prints on stderr on every run -- they aren't DataWeave errors
    and would otherwise pollute the feedback we hand back to the model."""
    lines = [
        line for line in raw_stderr.splitlines()
        if not any(marker in line for marker in _NOISE_MARKERS)
    ]
    # Strip ANSI color codes DataWeave uses for [ERROR] highlighting.
    import re
    cleaned = "\n".join(lines).strip()
    return re.sub(r"\x1b\[[0-9;]*m", "", cleaned)


def run_script(
    script_path: str | Path,
    inputs: dict[str, str | Path] | None = None,
    timeout: float = 30.0,
) -> ExecutionResult:
    """Actually execute a DataWeave 2.0 script against real input file(s)
    using the downloaded native ``dw`` CLI, and return its real stdout and
    (cleaned) stderr. Raises DataWeaveRuntimeUnavailable if no binary can
    be obtained for this platform."""
    binary = ensure_binary(download=True)
    cmd = [str(binary), "run", "-s", "-f", str(script_path)]
    for name, path in (inputs or {}).items():
        cmd.extend(["-i", f"{name}={path}"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return ExecutionResult(
            success=False, stdout="", stderr=f"Execution timed out after {timeout}s: {exc}",
            returncode=-1, command=cmd,
        )
    except OSError as exc:
        raise DataWeaveRuntimeUnavailable(f"Failed to execute DataWeave CLI: {exc}") from exc

    return ExecutionResult(
        success=(proc.returncode == 0),
        stdout=proc.stdout,
        stderr=_clean_stderr(proc.stderr),
        returncode=proc.returncode,
        command=cmd,
    )
