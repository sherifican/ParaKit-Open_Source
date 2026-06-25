"""Jarredou MDX23C 6-stem drum separator (F-INT-001 v4.4.4 first plug-in).

Wraps the python-audio-separator library (already in ParaKit's venv) which
ships an inference path for MDX23C checkpoints. The model is the public
"DrumSep 6stem | (by aufr33 & jarredou)" checkpoint — see download_url
below for upstream provenance.

Output classes (per the upstream model card):
    kick, snare, hihat (upstream "hh"), toms, cymbals (upstream "crash"), ride

Ride stem is captured but NOT yet routed to detection in v4.4.4. Production
hybrid uses ride from default detection only; integrating Jarredou's ride
stem into the detector is a future capability tracked separately. The
captured ride.wav is preserved on disk so a future ParaKit version can
read it without re-running the separator.

License classification: NOASSERTION. Upstream repo (jarredou/models) does
not declare a license; ParaKit therefore does NOT bundle the weights and
flags this clearly in the first-use download dialog.

Reconciliation note (per F-DET-012 verdict): F-DET-012 rejected DrumSep
stems as a per-component detection source because DrumSep stems' RMS-louder
quirk inflated onset FPs. F-DET-014 v2 verdict A measured Jarredou stems
do NOT share that regression and lift production hybrid F1 by +0.240 snare /
+0.162 kick / +0.133 crash on Track A metal corpus. The slot's allowlist
explicitly excludes DrumSep.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from . import StemSeparator
from . import model_resolver


# ---------------------------------------------------------------------------
# Model identity + provenance
# ---------------------------------------------------------------------------
# Upstream filename + URLs from python-audio-separator's
# `download_checks.json` line 272 ("MDX23C Model: DrumSep 6stem | (by aufr33
# & jarredou)"). Documented here so ParaKit's downloader is library-
# agnostic — if we swap inference layers later, the URL stays the same.
MODEL_FILENAME = "MDX23C-DrumSep-aufr33-jarredou.ckpt"
UPSTREAM_FILENAME = "aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.ckpt"
# 2026-06-04 (F-INT-002): the original GitHub source 404'd — jarredou/models repo +
# releases were deleted, breaking first-use download for new users. The checkpoint is
# mirrored BYTE-IDENTICALLY (verified 2026-06-04: its SHA256 == EXPECTED_SHA256 below)
# at Politrees/UVR_resources on Hugging Face. urllib (model_resolver.download_with_progress)
# follows the HF resolve->CDN 302 redirect, and the post-download SHA256 verify still gates
# correctness. UPSTREAM_FILENAME + the old GitHub URL are kept above/here for provenance only.
#   Old (dead): github.com/jarredou/models/releases/download/aufr33-jarredou_MDX23C_DrumSep_model_v0.1/<UPSTREAM_FILENAME>
#   Fallback mirror (now WIRED as DOWNLOAD_URL_FALLBACK below — tried automatically on primary
#   failure; SHA NOT pre-verified, but the post-download check rejects it if the bytes differ):
#     https://huggingface.co/Sucial/MSST-WebUI/resolve/main/All_Models/multi_stem_models/aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.ckpt
DOWNLOAD_URL = (
    "https://huggingface.co/Politrees/UVR_resources/resolve/main/"
    "models/MDX23C/MDX23C-DrumSep-aufr33-jarredou.ckpt"
)
# Secondary HF mirror — tried automatically if the primary fails (wired 2026-06-25). Same
# checkpoint, different uploader (Sucial/MSST-WebUI); verified 2026-06-25 to be byte-size-identical
# (437,652,699 B) to the primary. The post-download SHA256 verify still gates correctness either way.
DOWNLOAD_URL_FALLBACK = (
    "https://huggingface.co/Sucial/MSST-WebUI/resolve/main/"
    "All_Models/multi_stem_models/"
    "aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.ckpt"
)
CONFIG_URL = (
    "https://raw.githubusercontent.com/TRvlvr/application_data/main/"
    "mdx_model_data/mdx_c_configs/"
    "aufr33-jarredou_DrumSep_model_mdx23c_ep_141_sdr_10.8059.yaml"
)
# SHA256 of the known-good local file (computed 2026-05-09 from the
# 437,652,699-byte checkpoint at jarredou_model/MDX23C-DrumSep-aufr33-
# jarredou.ckpt). Used by model_resolver.download_with_progress to verify
# fresh downloads. Update this when the upstream artifact changes.
EXPECTED_SHA256 = "D2A4AA53EB584D21EEAD358A4E66D1882AD182911BE018F052B5DA73BE9096D0"
EXPECTED_SIZE_BYTES = 437652699
APPROX_SIZE_MB = 417.4

# Mapping from upstream Jarredou stem names → ParaKit canonical names.
# Same convention used by tools/dev/jarredou_detector.py and
# _f_det_014_jarredou_extract.py — keeps the existing extraction-script
# corpus consumable without renaming. "ride" stays "ride" (captured but
# not routed to detection in v4.4.4 — see module docstring).
STEM_MAP_UPSTREAM_TO_CANONICAL = {
    "kick":  "kick",
    "snare": "snare",
    "hh":    "hihat",
    "toms":  "toms",
    "crash": "cymbals",
    "ride":  "ride",
}

# Per-class stem files this plug-in writes. Order matters for the composite
# stage — the downstream detection path expects all-but-ride mixed together
# as the drums-equivalent input.
OUTPUT_CLASSES = ["kick", "snare", "hihat", "toms", "cymbals", "ride"]

# Classes that get fed into the composite (and therefore the detector). Ride
# is captured to disk but excluded from the composite — hybrid detection
# already handles ride from the original drums-only audio, so re-feeding it
# would double-count. When ride routing matures, flip this list.
COMPOSITE_CLASSES = ["kick", "snare", "hihat", "toms", "cymbals"]


class JarredouMDX23C(StemSeparator):
    name = "jarredou_mdx23c"
    output_classes = OUTPUT_CLASSES
    license_classification = "NOASSERTION"
    bundling_status = "user-downloaded-only"
    model_size_mb = APPROX_SIZE_MB
    display_name = "Jarredou MDX23C 6-stem"

    # Legacy on-disk paths from earlier ParaKit versions where this model
    # may already exist. Checked AFTER bundled / cache so a packaged
    # release can override an old local copy if needed.
    LEGACY_PATHS = (
        # F-DET-005 / F-DET-014 era: model lived under jarredou_model/
        model_resolver.project_root() / "jarredou_model" / MODEL_FILENAME,
    )

    def resolve_model_path(self) -> "Path | None":
        return model_resolver.resolve(
            self.name, MODEL_FILENAME, legacy_paths=self.LEGACY_PATHS)

    def is_available(self) -> bool:
        p = self.resolve_model_path()
        return p is not None and p.is_file() and p.stat().st_size > 0

    def separate(self, input_audio: Path, output_dir: Path) -> Dict[str, Path]:
        """Run MDX23C inference, write 6 stem WAVs to output_dir, return
        {canonical_class: path} dict.

        Uses python-audio-separator under the hood. The library handles
        config-yaml lookup automatically when the .ckpt is in a folder it
        scans. We pass `model_file_dir` pointing at the model's parent so
        the library finds both files together.

        CPU-only in v4.4.4. RTX 5070 sm_120 PyTorch incompatibility blocks
        GPU; future versions add a device parameter when PyTorch catches up.
        """
        from audio_separator.separator import Separator

        model_path = self.resolve_model_path()
        if model_path is None:
            raise RuntimeError(
                "Jarredou MDX23C model not found. Run the download flow "
                "from the Audio → MIDI Settings panel first.")

        input_audio = Path(input_audio).resolve()
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        s = Separator(
            model_file_dir=str(model_path.parent),
            output_dir=str(output_dir),
            output_format="WAV",
            mdxc_params={"overlap": 1, "batch_size": 1},
        )
        s.load_model(model_path.name)
        produced = s.separate(str(input_audio))

        # Walk the produced files, normalize their names, build the result
        # dict. python-audio-separator names files like
        # "<input_stem>_(<class>)_<model_id>.wav", so we extract the class
        # token between the first "_(" and the next ")_".
        result: Dict[str, Path] = {}
        for fname in produced:
            src = output_dir / fname
            if not src.is_file():
                continue
            # Defensive parse — skip anything that doesn't match the pattern
            if "_(" not in fname or ")_" not in fname:
                continue
            upstream_class = fname.split("_(", 1)[1].split(")_", 1)[0]
            canonical = STEM_MAP_UPSTREAM_TO_CANONICAL.get(upstream_class)
            if canonical is None:
                continue
            dst = output_dir / f"{canonical}.wav"
            if dst.exists() and dst != src:
                try:
                    dst.unlink()
                except OSError:
                    pass
            if src != dst:
                try:
                    src.rename(dst)
                except OSError:
                    # If rename fails (locked file?), fall back to copy
                    import shutil
                    shutil.copy2(str(src), str(dst))
            result[canonical] = dst.resolve()

        return result
