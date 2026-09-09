#!/usr/bin/env python3
"""Check 1 (rooted asset inventory) and Check 2 (candidate identity / raw hashes / delivery set / frozen-reference drift / served NPZ validity / served ONNX validity).

No application module is imported. The selected manifest generator is imported
by path, but its main() and git helpers are never called.

This is a bounded symbolic scanner, not arbitrary Python execution:
* rooted expressions and named required leaves are inventoried;
* arbitrary caller-supplied paths without that provenance are outside its scope;
* unresolved inventoried references remain visible;
* local-import delivery: root sidecars, shipped packages, relative
  imports, literal importlib names, and the rlrr_parse dual layout.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

sys.dont_write_bytecode = True

APP = "ParaKit v4.0.py"
UNKNOWN = (("unknown", "?"),)
ROOT_KINDS = {"install", "source", "executable", "bundle", "unresolved"}
# The syntactic guard matcher (Analyzer.exists_guard) recognizes these
# spellings and no others. An unrecognized spelling is not evidence.
GUARD_ROOT_CALLS = ("_external_base_dir",)
GUARD_DIRNAMES = ("os.path.dirname", "posixpath.dirname", "ntpath.dirname")
GUARD_ABSPATHS = (
    "os.path.abspath", "os.path.realpath", "os.path.normpath",
    "posixpath.abspath", "ntpath.abspath",
)
GUARD_JOINS = ("os.path.join", "posixpath.join", "ntpath.join")
GUARD_MODULES = ("os.path", "posixpath", "ntpath")
HELPERS = {
    "_external_base_dir",
    "_external_search_dirs",
    "_external_path",
    "_neon_resource_path",
}
ROOT_TABLE = {
    "_external_base_dir": "install",
    "_external_search_dirs": "Requirements and install alternatives",
    "_external_path": "Requirements and install alternatives; write intent retained",
    "_neon_resource_path": "resource",
    "dirname(abspath(__file__))": "module directory",
    "dirname(sys.executable)": "executable directory; conditions retained",
    "getattr(sys, '_MEIPASS', ...)": "bundle directory",
    "Path / join / aliases": "symbolic path operations",
}

REQUIRED_SETS = {
    "templates": (
        "default.json",
        "fast_rock.json",
        "metal.json",
    ),
    "assets/progressbar": (
        "dot_lit_v2.png",
        "dot_unlit.png",
        "dot_lit_v3_tight.png",
    ),
    "assets/ui/progress_bars/snare_node_line": tuple(
        f"{kind}_{size}.png"
        for kind in ("snare_idle", "snare_hit_left", "snare_hit_right")
        for size in (24, 32)
    ),
}
REQUIRED_DIRS = {
    "templates",
    "icons",
    "assets/progressbar",
    "assets/ui/progress_bars",
    "assets/ui/progress_bars/snare_node_line",
}
REQUIRED_LEAVES = {
    "templates",
    "default.json",
    "fast_rock.json",
    "metal.json",
    "parakit_cymbal_cleanup.npz",
    "parakit_kick_cleanup.npz",
}
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
TOOL_REFERENCE_PATHS = (
    "tools/gen_update_manifest.py",
    "tools/release_gate.py",
)
DELIVERY_S = (
    APP,
    "update_manifest.json",
)
REQUIRED_GATE_TOOLS = (
    "tools/gen_update_manifest.py",
    "tools/release_gate.py",
    "tools/ship_parity.py",
    "tools/check_release_file_drift.py",
)
DELIVERY_SCREENSHOTS = (
    "screenshots/app-01-single-song-creator.png",
    "screenshots/app-02-create-multiple-songs.png",
    "screenshots/app-02b-create-multiple-songs-batch.png",
    "screenshots/app-03-audio-to-ogg.png",
    "screenshots/app-04-stem-splitter.png",
    "screenshots/app-05-audio-to-midi.png",
    "screenshots/app-06-midi-editor.png",
    "screenshots/app-06b-spectral-comparison.png",
    "screenshots/app-06c-spectral-spectrogram.png",
    "screenshots/app-06d-spectral-waveform.png",
    "screenshots/app-07-sheet-music-to-midi.png",
    "screenshots/app-08-youtube-to-flac.png",
    "screenshots/app-09-asset-manager.png",
    "screenshots/app-10-song-tester.png",
    "screenshots/app-11a-preview.png",
    "screenshots/app-11b-practice.png",
    "screenshots/app-11c-practice-gameplay.png",
    "screenshots/app-12-quick-start-faq.png",
    "screenshots/kick-after-before.png",
    "screenshots/kick-after-dedup-button.png",
    "screenshots/kick-after-dedup-dialog.png",
    "screenshots/kick-after-result.png",
    "screenshots/kick-after-zoomed-out.png",
    "screenshots/kick-dedup-enable.png",
    "screenshots/kick-dedup-kick-slider.png",
    "screenshots/practice-v3-gameplay.png",
    "screenshots/practice-v3-home.png",
    "screenshots/practice-v3-kit-studio.png",
    "screenshots/practice-v3-song-details.png",
    "screenshots/practice-web-gameplay-1.png",
    "screenshots/practice-web-gameplay-2.png",
    "screenshots/practice-web-setup.png",
    "screenshots/research-hub.png",
    "screenshots/web-midi-baseline-badge.png",
    "screenshots/web-midi-compat-table.png",
)
DELIVERY_E = (
    ".gitattributes",
    ".gitignore",
) + REQUIRED_GATE_TOOLS + DELIVERY_SCREENSHOTS
JUSTIFIED_GIT_ONLY = (
    (
        "parakit_cleanup/parakit_kick_cleanup_40feat_backup_pre4553.npz",
        "pre-4.5.5.3 40-feature kick-cleanup model; generator EXCLUDE_SUBSTR omits it from M; git-only backup, not updater-delivered",
    ),
    (
        "parakit_cleanup/parakit_kick_cleanup_40feat_backup_pre4553.npz.json",
        "json sidecar of the pre-4.5.5.3 40-feature kick-cleanup backup pair; same justification",
    ),
)
# Served cleanup models the app loads with NumpyRF.load (parakit_cleanup/).
SERVED_CLEANUP_NPZ = (
    "parakit_cleanup/parakit_cymbal_cleanup.npz",
    "parakit_cleanup/parakit_kick_cleanup.npz",
)
# Every key NumpyRF.load reads unconditionally, in its read order.
NPZ_REQUIRED_KEYS = (
    "classes",
    "scaler_mean",
    "scaler_scale",
    "n_trees",
    "tree_offsets",
    "feat",
    "thr",
    "left",
    "right",
    "leaf_proba",
)
# Served drum model the app loads with InferenceSession on bytes
# (ParaKit v4.0.py `_a2m_model_is_loadable`) and converts with a 90-mel /
# 5-class layout (`_a2m_run_onnx_detection`). Names are read dynamically;
# the layout is not.
SERVED_ONNX = (
    "parakit_drum_model.onnx",
)
ONNX_PROBE_FRAMES = 8
ONNX_MEL_BINS = 90
ONNX_N_CLASSES = 5

# Exact code-supported dispositions, not extension-based exemptions.
# Main app: music21_data, logs, and album-art directory are created/written.
# Sprite sidecars: the two self-test images are saved by their self-tests.
GENERATED = {
    "music21_data",
    "ALBUM ART ASSETS",
    "midi_input_test_log.txt",
    "parakit_debug.log",
    "parakit_crash.txt",
    "_sprites_selftest.png",
    "_practice_sprites_selftest.png",
}
if True:  # check1 generated practice_v2 recent_files
    GENERATED = GENERATED | {"practice_v2/recent_files.json"}
# Main app has explicit download/cache routes for these names.
# model_resolver.py documents parakit_models as an opt-in model lookup.
DOWNLOADED = {"deno.exe", "drumsep_model", "demucs_models", "parakit_models"}
# Exact local/PATH probes; these are not inferred merely from ".exe".
PROBES = {
    "node.exe",
    "node/node.exe",
    "ffmpeg.exe",
    "ffprobe.exe",
    "yt-dlp",
    "yt-dlp.exe",
    "adb.exe",
    "platform-tools/adb.exe",
}

# Exact code-supported dispositions, not extension-based exemptions.
# Generator CONTENT_DIRS comment: tools = dev-only. Public does not ship
# tools/dev/. Enhanced Detection is hard-forced off; these two joins are
# the unused default script and the convert-path fallback. Exact strings,
# not a tools/ prefix: the four gate tools under tools/ do ship.
DEV_ONLY_TOOLS = {
    "tools/dev/larsnet_detector.py",
    "tools/dev/alt_detector_stub.py",
}
# sys.path search directory. Not a CONTENT_DIRS entry. Public has no such
# folder and still imports root rlrr_parse.py (SERVED_LAYOUT_IMPORTS).
SEARCH_DIRS = {
    "Extractor Mini App",
}
# Optional fallback after a shipped ROOT_FILES name. MidiToRlrrApp.__init__
# joins drum_icon_final.ico only when exists(parakit.ico) is false.
OPTIONAL_FALLBACKS = {
    "drum_icon_final.ico": "parakit.ico",
}
# Optional legacy alternate filename. _a2m_find_model_path searches
# parakit_drum_model_v1.onnx beside ROOT_FILES parakit_drum_model.onnx;
# each candidate is exists-guarded.
OPTIONAL_ALTERNATES = {
    "parakit_drum_model_v1.onnx": "parakit_drum_model.onnx",
}

# The app's rlrr_parse is one module with two layouts. Dev inserts
# Extractor Mini App/ on sys.path and has no root copy. Public ships
# rlrr_parse.py at the install root (ROOT_FILES). extractor/rlrr_parse.py
# is a different file (widened CLASS_TO_MIDI) and is never a substitute.
SOURCE_LAYOUT_IMPORTS = {
    "rlrr_parse": "Extractor Mini App/rlrr_parse.py",
}
SERVED_LAYOUT_IMPORTS = {
    "rlrr_parse": "rlrr_parse.py",
}

# extractor/rlrr_extract_gui.py imports rlrr_parse from its own directory.
# That is the only supported package-adjacent contract. A bare import of a
# root sidecar from inside any other CONTENT_DIRS package is the root sidecar,
# never a sibling copy.
PACKAGE_ADJACENT_IMPORTS = {
    ("extractor", "rlrr_parse"): "extractor/rlrr_parse.py",
}


def values(*groups):
    result = tuple(sorted(set(itertools.chain.from_iterable(groups))))
    if len(result) > 256:
        raise ValueError("symbolic fanout exceeds 256 alternatives")
    return result


def literal(text):
    return (("literal", str(text)),)


def root_value(kind, path=""):
    return ((kind, path),)


def clean(path):
    path = path.replace("\\", "/")
    return "/".join(part for part in path.split("/") if part != ".")


def relative_safe(path):
    return (
        bool(path)
        and not path.startswith("/")
        and ":" not in path
        and ".." not in path.split("/")
    )


def without_requirements(path):
    prefix = "Requirements/"
    return path[len(prefix):] if path.startswith(prefix) else path


MAIN_GUARDS = (
    "__name__ == '__main__'",
    '__name__ == "__main__"',
)


def is_non_shipping_test(scope, conditions):
    if scope == "_selftest" or str(scope).endswith("._selftest"):
        return True
    return any(str(c).strip() in MAIN_GUARDS for c in conditions)


def parent_is_shipped(parent, policy):
    if not parent:
        return False
    if (
        parent in REQUIRED_DIRS
        or parent in REQUIRED_SETS
        or parent in policy.dirs
    ):
        return True
    return any(
        below(parent, directory)
        for directory in tuple(REQUIRED_DIRS) + tuple(REQUIRED_SETS) + tuple(policy.dirs)
    )


def last_only_unresolved_under_shipped(names, policy):
    if not names:
        return False
    for path in names:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 2:
            return False
        if any(c in part for part in parts[:-1] for c in "?*"):
            return False
        if not any(c in parts[-1] for c in "?*"):
            return False
        if not parent_is_shipped("/".join(parts[:-1]), policy):
            return False
    return True


def optional_mapped(names, mapping, policy, guards=(), guarded=False):
    """An optional name is excused only where its evidence holds: canonical
    root-level after without_requirements (beside the ROOT_FILES primary it
    stands in for; templates/drum_icon_final.ico is not that file), with the
    primary declared; and, when `guarded`, only under an existence guard that
    names the primary as ABSENT (the icon fallback runs under
    `if not os.path.exists(ico_path)` with ico_path resolved to parakit.ico).
    The legacy model alternate is bound to its primary in evaluate(): the
    record that names the alternate must come from the same expression as a
    record that names the primary (the app searches both names in one loop)."""
    if not names:
        return False
    for path in names:
        if "/" in path or any(c in path for c in "?*"):
            return False
        primary = mapping.get(path)
        if primary is None or primary not in policy.files:
            return False
        if guarded:
            if True:  # check1 fallback guard names its primary
                if not any(
                    g[0] == "absent" and primary in g[1] for g in guards
                ):
                    return False
    return True


def bound_names(node):
    """The names this statement binds. A subscript or attribute target counts
    its base name, so `probes[0] = x` is a binding of probes."""
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return set(node.names)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {
            alias.asname or alias.name.split(".")[0] for alias in node.names
        }
    if isinstance(node, ast.ExceptHandler):
        return {node.name} if node.name else set()
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        targets = [node.target]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        targets = [node.target]
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        targets = [item.optional_vars for item in node.items if item.optional_vars]
    elif isinstance(node, ast.Delete):
        targets = list(node.targets)
    else:
        return set()
    names = set()
    for target in targets:
        for inner in ast.walk(target):
            if isinstance(inner, ast.Name):
                names.add(inner.id)
    return names


def below(path, directory):
    return path == directory or path.startswith(directory + "/")


def root_sidecar_stems(policy):
    return {
        PurePosixPath(path).stem
        for path in policy.files
        if path.endswith(".py") and "/" not in path
    }


def content_dir_tops(policy):
    return {path.split("/")[0] for path in policy.dirs}


def is_local_module(name, policy):
    if not name:
        return False
    top = name.split(".")[0]
    if top == "__future__":
        return False
    if top.startswith("parakit_"):
        return True
    if top in SOURCE_LAYOUT_IMPORTS or top in SERVED_LAYOUT_IMPORTS:
        return True
    if top in root_sidecar_stems(policy):
        return True
    if top in content_dir_tops(policy):
        return True
    return False


def import_whitelisted(path, kind, policy):
    if "/" not in path and path.endswith(".py"):
        return path in policy.files  # check1 import root-sidecar membership
    return policy.covers(path, kind)


def resolve_package_member(source, module, level, name=None):
    """Map a relative or dotted package import to a served path.

    `from .numpy_rf import X` inside parakit_cleanup/passes.py becomes
    parakit_cleanup/numpy_rf.py. Returns (paths, kind) or ((), "file").
    """
    source = clean(source)
    if level:
        parent = str(PurePosixPath(source).parent)
        parts = [] if parent in (".", "") else parent.split("/")
        drop = level - 1
        if drop > len(parts):
            return (), "file"
        if drop:
            parts = parts[:len(parts) - drop]
    else:
        parts = []
    if module:
        parts.extend(module.split("."))
    elif name and name != "*":
        parts.append(name)
    if not parts:
        return (), "file"
    resolved = ["/".join(parts) + ".py"]
    return resolved, "file"  # check1 import package resolution


def assign_target_names(target):
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names = set()
        for elt in target.elts:
            names.update(assign_target_names(elt))
        return names
    return set()


def exported_package_names(text):
    """Top-level names bound by a package __init__.py. No execution.

    Bounded to FunctionDef / AsyncFunctionDef / ClassDef / Assign Name
    targets / Import and ImportFrom aliases. Star imports are ignored.
    """
    tree = ast.parse(text)
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(assign_target_names(target))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name)
    return names


def load_module(path, name):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"MISSING {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Policy:
    def __init__(self, generator):
        self.path = Path(generator)
        self.module = load_module(
            self.path, "_ship_generator_" + str(len(sys.modules))
        )
        for name in ("ROOT_FILES", "CONTENT_DIRS"):
            items = getattr(self.module, name, None)
            if (
                not isinstance(items, (list, tuple))
                or not items
                or any(not isinstance(item, str) for item in items)
            ):
                raise ValueError(f"invalid generator {name}: {self.path}")
        if not callable(getattr(self.module, "_skip", None)):
            raise ValueError(f"generator has no callable _skip: {self.path}")
        excluded = getattr(self.module, "EXCLUDE_DIRNAMES", None)
        if not isinstance(excluded, (set, list, tuple)):
            raise ValueError("generator has no EXCLUDE_DIRNAMES collection")
        self.files = tuple(clean(p) for p in self.module.ROOT_FILES)
        self.dirs = tuple(clean(p).rstrip("/") for p in self.module.CONTENT_DIRS)
        self.excluded_dirs = set(excluded)
        for item in self.files + self.dirs:
            if not relative_safe(item):
                raise ValueError(f"unsafe generator path: {item!r}")

    def covers(self, path, kind):
        if not relative_safe(path) or any(c in path for c in "?*"):
            return False
        parts = path.split("/")
        if any(part in self.excluded_dirs for part in parts[:-1]):
            return False
        if kind == "file" and self.module._skip(parts[-1]):
            return False
        if path in self.files:
            return not self.module._skip(path)
        return any(below(path, directory) for directory in self.dirs)

    def classify(self, paths, roots, conditions, scope="", guards=()):
        names = [without_requirements(p) for p in paths]
        dynamic = any("?" in p or "*" in p for p in paths)
        known_required = any(
            any(below(p, directory) for directory in REQUIRED_DIRS)
            or PurePosixPath(p).name in REQUIRED_LEAVES
            for p in names
        )
        if is_non_shipping_test(scope, conditions):
            if True:  # check1 main/selftest not a shipping contract
                return (
                    "non_shipping_test",
                    False,
                    "not a release shipping contract (__main__ or _selftest)",
                )
        if any(
            p == item or below(p, item)
            for p in names for item in GENERATED
        ):
            return "generated_user_state", False, "explicit create/write contract"
        if names and all(PurePosixPath(p).name.endswith(".part") for p in names):
            # The three download workers write `<dest>.part` beside their target and
            # rename it into place after validation. Only a download target's .part
            # is a temporary; any other .part keeps the ordinary rules below.
            stripped = [p[:-5] for p in names]
            if all(
                any(p == item or below(p, item) for item in DOWNLOADED)
                or PurePosixPath(p).name == "parakit_drum_model.onnx"
                for p in stripped
            ):
                return (
                    "generated_user_state",
                    False,
                    "download temporary written beside its target",
                )
        if any(
            p == item or below(p, item)
            for p in names for item in DOWNLOADED
        ):
            return (
                "optional_downloaded_runtime",
                False,
                "exact download/cache contract",
            )
        if all(p in PROBES for p in names):
            return "third_party_probe", False, "exact local/PATH probe"
        if names and all(p in DEV_ONLY_TOOLS for p in names):
            if True:  # check1 generator-declared non-shipping tools
                return (
                    "dev_only_tool",
                    False,
                    "generator-declared non-shipping tools/ path",
                )
        if names and all(p in SEARCH_DIRS for p in names):
            if True:  # check1 sys.path search directory
                return (
                    "search_directory",
                    False,
                    "sys.path search directory, not shipped content",
                )
        if optional_mapped(names, OPTIONAL_FALLBACKS, self, guards, guarded=True):
            if True:  # check1 optional fallback after shipped ROOT_FILES name
                return (
                    "optional_fallback",
                    False,
                    "optional fallback after a shipped ROOT_FILES name",
                )
        if optional_mapped(names, OPTIONAL_ALTERNATES, self):
            if True:  # check1 optional legacy alternate filename
                return (
                    "optional_alternate",
                    False,
                    "optional legacy alternate beside a shipped ROOT_FILES name",
                )
        if dynamic:
            if True:  # check1 dynamic leaf under shipped directory
                if last_only_unresolved_under_shipped(names, self):
                    return (
                        "shipped_content",
                        False,
                        "dynamic leaf covered by shipped directory declaration",
                    )
            return (
                "unresolved",
                True if known_required else None,
                "symbolic component or wildcard needs resolution",
            )
        if roots == ["executable"] and not any(
            "frozen" in condition for condition in conditions
        ):
            return (
                "unresolved",
                None,
                "executable directory is not proven to be the app directory",
            )
        if known_required or any(self.covers(p, "file") for p in paths):
            return "shipped_content", True, "required contract or generator declaration"
        folded = {p.casefold() for p in self.files}
        if any(p.casefold() in folded for p in paths):
            reason = "path spelling differs from generator ROOT_FILES"
        else:
            reason = "no code-supported shipping or non-shipping disposition"
        return "unresolved", None, reason


class Analyzer:
    def __init__(self, source, text, policy, root=None):
        self.source = source
        self.tree = ast.parse(text, filename=source)
        self.policy = policy
        self.root = Path(root) if root is not None else None
        self.records = []
        self.pending = []
        # Existence guards in force for the block being walked:
        # ("absent" | "present", (canonical paths,)). Pushed by the if/else
        # handling in block(), read by emit() so a record says which
        # existence test it sits under, with the tested path resolved the
        # same way the record's own path is.
        self._guards = []
        # The function (or module) whose body block() is walking: the
        # scope the guard matcher reads bindings from. run() sets it
        # before any statement is walked.
        self._function = None
        self.global_env = {}
        self.summaries = {}
        self.summarizing = set()
        # Collected from sys.path insert/append. Resolution never consults
        # this list; a recorded root must never waive a whitelist or presence
        # issue.
        self.import_roots = []
        grouped = {}
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                grouped.setdefault(node.name, []).append(node)
        self.functions = {
            name: nodes[0] for name, nodes in grouped.items() if len(nodes) == 1
        }

    def qualify(self, node, env):
        if isinstance(node, ast.Name):
            bound = env.get(node.id)
            if bound and len(bound) == 1 and bound[0][0] == "callable":
                return bound[0][1]
            return node.id
        if isinstance(node, ast.Attribute):
            return self.qualify(node.value, env) + "." + node.attr
        return ""

    def bind(self, target, result, env):
        if isinstance(target, ast.Name):
            env[target.id] = result  # check1 assignment propagation
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self.bind(item, result, env)

    def join(self, groups):
        if not groups:
            return UNKNOWN
        result = []
        for combination in itertools.product(*groups):
            first_kind, first_path = combination[0]
            suffix = [
                text if kind == "literal" else "?"
                for kind, text in combination[1:]
            ]
            if first_kind == "literal":
                if all(kind == "literal" for kind, text in combination):
                    result.append(("literal", clean("/".join(
                        text for kind, text in combination
                    ))))
                continue
            if first_kind in ROOT_KINDS:
                path = clean("/".join([first_path] + suffix)).lstrip("/")
                result.append((first_kind, path))
            elif first_kind == "unknown":
                tail = clean("/".join(suffix))
                leaf = PurePosixPath(tail).name
                served = "parakit_cleanup/" + leaf
                if True:  # check1 unknown-dir cleanup leaf
                    if served in SERVED_CLEANUP_NPZ and tail == leaf:
                        result.append(("install", served))
                        continue
                if (
                    leaf in REQUIRED_LEAVES
                    or any(below(tail, d) for d in REQUIRED_DIRS)
                ):
                    result.append(("unresolved", "?/" + tail))
        return values(tuple(result)) or UNKNOWN

    def source_directory(self):
        """The scanned file's own directory, as a canonical prefix: "" for a
        file at the root. A __file__ root stands for THIS, not for the
        installation root: in practice_v2/main.py it is practice_v2."""
        parent = str(PurePosixPath(clean(self.source)).parent)
        return "" if parent in (".", "/", "") else parent

    def guard_root(self, node):
        """The directory prefix `node` stands for when it is SYNTACTICALLY a
        root directory, or None. "" for _external_base_dir() and
        os.path.dirname(sys.executable); the source's own directory for
        os.path.dirname(os.path.abspath(__file__)) and its realpath/normpath
        and bare-__file__ spellings. A conditional or `or`, and a name, are
        roots only when every alternative agrees on the prefix. No value is
        resolved: this reads the code, not an abstraction of it."""
        if isinstance(node, (ast.IfExp, ast.BoolOp)):
            parts = (
                [node.body, node.orelse] if isinstance(node, ast.IfExp)
                else list(node.values)
            )
            prefixes = {self.guard_root(part) for part in parts}
            if len(prefixes) != 1 or None in prefixes:
                return None
            return prefixes.pop()
        if isinstance(node, ast.Call):
            if node.keywords:
                return None
            name = self.qualify(node.func, {})
            if name in GUARD_ROOT_CALLS and not node.args:
                return ""
            if name in GUARD_DIRNAMES and len(node.args) == 1:
                inner = node.args[0]
                if isinstance(inner, ast.Name) and inner.id == "__file__":
                    return self.source_directory()
                if (
                    isinstance(inner, ast.Attribute)
                    and self.qualify(inner, {}) == "sys.executable"
                ):
                    return ""
                if (
                    isinstance(inner, ast.Call)
                    and self.qualify(inner.func, {}) in GUARD_ABSPATHS
                    and len(inner.args) == 1
                    and not inner.keywords
                    and isinstance(inner.args[0], ast.Name)
                    and inner.args[0].id == "__file__"
                ):
                    return self.source_directory()
            return None
        if isinstance(node, ast.Name):
            if node.id in self.function_parameters():
                return None
            bindings = self.name_bindings(node.id)
            if not bindings:
                return None
            prefixes = set()
            for binding in bindings:
                if not isinstance(binding, ast.Assign):
                    return None
                prefixes.add(self.guard_root(binding.value))
            if len(prefixes) != 1 or None in prefixes:
                return None
            return prefixes.pop()
        return None

    def function_parameters(self):
        node = self._function
        arguments = getattr(node, "args", None)
        if not isinstance(arguments, ast.arguments):
            return frozenset()
        names = [
            argument.arg for argument in
            arguments.posonlyargs + arguments.args + arguments.kwonlyargs
        ]
        for extra in (arguments.vararg, arguments.kwarg):
            if extra is not None:
                names.append(extra.arg)
        return frozenset(names)

    def name_bindings(self, name):
        """Every statement in the enclosing function that binds `name`, nested
        scopes excluded. A binding that is not a plain assignment is returned
        as itself, so a caller that requires assignments refuses it."""
        found = []
        stack = list(ast.iter_child_nodes(self._function))
        while stack:
            node = stack.pop()
            if isinstance(node, (
                ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda
            )):
                if getattr(node, "name", None) == name:
                    found.append(node)
                continue
            if name in bound_names(node):
                found.append(node)
            stack.extend(ast.iter_child_nodes(node))
        return found

    def guard_path(self, value, strict=True, path_object=False):
        """The one canonical path `value` names, or None. The shape is
        os.path.join(<root>, "lit", ...), Path(<root>, "lit", ...), or
        Path(<root>) / "lit" ... : a recognized root and string literals.
        With `path_object`, only the two Path spellings count: a str
        built by os.path.join has no .exists() and that code would
        raise, so it is not a guard."""
        parts = []
        node = value
        while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                parts.insert(0, right.value)
            elif strict:
                return None
            else:
                parts.insert(0, "?")
            node = node.left
        if not (isinstance(node, ast.Call) and not node.keywords and node.args):
            return None
        name = self.qualify(node.func, {})
        constructed = name in ("Path", "pathlib.Path")
        if name not in GUARD_JOINS and not constructed:
            return None
        if path_object and not constructed:
            return None
        prefix = self.guard_root(node.args[0])
        assert prefix is None or isinstance(prefix, str), prefix
        if strict and prefix is None:
            return None
        if True:  # check1 a source-relative guard root keeps its directory
            # dirname(abspath(__file__)) in practice_v2/main.py is
            # practice_v2, so the guard is about practice_v2/parakit.ico
            # and says nothing about the root-level file of that name.
            head = [prefix] if prefix else []
        else:
            head = []
        for argument in node.args[1:]:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                head.append(argument.value)
            elif strict:
                return None
            else:
                head.append("?")
        parts = head + parts
        if not parts:
            return None
        path = clean("/".join(parts))
        if strict and (not relative_safe(path) or any(c in path for c in "?*")):
            return None
        return path

    def preceding_assignment(self, name, before):
        """The last assignment to `name` anywhere in the enclosing function
        that begins above line `before`. Only the deletion mutant of the
        adjacency rule reaches this."""
        candidates = [
            binding for binding in self.name_bindings(name)
            if isinstance(binding, ast.Assign) and binding.lineno < before
        ]
        return max(candidates, key=lambda node: node.lineno) if candidates else None

    def exists_guard(self, test, previous):
        """("absent"|"present", (canonical path,)) when `test` is [not] an
        existence call on a name the statement above assigned from a literal
        path under a recognized installation root; None otherwise. Nothing is
        resolved here: an unrecognized shape is simply not evidence."""
        polarity = "present"
        node = test
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            polarity = "absent"
            node = node.operand
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("exists", "isfile", "is_file")
            and not node.keywords
        ):
            return None
        if True:  # check1 existence guard needs a filesystem predicate
            # os.path.exists(NAME) / os.path.isfile(NAME) with one positional
            # argument, or NAME.exists() / NAME.is_file(). registry.exists(p)
            # says nothing about the filesystem.
            if node.args:
                if len(node.args) != 1 or node.func.attr == "is_file":
                    return None
                if self.qualify(node.func.value, {}) not in GUARD_MODULES:
                    return None
                target = node.args[0]
                wants_path_object = False
            else:
                if node.func.attr not in ("exists", "is_file"):
                    return None
                target = node.func.value
                wants_path_object = True
        else:
            target = node.args[0] if node.args else node.func.value
            wants_path_object = False
        if not isinstance(target, ast.Name):
            return None
        if True:  # check1 a guard name is assigned immediately before its test
            # The statement above the `if` is the whole history the matcher
            # needs: nothing can rebind the name between them.
            if not (
                isinstance(previous, ast.Assign)
                and len(previous.targets) == 1
                and isinstance(previous.targets[0], ast.Name)
                and previous.targets[0].id == target.id
            ):
                return None
            assigned = previous.value
        else:
            found = self.preceding_assignment(target.id, test.lineno)
            if found is None:
                return None
            assigned = found.value
        if True:  # check1 a guard join takes string literals under a recognized root
            path = self.guard_path(assigned, path_object=wants_path_object)
        else:
            path = self.guard_path(
                assigned, strict=False, path_object=wants_path_object
            )
        if path is None:
            return None
        return (polarity, (path,))

    def emit(self, node, result, scope, conditions, intent="read", path_role=""):
        guards = [g for g in self._guards if g is not None]
        rooted = [
            (kind, clean(path))
            for kind, path in result
            if kind in ROOT_KINDS and path not in ("", "Requirements")
        ]
        grouped = {}
        for kind, path in rooted:
            # One constructor may produce alternative search locations.
            key = PurePosixPath(path).name
            grouped.setdefault(key, []).append((kind, path))
        for entries in grouped.values():
            paths = sorted({path for kind, path in entries})
            roots = sorted({kind for kind, path in entries})
            category, required, reason = self.policy.classify(
                paths, roots, list(conditions), scope, guards=guards
            )
            if any(not relative_safe(p) for p in paths):
                category, required = "unresolved", None
                reason = "path cannot be compared as a safe repository-relative name"
            canonical = [without_requirements(p) for p in paths]
            is_dir = all(
                p in REQUIRED_DIRS
                or p in self.policy.dirs
                or p in GENERATED
                or p in DOWNLOADED
                or p in SEARCH_DIRS
                for p in canonical
            )
            if True:  # check1 path-div prefix kind
                if (
                    path_role in ("div", "div_prefix")
                    and canonical
                    and all(not any(c in p for c in "?*") for p in canonical)
                    and all(
                        p in REQUIRED_DIRS
                        or any(below(p, d) for d in self.policy.dirs)
                        for p in canonical
                    )
                ):
                    # Only a PREFIX of the chain is a directory by construction.
                    # A final expression keeps the kind the policy gives it; the
                    # audited disk never decides what kind a dependency has.
                    if path_role == "div_prefix":
                        is_dir = True
            kind = "directory" if is_dir else "file"
            self.records.append({
                "source": self.source,
                "line": node.lineno,
                "column": node.col_offset,
                "scope": scope,
                "expression": ast.unparse(node),
                "conditions": list(conditions),
                "roots": roots,
                "intent": intent,
                "logical_paths": paths,
                "category": category,
                "required": required,
                "kind": kind,
                "reason": reason,
                "path_role": path_role,
                "guards": [[g[0], list(g[1])] for g in guards],
            })

    def emit_import(self, node, paths, kind, reason, scope, conditions):
        paths = [clean(p) for p in paths if p]
        if not paths:
            return
        self.records.append({
            "source": self.source,
            "line": node.lineno,
            "column": node.col_offset,
            "scope": scope,
            "expression": ast.unparse(node),
            "conditions": list(conditions),
            "roots": ["install"],
            "intent": "import",
            "logical_paths": paths,
            "category": "local_import",
            "required": True,
            "kind": kind,
            "reason": reason,
        })

    def emit_unresolved_required(self, node, paths, reason, scope, conditions):
        paths = [clean(p) for p in paths if p]
        if not paths:
            return
        self.records.append({
            "source": self.source,
            "line": node.lineno,
            "column": node.col_offset,
            "scope": scope,
            "expression": ast.unparse(node),
            "conditions": list(conditions),
            "roots": ["install"],
            "intent": "import",
            "logical_paths": paths,
            "category": "unresolved",
            "required": True,
            "kind": "file",
            "reason": reason,
        })

    def existing_import_path(self, path, kind):
        if self.root is None:
            return False
        target = self.root / path
        try:
            return target.is_dir() if kind == "directory" else target.is_file()
        except OSError:
            return False

    def package_exports(self, package_dir, name):
        if self.root is None or not name or name == "*":
            return False
        init_path = self.root / package_dir / "__init__.py"
        try:
            text = init_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            return False
        try:
            return name in exported_package_names(text)  # check1 import exported-attribute evidence
        except (SyntaxError, ValueError):
            return False

    def finalize_package_paths(self, paths, kind, module, name):
        if not paths:
            return (), kind, None
        if self.root is None or module:
            return paths, kind, None
        present = [p for p in paths if self.existing_import_path(p, kind)]
        if present:
            return present, kind, None
        parent = str(PurePosixPath(paths[0]).parent)
        if parent in (".", ""):
            return paths, kind, None
        if self.package_exports(parent, name):
            return (parent,), "directory", None
        return paths, kind, name

    def resolve_root_or_layout(self, name):
        top = name.split(".")[0]
        parent = str(PurePosixPath(clean(self.source)).parent)
        if parent in (".", ""):
            parent = ""
        if parent:
            top_dir = parent.split("/")[0]
            adjacent = PACKAGE_ADJACENT_IMPORTS.get((top_dir, top))
            if adjacent is not None:
                return [adjacent], "file", "package-adjacent module"
        if top in SOURCE_LAYOUT_IMPORTS or top in SERVED_LAYOUT_IMPORTS:
            served = SERVED_LAYOUT_IMPORTS.get(top, top + ".py")
            source_path = SOURCE_LAYOUT_IMPORTS.get(top)
            paths = [served]
            if source_path and source_path not in paths:
                paths.append(source_path)
            return paths, "file", "source-layout and served-layout contract"
        if top in content_dir_tops(self.policy):
            return [top], "directory", "shipped package"
        if top.startswith("parakit_") or top + ".py" in self.policy.files:
            return [top + ".py"], "file", "root sidecar module"
        return (), "file", ""

    def consider_import(self, node, name, scope, conditions):
        if not is_local_module(name, self.policy):
            return
        if "." in name:
            paths, kind = resolve_package_member(self.source, name, 0)
            paths, kind, missing = self.finalize_package_paths(
                paths, kind, name, None
            )
            reason = "absolute package member"
            if missing:
                self.emit_unresolved_required(
                    node, paths,
                    "imported member %s is not a package file and is not exported"
                    % missing,
                    scope, conditions,
                )
                return
        else:
            paths, kind, reason = self.resolve_root_or_layout(name)
        if paths:
            self.emit_import(node, paths, kind, reason, scope, conditions)

    def consider_importfrom(self, node, scope, conditions):
        module = node.module
        level = node.level
        if module == "__future__":
            return
        names = [alias.name for alias in node.names]
        if level:
            if module:
                paths, kind = resolve_package_member(
                    self.source, module, level
                )
                paths, kind, missing = self.finalize_package_paths(
                    paths, kind, module, None
                )
                if missing:
                    self.emit_unresolved_required(
                        node, paths,
                        "imported member %s is not a package file and is not exported"
                        % missing,
                        scope, conditions,
                    )
                elif paths:
                    self.emit_import(
                        node, paths, kind,
                        "relative package import", scope, conditions,
                    )
                return
            emitted = set()
            for name in names:
                paths, kind = resolve_package_member(
                    self.source, None, level, name
                )
                paths, kind, missing = self.finalize_package_paths(
                    paths, kind, None, name
                )
                if missing:
                    self.emit_unresolved_required(
                        node, paths,
                        "imported member %s is not a package file and is not exported"
                        % missing,
                        scope, conditions,
                    )
                    continue
                key = (tuple(paths), kind)
                if paths and key not in emitted:
                    emitted.add(key)
                    self.emit_import(
                        node, paths, kind,
                        "relative package import", scope, conditions,
                    )
            return
        if module and is_local_module(module, self.policy):
            if "." in module:
                paths, kind = resolve_package_member(self.source, module, 0)
                paths, kind, missing = self.finalize_package_paths(
                    paths, kind, module, None
                )
                if missing:
                    self.emit_unresolved_required(
                        node, paths,
                        "imported member %s is not a package file and is not exported"
                        % missing,
                        scope, conditions,
                    )
                elif paths:
                    self.emit_import(
                        node, paths, kind,
                        "absolute package member", scope, conditions,
                    )
                return
            paths, kind, reason = self.resolve_root_or_layout(module)
            if paths:
                self.emit_import(node, paths, kind, reason, scope, conditions)
            if kind == "directory":
                pkg = module.replace(".", "/")
                for name in names:
                    if name == "*":
                        continue
                    candidate = pkg + "/" + name + ".py"
                    if self.existing_import_path(candidate, "file"):
                        self.emit_import(
                            node, [candidate], "file",
                            "absolute package member", scope, conditions,
                        )
                    elif self.package_exports(pkg, name):
                        continue
                    elif self.root is not None:
                        self.emit_unresolved_required(
                            node, [candidate],
                            "imported member %s is not a package file and is not exported"
                            % name,
                            scope, conditions,
                        )
            return
        if module is None:
            return
        for name in names:
            if is_local_module(name, self.policy):
                self.consider_import(node, name, scope, conditions)

    def note_path_insert(self, result):
        for kind, path in result:
            if kind in ROOT_KINDS and path and path not in ("", "Requirements"):
                cleaned = without_requirements(clean(path))
                if cleaned and cleaned not in self.import_roots:
                    self.import_roots.append(cleaned)

    def summary(self, name):
        name = name.rsplit(".", 1)[-1]
        if name in self.summaries:
            return self.summaries[name]
        node = self.functions.get(name)
        if node is None or name in self.summarizing:
            return UNKNOWN
        body = [
            item for item in node.body
            if not (
                isinstance(item, ast.Expr)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, str)
            )
        ]
        if (
            len(body) > 8
            or not body
            or any(
                not isinstance(item, (ast.Assign, ast.AnnAssign, ast.Return))
                for item in body
            )
        ):
            return UNKNOWN
        self.summarizing.add(name)
        outer_function = self._function
        self._function = node
        try:
            env = self.function_env(node, self.global_env)
            env, returned = self.block(body, env, name, (), False)
            result = values(*returned) if returned else UNKNOWN
            self.summaries[name] = result
            return result
        finally:
            self.summarizing.remove(name)
            self._function = outer_function

    def expr_div_left(self, node, env, scope, conditions, emit):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self.expr_div_left(node.left, env, scope, conditions, emit)
            right = self.expr(node.right, env, scope, conditions, emit)
            result = self.join([left, right])
            if emit:
                self.emit(node, result, scope, conditions, path_role="div_prefix")
            return result
        return self.expr(node, env, scope, conditions, emit)

    def expr(self, node, env, scope, conditions, emit=True):
        if node is None:
            return UNKNOWN

        def e(child):
            return self.expr(child, env, scope, conditions, emit)

        if isinstance(node, ast.Constant):
            return literal(node.value) if isinstance(node.value, str) else UNKNOWN
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return root_value("source", self.source)
            return env.get(node.id, UNKNOWN)
        if isinstance(node, ast.Attribute):
            qualified = self.qualify(node, env)
            if qualified == "sys.executable":
                return root_value("executable", "<executable>")
            if qualified == "sys._MEIPASS":
                return root_value("bundle")
            if node.attr == "parent":
                return tuple(
                    (kind, path.rsplit("/", 1)[0] if "/" in path else "")
                    for kind, path in e(node.value)
                    if kind in ROOT_KINDS
                ) or UNKNOWN
            return UNKNOWN
        if isinstance(node, ast.Subscript):
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "parents"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int)
                and node.slice.value >= 0
            ):
                result = e(node.value.value)
                for unused in range(node.slice.value + 1):
                    result = tuple(
                        (kind, path.rsplit("/", 1)[0] if "/" in path else "")
                        for kind, path in result
                    )
                return result
            e(node.slice)
            return e(node.value)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return values(*(e(item) for item in node.elts))
        if isinstance(node, ast.IfExp):
            e(node.test)
            return values(e(node.body), e(node.orelse))
        if isinstance(node, ast.BoolOp):
            return values(*(e(item) for item in node.values))
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div):
                left = self.expr_div_left(node.left, env, scope, conditions, emit)
                right = e(node.right)
                result = self.join([left, right])
                if emit:
                    self.emit(node, result, scope, conditions, path_role="div")
                return result
            left, right = e(node.left), e(node.right)
            if isinstance(node.op, ast.Add):
                # A list or tuple operand, or a symbolic product too large to
                # be one string, is sequence concatenation: the union of the
                # alternatives, never a path join. A command line such as
                # [sys.executable, "-m", "demucs"] + args is not a path, and
                # a loop over TUPLE_A + TUPLE_B must not leave the file unmeasured.
                if (
                    isinstance(node.left, (ast.List, ast.Tuple))
                    or isinstance(node.right, (ast.List, ast.Tuple))
                    or len(left) * len(right) > 256
                ):
                    try:
                        return values(left, right)
                    except ValueError:
                        # Too many alternatives to carry. The expression is recorded
                        # as unresolved so the overflow is visible in the review
                        # queue; it is never dropped on the floor.
                        if emit:
                            self.emit(node, (("unresolved", "?/fanout-overflow"),), scope, conditions)  # check1 overflow record
                        return UNKNOWN
                combined = []
                for (lk, lp), (rk, rp) in itertools.product(left, right):
                    if lk in ROOT_KINDS and rk == "literal":
                        combined.append((lk, clean(lp + rp).lstrip("/")))
                    elif lk == rk == "literal":
                        combined.append(("literal", lp + rp))
                result = values(tuple(combined)) or UNKNOWN
            else:
                return UNKNOWN
            if emit:
                self.emit(node, result, scope, conditions)
            return result
        if isinstance(node, ast.Call):
            name = self.qualify(node.func, env)
            args = [e(arg.value if isinstance(arg, ast.Starred) else arg)
                    for arg in node.args]
            for keyword in node.keywords:
                e(keyword.value)
            path_operation = False
            intent = "read"
            if name == "_external_base_dir":
                result = root_value("install")
            elif name == "_external_search_dirs":
                result = (
                    ("install", "Requirements"),
                    ("install", ""),
                )
            elif name == "_external_path":
                result = self.join([
                    (("install", "Requirements"), ("install", "")),
                    args[0] if args else UNKNOWN,
                ])
                path_operation = True
                for keyword in node.keywords:
                    if keyword.arg == "for_writing":
                        if isinstance(keyword.value, ast.Constant):
                            intent = "write" if keyword.value.value is True else "read"
                        else:
                            intent = "unknown"
            elif name == "_neon_resource_path":
                result = self.join([root_value("bundle")] + args)
                path_operation = True
            elif name in ("os.path.abspath", "os.path.normpath"):
                result = args[0] if args else UNKNOWN
            elif name == "os.path.dirname":
                result = tuple(
                    (kind, path.rsplit("/", 1)[0] if "/" in path else "")
                    for kind, path in (args[0] if args else UNKNOWN)
                )
            elif (
                name == "getattr"
                and len(node.args) >= 2
                and self.qualify(node.args[0], env) == "sys"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "_MEIPASS"
            ):
                result = root_value("bundle")
            elif (
                name in (
                    "importlib.import_module",
                    "import_module",
                    "__import__",
                    "builtins.__import__",
                )
                or name.endswith(".import_module")
            ):
                result = UNKNOWN
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and emit
                ):
                    self.consider_import(
                        node, node.args[0].value, scope, conditions
                    )
            elif name in ("Path", "pathlib.Path"):
                if True:  # check1 Path constructor joins every argument
                    # Path(a, b) is a / b: keeping only `a` would name the
                    # parent where the code names a child.
                    result = self.join(args) if args else UNKNOWN
                    if len(node.args) > 1:
                        path_operation = True
                else:
                    result = args[0] if args else UNKNOWN
                if node.args and isinstance(node.args[0], ast.Constant):
                    result = tuple(
                        ("unresolved", path) for kind, path in result
                    )
                    path_operation = True
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in ("resolve", "absolute")
            ):
                result = e(node.func.value)
            elif name == "os.path.join":
                result = self.join(args)
                path_operation = True
            elif (
                isinstance(node.func, ast.Name)
                or (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in ("self", "cls")
                )
            ):
                result = self.summary(name)
            else:
                if isinstance(node.func, ast.Attribute):
                    e(node.func.value)
                result = UNKNOWN
            if emit and path_operation:
                self.emit(node, result, scope, conditions, intent)
            return result
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                e(child)
        return UNKNOWN

    def merge_envs(self, *envs):
        keys = set().union(*(env.keys() for env in envs))
        return {
            key: values(*(env.get(key, UNKNOWN) for env in envs))
            for key in keys
        }

    def function_env(self, node, outer):
        env = dict(outer)

        def shadow(item):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if item is not node:
                    env[item.name] = UNKNOWN
                    return
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                env[item.id] = UNKNOWN
            for child in ast.iter_child_nodes(item):
                shadow(child)

        shadow(node)
        arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        if node.args.vararg:
            arguments.append(node.args.vararg)
        if node.args.kwarg:
            arguments.append(node.args.kwarg)
        for argument in arguments:
            env[argument.arg] = UNKNOWN
        return env

    def block(self, statements, env, scope, conditions, emit=True):
        returned = []
        for index, node in enumerate(statements):
            def e(expr):
                return self.expr(expr, env, scope, conditions, emit)

            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    target = alias.name if alias.asname else name
                    env[name] = (("callable", target),)
                    if emit:
                        self.consider_import(
                            node, alias.name, scope, conditions
                        )
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    env[alias.asname or alias.name] = (
                        ("callable", (node.module or "") + "." + alias.name),
                    )
                if emit:
                    self.consider_importfrom(node, scope, conditions)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                env[node.name] = (("callable", node.name),)
                if emit and node.name not in HELPERS:
                    self.pending.append((node, scope, dict(env) if scope else None))
            elif isinstance(node, ast.ClassDef):
                env[node.name] = (("callable", node.name),)
                if emit:
                    self.pending.append((node, scope, dict(env) if scope else None))
            elif isinstance(node, ast.Assign):
                result = e(node.value)
                for target in node.targets:
                    self.bind(target, result, env)
            elif isinstance(node, ast.AnnAssign):
                self.bind(node.target, e(node.value), env)
            elif isinstance(node, ast.AugAssign):
                if isinstance(node.target, ast.Name):
                    self.bind(
                        node.target,
                        values(env.get(node.target.id, UNKNOWN), e(node.value)),
                        env,
                    )
                else:
                    e(node.value)
            elif isinstance(node, ast.Expr):
                e(node.value)
                call = node.value
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.attr in ("append", "extend")
                    and call.args
                ):
                    name = call.func.value.id
                    env[name] = values(
                        env.get(name, ()),
                        self.expr(call.args[0], env, scope, conditions, False),
                    )
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("insert", "append")
                    and isinstance(call.func.value, ast.Attribute)
                    and call.func.value.attr == "path"
                    and self.qualify(call.func.value.value, env) in (
                        "sys", "_sys"
                    )
                ):
                    argument = (
                        call.args[1] if call.func.attr == "insert"
                        and len(call.args) >= 2
                        else (call.args[0] if call.args else None)
                    )
                    if argument is not None:
                        self.note_path_insert(e(argument))
            elif isinstance(node, ast.Return):
                returned.append(e(node.value))
            elif isinstance(node, ast.If):
                e(node.test)
                condition = ast.unparse(node.test)
                guard = self.exists_guard(
                    node.test, statements[index - 1] if index else None
                )
                self._guards.append(guard)
                try:
                    yes, yr = self.block(
                        node.body, dict(env), scope, conditions + (condition,), emit
                    )
                finally:
                    self._guards.pop()
                inverse = None
                if guard is not None:
                    inverse = (
                        "absent" if guard[0] == "present" else "present",
                        guard[1],
                    )
                self._guards.append(inverse)
                try:
                    no, nr = self.block(
                        node.orelse, dict(env), scope,
                        conditions + ("not (" + condition + ")",), emit
                    )
                finally:
                    self._guards.pop()
                env = self.merge_envs(yes, no)
                returned.extend(yr + nr)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                iterable = e(node.iter)
                loop = dict(env)
                self.bind(node.target, iterable, loop)
                loop, lr = self.block(node.body, loop, scope, conditions, emit)
                env = self.merge_envs(env, loop)
                env, er = self.block(node.orelse, env, scope, conditions, emit)
                returned.extend(lr + er)
            elif isinstance(node, ast.While):
                e(node.test)
                loop, lr = self.block(
                    node.body, dict(env), scope,
                    conditions + (ast.unparse(node.test),), emit
                )
                env = self.merge_envs(env, loop)
                env, er = self.block(node.orelse, env, scope, conditions, emit)
                returned.extend(lr + er)
            elif isinstance(node, (ast.Try, ast.TryStar)):
                original = dict(env)
                good, gr = self.block(node.body, dict(env), scope, conditions, emit)
                good, er = self.block(node.orelse, good, scope, conditions, emit)
                branches = [good]
                returned.extend(gr + er)
                for handler in node.handlers:
                    branch, hr = self.block(
                        handler.body, dict(original), scope, conditions, emit
                    )
                    branches.append(branch)
                    returned.extend(hr)
                env = self.merge_envs(*branches)
                env, fr = self.block(node.finalbody, env, scope, conditions, emit)
                returned.extend(fr)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    result = e(item.context_expr)
                    if item.optional_vars:
                        self.bind(item.optional_vars, result, env)
                env, wr = self.block(node.body, env, scope, conditions, emit)
                returned.extend(wr)
            else:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.expr):
                        e(child)
        return env, returned

    def run(self):
        self._function = self.tree
        self.global_env, unused = self.block(self.tree.body, {}, "", ())
        while self.pending:
            node, parent_scope, captured = self.pending.pop(0)
            scope = ".".join(part for part in (parent_scope, node.name) if part)
            outer = dict(self.global_env if captured is None else captured)
            self._function = node
            if isinstance(node, ast.ClassDef):
                self.block(node.body, outer, scope, ())
            else:
                self.block(
                    node.body, self.function_env(node, outer), scope, ()
                )
        unique = {}
        for record in self.records:
            key = (
                record["source"], record["line"], record["column"],
                tuple(record["logical_paths"]),
            )
            unique[key] = record
        return sorted(
            unique.values(),
            key=lambda r: (
                r["source"], r["line"], r["column"], r["logical_paths"]
            ),
        )


def scan_text(source, text, policy, root=None):
    return Analyzer(source, text, policy, root).run()


def source_inventory(root, policy):
    root = Path(root)
    files = {APP}
    gaps = []
    for path in policy.files:
        if path.endswith(".py"):
            files.add(path)
    for stem, layout in SOURCE_LAYOUT_IMPORTS.items():
        served = SERVED_LAYOUT_IMPORTS.get(stem, stem + ".py")
        if (
            served in files
            and not (root / served).is_file()
            and (root / layout).is_file()
        ):
            files.discard(served)
            files.add(layout)
    for directory in policy.dirs:
        base = root / directory
        if not base.is_dir():
            gaps.append(f"source directory absent: {directory}")
            continue

        def walk_error(error):
            raise error

        for parent, dirs, names in os.walk(base, onerror=walk_error):
            dirs[:] = sorted(
                name for name in dirs if name not in policy.excluded_dirs
            )
            for name in names:
                if name.endswith(".py") and not policy.module._skip(name):
                    files.add((Path(parent) / name).relative_to(root).as_posix())
    records = []
    for relative in sorted(files):
        path = root / relative
        if not path.is_file():
            gaps.append(f"source file absent: {relative}")
            continue
        try:
            records.extend(scan_text(
                relative, path.read_text(encoding="utf-8-sig"), policy, root
            ))
        except (OSError, UnicodeError, SyntaxError, ValueError) as error:
            gaps.append(f"source unmeasured: {relative}: {type(error).__name__}: {error}")
    return records, gaps


def expand_required(records):
    expanded = list(records)
    seen = set()
    for record in records:
        for path in record["logical_paths"]:
            canonical = without_requirements(path)
            if canonical not in REQUIRED_SETS:
                continue
            for filename in REQUIRED_SETS[canonical]:
                required_path = canonical + "/" + filename
                if required_path in seen:
                    continue
                seen.add(required_path)
                item = dict(record)
                item.update({
                    "logical_paths": [required_path],
                    "category": "shipped_content",
                    "required": True,
                    "kind": "file",
                    "reason": "named required asset inventory",
                })
                expanded.append(item)
    return expanded


def declarations(policy):
    records = []
    for kind, paths in (("file", policy.files), ("directory", policy.dirs)):
        for path in paths:
            records.append({
                "source": str(policy.path),
                "line": 0,
                "column": 0,
                "scope": "",
                "expression": "ROOT_FILES" if kind == "file" else "CONTENT_DIRS",
                "conditions": [],
                "roots": ["install"],
                "intent": "read",
                "logical_paths": [path],
                "category": "shipped_content",
                "required": True,
                "kind": kind,
                "reason": "generator declaration",
            })
    return records


class Presence:
    """Exact spelling, with each required parent directory listed at most once."""
    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"served tree absent: {self.root}")
        self.cache = {}

    def entries(self, directory):
        key = str(directory)
        if key not in self.cache:
            self.cache[key] = {p.name: p for p in directory.iterdir()}
        return self.cache[key]

    def has(self, relative, kind):
        if not relative_safe(relative) or any(c in relative for c in "?*"):
            return False
        current = self.root
        parts = relative.split("/")
        for index, part in enumerate(parts):
            entry = self.entries(current).get(part)
            if entry is None:
                return False
            if index < len(parts) - 1:
                if not entry.is_dir():
                    return False
                current = entry
            else:
                return entry.is_dir() if kind == "directory" else entry.is_file()
        return False


def review_key(record):
    return (
        record["source"],
        record["line"],
        tuple(record["logical_paths"]),
        record["required"],
    )


def evaluate(records, policy, served=None, release=False, gaps=()):
    if True:  # check1 final path divided further elsewhere
        # A final Path expression whose logical path is a div_prefix (a
        # directory by construction) of some chain in the scanned set is that
        # directory: practice_v2/practice_window_v2_launcher.py stores
        # Path(__file__).parent / "assets" / "ui" and practice_v2/main.py
        # divides here / "assets" / "ui" / name. Evidence from the code,
        # never from the audited disk.
        prefix_dirs = set()
        for r in records:
            if r.get("path_role") != "div_prefix" or r.get("kind") != "directory":
                continue
            if True:  # check1 prefix donors are shipping contracts
                # Only a shipping directory contract may lend its kind: a
                # __main__/_selftest chain or an optional runtime path is not
                # evidence about what ships.
                if r.get("category") != "shipped_content" or r.get("required") is not True:
                    continue
            prefix_dirs.update(r.get("logical_paths", ()))
        for r in records:
            if (
                r.get("path_role") == "div"
                and r.get("kind") == "file"
                and r.get("logical_paths")
                and all(p in prefix_dirs for p in r["logical_paths"])
            ):
                r["kind"] = "directory"
    if True:  # check1 optional alternate needs its primary in the same expression
        # parakit_drum_model_v1.onnx is excused because the app searches it in
        # the same loop as parakit_drum_model.onnx: the two records come from
        # one join. An alternate named on its own is a plain unresolved path.
        by_site = {}
        for r in records:
            by_site.setdefault(
                (r.get("source"), r.get("line"), r.get("column")), []
            ).append(r)
        for r in records:
            if r.get("category") != "optional_alternate":
                continue
            names = [without_requirements(p) for p in r.get("logical_paths", ())]
            primaries = {OPTIONAL_ALTERNATES.get(n) for n in names}
            siblings = by_site.get(
                (r.get("source"), r.get("line"), r.get("column")), []
            )
            bound = any(
                s is not r
                and s.get("category") != "optional_alternate"
                and primaries & {
                    without_requirements(p) for p in s.get("logical_paths", ())
                }
                for s in siblings
            )
            if not bound:
                r["category"] = "unresolved"
                r["required"] = None
                r["reason"] = (
                    "optional alternate named without its primary in the same "
                    "expression"
                )
    records = expand_required(records)
    issues = []
    review = []
    setup = list(gaps)
    presence = None
    if served is not None:
        try:
            presence = Presence(served)
        except OSError as error:
            setup.append(str(error))
    else:
        setup.append("served presence unmeasured: no --served")

    def issue(code, record):
        item = dict(record)
        item["stage"] = "check1_required_assets"
        item["code"] = code
        issues.append(item)

    for record in records:
        if record["category"] == "unresolved":
            review.append(record)
            block = (
                record["required"] is True
                or (release and record["required"] is None)
            )
            if block:  # check1 unresolved refusal
                issue(
                    "unresolved_required_path"
                    if record["required"] is True else "review_required",
                    record,
                )
            continue
        if record["category"] == "local_import":
            paths = record["logical_paths"]
            covered = any(
                import_whitelisted(p, record["kind"], policy) for p in paths
            )
            if not covered:  # check1 import root-sidecar membership
                issue("whitelist_missing", record)
            if presence is not None:
                try:
                    present = any(
                        presence.has(p, record["kind"]) for p in paths
                    )
                    if not present:  # check1 import served presence
                        issue(
                            "required_directory_absent"
                            if record["kind"] == "directory"
                            else "required_file_absent",
                            record,
                        )
                except OSError as error:
                    setup.append(
                        "served presence unmeasured: "
                        + repr(paths) + ": " + str(error)
                    )
            continue
        if record["required"] is not True:
            continue
        paths = record["logical_paths"]
        covered = any(policy.covers(p, record["kind"]) for p in paths)
        if not covered:  # check1 whitelist comparison
            issue("whitelist_missing", record)
        if presence is not None:
            try:
                present = any(presence.has(p, record["kind"]) for p in paths)
                if not present:  # check1 required presence
                    issue(
                        "required_directory_absent"
                        if record["kind"] == "directory"
                        else "required_file_absent",
                        record,
                    )
            except OSError as error:
                setup.append(
                    "served presence unmeasured: "
                    + repr(paths) + ": " + str(error)
                )
    rc = 1 if issues else 2 if setup or review else 0
    return {
        "schema": 1,
        "stage": "check1_required_assets",
        "rc": rc,
        "release": bool(release),
        "records": records,
        "issues": issues,
        "review_queue": sorted(review, key=review_key),
        "setup": sorted(set(setup)),
        "coverage": {
            "local_import_delivery": (
                "root sidecars, packages, relative imports, dual-layout"
            ),
            "candidate_identity": "check_identity (Check 2); not this function",
            "manifest_git_membership": "check_delivery_set (Check 2); not this function",
            "arbitrary_caller_paths": "outside rooted inventory",
            "served_directory_listings": len(presence.cache) if presence else 0,
        },
    }


def check(root, served=None, generator=None, release=False):
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"source root absent: {root}")
    source_generator = root / "tools" / "gen_update_manifest.py"
    source_policy = Policy(source_generator)
    selected = Path(generator) if generator is not None else source_generator
    policy = (
        source_policy
        if selected.resolve() == source_generator.resolve()
        else Policy(selected)
    )
    # Discover from the source tree's generator, compare against the selected one.
    # A stale comparison generator cannot narrow source discovery.
    records, gaps = source_inventory(root, source_policy)
    if release:
        records.extend(declarations(source_policy))
    return evaluate(records, policy, served, release, gaps)


def sha256_raw(data):
    return hashlib.sha256(data).hexdigest()


def sha256_lf(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def is_lfs_pointer(blob):
    first = blob.split(b"\n", 1)[0].strip()
    return first.startswith(LFS_POINTER_PREFIX)


def _git_env(isolate_config=True):
    # Inherited GIT_* variables (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, ...)
    # could point git at a repository or index other than the candidate's;
    # every call drops them. Config isolation is separate: cat-file and
    # archive run without the machine's config so raw bytes are the verdict,
    # while the dirty check keeps it, because `git add` would.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    if isolate_config:
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


def git_run(root, *args, config=("core.autocrlf=false", "core.eol=lf"),
            isolated=True):
    # --work-tree pins the candidate: a core.worktree in the repository
    # configuration would otherwise move the dirty check to another directory
    # while write-tree and cat-file read the candidate's index.
    root = Path(root)
    cmd = ["git", "-C", str(root), "--work-tree=" + str(root.resolve())]
    for item in config:
        cmd.extend(["-c", item])
    cmd.extend(args)
    return subprocess.run(
        cmd, capture_output=True, env=_git_env(isolate_config=isolated)
    )


def git_write_tree(root):
    result = git_run(root, "write-tree")
    if result.returncode != 0:
        raise RuntimeError(
            "git write-tree failed: "
            + (result.stderr or result.stdout).decode("utf-8", "replace")
        )
    return result.stdout.decode("ascii").strip()


def cat_file_blob(root, spec):
    result = git_run(root, "cat-file", "blob", spec)
    if result.returncode != 0:
        raise FileNotFoundError(
            spec + ": "
            + (result.stderr or result.stdout).decode("utf-8", "replace")
        )
    return result.stdout


def git_diff_names(root, paths=None):
    args = ["diff", "--name-only", "--"]
    if paths:
        args.extend(paths)
    # This asks whether `git add` would change the staged blob, so it runs
    # under the normalization `git add` uses: the repository's effective
    # config, including the machine's core.autocrlf. The isolated LF config
    # belongs to cat-file and archive, where raw bytes are the verdict;
    # forcing it here made a CRLF worktree file over an LF index blob read
    # as an unstaged edit whenever git re-read the content. Repository, index,
    # and work-tree selection stay pinned to the candidate (GIT_* is still
    # dropped; git_run passes --work-tree).
    result = git_run(root, *args, config=(), isolated=False)
    if result.returncode != 0:
        raise RuntimeError(
            "git diff failed: "
            + (result.stderr or result.stdout).decode("utf-8", "replace")
        )
    text = result.stdout.decode("utf-8", "replace")
    return [line.replace("\\", "/") for line in text.splitlines() if line.strip()]


def git_archive_members(root, tree_id, wanted=None):
    result = git_run(root, "archive", "--format=tar", tree_id)
    if result.returncode != 0:
        raise RuntimeError(
            "git archive failed: "
            + (result.stderr or result.stdout).decode("utf-8", "replace")
        )
    archive = tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:")
    out = {}
    for member in archive.getmembers():
        if not member.isfile():
            continue
        name = member.name.replace("\\", "/")
        if wanted is not None and name not in wanted:
            continue
        handle = archive.extractfile(member)
        out[name] = handle.read() if handle is not None else b""
    return out


def index_blob_sha256(root, rel, treeish="HEAD"):
    return sha256_raw(cat_file_blob(root, "%s:%s" % (treeish, rel)))


def tool_reference_digests(dev_root, treeish="HEAD"):
    """Dev HEAD index blobs of the two release tools. 9d records; 9f fails."""
    return {
        path: index_blob_sha256(dev_root, path, treeish)
        for path in TOOL_REFERENCE_PATHS
    }


def check_tool_reference_drift(dev_root, candidate_root, tree_id):
    """RED path for 9d's tool_reference diagnostic: generator and gate vs dev HEAD."""
    issues = []
    setup = []
    for path in TOOL_REFERENCE_PATHS:
        try:
            ref = index_blob_sha256(dev_root, path, "HEAD")
        except Exception as exc:
            issues.append({
                "stage": "check2_drift",
                "code": "reference_unavailable",
                "path": path,
                "reason": str(exc),
            })
            continue
        try:
            cand = sha256_raw(
                cat_file_blob(candidate_root, "%s:%s" % (tree_id, path))
            )
        except Exception as exc:
            code = (
                "generator_stale"
                if path == "tools/gen_update_manifest.py"
                else "gate_stale"
            )
            issues.append({
                "stage": "check2_drift",
                "code": code,
                "path": path,
                "reason": "candidate tool blob unreadable: %s" % exc,
            })
            continue
        if path == "tools/gen_update_manifest.py" and cand != ref:  # check2 generator comparison
            issues.append({
                "stage": "check2_drift",
                "code": "generator_stale",
                "path": path,
                "dev_sha256": ref,
                "candidate_sha256": cand,
                "reason": "candidate generator index blob differs from dev HEAD",
            })
        if path == "tools/release_gate.py" and cand != ref:  # check2 gate comparison
            issues.append({
                "stage": "check2_drift",
                "code": "gate_stale",
                "path": path,
                "dev_sha256": ref,
                "candidate_sha256": cand,
                "reason": "candidate gate index blob differs from dev HEAD",
            })
    return issues, setup


