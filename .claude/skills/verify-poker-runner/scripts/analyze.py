#!/usr/bin/env python3
"""Turn a match log into an improvement report for one bot.

    analyze.py <run_id> <match_key> <bot>        (match_key as shown in the UI URL)

Reads data/verify/<run_id>/runs/<match_key>/events.jsonl and writes
data/verify/<run_id>/analysis_<bot>_<match>.md (also printed). The report is what
an agent reads to decide how to change the bot: where the chips went, which
decisions lost them, and the bot's own printed reasoning/debug for those spots.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]


def main(run_id, key, bot):
    evid = REPO / "data" / "verify" / run_id
    events = [json.loads(l) for l in (evid / "runs" / key / "events.jsonl").read_text().splitlines() if l.strip()]
    hands = defaultdict(lambda: {"decisions": [], "board": [], "delta": 0, "showdown": None})
    acts, by_street, errors, think = Counter(), defaultdict(Counter), Counter(), []
    for e in events:
        t = e["type"]
        if t == "hand_start":
            hands[e["hand"]]["hole"] = e["hole"].get(bot)
            hands[e["hand"]]["bb"] = e["blinds"]["bb"]
        elif t == "decision" and e["bot"] == bot:
            a = e["applied"]["action"]
            acts[a] += 1
            by_street[e["street"]][a] += 1
            if e.get("error"):
                errors[e["error"]] += 1
            if e.get("think_ms") is not None:
                think.append(e["think_ms"])
            hands[e["hand"]]["decisions"].append(e)
        elif t == "showdown" and bot in e["reveal"]:
            hands[e["hand"]]["showdown"] = e["ranks"]
        elif t == "hand_end" and bot in e["deltas"]:
            hands[e["hand"]]["delta"] = e["deltas"][bot]
            hands[e["hand"]]["board"] = e["board"]
    end = next((e for e in reversed(events) if e["type"] == "match_end"), None)
    me = next((r for r in (end or {}).get("results", []) if r["bot"] == bot), None)

    out = [f"# {bot} in {key}", ""]
    if me:
        out.append(f"Result: place {me['place']}, {me['delta']:+,} chips over {me['hands_played']} hands "
                   f"({me['bb_per_100']:+.1f} bb/100).")
    out.append(f"Decisions: {sum(acts.values())} ({', '.join(f'{k} {v}' for k, v in acts.most_common())}).")
    for street in ("preflop", "flop", "turn", "river"):
        if by_street[street]:
            out.append(f"  {street}: " + ", ".join(f"{k} {v}" for k, v in by_street[street].most_common()))
    if think:
        out.append(f"Think time: avg {sum(think) / len(think):.2f} ms, max {max(think):.2f} ms.")
    out.append("Errors/timeouts: " + (", ".join(f"{k} {v}" for k, v in errors.items()) or "none") + ".")
    shown = [h for h in hands.values() if h["showdown"]]
    lost_sd = [h for h in shown if h["delta"] < 0]
    out.append(f"Showdowns: {len(shown)} ({len(lost_sd)} lost).")
    calls_lost = sum(1 for h in hands.values() if h["delta"] < 0
                     and any(d["applied"]["action"] == "call" for d in h["decisions"]))
    out.append(f"Hands lost after calling at least once: {calls_lost}.")
    out += ["", "## Biggest losing hands", ""]
    for n, h in sorted(hands.items(), key=lambda kv: kv[1]["delta"])[:5]:
        if h["delta"] >= 0:
            break
        out.append(f"### Hand {n + 1}: {h['delta']:+,} chips (bb {h.get('bb')}), "
                   f"cards {' '.join(h.get('hole') or [])}, board {' '.join(h['board']) or '-'}"
                   + (f", showdown {h['showdown']}" if h["showdown"] else ""))
        for d in h["decisions"]:
            st = d["state"]
            out.append(f"- {d['street']}: {d['applied']['action']} {d['applied']['amount'] or ''} "
                       f"(to call {st['amount_owed']}, pot {st['pot']}) debug={json.dumps(d.get('debug'))}")
            if d.get("logs"):
                out.append(f"  logs: {d['logs'].strip()[:200]}")
        out.append("")
    report = "\n".join(out)
    path = evid / f"analysis_{bot}_{key.replace('/', '_')}.md"
    path.write_text(report)
    print(report)
    print(f"\n(saved to {path})")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit("usage: analyze.py <run_id> <match_key> <bot>")
    main(*sys.argv[1:])
