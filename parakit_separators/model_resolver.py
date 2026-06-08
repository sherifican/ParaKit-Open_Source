"""Bundled / cache / legacy path resolution + download helpers (F-INT-001).

Every separator plug-in calls into this module to figure out where its model
file lives (or doesn't) and to download it on first opt-in. ParaKit owns the
download UX so error messages, progress dialogs, retry paths, and hash
verification all match the rest of the app.

Path priority (high → low):
    1. Bundled:   <project_root>/parakit_models/<name>/<model_file>
    2. User cache:
         Windows: %APPDATA%\\ParaKit\\separators\\<name>\\<model_file>
         macOS / Linux: ~/.config/parakit/separators/<name>/<model_file>
    3. Legacy on-disk locations from earlier ParaKit versions, registered
       per-plug-in (e.g. Jarredou's pre-existing jarredou_model/ directory).
    4. None — caller offers download dialog.

Why three layers: bundled-first lets a packaged release ship a model with
zero runtime download. User-cache-second lets the opt-in download flow
work without write-access to the install directory. Legacy-third keeps
the v4.4.4 transition smooth for users who already have the model from
F-DET-005 / F-DET-014 era work.
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# Path roots
# ---------------------------------------------------------------------------
def project_root() -> Path:
    """ParaKit project root. parakit_separators/ lives one level below it."""
    return Path(__file__).resolve().parent.parent


def bundled_root() -> Path:
    """parakit_models/ — empty in v4.4.4 (no bundlable separator licenses
    yet) but the lookup path is wired in for future use."""
    return project_root() / "parakit_models"


def user_cache_root() -> Path:
    """Per-user writable cache for opt-in-downloaded models. Windows uses
    %APPDATA%\\ParaKit\\separators; macOS/Linux use ~/.config/parakit/separators
    so the same file paths work across operating systems. Created on first
    download attempt; safe to call when it doesn't exist yet."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "ParaKit" / "separators"
        # APPDATA missing — extremely rare, fall back to home
        return Path.home() / ".parakit" / "separators"
    return Path.home() / ".config" / "parakit" / "separators"


# ---------------------------------------------------------------------------
# Per-separator path resolution
# ---------------------------------------------------------------------------
def candidate_paths(separator_name: str, model_filename: str,
                    legacy_paths: Optional[Iterable[Path]] = None) -> list[Path]:
    """Return ordered list of paths to check for a model file.

    `legacy_paths` is an optional iterable of plug-in-specific extra paths
    checked AFTER the bundled and user-cache locations. Jarredou uses this
    to find a pre-v4.4.4 jarredou_model/ checkout."""
    out: list[Path] = []
    out.append(bundled_root() / separator_name / model_filename)
    out.append(user_cache_root() / separator_name / model_filename)
    if legacy_paths:
        for p in legacy_paths:
            out.append(Path(p))
    return out


def resolve(separator_name: str, model_filename: str,
            legacy_paths: Optional[Iterable[Path]] = None) -> Optional[Path]:
    """Return the first existing, non-zero-byte path from candidate_paths(),
    or None if no path resolves. Caller treats None as 'offer download'."""
    for p in candidate_paths(separator_name, model_filename, legacy_paths):
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p.resolve()
        except OSError:
            # Permission issues or unreadable network paths — keep looking
            continue
    return None


def cache_path_for_download(separator_name: str, model_filename: str) -> Path:
    """Where this separator's model file SHOULD land when downloaded.
    Always points at the user cache (never bundled, since bundled is the
    install-time location and we can't write there). Parent dirs are
    created as a side-effect — caller can write immediately."""
    p = user_cache_root() / separator_name / model_filename
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Download + hash verify
# ---------------------------------------------------------------------------
def sha256_of_file(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    """Stream the file through sha256. ~1 GB models are too big to slurp
    into memory all at once. chunk_bytes=1 MB is a good balance between
    syscall count and RAM peak."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_bytes)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class DownloadError(Exception):
    """Raised by download_with_progress on any non-recoverable failure.
    Caller (Settings UI / first-use dialog) catches this and shows a
    user-readable message + Retry button."""


def download_with_progress(url: str,
                           dest_path: Path,
                           expected_size_bytes: Optional[int] = None,
                           expected_sha256: Optional[str] = None,
                           progress_cb: Optional[Callable[[int, int], None]] = None,
                           timeout_s: int = 300) -> Path:
    """Stream a URL to dest_path, optionally verify size + sha256.

    progress_cb(bytes_done, bytes_total) is called periodically (~16 times
    per second of download wall time when total is known). On hash or size
    mismatch the partial file is unlinked and DownloadError is raised — the
    caller can offer Retry without leaving a corrupted file behind.

    Network operates over plain urllib (already in stdlib, no new deps).
    HTTPS is used unconditionally; we don't verify cert pinning beyond
    what the system trust store enforces, matching ParaKit's existing
    Deno/yt-dlp downloads."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

    req = urllib.request.Request(
        url,
        headers={"User-Agent":
                 "ParaKit/4.4.4 (separator-slot model fetcher)"})

    last_progress_emit = 0.0
    bytes_done = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            total = expected_size_bytes
            if total is None:
                length_hdr = resp.headers.get("Content-Length")
                if length_hdr:
                    try:
                        total = int(length_hdr)
                    except (TypeError, ValueError):
                        total = None
            with open(tmp_path, "wb") as out_f:
                import time as _t
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    bytes_done += len(chunk)
                    if progress_cb is not None:
                        now = _t.time()
                        if now - last_progress_emit >= 1.0 / 16.0:
                            try:
                                progress_cb(bytes_done, total or 0)
                            except Exception:
                                pass
                            last_progress_emit = now
    except urllib.error.URLError as e:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise DownloadError(f"Network error: {e}") from e
    except Exception as e:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise DownloadError(f"Download failed: {e}") from e

    # Final progress emit so the dialog reads 100% before the verify step
    if progress_cb is not None:
        try:
            progress_cb(bytes_done, bytes_done)
        except Exception:
            pass

    # Size check (when known)
    if expected_size_bytes is not None and bytes_done != expected_size_bytes:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise DownloadError(
            f"Size mismatch after download: got {bytes_done} bytes, "
            f"expected {expected_size_bytes}.")

    # Hash check (when known). Skipped when expected_sha256 is None — first
    # opt-in cycle for a new separator may ship without a pinned hash; the
    # next ParaKit version can backfill it from a known-good local file.
    if expected_sha256:
        actual = sha256_of_file(tmp_path)
        if actual.lower() != expected_sha256.lower():
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise DownloadError(
                f"Hash mismatch after download. Expected {expected_sha256}, "
                f"got {actual}. The downloaded file has been removed; "
                f"please retry.")

    # Atomic rename so a partial download never gets mistaken for a
    # complete one (resolve() looks at non-zero size, which a partial would
    # also pass).
    try:
        if dest_path.exists():
            dest_path.unlink()
        tmp_path.rename(dest_path)
    except OSError as e:
        raise DownloadError(f"Failed to finalize download: {e}") from e

    return dest_path