def check_identity(root, *, archive=True, dev_root=None, tree_id=None):
    """Check 2 identity: staged tree, staged manifest, raw hashes, archive bytes."""
    root = Path(root)
    issues = []
    setup = []
    diagnostics = []
    entry_count = 0
    try:
        if tree_id is None:
            tree_id = git_write_tree(root)
    except Exception as exc:
        setup.append("write-tree unmeasured: %s" % exc)
        return _identity_result(1, tree_id, 0, issues, setup, diagnostics)

    try:
        staged_manifest = cat_file_blob(root, "%s:update_manifest.json" % tree_id)
    except Exception as exc:
        issues.append({
            "stage": "check2_identity",
            "code": "manifest_not_in_index",
            "path": "update_manifest.json",
            "reason": str(exc),
        })
        return _identity_result(1, tree_id, 0, issues, setup, diagnostics)

    try:
        man = json.loads(staged_manifest.decode("utf-8"))
    except Exception as exc:
        issues.append({
            "stage": "check2_identity",
            "code": "invalid_manifest",
            "path": "update_manifest.json",
            "reason": str(exc),
        })
        return _identity_result(1, tree_id, 0, issues, setup, diagnostics)

    try:
        manifest_worktree_dirty = "update_manifest.json" in git_diff_names(
            root, ["update_manifest.json"]
        )
    except Exception as exc:
        setup.append("manifest dirty-check unmeasured: %s" % exc)
        manifest_worktree_dirty = False

    if manifest_worktree_dirty:  # check2 staged-manifest identity
        issues.append({
            "stage": "check2_identity",
            "code": "staged_manifest_dirty",
            "path": "update_manifest.json",
            "reason": "worktree update_manifest.json differs from the staged blob",
        })

    entries = man.get("files") or []
    if not isinstance(entries, list):
        issues.append({
            "stage": "check2_identity",
            "code": "invalid_manifest",
            "path": "update_manifest.json",
            "reason": "files is not a list",
        })
        return _identity_result(1, tree_id, 0, issues, setup, diagnostics)

    shipping = []
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        rel = (ent.get("path") or "").replace("\\", "/")
        want = ent.get("sha256")
        if rel and want:
            shipping.append((rel, want))
    entry_count = len(shipping)

    try:
        dirty = set(git_diff_names(root))
    except Exception as exc:
        setup.append("unstaged-shipping dirty-check unmeasured: %s" % exc)
        dirty = set()
    shipped_names = {rel for rel, _ in shipping}
    for rel in sorted(dirty & shipped_names):
        issues.append({
            "stage": "check2_identity",
            "code": "unstaged_shipping_edit",
            "path": rel,
            "reason": "shipping file modified but not staged",
        })

    members = {}
    if archive:
        try:
            members = git_archive_members(
                root, tree_id, wanted={rel for rel, _ in shipping}
            )
        except Exception as exc:
            setup.append("git archive unmeasured: %s" % exc)

    for rel, want in shipping:
        try:
            blob = cat_file_blob(root, "%s:%s" % (tree_id, rel))
        except Exception as exc:
            issues.append({
                "stage": "check2_identity",
                "code": "manifest_entry_not_in_tree",
                "path": rel,
                "reason": str(exc),
            })
            continue
        raw = sha256_raw(blob)
        lf = sha256_lf(blob)
        if is_lfs_pointer(blob):  # check2 lfs pointer refusal
            issues.append({
                "stage": "check2_identity",
                "code": "lfs_pointer_not_content",
                "path": rel,
                "raw_sha256": raw,
                "manifest_sha256": want,
                "reason": "index blob is a Git LFS pointer; not hashed as model content",
            })
        elif raw != want:  # check2 raw-hash comparison
            code = (
                "raw_hash_mismatch_lf_normalized"
                if lf == want
                else "raw_hash_mismatch"
            )
            issues.append({
                "stage": "check2_identity",
                "code": code,
                "path": rel,
                "raw_sha256": raw,
                "lf_sha256": lf,
                "manifest_sha256": want,
                "reason": (
                    "manifest hash equals LF-normalized blob but not raw index blob"
                    if code.endswith("lf_normalized")
                    else "manifest hash does not equal raw index blob"
                ),
            })
        archive_measured = archive and not any(
            item.startswith("git archive") for item in setup
        )
        archive_ok = rel in members and members[rel] == blob
        if archive_measured and not archive_ok:  # check2 archive/blob identity
            issues.append({
                "stage": "check2_identity",
                "code": (
                    "archive_member_missing"
                    if rel not in members
                    else "archive_blob_mismatch"
                ),
                "path": rel,
                "reason": (
                    "git archive omitted a staged manifest path"
                    if rel not in members
                    else "git archive member bytes differ from the index blob"
                ),
            })

    if dev_root is not None:
        try:
            candidate = {}
            for path in TOOL_REFERENCE_PATHS:
                try:
                    candidate[path] = sha256_raw(
                        cat_file_blob(root, "%s:%s" % (tree_id, path))
                    )
                except Exception:
                    candidate[path] = None
            diagnostics.append({
                "tool_reference": tool_reference_digests(dev_root, "HEAD"),
                "tool_candidate": candidate,
            })
        except Exception as exc:
            diagnostics.append({"tool_reference_unmeasured": str(exc)})

    rc = 1 if issues else 2 if setup else 0
    return _identity_result(rc, tree_id, entry_count, issues, setup, diagnostics)


