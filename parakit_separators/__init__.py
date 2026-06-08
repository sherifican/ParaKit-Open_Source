"""ParaKit separator-slot architecture (F-INT-001, v4.4.4).

This package defines a small, model-agnostic interface for plugging neural
drum-stem separators into ParaKit's Audio → MIDI pipeline. The first plug-in
is Jarredou MDX23C (6-stem). Future research-validated separators (Mel-Band
RoFormer, SCNet XL, Banquet, etc., per F-DET-014b/c roadmap) plug into the
same slot without further architectural rework.

Reconciliation note: this slot reuses the stems-then-hybrid pipeline pattern
that F-DET-012 v1 tested with DrumSep and rejected. F-DET-014 v2 verdict A
confirms the pattern is viable with separators that don't share DrumSep's
RMS-louder regression: Jarredou MDX23C drives +0.240 snare / +0.162 kick /
+0.133 crash F1 over production hybrid B0. DrumSep is excluded from the
allowlist per its F-DET-012 verdict.

Architecture summary:
    1. User opts in via Settings ("Neural Stem Isolation (experimental)").
    2. _a2m_do_convert sees opt-in, calls active_separator.separate(audio).
    3. Result dict {class: stem_path} is composited into a single drums-
       equivalent .wav by composite.py.
    4. Composite is fed into the existing hybrid detection path (no
       detection logic changed).
    5. Default OFF; OFF path = current behavior verbatim.

The base class is intentionally narrow: name + output classes + license/
bundling status + path resolution + availability check + separate(). Any
future separator just implements these.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List


class StemSeparator(ABC):
    """Abstract base for any neural drum-stem separator that ParaKit can run.

    Concrete implementations live in this package as `<separator>.py` files.
    See `jarredou_mdx23c.py` for the v4.4.4 reference implementation.
    """

    # Short stable identifier, lowercase + underscore. Used as the cache
    # subdirectory name and the value persisted in config when this
    # separator is the active opt-in.
    name: str = ""

    # Per-class output names this separator produces, lowercase. ParaKit's
    # canonical drum-class set is {kick, snare, hihat, toms, cymbals, ride}.
    # The Jarredou plug-in returns all six; future plug-ins may produce
    # subsets. composite.py mixes whatever subset is present.
    output_classes: List[str] = []

    # License classification of the model weights. Used for owner-facing
    # disclosure in the first-use download dialog so the user knows what
    # they're agreeing to. Strings are free-form but kept consistent across
    # plug-ins ("NOASSERTION", "MIT", "Apache-2.0", "CC BY-NC 4.0", etc.).
    license_classification: str = "UNKNOWN"

    # Bundling status — what ParaKit's installer ships. Currently every
    # plug-in is "user-downloaded-only" because no separator weights have
    # licenses that permit bundling. The bundled-path lookup in
    # resolve_model_path() exists for future use if/when this changes.
    bundling_status: str = "user-downloaded-only"

    # Approximate model file size in MB, for the first-use dialog copy.
    model_size_mb: float = 0.0

    # Display name shown in the Settings dropdown — beginner-friendly,
    # not the raw `name`. For Jarredou this is "Jarredou MDX23C 6-stem".
    display_name: str = ""

    @abstractmethod
    def resolve_model_path(self) -> "Path | None":
        """Return the path to the model checkpoint on disk, or None if
        not present.

        Priority order (implemented uniformly in `model_resolver.py`):
            1. Bundled: `parakit_models/<name>/<model_file>`
            2. User cache: `%APPDATA%\\ParaKit\\separators\\<name>\\<model_file>`
               on Windows; `~/.config/parakit/separators/<name>/<model_file>`
               cross-platform.
            3. Legacy on-disk locations from earlier ParaKit versions
               (e.g. `jarredou_model/`) that the plug-in knows about.
            4. None — caller offers download dialog or falls back gracefully.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Return True iff resolve_model_path() returns a path AND the file
        passes a basic sanity check (exists, non-zero size). Used by the
        Settings UI to enable/disable the active-toggle and by
        _a2m_do_convert to decide whether to route through this separator
        or fall back to the default hybrid path."""

    @abstractmethod
    def separate(self, input_audio: "Path", output_dir: "Path") -> Dict[str, "Path"]:
        """Run inference on `input_audio` and write per-class stem WAVs into
        `output_dir`. Return a dict mapping class name (lowercase, e.g.
        "kick") to the absolute path of the corresponding stem WAV.

        The dict's keys are restricted to `self.output_classes`; missing
        classes are dropped silently rather than raising. Inference is CPU
        only in v4.4.4 (RTX 5070 sm_120 PyTorch incompatibility blocks
        GPU); future versions may add a device parameter.
        """


# Public registry of plug-ins. New separators added in future versions
# (Mel-Band RoFormer, SCNet XL, Banquet, etc., per F-DET-014b/c) register
# themselves here. Lazy-imported so the parakit_separators package itself
# doesn't pull torch / audio_separator on every ParaKit launch — only the
# active separator's module gets imported when the user opts in.
def get_separator(name: str) -> "StemSeparator | None":
    """Resolve a separator by `name`. Returns a fresh instance, or None if
    the name isn't recognized. Caller is responsible for caching the
    instance across calls if they want one stable object."""
    if name == "jarredou_mdx23c":
        from . import jarredou_mdx23c
        return jarredou_mdx23c.JarredouMDX23C()
    return None


def list_available_separators() -> List[Dict[str, str]]:
    """Return [{name, display_name, license_classification, bundling_status,
    model_size_mb, is_available}] for every registered separator. Used by
    the Settings UI to populate the dropdown and show per-separator
    download status. v4.4.4 has exactly one entry."""
    out: List[Dict[str, str]] = []
    for sep_name in ("jarredou_mdx23c",):
        sep = get_separator(sep_name)
        if sep is None:
            continue
        try:
            available = sep.is_available()
        except Exception:
            available = False
        out.append({
            "name":                   sep.name,
            "display_name":           sep.display_name,
            "license_classification": sep.license_classification,
            "bundling_status":        sep.bundling_status,
            "model_size_mb":          str(sep.model_size_mb),
            "is_available":           "1" if available else "0",
        })
    return out
