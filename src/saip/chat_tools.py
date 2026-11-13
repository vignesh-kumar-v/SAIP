"""What the chat model may do for you, and the guard rails around each thing.

Reading tools (the screen, the activity summary, recent commits, files in your watched folders, the web) run at once.
Writing tools (the clipboard, saving a file) only ever return a preview here; `perform` runs after you approve, and
`undo_write` puts a file back if it has not changed since. Files are reached only inside the watched folders, never
through a symlink that leaves them, never inside `.git`, and only plain-text kinds. A tool's failure is plain words
given to the model and to you, never an exception.

Because the model may be small, local and easy to talk round (by you, or by text it reads in a file or a web page),
some limits do not depend on it behaving:

* Secret files (keys, credentials, `.env`, SAIP's own `private.toml`) are never read or written, and files git
  ignores are never read: that is where secrets usually live.
* Everything a tool returns has keys, tokens and passwords masked as "[hidden]" (`mask_output`: the token rules
  of screen text, plus `name = "value"` rules that leave ordinary code such as `api_key: str` alone).
  A write that would put "[hidden]" into a file is refused, so a masked secret is never written over the real one.
* CI and editor settings (`.github`, `.gitlab-ci.yml`, `.vscode`, pre-commit and git-hook managers) are never
  written. Files that run code by themselves (shell scripts, package.json, pyproject.toml, conftest.py and the like)
  can be written, but the approval card says so.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import subprocess
from pathlib import Path

from . import repos as repos_mod
from . import websearch
from .screen import TOKEN_SECRETS, copy_to_clipboard
from llm.checks import injection_signs
from llm.sanitize import strip_hidden

TEXT_EXTENSIONS = frozenset({".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml",
                             ".html", ".css", ".csv", ".tex", ".sh", ".c", ".cpp", ".h", ".java", ".go", ".rs"})
READ_LIMIT, WRITE_LIMIT, OUTPUT_LIMIT = 60_000, 200_000, 12_000
MAX_WALKED = 20_000          # folder entries list_files and search_text will look at, so a huge folder cannot stall a reply
READ_TOOLS = ("read_screen", "activity_summary", "list_repos", "git_status", "git_log", "git_diff",
              "list_files", "search_text", "read_file", "web_search", "fetch_page")
APPROVE_TOOLS = ("copy_to_clipboard", "write_file", "edit_file")
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"})
# Tools whose result is text the person did not write for the model: it can carry instructions meant to steer it.
# An action proposed after any of them (anywhere in the chat, since the model still remembers it) has to be
# confirmed on purpose. File and folder listings count: they return names and contents from the person's disk.
UNTRUSTED_TOOLS = frozenset({"read_file", "git_diff", "git_log", "git_status", "list_repos", "list_files",
                             "search_text", "read_screen", "web_search", "fetch_page"})


# Never read or written, whatever the model says. Matched against the file name, ignoring case.
SECRET_NAMES = re.compile(
    r"(private\.toml|\.env(\..*)?|.*\.env|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|(secrets?|credentials?)([._-].*)?"
    r"|.*[._-](secrets?|credentials?)(\..*)?|service[-_]?account.*\.json|.*\.(pem|key|p12|pfx|jks|keystore|kdbx)"
    r"|\.(npmrc|pypirc|netrc|pgpass|git-credentials)|kubeconfig.*|tokens?\.json)", re.IGNORECASE)
SECRET_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure", ".gcloud", ".password-store"})
# Never written: they run on other machines or every time you open the project.
PROTECTED_DIRS = frozenset({".github", ".gitlab", ".circleci", ".buildkite", ".husky", ".vscode", ".idea", ".devcontainer"})
PROTECTED_NAMES = frozenset({".gitlab-ci.yml", ".pre-commit-config.yaml", ".travis.yml", "azure-pipelines.yml",
                             "bitbucket-pipelines.yml", "lefthook.yml", ".lefthook.yml"})
# Written only with a warning on the approval card: these run code without anyone opening them.
RUNS_CODE_NAMES = frozenset({"package.json", "pyproject.toml", "setup.py", "conftest.py", "noxfile.py",
                             "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"})
RUNS_CODE_WARNING = ("This file runs code by itself ({why}). Read every change before approving: "
                     "a bad edit here runs without you opening the file.")
HIDDEN = "[hidden]"
# Masking for what tools return (code, config, diffs, pages). The name is kept and only the value hidden, and code
# such as `def f(api_key: str)` or `token = cfg.token` is left alone.
_NAME = r"[\w.-]*(?:password|passwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key)[\w.-]*"
_QUOTED_SECRET = re.compile(                        # db_password = "hunter2", "apiKey": 'abc123', TOKEN: "x9y8"
    rf"""(?i)(\b{_NAME}["']?\s*[:=]\s*)(["'])(?!\s*[{{$<%])(?!{_NAME}\2)([^"'\n]{{4,}})\2""")
_BARE_SECRET = re.compile(                          # PASSWORD=hunter2 or `token: abc123` alone on a config line
    rf"""(?im)^(\s*(?:export\s+)?{_NAME}\s*[:=]\s*)"""
    r"""(?!(?:none|null|nil|true|false|str|string|int|undefined|optional\b)\s*(?:#.*)?$)"""
    r"""(?![A-Za-z_]+(?:\.[A-Za-z_]+)*[ \t]*(?:#.*)?$)"""   # a plain name such as cfg.token is code, not a value
    r"""(?![$<{%*])([^\s#'"(){}\[\],;]{4,})[ \t]*(?:#.*)?$""")


_WORDS = re.compile(r"[a-z0-9]{1,15}(?:[_-][a-z0-9]{1,15}){3,}")      # test_f1_stops_the_listener: a name, not a token


def mask_output(text: str) -> str:
    """Hide secrets in what a tool returns before the model sees it."""
    text = _QUOTED_SECRET.sub(lambda m: f"{m[1]}{m[2]}{HIDDEN}{m[2]}", text)
    text = _BARE_SECRET.sub(lambda m: f"{m[1]}{HIDDEN}", text)
    for pat in TOKEN_SECRETS:
        text = pat.sub(lambda m: m[0] if _WORDS.fullmatch(m[0]) else HIDDEN, text)
    return text


class ToolError(Exception):
    """Something the tool cannot or will not do; the message is shown to the model and to the person."""


def _tilde(path) -> str:
    return repos_mod.tilde(Path(path))


def _roots(roots) -> list[Path]:
    return [Path(r).expanduser().resolve() for r in roots]


def safe_path(path: str, roots: list[str], *, for_write: bool = False) -> Path:
    if not roots:
        raise ToolError("no watched folder is set up, so there is nowhere I may read or write")
    if not isinstance(path, str) or not path.strip():
        raise ToolError("no file path was given")
    resolved_roots = _roots(roots)
    given = Path(path.strip()).expanduser()
    if given.is_absolute():
        candidates = [given]
    else:
        candidates = [r / given for r in resolved_roots]
        if not for_write:
            candidates = [c for c in candidates if c.exists()] or candidates[:1]
        else:
            candidates = candidates[:1]
    target = candidates[0]
    if for_write and target.is_symlink():
        raise ToolError(f"{_tilde(target)} is a symbolic link, so I will not write to it")
    try:
        full = target.resolve() if (target.exists() or not for_write) else target.parent.resolve() / target.name
    except (OSError, RuntimeError) as e:
        raise ToolError(f"cannot resolve {path}: {e}") from e
    root = next((r for r in resolved_roots if full.is_relative_to(r)), None)
    if root is None:
        raise ToolError(f"{path} is outside the watched folders")
    inside = full.relative_to(root)                             # folder rules look only below the watched folder
    if ".git" in full.parts:
        raise ToolError("files inside .git are off limits")
    if secret_file(inside):
        raise ToolError(f"{full.name} looks like it holds keys or passwords, so I will not open it")
    if for_write and protected_file(inside):
        raise ToolError(f"{_tilde(full)} is CI or editor configuration, which I never change: edit it yourself")
    if full.suffix.lower() not in TEXT_EXTENSIONS:
        raise ToolError(f"{full.name} is not a plain-text kind of file I handle")
    if for_write:
        if not full.parent.is_dir():
            raise ToolError(f"the folder {_tilde(full.parent)} does not exist")
        if full.is_dir():
            raise ToolError(f"{_tilde(full)} is a folder")
    elif not full.exists():
        raise ToolError(f"{path} does not exist")
    elif not full.is_file():
        raise ToolError(f"{path} is not a regular file")
    return full


def secret_file(path: Path) -> bool:
    """Keys, credentials and settings files, by name or by the folder they sit in."""
    return bool(SECRET_NAMES.fullmatch(path.name)) or any(part in SECRET_DIRS for part in path.parts)


def protected_file(path: Path) -> bool:
    return path.name.lower() in PROTECTED_NAMES or any(part in PROTECTED_DIRS for part in path.parts)


def runs_code(path: Path) -> str | None:
    """Why writing this file is riskier than most, or None."""
    name = path.name.lower()
    if path.suffix.lower() == ".sh":
        return "it is a shell script"
    if name == "package.json":
        return "npm runs its scripts, some on install"
    if name in ("pyproject.toml", "setup.py"):
        return "it runs when the project is built or installed"
    if name == "conftest.py":
        return "pytest runs it before every test"
    if name in RUNS_CODE_NAMES:
        return "a tool runs it when the project starts"
    return None


def git_ignored(path: Path) -> bool:
    """True when the file is in a git repository that ignores it. No git, or not a repository: False."""
    try:
        done = subprocess.run(["git", "-C", str(path.parent), "check-ignore", "-q", "--", path.name],
                              capture_output=True, timeout=5, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def _cap(text: str, limit: int = OUTPUT_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + " […]"


def _git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except FileNotFoundError as e:
        raise ToolError("git is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise ToolError("git took too long to answer") from e
    if done.returncode != 0:
        raise ToolError("git failed: " + (done.stderr.strip() or "unknown error")[:200])
    return done.stdout


SCHEMAS = {
    "read_screen": ("Read the text in the app the person has in front of them, near the cursor.", {}, []),
    "activity_summary": ("Get SAIP's current summary of the person's recent activity.", {}, []),
    "list_repos": ("List the watched git repositories (name, path, and current branch).", {}, []),
    "git_status": ("Show the current branch and modified/untracked files of a watched git repository.",
                   {"repo": {"type": "string", "description": "repository name or folder; omit if only one or active"}}, []),
    "git_log": ("List recent commits (one line each) of a watched git repository.",
                {"repo": {"type": "string", "description": "repository name or folder; omit if only one or active"},
                 "limit": {"type": "integer", "description": "how many commits, at most 20"}}, []),
    "git_diff": ("Show the uncommitted changes (diffstat, then the diff) of a watched git repository.",
                 {"repo": {"type": "string", "description": "repository name or folder; omit if only one or active"}}, []),
    "list_files": ("List plain-text files inside a watched folder or repository.",
                   {"path": {"type": "string", "description": "folder or repository name; omit for active repo or root"},
                    "limit": {"type": "integer", "description": "max files to list, at most 200"}}, []),
    "search_text": ("Search for text across plain-text files inside a watched folder or repository.",
                    {"query": {"type": "string", "description": "text to search for (case-insensitive)"},
                     "path": {"type": "string", "description": "folder or repository name; omit for active repo or root"}},
                    ["query"]),
    "read_file": ("Read a text file inside a watched folder.",
                  {"path": {"type": "string", "description": "absolute, ~/..., or relative to a watched folder"}},
                  ["path"]),
    "web_search": ("Search the web and get titles, links and snippets.",
                   {"query": {"type": "string"}}, ["query"]),
    "fetch_page": ("Open a public web page and read its text.", {"url": {"type": "string"}}, ["url"]),
    "copy_to_clipboard": ("Propose copying text to the clipboard; the person must approve first.",
                          {"text": {"type": "string"}}, ["text"]),
    "write_file": ("Propose saving a text file inside a watched folder (new or replacing); the person must approve "
                   "first.", {"path": {"type": "string"}, "content": {"type": "string", "description": "whole file"}},
                   ["path", "content"]),
    "edit_file": ("Propose replacing one exact block of text in an existing file; the person must approve first.",
                  {"path": {"type": "string"},
                   "old_text": {"type": "string", "description": "exact text currently in the file (unique match)"},
                   "new_text": {"type": "string", "description": "replacement text"}},
                  ["path", "old_text", "new_text"]),
}


class Toolbox:
    def __init__(self, settings, runtime=None, plat=None, *, search_opener=None, resolve=None):
        self.settings, self.runtime, self.plat = settings, runtime, plat
        self.search_opener, self.resolve = search_opener, resolve
        llm, roots = settings.llm, list(settings.agent.watch_roots)
        names = []
        if llm.chat_tools:
            if runtime is not None:
                names.append("activity_summary")
                if llm.read_screen:
                    names.append("read_screen")
            if roots:
                names += ["list_repos", "git_status", "git_log", "git_diff", "list_files", "search_text",
                          "read_file", "write_file", "edit_file"]
            names.append("copy_to_clipboard")
            if llm.web_search:
                names += ["web_search", "fetch_page"]
        self.names = names

    def reading_only(self) -> "Toolbox":
        """This toolbox without the actions that need approval (for the help written right after "Help me")."""
        self.names = [n for n in self.names if n in READ_TOOLS]
        return self

    @property
    def roots(self) -> list[str]:
        return list(self.settings.agent.watch_roots)

    def _path(self, path, *, for_write: bool = False) -> Path:
        """safe_path, plus nested-repo prefix resolution and SAIP's own settings file."""
        if isinstance(path, str) and path.strip():
            raw = path.strip()
            p = Path(raw).expanduser()
            if not p.is_absolute() and not any((r / p).exists() for r in _roots(self.roots)):
                parts = p.parts
                if len(parts) >= 2:
                    try:
                        repo = self._repo({"repo": parts[0]})
                        path = str(repo.joinpath(*parts[1:]))
                    except ToolError:
                        pass
                if path == raw:
                    try:
                        repo = self._repo({})
                        if (repo / p).exists() or (for_write and (repo / p).parent.is_dir()):
                            path = str(repo / p)
                    except ToolError:
                        pass
        full = safe_path(path, self.roots, for_write=for_write)
        if for_write and full.exists() and git_ignored(full):
            # Not even to propose an edit: the checks answer the model before any approval ("old_text not found",
            # "matched 2 places"), and guessing at old_text would let it learn the contents of a file piece by piece.
            raise ToolError(f"git ignores {full.name}, and ignored files often hold secrets, so I will not edit it")
        from llm.config import find_config
        config = find_config(getattr(getattr(self.runtime, "opts", None), "config", None))
        if config is not None and full == Path(config).expanduser().resolve():
            raise ToolError(f"{full.name} holds SAIP's own settings, so I will not open it")
        return full

    def schemas(self) -> list[dict]:
        out = []
        for n in self.names:
            desc, props, required = SCHEMAS[n]
            out.append({"type": "function", "function": {"name": n, "description": desc, "parameters": {
                "type": "object", "properties": props, "required": required}}})
        return out

    # ------------------------------------------------------------------------------------------------ running
    def run(self, name: str, arguments: dict) -> dict:
        try:
            if name not in self.names:
                raise ToolError(f"the tool '{name}' is not available")
            args = arguments if isinstance(arguments, dict) else {}
            if name in APPROVE_TOOLS:
                self._check(name, args)
                return {"kind": "approve", "preview": self.preview(name, args)}
            result, label = getattr(self, "_" + name)(args)
            text, hidden = strip_hidden(result)
            flag = injection_signs(text)
            if hidden and hidden["message"]:
                flag.insert(0, f"hides a message in invisible characters: \u201c{hidden['message'][:120]}\u201d")
            out = {"kind": "read", "result": _cap(mask_output(text)), "label": label}
            if flag:
                out["flag"] = flag
            return out
        except (ToolError, websearch.SearchError) as e:
            return {"kind": "error", "result": str(e), "label": f"Could not use {name}: {e}"[:120]}
        except Exception as e:                                  # a tool must never take the chat down
            return {"kind": "error", "result": f"{name} failed: {e}", "label": f"Could not use {name}"}

    def _edit_content(self, args: dict) -> tuple[Path, str, str]:
        path = self._path(args.get("path"), for_write=True)
        if not path.exists():
            raise ToolError(f"{_tilde(path)} does not exist")
        old_text, new_text = args.get("old_text"), args.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise ToolError("no old_text was given")
        if not isinstance(new_text, str):
            raise ToolError("no new_text was given")
        previous = path.read_text(encoding="utf-8", errors="replace")
        matches = previous.count(old_text)
        if matches == 0:
            raise ToolError(f"old_text was not found in {_tilde(path)}")
        if matches > 1:
            raise ToolError(f"old_text matched {matches} places in {_tilde(path)}; include more surrounding lines")
        updated = previous.replace(old_text, new_text, 1)
        if len(updated) > WRITE_LIMIT:
            raise ToolError(f"that file is longer than the {WRITE_LIMIT:,} characters I may write")
        if updated.count(HIDDEN) > previous.count(HIDDEN):
            raise ToolError(f"the new text contains {HIDDEN}, where SAIP masked a secret I was not shown; "
                            "writing it would replace the real value, so edit that part yourself")
        return path, previous, updated

    def _check(self, name: str, args: dict) -> None:
        if name == "write_file":
            path = self._path(args.get("path"), for_write=True)
            content = args.get("content")
            if not isinstance(content, str):
                raise ToolError("no file content was given")
            if len(content) > WRITE_LIMIT:
                raise ToolError(f"that file is longer than the {WRITE_LIMIT:,} characters I may write")
            before = path.read_text(encoding="utf-8", errors="replace").count(HIDDEN) if path.exists() else 0
            if content.count(HIDDEN) > before:
                raise ToolError(f"the new text contains {HIDDEN}, where SAIP masked a secret I was not shown; "
                                "writing it would replace the real value, so edit that part yourself")
        elif name == "edit_file":
            self._edit_content(args)
        elif not isinstance(args.get("text"), str) or not args["text"]:
            raise ToolError("there was no text to copy")

    def _read_screen(self, args):
        screen = self.runtime.look()
        if screen is None:
            raise ToolError("nothing readable in the app in front")
        return f"Text near the cursor in {screen.app}:\n{screen.text}", f"Looked at your screen ({screen.app})"

    def _activity_summary(self, args):
        text = self.runtime.snapshot_context() or ""
        if not text:
            raise ToolError("there is no activity summary yet")
        return text, "Got the activity summary"

    def _repo(self, args) -> Path:
        given = args.get("repo")
        found = repos_mod.discover(self.settings.agent)
        if not given:
            if not found:
                raise ToolError("no git repository is being watched")
            if len(found) == 1:
                return found[0]
            ctx = (getattr(self.runtime, "last_context", "") or "").lower()
            if ctx:
                # A repository counts as "the one in front" only when its whole name appears in the recent activity
                # and exactly one repository does. A short name ("repo") must not match inside another word.
                by_ctx = [r for r in found if len(r.name) >= 4
                          and re.search(rf"(?<![\w-]){re.escape(r.name.lower())}(?![\w-])", ctx)]
                if len(by_ctx) == 1:
                    return by_ctx[0]
            names = ", ".join(r.name for r in found[:10])
            more = f" (and {len(found) - 10} more)" if len(found) > 10 else ""
            raise ToolError(f"multiple repositories are watched ({names}{more}); pass repo=... to choose one")
        raw = str(given).strip()
        repo = Path(raw).expanduser()
        if not repo.is_absolute():
            # Match against discovered nested repos by exact name, normalized name, or suffix before root / repo.
            low = raw.lower()
            norm = re.sub(r"[-_\s]+", "", low)
            exact = [r for r in found if r.name.lower() == low]
            if len(exact) == 1:
                return exact[0]
            normed = [r for r in found if re.sub(r"[-_\s]+", "", r.name.lower()) == norm]
            if len(normed) == 1:
                return normed[0]
            partial = [r for r in found if r.as_posix().lower().endswith("/" + low) or low in r.name.lower()]
            if len(partial) == 1:
                return partial[0]
            repo = next((r / repo for r in _roots(self.roots) if (r / repo).is_dir()), _roots(self.roots)[0] / repo)
        repo = repo.resolve()
        if not any(repo.is_relative_to(r) for r in _roots(self.roots)):
            raise ToolError(f"{given} is outside the watched folders")
        if not (repo / ".git").exists():
            by_name = {r.name.lower(): r.name for r in found}
            close = [by_name[m] for m in difflib.get_close_matches(raw.lower(), by_name, n=3, cutoff=0.55)]
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            if not hint and found:
                hint = f" Available: {', '.join(r.name for r in found[:8])}."
            raise ToolError(f"{_tilde(repo)} is not a git repository.{hint}")
        return repo

    def _list_repos(self, args):
        found = repos_mod.discover(self.settings.agent)
        if not found:
            return "(no git repositories are being watched)", "Listed watched repositories"
        lines = []
        for r in found[:40]:
            try:
                branch = _git(r, "rev-parse", "--abbrev-ref", "HEAD").strip() or "?"
            except ToolError:
                branch = "?"
            lines.append(f"{r.name}\t{branch}\t{_tilde(r)}")
        if len(found) > 40:
            lines.append(f"... and {len(found) - 40} more")
        return "\n".join(lines), f"Listed {len(found)} watched repositories"

    def _git_status(self, args):
        repo = self._repo(args)
        out = _git(repo, "status", "-sb").strip() or "(clean)"
        return out, f"Checked git status in {_tilde(repo)}"

    def _git_log(self, args):
        repo = self._repo(args)
        try:
            n = max(1, min(int(args.get("limit") or 10), 20))
        except (TypeError, ValueError):
            n = 10
        return _git(repo, "log", "--oneline", "-n", str(n), "--no-color") or "(no commits)", \
            f"Read recent commits in {_tilde(repo)}"

    def _git_diff(self, args):
        repo = self._repo(args)
        plain = ("diff", "--no-color", "--no-ext-diff", "--no-textconv")
        stat, diff = _git(repo, *plain, "--stat"), _git(repo, *plain)
        return ((stat + "\n" + diff).strip() or "(no uncommitted changes)"), f"Read the working diff in {_tilde(repo)}"

    def _dir(self, given) -> tuple[Path, Path]:
        resolved_roots = _roots(self.roots)
        if not resolved_roots:
            raise ToolError("no watched folder is set up, so there is nowhere I may read")
        if not given:
            try:
                base = self._repo({})
            except ToolError:
                base = resolved_roots[0]
        else:
            raw = str(given).strip()
            p = Path(raw).expanduser()
            if p.is_absolute():
                base = p
            else:
                direct = next((r / p for r in resolved_roots if (r / p).is_dir()), None)
                if direct is not None:
                    base = direct
                elif len(p.parts) >= 2:
                    base = self._repo({"repo": p.parts[0]}).joinpath(*p.parts[1:])
                else:
                    try:
                        base = self._repo({"repo": raw})
                    except ToolError:
                        base = self._repo({}) / p
        full = base.resolve()
        root = next((r for r in resolved_roots if full.is_relative_to(r)), None)
        if root is None:
            raise ToolError(f"{given} is outside the watched folders")
        if ".git" in full.parts or any(part in SECRET_DIRS for part in full.relative_to(root).parts):
            raise ToolError(f"{_tilde(full)} is off limits")
        if not full.is_dir():
            raise ToolError(f"{_tilde(full)} is not a folder")
        return full, root

    def _walk_files(self, base: Path, root: Path, *, max_depth: int = 4) -> list[Path]:
        files: list[Path] = []
        asked: dict[Path, set[Path]] = {}                       # directory -> its files git ignores, asked once
        stack: list[tuple[Path, int]] = [(base, 0)]
        looked = 0
        while stack and looked < MAX_WALKED:
            cur, depth = stack.pop()
            try:
                entries = sorted(cur.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for entry in entries:
                looked += 1
                if entry.is_symlink():
                    continue
                rel = entry.relative_to(root)
                if entry.is_dir():
                    if entry.name in SKIP_DIRS or entry.name in SECRET_DIRS:
                        continue
                    if depth < max_depth:
                        stack.append((entry, depth + 1))
                elif entry.is_file() and entry.suffix.lower() in TEXT_EXTENSIONS:
                    if not secret_file(rel) and entry not in self._ignored_in(cur, asked):
                        files.append(entry)
        return sorted(files, key=lambda p: (len(p.relative_to(base).parts), str(p.relative_to(base)).lower()))

    @staticmethod
    def _ignored_in(folder: Path, asked: dict) -> set:
        """The files in `folder` that git ignores: one `git check-ignore` call for the whole folder, not one per file."""
        if folder in asked:
            return asked[folder]
        ignored: set[Path] = set()
        try:
            names = [p.name for p in folder.iterdir() if p.is_file()]
            if names:
                done = subprocess.run(["git", "-C", str(folder), "check-ignore", "--stdin", "-z"], input="\0".join(names) + "\0",
                                      capture_output=True, text=True, timeout=10, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
                if done.returncode in (0, 1):                    # 1 means none of them is ignored
                    ignored = {folder / n for n in done.stdout.split("\0") if n}
        except (OSError, subprocess.TimeoutExpired):
            pass
        asked[folder] = ignored
        return ignored

    def _list_files(self, args):
        base, root = self._dir(args.get("path"))
        try:
            limit = max(1, min(int(args.get("limit") or 100), 200))
        except (TypeError, ValueError):
            limit = 100
        files = self._walk_files(base, root)
        shown = [f.relative_to(base).as_posix() for f in files[:limit]]
        if len(files) > limit:
            shown.append(f"... and {len(files) - limit} more")
        return ("\n".join(shown) or "(no text files found)"), f"Listed files in {_tilde(base)}"

    def _search_text(self, args):
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolError("no search query was given")
        base, root = self._dir(args.get("path"))
        low = query.lower()
        hits: list[str] = []
        for f in self._walk_files(base, root):
            try:
                if f.stat().st_size > READ_LIMIT:
                    continue
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = f.relative_to(base).as_posix()
            for idx, line in enumerate(text.splitlines(), 1):
                if low in line.lower():
                    hits.append(f"{rel}:{idx}: {line.strip()[:160]}")
                    if len(hits) >= 50:
                        return "\n".join(hits) + "\n... (stopped at 50 matches)", f"Searched '{query}' in {_tilde(base)}"
        return ("\n".join(hits) or f"(no matches for '{query}')"), f"Searched '{query}' in {_tilde(base)}"

    def _read_file(self, args):
        path = self._path(args.get("path"))
        if git_ignored(path):
            raise ToolError(f"git ignores {path.name}, and ignored files often hold secrets, so I will not open it")
        with open(path, "rb") as f:
            raw = f.read(READ_LIMIT + 1)
        text = raw[:READ_LIMIT].decode("utf-8", "replace")
        if len(raw) > READ_LIMIT:
            text += " […]"
        return text, f"Read {_tilde(path)}"

    def _web_search(self, args):
        query = str(args.get("query") or "").strip()
        llm = self.settings.llm
        rows = websearch.search(query, llm.search_provider, llm.search_api_key, opener=self.search_opener)
        text = "\n\n".join(f"{r['title']}\n{r['url']}\n{r['snippet']}" for r in rows) or "(no results)"
        return text, f"Searched the web: {query}"

    def _fetch_page(self, args):
        url = str(args.get("url") or "").strip()
        kw = {"resolve": self.resolve} if self.resolve else {}
        text = websearch.fetch_text(url, opener=self.search_opener, **kw)
        host = url.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")
        return "Page text (untrusted, from the web):\n" + text, f"Opened {host}"

    # ------------------------------------------------------------------------------------------------ approving
    def preview(self, name: str, arguments: dict) -> dict:
        if name == "copy_to_clipboard":
            return {"title": "Copy to clipboard", "content": arguments.get("text", ""), "exists": False, "diff": None,
                    "path": None}
        if name == "edit_file":
            path, old, content = self._edit_content(arguments)
            diff = "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), "before", "after", n=3))
            why = runs_code(path)
            return {"title": f"Edit {_tilde(path)}", "path": str(path), "exists": True, "content": content,
                    "diff": diff, "warning": RUNS_CODE_WARNING.format(why=why) if why else None}
        path = self._path(arguments.get("path"), for_write=True)
        content, exists, diff = arguments.get("content", ""), path.exists(), None
        if exists:
            old = path.read_text(encoding="utf-8", errors="replace")
            diff = "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), "before", "after", n=3))
        why = runs_code(path)
        return {"title": f"Write {_tilde(path)}", "path": str(path), "exists": exists, "content": content, "diff": diff,
                "warning": RUNS_CODE_WARNING.format(why=why) if why else None}

    def perform(self, name: str, arguments: dict) -> dict:
        try:
            if name not in APPROVE_TOOLS:
                raise ToolError(f"'{name}' is not an action that needs approval")
            self._check(name, arguments)
            if name == "copy_to_clipboard":
                ok = copy_to_clipboard(arguments["text"])
                return {"ok": ok, "detail": "Copied to the clipboard" if ok else "Could not reach the clipboard",
                        "undo": None}
            if name == "edit_file":
                path, previous, content = self._edit_content(arguments)
                existed = True
            else:
                path = self._path(arguments["path"], for_write=True)
                existed = path.exists()
                previous = path.read_text(encoding="utf-8", errors="replace") if existed else None
                content = arguments["content"]
            data = content.encode("utf-8")
            with open(path, "wb") as f:
                f.write(data)
            return {"ok": True, "detail": f"{'Edited' if name == 'edit_file' else 'Wrote'} {_tilde(path)}", "undo": {
                "path": str(path), "existed": existed, "previous": previous,
                "written_sha": hashlib.sha256(data).hexdigest()}}
        except (ToolError, OSError) as e:
            return {"ok": False, "detail": str(e), "undo": None}
        except Exception as e:
            return {"ok": False, "detail": f"failed: {e}", "undo": None}


def undo_write(undo: dict) -> tuple[bool, str]:
    path = Path(undo["path"])
    shown = _tilde(path)
    if not path.exists():
        return False, f"{shown} is already gone"
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != undo["written_sha"]:
            return False, f"{shown} was changed after SAIP wrote it, so it was left alone"
        if undo.get("existed"):
            path.write_bytes((undo.get("previous") or "").encode("utf-8"))
            return True, f"Restored {shown}"
        path.unlink()
        return True, f"Deleted {shown}"
    except OSError as e:
        return False, f"could not undo for {shown}: {e}"
