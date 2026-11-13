"""Checks on what the chat model says, for a small local model that can be wrong or talked round.

Two things are looked for in a reply, and shown to the person as warnings beside it (the reply itself is not changed):

* Risky commands: code in the reply (a fenced block or `inline code`) that deletes for good, runs a script straight
  from the internet, runs as administrator, rewrites git history, erases a disk or switches off a macOS protection.
  Prose is not checked, so "never run rm -rf" in words is left alone.
* Claims that did not happen: "I've saved the file", "I copied it to your clipboard", "I ran the tests". SAIP's model
  can only propose a file write or a clipboard copy, which happen once the person approves; it can never run
  commands, commit, push, delete or install. A claim is compared with the actions of the same reply.

And one thing is looked for in what tools return (files, diffs, the screen, web pages): signs of text written to
steer an AI rather than inform a person ("ignore previous instructions", "do not tell the user", a fake system
message, a tool named in an order). Such text is labelled for the model and shown to the person.

All matching runs on `sanitize.for_matching`, so hidden characters and lookalike letters do not get past it.
"""
from __future__ import annotations

import re

from .sanitize import for_matching

# (pattern, why it is risky). Matched against each line of code in a reply.
RISKY = [
    (re.compile(r"\brm\s+(?:-\w+\s+)*-\w*[rRf]\w*\b"), "deletes files for good: there is no bin and no undo"),
    (re.compile(r"\b(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b"),
     "runs a script from the internet without showing it to you first"),
    (re.compile(r"\b(?:ba|z)?sh\s+(?:-c\s+)?[\"']?(?:<\(|\$\()\s*(?:curl|wget)\b"),
     "runs a script from the internet without showing it to you first"),
    (re.compile(r"\bsudo\b"), "runs as administrator, with full access to your computer"),
    (re.compile(r"\bchmod\s+(?:-\w+\s+)*0?777\b"), "lets every user and program change these files"),
    (re.compile(r"\bgit\s+push\b[^\n]*\s(?:--force(?:-with-lease)?|-f)\b"),
     "overwrites history on the remote, so other people can lose work"),
    (re.compile(r"\bgit\s+reset\s+(?:\S+\s+)*--hard\b"), "throws away uncommitted changes for good"),
    (re.compile(r"\bgit\s+clean\s+(?:-\w+\s+)*-\w*f"), "deletes untracked files for good"),
    (re.compile(r"\bgit\s+(?:checkout\s+--|restore)\s+\.(?:\s|$)"), "throws away uncommitted changes for good"),
    (re.compile(r"\bdd\s+[^\n]*\bof=/dev/"), "writes straight onto a disk"),
    (re.compile(r"\b(?:mkfs(?:\.\w+)?|diskutil\s+(?:erase\w*|zeroDisk|partitionDisk))\b"), "erases a disk"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "starts processes until the computer stops responding"),
    (re.compile(r"(?i)\b(?:drop\s+(?:table|database|schema)|truncate\s+table)\b"), "deletes database data for good"),
    (re.compile(r"(?i)\bdelete\s+from\s+[\w.\"`]+\s*(?:;|$)"), "deletes every row of the table"),
    (re.compile(r"\b(?:csrutil\s+disable|spctl\s+--(?:master|global)-disable)\b"),
     "switches off a macOS security protection"),
    (re.compile(r"(?<![>&\d])>\s*~?/?(?:[\w.-]*/)*\.(?:zshrc|bashrc|bash_profile|profile|zprofile)\b"),
     "replaces your shell start-up file instead of adding to it"),
]

_FENCE = re.compile(r"```[^\n]*\n(.*?)(?:```|\Z)", re.S)
_INLINE = re.compile(r"`([^`\n]+)`")


def code_lines(text: str) -> list[str]:
    """The lines of code in a Markdown reply: fenced blocks first, then inline code."""
    blocks = _FENCE.findall(text)
    rest = _FENCE.sub(" ", text)
    lines = [line for b in blocks for line in b.splitlines()]
    return [ln.strip() for ln in lines + _INLINE.findall(rest) if ln.strip()]


def risky_commands(text: str) -> list[dict]:
    """[{"command", "why"}] for each line of code that matches, in order, without repeats."""
    out, seen = [], set()
    for line in code_lines(for_matching(text or "")):
        for pat, why in RISKY:
            if pat.search(line):
                command = line.removeprefix("$ ")[:160]
                if command not in seen:
                    seen.add(command)
                    out.append({"command": command, "why": why})
                break
    return out


# ---- claims -------------------------------------------------------------------------------------------------------
_CLAIM = re.compile(
    r"(?i)\bI(?:\s+have|['’]ve)?\s+(?:now\s+|just\s+|already\s+|also\s+|successfully\s+)?"
    r"(?P<verb>written|wrote|saved|created|updated|edited|modified|changed|copied|put|placed|ran|run|executed"
    r"|committed|pushed|deleted|removed|installed|restarted)\b(?P<rest>(?:[^.!?\n]|[.](?=\w))*)")   # notes.md stays whole