def _identity_result(rc, tree_id, entry_count, issues, setup, diagnostics):
    return {
        "schema": 1,
        "stage": "check2_identity",
        "rc": rc,
        "tree_id": tree_id,
        "entry_count": entry_count,
        "issues": issues,
        "setup": setup,
        "diagnostics": diagnostics,
    }


def git_ls_tree_entries(root, tree_id):
    """(path, object type) for every entry of the tree, NUL-delimited and unquoted.

    `--name-only` without `-z` quotes and escapes unusual names (a non-ASCII name
    arrives as "caf\\303\\251.txt"), so the spelling git stores would not be the
    spelling compared. Records are `<mode> <type> <oid>\\t<path>\\0`; the path is
    decoded strictly, and an undecodable name is a measurement failure, never a
    silently altered one.
    """
    result = git_run(root, "ls-tree", "-r", "-z", tree_id)
    if result.returncode != 0:
        raise RuntimeError(
            "git ls-tree failed: "
            + (result.stderr or result.stdout).decode("utf-8", "replace")
        )
    entries = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        meta, _tab, raw_path = record.partition(b"\t")
        fields = meta.split(b" ")
        if not _tab or len(fields) != 3:
            raise RuntimeError("git ls-tree record not understood: %r" % record[:80])
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("tree path is not UTF-8: %r (%s)" % (raw_path, exc))
        entries.append((path, fields[1].decode("ascii", "replace")))
    return entries


