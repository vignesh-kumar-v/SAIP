"""Write a generated day to disk.

Layout of <out>/:
  window.jsonl afk.jsonl input.jsonl web.jsonl terminal.jsonl fs.jsonl   ActivityWatch-shaped events
  labels.jsonl          ground-truth state segments (generator only; never a model input)
  help_requests.jsonl   when a chatbot was opened (ground truth for the help-seeking signal)
  manifest.json         seed, date, style parameters, counts
  aw-export.json        same events as importable ActivityWatch buckets (real-style ids, synthetic host)
"""
from __future__ import annotations

import json
from pathlib import Path

from .generator import BUCKETS, HOST


def _jsonl(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_day(result: dict, outdir: str | Path) -> Path:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    counts, export = {}, {}
    for key, (bid, btype, client) in BUCKETS.items():
        events = result["events"][key]
        _jsonl(out / f"{key}.jsonl", events)
        counts[key] = len(events)
        # newest first, as a real ActivityWatch export is stored
        export[bid] = dict(id=bid, created=result["start_utc"], name=None, type=btype,
                           client=client, hostname=HOST, data={}, events=events[::-1])
    _jsonl(out / "labels.jsonl", result["labels"])
    _jsonl(out / "help_requests.jsonl", result["help_requests"])
    manifest = {k: result[k] for k in
                ("seed", "user", "date", "tz_offset_h", "start_utc", "length_s", "style", "noise",
                 "generator_version")}
    manifest["counts"] = counts
    manifest["buckets"] = {k: v[0] for k, v in BUCKETS.items()}
    manifest["note"] = ("Synthetic. For pipeline development and tests only; "
                        "not valid evidence of real-world classifier accuracy.")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "aw-export.json").write_text(json.dumps({"buckets": export}, ensure_ascii=False), encoding="utf-8")
    return out
