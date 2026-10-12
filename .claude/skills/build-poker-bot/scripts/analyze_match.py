#!/usr/bin/env python3
"""Improvement report for one bot in one match.

    analyze_match.py <match_dir | match_key> <bot> [--server URL]

<match_dir> is the run_dir printed by `poker match` (or a match folder);
<match_key> is the id in the watch URL (#/match/<key>), looked up in the
server's runs dir. Prints a Markdown report: result, actions by street,
errors/timeouts, think time, showdowns, and the biggest losing hands with the
bot's own logs and debug for each decision.
"""
import argparse
import json
import os
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path


def find_match(ref, server):
    p = Path(ref).expanduser()
    if (p / "events.jsonl").is_file():
        return p
    runs = json.load(urllib.request.urlopen(server.rstrip("/") + "/api/health"))["runs_dir"]
    p = Path(runs) / ref
    if (p / "events.jsonl").is_file():
        return p
    raise SystemExit(f"no match log at {ref} or {p}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("match")
    ap.add_argument("bot")
    ap.add_argument("--server", default=os.environ.get("POKER_SERVER", "http://127.0.0.1:8765"))
    a = ap.parse_args()
    d = find_match(a.match, a.server)
    events = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines() if l.strip()]
    bot = a.bot
    hands = defaultdict(lambda: {"decisions": [], "board": [], "delta": 0, "showdown": None})
    acts, by_street, errors, think = Counter(), defaultdict(Counter), Counter(), []
    for e in events:
        t = e["type"]
        if t == "hand_start":
            hands[e["hand"]].update(hole=e["hole"].get(bot), bb=e["blinds"]["bb"])
        elif t == "decision" and e["bot"] == bot:
            act = e["applied"]["action"]
            acts[act] += 1
            by_street[e["street"]][act] += 1
            if e.get("error"):
                errors[e["error"]] += 1
            if e.get("think_ms") is not None:
                think.append(e["think_ms"])
            hands[e["hand"]]["decisions"].append(e)
        elif t == "showdown" and bot in e["reveal"]:
            hands[e["hand"]]["showdown"] = e["ranks"].get(bot)
        elif t == "hand_end" and bot in e["deltas"]:
            hands[e["hand"]].update(delta=e["deltas"][bot], board=e["board"])
    end = next((e for e in reversed(events) if e["type"] == "match_end"), None)
    me = next((r for r in (end or {}).get("results", []) if r["bot"] == bot), None)
    if not acts:
        raise SystemExit(f"{bot} made no decisions in {d} (bot names: "
                         f"{sorted({e['bot'] for e in events if e['type'] == 'decision'})})")

    out = [f"# {bot} in {d.name}", ""]
    if me:
        out.append(f"Result: place {me['place']}, {me['delta']:+,} chips over {me['hands_played']} hands "
                   f"({me['bb_per_100']:+.1f} bb/100).")
    out.append(f"Decisions: {sum(acts.values())} ({', '.join(f'{k} {v}' for k, v in acts.most_common())}).")
    for street in ("preflop", "flop", "turn", "river"):
        if by_street[street]:
            out.append(f"  {street}: " + ", ".join(f"{k} {v}" for k, v in by_street[street].most_common()))
    if think:
        srt = sorted(think)
        out.append(f"Think time: avg {sum(think) / len(think):.2f} ms, p95 {srt[int(0.95 * (len(srt) - 1))]:.2f} ms, "
                   f"max {srt[-1]:.2f} ms (limit 2000 ms).")
    out.append("Errors/timeouts: " + (", ".join(f"{k} {v}" for k, v in errors.items()) or "none") + ".")
    shown = [h for h in hands.values() if h["showdown"]]
    out.append(f"Showdowns: {len(shown)} ({sum(1 for h in shown if h['delta'] < 0)} lost).")
    vpip = sum(1 for h in hands.values() if any(x["street"] == "preflop" and x["applied"]["action"] in
                                                ("call", "raise", "all_in") for x in h["decisions"]))
    out.append(f"VPIP: {vpip}/{len(hands)} hands ({100 * vpip / max(1, len(hands)):.0f}%).")
    out += ["", "## Biggest losing hands", ""]
    for n, h in sorted(hands.items(), key=lambda kv: kv[1]["delta"])[:5]:
        if h["delta"] >= 0:
            break
        out.append(f"### Hand {n + 1}: {h['delta']:+,} (bb {h.get('bb')}), cards {' '.join(h.get('hole') or [])}, "
                   f"board {' '.join(h['board']) or '-'}" + (f", showdown {h['showdown']}" if h["showdown"] else ""))
        for x in h["decisions"]:
            st = x["state"]
            out.append(f"- {x['street']}: {x['applied']['action']} {x['applied']['amount'] or ''} "
                       f"(to call {st['amount_owed']}, pot {st['pot']}) debug={json.dumps(x.get('debug'))}")
            if x.get("logs"):
                out.append(f"  logs: {x['logs'].strip()[:200]}")
        out.append("")
    print("\n".join(out))


if __name__ == "__main__":
    main()