_FILEISH = re.compile(r"(?i)\bfiles?\b|\b[\w-]+\.[a-z]{1,5}\b|/")
_COMMANDISH = re.compile(r"(?i)\b(?:commands?|scripts?|tests?|builds?|install|migrations?)\b|`")
WRITE_VERBS = {"written", "wrote", "saved", "created", "updated", "edited", "modified", "changed"}
COPY_VERBS = {"copied", "put", "placed"}
NEVER_VERBS = {"ran", "run", "executed", "committed", "pushed", "deleted", "removed", "installed", "restarted"}

CANNOT = ("SAIP cannot run commands, commit, push, delete or install anything, so this did not happen. "
          "Do it yourself if you want it.")
NOT_YET = "Not done yet: nothing happens until you approve the action."
NO_WRITE = "SAIP did not write any file in this reply. Check before relying on it."
NO_COPY = "Nothing was copied to your clipboard in this reply."


def unbacked_claim(text: str, approved: set[str], pending: set[str]) -> str | None:
    """A warning when the reply says it did something its actions do not show, else None.
    `approved` and `pending` are the action names ("write_file", "copy_to_clipboard") of the same reply."""
    for m in _CLAIM.finditer(text or ""):
        verb, rest = m["verb"].lower(), m["rest"]
        if verb in NEVER_VERBS:
            if verb in ("ran", "run", "executed") and not _COMMANDISH.search(rest):
                continue
            if verb in ("deleted", "removed") and not _FILEISH.search(rest):
                continue
            return CANNOT
        if verb in COPY_VERBS and "clipboard" in rest.lower():
            if "copy_to_clipboard" in approved:
                continue
            return NOT_YET if "copy_to_clipboard" in pending else NO_COPY
        if verb in WRITE_VERBS and _FILEISH.search(rest):
            if "write_file" in approved:
                continue
            return NOT_YET if "write_file" in pending else NO_WRITE
    return None


# ---- text aimed at the model ---------------------------------------------------------------------------------------
_AI = r"(?:ai|a\.i\.|assistant|chatbot|bot|llm|language model|model|agent|claude|chatgpt|gpt|qwen|llama|copilot)"
_INJECTION = [
    (re.compile(r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+|any\s+|the\s+|your\s+|these\s+|those\s+)*"
                r"(?:previous|prior|above|earlier|preceding|original|system|initial)\s+(?:instructions?|prompts?|messages?|"
                r"rules|directions|guidelines|context)"), "tells the model to ignore its instructions"),
    (re.compile(r"\b(?:do\s+not|don'?t|never)\s+(?:tell|inform|mention\s+(?:this\s+)?to|alert|reveal\s+(?:this\s+)?to|"
                r"notify)\s+(?:the\s+)?(?:user|person|human|operator)\b"), "tells the model to keep something from you"),
    (re.compile(r"(?:<\|im_start\|>|<\|start_header_id\|>|\[/?inst\]|<<sys>>|</?system>|^\s*#{1,4}\s*system\b|"
                r"^\s*(?:system|assistant)\s*(?:prompt|message|instructions?)?\s*:)"), "imitates a system or assistant message"),
    (re.compile(rf"\b(?:if\s+you\s+are|you\s+are\s+now)\s+(?:an?\s+)?(?:[a-z-]+\s+){{0,3}}{_AI}\b"), "addresses an AI directly"),
    (re.compile(r"\bas\s+an?\s+(?:ai|llm|large\s+language\s+model|language\s+model|ai\s+assistant)\b"), "addresses an AI directly"),
    (re.compile(rf"\b(?:attention|note|important|message)\s*(?:to|for)\s+(?:the\s+|any\s+)?{_AI}\b"), "addresses an AI directly"),
    (re.compile(r"\b(?:new|updated|real|actual)\s+(?:system\s+)?instructions?\s*:"), "claims to give the model new instructions"),
    (re.compile(r"(?:^|[.!?:;]\s*|\b(?:please|now|must|should|immediately)\s+)"        # said as an order, not "you can call"
                r"(?:call|use|run|invoke|execute)\s+(?:the\s+)?`?(?:write_file|copy_to_clipboard|fetch_page|web_search|read_file)"
                r"`?(?:\s+tool)?\b"), "tells the model to use one of its tools"),
]


INJECTION = [(re.compile(p.pattern, re.M), why) for p, why in _INJECTION]       # ^ marks a line start


def injection_signs(text: str) -> list[str]:
    """The reasons text from a tool looks written to steer the model, without repeats ([] for ordinary text)."""
    plain = for_matching(text or "").lower()
    found = []
    for pat, why in INJECTION:
        if why not in found and pat.search(plain):
            found.append(why)
    return found


__all__ = ["INJECTION", "RISKY", "code_lines", "injection_signs", "risky_commands", "unbacked_claim"]
