"""What the chat model may do for you, and the guard rails around each thing.

Reading tools (the screen, the activity summary, recent commits, files in your watched folders, the web) run at once.
Writing tools (the clipboard, saving a file) only ever return a preview here; `perform` runs after you approve, and
`undo_write` puts a file back if it has not changed since. Files are reached only inside the watched folders, never
through a symlink that leaves them, never inside `.git`, and only plain-text kinds. A tool's failure is plain words
given to the model and to you, never an exception.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import subprocess
from pathlib import Path

from . import repos as repos_mod
from . import websearch
from .screen import copy_to_clipboard

TEXT_EXTENSIONS = frozenset({".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml",
                             ".html", ".css", ".csv", ".tex", ".sh", ".c", ".cpp", ".h", ".java", ".go", ".rs"})
READ_LIMIT, WRITE_LIMIT, OUTPUT_LIMIT = 60_000, 200_000, 12_000
READ_TOOLS = ("read_screen", "activity_summary", "git_log", "git_diff", "read_file", "web_search", "fetch_page")
APPROVE_TOOLS = ("copy_to_clipboard", "write_file")


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
    if not any(full.is_relative_to(r) for r in resolved_roots):
        raise ToolError(f"{path} is outside the watched folders")
    if ".git" in full.parts:
        raise ToolError("files inside .git are off limits")
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
    "git_log": ("List recent commits (one line each) of a watched git repository.",
                {"repo": {"type": "string", "description": "repository folder; omit for the first watched one"},
                 "limit": {"type": "integer", "description": "how many commits, at most 20"}}, []),
    "git_diff": ("Show the uncommitted changes (diffstat, then the diff) of a watched git repository.",
                 {"repo": {"type": "string", "description": "repository folder; omit for the first watched one"}}, []),
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
                names += ["git_log", "git_diff", "read_file", "write_file"]
            names.append("copy_to_clipboard")
            if llm.web_search:
                names += ["web_search", "fetch_page"]
        self.names = names

    @property
    def roots(self) -> list[str]:
        return list(self.settings.agent.watch_roots)

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
            return {"kind": "read", "result": _cap(result), "label": label}
        except (ToolError, websearch.SearchError) as e:
            return {"kind": "error", "result": str(e), "label": f"Could not use {name}: {e}"[:120]}
        except Exception as e:                                  # a tool must never take the chat down
            return {"kind": "error", "result": f"{name} failed: {e}", "label": f"Could not use {name}"}

    def _check(self, name: str, args: dict) -> None:
        if name == "write_file":
            safe_path(args.get("path"), self.roots, for_write=True)
            content = args.get("content")
            if not isinstance(content, str):
                raise ToolError("no file content was given")
            if len(content) > WRITE_LIMIT:
                raise ToolError(f"that file is longer than the {WRITE_LIMIT:,} characters I may write")
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
        if not given:
            found = repos_mod.discover(self.settings.agent)
            if not found:
                raise ToolError("no git repository is being watched")
            return found[0]
        repo = Path(str(given)).expanduser()
        if not repo.is_absolute():
            repo = next((r / repo for r in _roots(self.roots) if (r / repo).is_dir()), _roots(self.roots)[0] / repo)
        repo = repo.resolve()
        if not any(repo.is_relative_to(r) for r in _roots(self.roots)):
            raise ToolError(f"{given} is outside the watched folders")
        if not (repo / ".git").exists():
            raise ToolError(f"{_tilde(repo)} is not a git repository")
        return repo

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

    def _read_file(self, args):
        path = safe_path(args.get("path"), self.roots)
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
        path = safe_path(arguments.get("path"), self.roots, for_write=True)
        content, exists, diff = arguments.get("content", ""), path.exists(), None
        if exists:
            old = path.read_text(encoding="utf-8", errors="replace")
            diff = "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), "before", "after", n=3))
        return {"title": f"Write {_tilde(path)}", "path": str(path), "exists": exists, "content": content, "diff": diff}

    def perform(self, name: str, arguments: dict) -> dict:
        try:
            if name not in APPROVE_TOOLS:
                raise ToolError(f"'{name}' is not an action that needs approval")
            self._check(name, arguments)
            if name == "copy_to_clipboard":
                ok = copy_to_clipboard(arguments["text"])
                return {"ok": ok, "detail": "Copied to the clipboard" if ok else "Could not reach the clipboard",
                        "undo": None}
            path = safe_path(arguments["path"], self.roots, for_write=True)
            existed = path.exists()
            previous = path.read_text(encoding="utf-8", errors="replace") if existed else None
            data = arguments["content"].encode("utf-8")
            with open(path, "wb") as f:
                f.write(data)
            return {"ok": True, "detail": f"Wrote {_tilde(path)}", "undo": {
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