def git_ls_tree_names(root, tree_id):
    return [path for path, _kind in git_ls_tree_entries(root, tree_id)]


def manifest_path_unsafe_reason(path):
    """Stored manifest path; stricter than the updater's _safe (backslash, colon, dot)."""
    if not isinstance(path, str):
        return "not a string"
    if "\\" in path:
        return "backslash"
    text = path.strip()
    if not text:
        return "empty"
    if text.startswith("/"):
        return "absolute"
    if text.startswith("./"):
        return "leading ./"
    if text.endswith("/"):
        return "trailing slash"
    if ":" in text:
        return "colon"
    parts = text.split("/")
    if any(part == "" for part in parts):
        return "empty segment"
    if any(part == ".." for part in parts):
        return "dotdot"
    if any(part == "." for part in parts):
        return "dot"
    return None


def _policy_from_tree(root, tree_id):
    blob = cat_file_blob(root, "%s:tools/gen_update_manifest.py" % tree_id)
    scratch = tempfile.TemporaryDirectory(prefix="pk_delivery_gen_")
    path = Path(scratch.name) / "gen_update_manifest.py"
    path.write_bytes(blob)
    policy = Policy(path)
    return policy, scratch


def check_delivery_set(root, tree_id, manifest):
    """Check 2 delivery set: M⊆G, remainder, duplicates, casefold, unsafe paths."""
    root = Path(root)
    issues = []
    setup = []
    git_names = []
    git_types = {}
    try:
        tree_entries = git_ls_tree_entries(root, tree_id)
    except Exception as exc:
        setup.append("ls-tree unmeasured: %s" % exc)
        return _delivery_result(2, tree_id, 0, 0, issues, setup)
    git_names = [path for path, _kind in tree_entries]
    git_types = dict(tree_entries)
    g_set = set(git_names)

    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        issues.append({
            "stage": "check2_delivery_set",
            "code": "invalid_manifest",
            "path": "update_manifest.json",
            "reason": "files is not a list",
        })
        return _delivery_result(1, tree_id, len(git_names), 0, issues, setup)

    m_paths = []
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        m_paths.append(ent.get("path"))

    for rel in m_paths:
        unsafe_reason = manifest_path_unsafe_reason(rel)
        if unsafe_reason:  # check2 delivery unsafe-path rejection
            issues.append({
                "stage": "check2_delivery_set",
                "code": "unsafe_manifest_path",
                "path": rel,
                "reason": unsafe_reason,
            })

    counts = {}
    for rel in m_paths:
        if isinstance(rel, str):
            counts[rel] = counts.get(rel, 0) + 1
    for rel, count in counts.items():
        if count > 1:  # check2 delivery duplicate detection
            issues.append({
                "stage": "check2_delivery_set",
                "code": "duplicate_manifest_path",
                "path": rel,
                "count": count,
                "reason": "manifest files list repeats this path",
            })

    m_set = {rel for rel in m_paths if isinstance(rel, str) and rel}
    groups = {}
    for rel in m_set | g_set:
        groups.setdefault(rel.casefold(), set()).add(rel)
    for folded, spellings in groups.items():
        if len(spellings) > 1:  # check2 delivery case-collision detection
            issues.append({
                "stage": "check2_delivery_set",
                "code": "case_collision",
                "path": folded,
                "paths": sorted(spellings),
                "reason": "casefold collision in M, G, or across M/G",
            })

    for rel in sorted(m_set):
        if manifest_path_unsafe_reason(rel):
            continue
        if rel not in g_set:  # check2 delivery MsubseteqG
            issues.append({
                "stage": "check2_delivery_set",
                "code": "manifest_path_untracked",
                "path": rel,
                "reason": "manifest path is not in the staged tree",
            })
        elif git_types.get(rel) != "blob":
            issues.append({
                "stage": "check2_delivery_set",
                "code": "manifest_path_not_blob",
                "path": rel,
                "kind": git_types.get(rel),
                "reason": "manifest path is a tree entry but not a blob",
            })

    runtime_files = set()
    gen_scratch = None
    try:
        policy, gen_scratch = _policy_from_tree(root, tree_id)
        runtime_files = set(policy.files)
    except Exception as exc:
        setup.append("generator whitelist unmeasured: %s" % exc)
    finally:
        if gen_scratch is not None:
            gen_scratch.cleanup()

    justified = {path for path, _reason in JUSTIFIED_GIT_ONLY}
    remainder = g_set - m_set - set(DELIVERY_S) - set(DELIVERY_E) - justified
    runtime_missing = []
    unexplained = []
    for rel in sorted(remainder):
        if rel in runtime_files and rel not in DELIVERY_S:
            runtime_missing.append(rel)
        else:
            unexplained.append(rel)
    for rel in runtime_missing:
        issues.append({
            "stage": "check2_delivery_set",
            "code": "required_runtime_absent_from_manifest",
            "path": rel,
            "reason": "staged ROOT_FILES path is in G but not in M",
        })
    if unexplained:  # check2 delivery unexplained remainder
        for rel in unexplained:
            issues.append({
                "stage": "check2_delivery_set",
                "code": "git_only_unexplained",
                "path": rel,
                "reason": "staged path is not in M, S, E, or JUSTIFIED_GIT_ONLY",
            })

    for rel in REQUIRED_GATE_TOOLS:
        if rel not in g_set:
            issues.append({
                "stage": "check2_delivery_set",
                "code": "required_gate_tool_absent",
                "path": rel,
                "reason": "required gate tool is not in the staged tree",
            })

    rc = 1 if issues else 2 if setup else 0
    return _delivery_result(
        rc, tree_id, len(git_names), len(entries), issues, setup
    )


