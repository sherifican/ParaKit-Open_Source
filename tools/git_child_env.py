"""Environment and command pins for every git subprocess a release tool starts.

Inherited GIT_* names are dropped. A parent GIT_DIR, GIT_WORK_TREE, or
GIT_INDEX_FILE must not retarget the child, and a parent GIT_CONFIG_* must
not put the machine's config back. Global and system config are pointed at
the null device. Replace refs are refused. A credential prompt is refused.
HOME, USERPROFILE, HOMEDRIVE, and HOMEPATH are kept so Path.home() still
resolves to the same directory the parent process uses.

XDG_CONFIG_HOME points at a directory this process creates and removes
at exit. Git reads ignore and attributes from $XDG_CONFIG_HOME/git when
that variable is set, and from $HOME/.config/git when it is not. The user's
XDG git files must not decide a child's result. HOME itself is not changed.
The directory's git/ignore is this program's own rule, not the user's file:
.claude/settings.local.json is ignored at any depth.

local_git_config_args() is the -c pair for a local git command:
core.fsmonitor=false and core.hooksPath set to the null device. An empty
core.hooksPath looks hooks up at the root of the repository's drive. The
null device is not a directory a hook can be written into. Callers insert
that pair immediately after "git" and before -C, --work-tree, and any
caller -c, so a later -c for the same key still wins.
"""
import atexit
import os
import shutil
import stat
import subprocess
import tempfile

_EMPTY_XDG = None
# Gitignore pattern. A leading ** matches at every depth, including the
# repository root. The user's backslash spelling of this path matches nothing.
_LOCAL_SETTINGS_IGNORE = "**/.claude/settings.local.json\n"


def hides_local_claude_settings(rel):
    """True for .claude/settings.local.json at any depth.

    The release tools decide this themselves. The user's git ignore is not read.
    """
    text = str(rel).replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    parts = [part for part in text.split("/") if part and part != "."]
    return (
        len(parts) >= 2
        and parts[-2] == ".claude"
        and parts[-1] == "settings.local.json"
    )


def _chmod_then_retry(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        return
    func(path)


def _cleanup_empty_xdg():
    """Remove only the directory this process created."""
    path = _EMPTY_XDG
    if not path or not os.path.isdir(path):
        return
    if not os.path.basename(path).startswith("pk_git_empty_xdg_"):
        return
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(tempfile.gettempdir()):
        return
    try:
        shutil.rmtree(path, onexc=_chmod_then_retry)
    except OSError:
        return


atexit.register(_cleanup_empty_xdg)


def _empty_xdg_config_home():
    """XDG config home whose git/ignore is only this program's settings rule."""
    global _EMPTY_XDG
    if _EMPTY_XDG is None or not os.path.isdir(_EMPTY_XDG):
        path = tempfile.mkdtemp(prefix="pk_git_empty_xdg_")
        ignore_dir = os.path.join(path, "git")
        os.mkdir(ignore_dir)
        with open(os.path.join(ignore_dir, "ignore"), "w", encoding="ascii", newline="\n") as handle:
            handle.write(_LOCAL_SETTINGS_IGNORE)
        _EMPTY_XDG = path
    return _EMPTY_XDG


def git_child_env(base=None):
    """A git subprocess environment built from `base` (default: os.environ)."""
    if base is None:
        base = os.environ
    env = {
        key: value
        for key, value in base.items()
        if not str(key).upper().startswith("GIT_")
    }
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["XDG_CONFIG_HOME"] = _empty_xdg_config_home()
    return env


def local_git_config_args():
    """-c pins inserted immediately after the git executable."""
    return ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull]


def run_git(args, *, cwd=None, text=False, timeout=None):
    """Run a git command with the child environment and the local -c pins.

    `args` starts with "git". The pins are inserted before every other
    argument. stdout and stderr are captured. `text` and `timeout` are the
    subprocess arguments of those names; the default is bytes and no timeout.
    """
    if isinstance(args, tuple):
        args = list(args)
    else:
        args = list(args)
    if not args or args[0] != "git":
        raise ValueError("run_git requires a command that starts with git")
    cmd = ["git", *local_git_config_args(), *args[1:]]
    kwargs = {"capture_output": True, "env": git_child_env()}
    if cwd is not None:
        kwargs["cwd"] = cwd
    if text:
        kwargs["text"] = True
    if timeout is not None:
        kwargs["timeout"] = timeout
    return subprocess.run(cmd, **kwargs)
