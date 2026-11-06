"""The shell snippets that call the emitter, and the marked block that installs and removes them.

The block is delimited by marker lines, so install is idempotent and uninstall removes exactly what
was added and nothing else. Supported shells: zsh, bash, PowerShell (fish is not supported yet).
"""
from __future__ import annotations

from pathlib import Path

BEGIN = "# >>> saip hook >>>"
END = "# <<< saip hook <<<"
NOTE = "# Reports each command's exit status (never its text) to the local ActivityWatcher. Remove: saip uninstall"

ZSH = """zmodload zsh/datetime 2>/dev/null
__saip_preexec() {{ __saip_cmd="${{1%% *}}"; __saip_start=$EPOCHREALTIME; }}
__saip_precmd() {{
  local rc=$?
  if [[ -n "$__saip_cmd" ]]; then
    "{py}" -m saip.hooks command --exit-code "$rc" --program "$__saip_cmd" --start "$__saip_start" >/dev/null 2>&1 &!
  fi
  __saip_cmd=
}}
autoload -Uz add-zsh-hook
add-zsh-hook preexec __saip_preexec
add-zsh-hook precmd __saip_precmd"""

BASH = """__saip_debug() {{
  if [[ -z "$__saip_cmd" && "$BASH_COMMAND" != __saip_* && "$BASH_COMMAND" != "$PROMPT_COMMAND" ]]; then
    __saip_cmd="${{BASH_COMMAND%% *}}"; __saip_start="${{EPOCHREALTIME:-$(date +%s)}}"
  fi
}}
__saip_prompt() {{
  local rc=$?
  if [[ -n "$__saip_cmd" ]]; then
    ( "{py}" -m saip.hooks command --exit-code "$rc" --program "$__saip_cmd" --start "$__saip_start" >/dev/null 2>&1 & )
  fi
  __saip_cmd=
  return $rc
}}
[[ -z "$(trap -p DEBUG)" ]] && trap '__saip_debug' DEBUG
PROMPT_COMMAND="__saip_prompt${{PROMPT_COMMAND:+;$PROMPT_COMMAND}}\""""

POWERSHELL = """$global:__saip_last = $null
$global:__saip_orig_prompt = $function:prompt
function global:prompt {{
  $ok = $?; $code = $LASTEXITCODE
  $h = Get-History -Count 1
  if ($h -and $h.Id -ne $global:__saip_last) {{
    $global:__saip_last = $h.Id
    $prog = ($h.CommandLine -split '\\s+')[0]
    $dur = ($h.EndExecutionTime - $h.StartExecutionTime).TotalSeconds
    $rc = if ($ok) {{ 0 }} elseif ($code) {{ $code }} else {{ 1 }}
    Start-Process -WindowStyle Hidden "{py}" -ArgumentList "-m","saip.hooks","command","--exit-code",$rc,"--program",$prog,"--duration",$dur
  }}
  & $global:__saip_orig_prompt
}}"""

SNIPPETS = {"zsh": ZSH, "bash": BASH, "powershell": POWERSHELL}


def snippet(shell: str, python: str) -> str:
    if shell not in SNIPPETS:
        raise ValueError(f"no hook for shell '{shell}' (supported: {', '.join(SNIPPETS)})")
    body = SNIPPETS[shell].format(py=python)
    return f"{BEGIN}\n{NOTE}\n{body}\n{END}\n"


def has_block(text: str) -> bool:
    return BEGIN in text and END in text


def strip_block(text: str) -> str:
    """Remove the marked block (and the blank line we added before it), leaving everything else untouched."""
    while has_block(text):
        a = text.index(BEGIN)
        b = text.index(END, a) + len(END)
        before, after = text[:a].rstrip("\n"), text[b:]
        after = after[1:] if after.startswith("\n") else after
        text = (before + "\n" + after) if before else after
    return text


def install_block(profile: Path, shell: str, python: str) -> str:
    """Add or refresh the block. Returns 'added', 'updated' or 'unchanged'."""
    profile.parent.mkdir(parents=True, exist_ok=True)
    old = profile.read_text() if profile.exists() else ""
    block = snippet(shell, python)
    base = strip_block(old)
    new = (base + ("\n" if base and not base.endswith("\n\n") else "") + block) if base else block
    if new == old:
        return "unchanged"
    profile.write_text(new)
    return "updated" if has_block(old) else "added"


def remove_block(profile: Path) -> bool:
    if not profile.exists():
        return False
    text = profile.read_text()
    if not has_block(text):
        return False
    profile.write_text(strip_block(text))
    return True