def _delivery_result(rc, tree_id, g_count, m_count, issues, setup):
    return {
        "schema": 1,
        "stage": "check2_delivery_set",
        "rc": rc,
        "tree_id": tree_id,
        "g_count": g_count,
        "m_count": m_count,
        "issues": issues,
        "setup": setup,
    }


def _npz_issue(code, path, reason, **extra):
    item = {
        "stage": "check2_npz_validity",
        "code": code,
        "path": path,
        "reason": reason,
    }
    item.update(extra)
    return item


def _materialize_required_npz_arrays(np, z, path):
    """Read every key NumpyRF.load reads, with its casts, and check slice bounds."""
    missing = [key for key in NPZ_REQUIRED_KEYS if key not in z.files]
    if missing:
        return _npz_issue(
            "npz_missing_key",
            path,
            "required loader key missing from npz",
            missing=missing,
        )
    arrays = {}
    for key in NPZ_REQUIRED_KEYS:
        try:
            arrays[key] = z[key]
        except Exception as exc:
            return _npz_issue(
                "npz_member_unreadable",
                path,
                "required loader key is listed but unreadable: %s: %s"
                % (type(exc).__name__, exc),
                key=key,
            )
    try:
        classes = arrays["classes"].astype(np.int64)
        scaler_mean = arrays["scaler_mean"].astype(np.float64)
        scaler_scale = arrays["scaler_scale"].astype(np.float64)
        n_trees = int(arrays["n_trees"])
        offsets = arrays["tree_offsets"].astype(np.int64)
        feat_all = arrays["feat"].astype(np.int64)
        thr_all = arrays["thr"].astype(np.float64)
        left_all = arrays["left"].astype(np.int64)
        right_all = arrays["right"].astype(np.int64)
        leaf_all = arrays["leaf_proba"].astype(np.float64)
    except Exception as exc:
        return _npz_issue(
            "npz_shape_invalid",
            path,
            "required array failed the loader dtype cast: %s: %s"
            % (type(exc).__name__, exc),
        )
    if classes.ndim != 1 or classes.size < 1:
        return _npz_issue(
            "npz_shape_invalid", path, "classes must be 1-d with size >= 1",
            shape=list(classes.shape),
        )
    if (
        scaler_mean.ndim != 1
        or scaler_scale.ndim != 1
        or scaler_mean.shape != scaler_scale.shape
        or scaler_mean.size < 1
    ):
        return _npz_issue(
            "npz_shape_invalid",
            path,
            "scaler_mean and scaler_scale must be 1-d and the same length",
            scaler_mean_shape=list(scaler_mean.shape),
            scaler_scale_shape=list(scaler_scale.shape),
        )
    if n_trees < 1:
        return _npz_issue(
            "npz_shape_invalid", path, "n_trees must be >= 1", n_trees=n_trees,
        )
    if offsets.ndim != 1 or int(offsets.shape[0]) != n_trees + 1:
        return _npz_issue(
            "npz_shape_invalid",
            path,
            "tree_offsets must be 1-d with length n_trees + 1",
            n_trees=n_trees,
            tree_offsets_shape=list(offsets.shape),
        )
    for name, arr in (
        ("feat", feat_all),
        ("thr", thr_all),
        ("left", left_all),
        ("right", right_all),
    ):
        if arr.ndim != 1:
            return _npz_issue(
                "npz_shape_invalid", path, "%s must be 1-d" % name,
                key=name, shape=list(arr.shape),
            )
    n_nodes = int(feat_all.shape[0])
    if not (
        int(thr_all.shape[0]) == n_nodes
        and int(left_all.shape[0]) == n_nodes
        and int(right_all.shape[0]) == n_nodes
    ):
        return _npz_issue(
            "npz_shape_invalid",
            path,
            "feat, thr, left, right must share one length",
            feat=int(feat_all.shape[0]),
            thr=int(thr_all.shape[0]),
            left=int(left_all.shape[0]),
            right=int(right_all.shape[0]),
        )
    if leaf_all.ndim != 2 or tuple(leaf_all.shape) != (n_nodes, int(classes.shape[0])):
        return _npz_issue(
            "npz_shape_invalid",
            path,
            "leaf_proba must have shape (n_nodes, n_classes)",
            leaf_proba_shape=list(leaf_all.shape),
            n_nodes=n_nodes,
            n_classes=int(classes.shape[0]),
        )
    for i in range(n_trees):
        a = int(offsets[i])
        b = int(offsets[i + 1])
        if not (0 <= a <= b <= n_nodes):
            return _npz_issue(
                "npz_shape_invalid",
                path,
                "tree slice is outside the flat arrays",
                tree=i, start=a, end=b, n_nodes=n_nodes,
            )
        # Touch the slices the loader materializes.
        _ = feat_all[a:b]
        _ = thr_all[a:b]
        _ = left_all[a:b]
        _ = right_all[a:b]
        _ = leaf_all[a:b]
    if "recommended_gate" in z.files:
        try:
            _ = z["recommended_gate"]
            if "gate_keys" in z.files:
                _ = z["gate_keys"]
        except Exception as exc:
            return _npz_issue(
                "npz_member_unreadable",
                path,
                "optional gate array is listed but unreadable: %s: %s"
                % (type(exc).__name__, exc),
                key="recommended_gate",
            )
    return None


