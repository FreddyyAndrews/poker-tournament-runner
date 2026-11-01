"""`poker export`: zip a match, run or series.

With --bot NAME the export is made for one participant: it keeps everything
public (actions, boards, showdowns, results) plus that bot's own private data
(its hole cards, what it was shown, its logs, debug output and errors), and
removes every other bot's private data:

  - other bots' hole cards, unless they were shown down in that hand
  - other bots' decision details (state seen, raw reply, logs, debug, errors)
  - other bots' paths, warmup/load logs and diagnostics
"""

import json
import zipfile
from pathlib import Path

from arena.events import EVENTS_FILE, EVENTS_GZ, SUMMARY_FILE, iter_lines

_PRIVATE_DECISION_FIELDS = ("state", "reply", "logs", "logs_truncated", "debug", "debug_error",
                            "error", "error_detail", "think_ms")


def _filter_events(lines, bot):
    events = [json.loads(l) for l in lines if l.strip()]
    shown = {}
    for e in events:
        if e["type"] == "showdown":
            key = (e.get("table"), e["hand"])
            shown.setdefault(key, set()).update(e["reveal"])
    out = []
    for e in events:
        t = e["type"]
        if t == "hand_start":
            visible = shown.get((e.get("table"), e["hand"]), set()) | {bot}
            e = {**e, "hole": {n: c for n, c in e["hole"].items() if n in visible}}
        elif t == "decision" and e["bot"] != bot:
            e = {k: v for k, v in e.items() if k not in _PRIVATE_DECISION_FIELDS}
        elif t == "match_start":
            e = {**e, "seats": [s if s["name"] == bot else
                                {k: v for k, v in s.items() if k in ("name", "kind", "seat",
                                                                     "time_limit_ms", "hash")}
                                for s in e["seats"]]}
        out.append(json.dumps(e))
    return "\n".join(out) + "\n"


def _filter_summary(text, bot):
    s = json.loads(text)
    if "bot_diagnostics" in s:
        s["bot_diagnostics"] = {k: v for k, v in s["bot_diagnostics"].items() if k == bot}
    if "seats" in s:
        s["seats"] = [x if x.get("name") == bot else
                      {k: v for k, v in x.items() if k in ("name", "kind", "time_limit_ms", "hash")}
                      for x in s["seats"]]
    return json.dumps(s, indent=2)


def export(run_dir, out_path, bot: str = None) -> dict:
    run_dir, out_path = Path(run_dir), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    files = 0
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(run_dir.rglob("*")):
            if not f.is_file() or f.name.endswith(".tmp"):
                continue
            arc = str(Path(run_dir.name) / f.relative_to(run_dir))
            if f.name == EVENTS_GZ:
                # exports always hold the plain log (the zip compresses it anyway)
                arc = arc[:-len(".gz")]
                lines = list(iter_lines(f.parent))
                zf.writestr(arc, _filter_events(lines, bot) if bot else
                            "".join(line + "\n" for line in lines))
            elif bot and f.name == EVENTS_FILE:
                zf.writestr(arc, _filter_events(f.read_text().splitlines(), bot))
            elif bot and f.name == SUMMARY_FILE:
                zf.writestr(arc, _filter_summary(f.read_text(), bot))
            else:
                zf.write(f, arc)
            files += 1
    return {"ok": True, "path": str(out_path), "files": files, "bot": bot}