def check_npz_validity(root, tree_id=None, manifest=None):
    """Check 2 NPZ validity: staged served cleanup models open and materialize."""
    root = Path(root)
    issues = []
    setup = []
    opened = 0
    materialized = 0
    ran = False
    try:
        import numpy as np
    except Exception as exc:
        setup.append("numpy missing: %s: %s" % (type(exc).__name__, exc))
        issues.append(_npz_issue(
            "npz_unmeasured",
            "numpy",
            "numpy is not importable; NPZ validity unmeasured",
        ))
        return _npz_result(1, tree_id, 0, 0, issues, setup, False)

    try:
        if tree_id is None:
            tree_id = git_write_tree(root)
        if manifest is None:
            staged = cat_file_blob(root, "%s:update_manifest.json" % tree_id)
            manifest = json.loads(staged.decode("utf-8"))
    except Exception as exc:
        setup.append("staged tree/manifest unmeasured: %s" % exc)
        issues.append(_npz_issue(
            "npz_unmeasured",
            "update_manifest.json",
            "staged tree or manifest unreadable: %s: %s" % (type(exc).__name__, exc),
        ))
        return _npz_result(1, tree_id, 0, 0, issues, setup, False)

    ran = True
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        issues.append(_npz_issue(
            "npz_unmeasured",
            "update_manifest.json",
            "files is not a list",
        ))
        return _npz_result(1, tree_id, 0, 0, issues, setup, True)

    m_set = set()
    for ent in entries:
        if isinstance(ent, dict):
            rel = (ent.get("path") or "").replace("\\", "/")
            if rel:
                m_set.add(rel)

    for rel in SERVED_CLEANUP_NPZ:
        if rel not in m_set:
            issues.append(_npz_issue(
                "npz_absent_from_manifest",
                rel,
                "served cleanup model is not in the staged manifest",
            ))
            continue
        try:
            blob = cat_file_blob(root, "%s:%s" % (tree_id, rel))
        except Exception as exc:
            issues.append(_npz_issue(
                "npz_blob_unreadable",
                rel,
                "staged blob unreadable: %s: %s" % (type(exc).__name__, exc),
            ))
            continue

        format_issue = None
        handle = None
        if is_lfs_pointer(blob):
            format_issue = _npz_issue(
                "npz_lfs_pointer",
                rel,
                "index blob is a Git LFS pointer; not a cleanup NPZ",
            )
        else:
            try:
                handle = np.load(io.BytesIO(blob), allow_pickle=False)
            except Exception as exc:
                format_issue = _npz_issue(
                    "npz_not_zip",
                    rel,
                    "np.load(allow_pickle=False) failed: %s: %s"
                    % (type(exc).__name__, exc),
                )
        if format_issue is not None:  # check2 npz format opening
            issues.append(format_issue)
        if format_issue is not None:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
            continue
        opened += 1
        try:
            arr_issue = _materialize_required_npz_arrays(np, handle, rel)
        finally:
            try:
                handle.close()
            except Exception:
                pass
        if arr_issue is not None:  # check2 npz required-key access
            issues.append(arr_issue)
            continue
        materialized += 1

    rc = 1 if issues else 2 if setup else 0
    return _npz_result(rc, tree_id, opened, materialized, issues, setup, ran)


def _npz_result(rc, tree_id, opened, materialized, issues, setup, ran):
    return {
        "schema": 1,
        "stage": "check2_npz_validity",
        "rc": rc,
        "tree_id": tree_id,
        "opened": opened,
        "materialized": materialized,
        "issues": issues,
        "setup": setup,
        "ran": ran,
    }


def _onnx_issue(code, path, reason, **extra):
    item = {
        "stage": "check2_onnx_validity",
        "code": code,
        "path": path,
        "reason": reason,
    }
    item.update(extra)
    return item


def _onnx_dim_eq(dim, want):
    if dim == want:
        return True
    try:
        return int(dim) == want
    except (TypeError, ValueError):
        return False


def _onnx_dim_symbolic(dim):
    if dim is None:
        return True
    if isinstance(dim, str) and dim != "" and not dim.lstrip("-").isdigit():
        return True
    return False


def _onnx_is_float(type_name):
    return type_name in ("tensor(float)", "tensor(float32)")


def _onnx_contract_and_run(ort, np, session, path):
    """One deterministic conversion scenario. Names are not required."""
    try:
        inputs = list(session.get_inputs())
        outputs = list(session.get_outputs())
    except Exception as exc:
        return _onnx_issue(
            "onnx_contract_input",
            path,
            "session metadata unreadable: %s: %s" % (type(exc).__name__, exc),
        )
    if len(inputs) != 1:
        return _onnx_issue(
            "onnx_contract_input",
            path,
            "model must have exactly one input",
            count=len(inputs),
        )
    inp = inputs[0]
    if not _onnx_is_float(inp.type):
        return _onnx_issue(
            "onnx_contract_input",
            path,
            "input must be tensor(float)",
            type=inp.type,
        )
    in_shape = list(inp.shape) if inp.shape is not None else []
    if not (
        len(in_shape) == 4
        and _onnx_dim_eq(in_shape[0], 1)
        and _onnx_dim_symbolic(in_shape[1])
        and _onnx_dim_eq(in_shape[2], ONNX_MEL_BINS)
        and _onnx_dim_eq(in_shape[3], 1)
    ):
        return _onnx_issue(
            "onnx_contract_input",
            path,
            "input shape must be [1, <symbolic>, %d, 1]" % ONNX_MEL_BINS,
            shape=in_shape,
        )
    if len(outputs) != 1:
        return _onnx_issue(
            "onnx_contract_output",
            path,
            "model must have exactly one output",
            count=len(outputs),
        )
    out = outputs[0]
    if not _onnx_is_float(out.type):
        return _onnx_issue(
            "onnx_contract_output",
            path,
            "output must be tensor(float)",
            type=out.type,
        )
    out_shape = list(out.shape) if out.shape is not None else []
    if not (
        len(out_shape) == 3
        and _onnx_dim_eq(out_shape[0], 1)
        and _onnx_dim_symbolic(out_shape[1])
        and _onnx_dim_eq(out_shape[2], ONNX_N_CLASSES)
    ):
        return _onnx_issue(
            "onnx_contract_output",
            path,
            "output shape must be [1, <symbolic>, %d]" % ONNX_N_CLASSES,
            shape=out_shape,
        )
    feed = {
        inp.name: np.zeros(
            (1, ONNX_PROBE_FRAMES, ONNX_MEL_BINS, 1), dtype=np.float32
        )
    }
    try:
        got = session.run([out.name], feed)
    except Exception as exc:
        return _onnx_issue(
            "onnx_run_failed",
            path,
            "session.run failed: %s: %s" % (type(exc).__name__, exc),
        )
    if not got:
        return _onnx_issue(
            "onnx_run_failed", path, "session.run returned no outputs",
        )
    arr = got[0]
    try:
        shape = tuple(arr.shape)
    except Exception as exc:
        return _onnx_issue(
            "onnx_run_failed",
            path,
            "run output has no shape: %s: %s" % (type(exc).__name__, exc),
        )
    want = (1, ONNX_PROBE_FRAMES, ONNX_N_CLASSES)
    if shape != want:
        return _onnx_issue(
            "onnx_run_failed",
            path,
            "run output shape must be %s" % (want,),
            shape=list(shape),
        )
    try:
        finite = bool(np.isfinite(arr).all())
    except Exception as exc:
        return _onnx_issue(
            "onnx_run_failed",
            path,
            "run output finiteness unreadable: %s: %s"
            % (type(exc).__name__, exc),
        )
    if not finite:
        return _onnx_issue(
            "onnx_run_failed",
            path,
            "run output contains non-finite values",
            shape=list(shape),
        )
    return None


def check_onnx_validity(root, tree_id=None, manifest=None):
    """Check 2 ONNX validity: staged drum model opens and runs the conversion contract."""
    root = Path(root)
    issues = []
    setup = []
    opened = 0
    scenario = 0
    ran = False
    try:
        import onnxruntime as ort
    except Exception as exc:
        setup.append("onnxruntime missing: %s: %s" % (type(exc).__name__, exc))
        issues.append(_onnx_issue(
            "onnx_unmeasured",
            "onnxruntime",
            "onnxruntime is not importable; ONNX validity unmeasured",
        ))
        return _onnx_result(1, tree_id, 0, 0, issues, setup, False)
    try:
        import numpy as np
    except Exception as exc:
        setup.append("numpy missing: %s: %s" % (type(exc).__name__, exc))
        issues.append(_onnx_issue(
            "onnx_unmeasured",
            "numpy",
            "numpy is not importable; ONNX validity unmeasured",
        ))
        return _onnx_result(1, tree_id, 0, 0, issues, setup, False)

    try:
        if tree_id is None:
            tree_id = git_write_tree(root)
        if manifest is None:
            staged = cat_file_blob(root, "%s:update_manifest.json" % tree_id)
            manifest = json.loads(staged.decode("utf-8"))
    except Exception as exc:
        setup.append("staged tree/manifest unmeasured: %s" % exc)
        issues.append(_onnx_issue(
            "onnx_unmeasured",
            "update_manifest.json",
            "staged tree or manifest unreadable: %s: %s" % (type(exc).__name__, exc),
        ))
        return _onnx_result(1, tree_id, 0, 0, issues, setup, False)

    ran = True
    entries = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        issues.append(_onnx_issue(
            "onnx_unmeasured",
            "update_manifest.json",
            "files is not a list",
        ))
        return _onnx_result(1, tree_id, 0, 0, issues, setup, True)

    m_set = set()
    for ent in entries:
        if isinstance(ent, dict):
            rel = (ent.get("path") or "").replace("\\", "/")
            if rel:
                m_set.add(rel)

    for rel in SERVED_ONNX:
        if rel not in m_set:
            issues.append(_onnx_issue(
                "onnx_absent_from_manifest",
                rel,
                "served drum model is not in the staged manifest",
            ))
            continue
        try:
            blob = cat_file_blob(root, "%s:%s" % (tree_id, rel))
        except Exception as exc:
            issues.append(_onnx_issue(
                "onnx_blob_unreadable",
                rel,
                "staged blob unreadable: %s: %s" % (type(exc).__name__, exc),
            ))
            continue

        format_issue = None
        session = None
        if is_lfs_pointer(blob):
            format_issue = _onnx_issue(
                "onnx_lfs_pointer",
                rel,
                "index blob is a Git LFS pointer; not an ONNX model",
            )
        else:
            try:
                session = ort.InferenceSession(
                    blob, providers=["CPUExecutionProvider"]
                )
            except Exception as exc:
                format_issue = _onnx_issue(
                    "onnx_session_failed",
                    rel,
                    "InferenceSession failed: %s: %s"
                    % (type(exc).__name__, exc),
                )
        if format_issue is not None:  # check2 onnx session opening
            issues.append(format_issue)
        if format_issue is not None:
            continue
        opened += 1
        contract_issue = _onnx_contract_and_run(ort, np, session, rel)
        if contract_issue is not None:  # check2 onnx contract check
            issues.append(contract_issue)
            continue
        scenario += 1

    if not issues and not setup:
        if (not ran) or opened < 1 or scenario < 1:
            issues.append(_onnx_issue(
                "onnx_unmeasured",
                SERVED_ONNX[0],
                "served model was not opened and run",
            ))
    rc = 1 if issues else 2 if setup else 0
    return _onnx_result(rc, tree_id, opened, scenario, issues, setup, ran)


def _onnx_result(rc, tree_id, opened, scenario, issues, setup, ran):
    return {
        "schema": 1,
        "stage": "check2_onnx_validity",
        "rc": rc,
        "tree_id": tree_id,
        "opened": opened,
        "scenario": scenario,
        "issues": issues,
        "setup": setup,
        "ran": ran,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--served", type=Path)
    parser.add_argument("--generator", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--identity", action="store_true")
    parser.add_argument("--delivery-set", action="store_true")
    parser.add_argument("--npz-validity", action="store_true")
    parser.add_argument("--onnx-validity", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.identity:
            result = check_identity(args.root)
        elif args.delivery_set:
            tree_id = git_write_tree(args.root)
            staged = cat_file_blob(args.root, "%s:update_manifest.json" % tree_id)
            result = check_delivery_set(
                args.root, tree_id, json.loads(staged.decode("utf-8"))
            )
        elif args.npz_validity:
            result = check_npz_validity(args.root)
        elif args.onnx_validity:
            result = check_onnx_validity(args.root)
        else:
            result = check(args.root, args.served, args.generator, args.release)
    except Exception as error:
        result = {
            "schema": 1,
            "stage": (
                "check2_identity" if args.identity
                else "check2_delivery_set" if args.delivery_set
                else "check2_npz_validity" if args.npz_validity
                else "check2_onnx_validity" if args.onnx_validity
                else "check1_required_assets"
            ),
            "rc": 2,
            "records": [],
            "issues": [],
            "review_queue": [],
            "setup": [f"{type(error).__name__}: {error}"],
            "entry_count": 0,
            "tree_id": None,
            "diagnostics": [],
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.identity:
        for issue in result["issues"]:
            print("RED " + json.dumps(issue, ensure_ascii=False, sort_keys=True))
        for gap in result["setup"]:
            print("UNMEASURED " + gap)
        print(
            "SUMMARY check2_identity "
            f"entries={result.get('entry_count', 0)} "
            f"red={len(result['issues'])} "
            f"unmeasured={len(result['setup'])} "
            f"tree={(result.get('tree_id') or '?')[:12]} rc={result['rc']}"
        )
    elif args.delivery_set:
        for issue in result["issues"]:
            print("RED " + json.dumps(issue, ensure_ascii=False, sort_keys=True))
        for gap in result["setup"]:
            print("UNMEASURED " + gap)
        print(
            "SUMMARY check2_delivery_set "
            f"G={result.get('g_count', 0)} "
            f"M={result.get('m_count', 0)} "
            f"red={len(result['issues'])} "
            f"unmeasured={len(result['setup'])} "
            f"tree={(result.get('tree_id') or '?')[:12]} rc={result['rc']}"
        )
    elif args.npz_validity:
        for issue in result["issues"]:
            print("RED " + json.dumps(issue, ensure_ascii=False, sort_keys=True))
        for gap in result["setup"]:
            print("UNMEASURED " + gap)
        print(
            "SUMMARY check2_npz_validity "
            f"opened={result.get('opened', 0)} "
            f"materialized={result.get('materialized', 0)} "
            f"red={len(result['issues'])} "
            f"unmeasured={len(result['setup'])} "
            f"tree={(result.get('tree_id') or '?')[:12]} rc={result['rc']}"
        )
    elif args.onnx_validity:
        for issue in result["issues"]:
            print("RED " + json.dumps(issue, ensure_ascii=False, sort_keys=True))
        for gap in result["setup"]:
            print("UNMEASURED " + gap)
        print(
            "SUMMARY check2_onnx_validity "
            f"opened={result.get('opened', 0)} "
            f"scenario={result.get('scenario', 0)} "
            f"red={len(result['issues'])} "
            f"unmeasured={len(result['setup'])} "
            f"tree={(result.get('tree_id') or '?')[:12]} rc={result['rc']}"
        )
    else:
        for record in result["review_queue"]:
            print("REVIEW " + json.dumps(record, ensure_ascii=False, sort_keys=True))
        for issue in result["issues"]:
            print("RED " + json.dumps(issue, ensure_ascii=False, sort_keys=True))
        for gap in result["setup"]:
            print("UNMEASURED " + gap)
        print(
            "SUMMARY check1_required_assets "
            f"records={len(result['records'])} "
            f"red={len(result['issues'])} "
            f"review={len(result['review_queue'])} "
            f"unmeasured={len(result['setup'])} rc={result['rc']}"
        )
    return result["rc"]


if __name__ == "__main__":
    raise SystemExit(main())
