#!/usr/bin/env python3
"""Grade a release-validation matrix: every (model, command) cell needs N passing trials.

usage: evals/verdict.py DIR [--trials 3] [--summary FILE] [--rows FILE]
  DIR       holds the trials' eval.json files (any depth; one per trial, from evals/run_eval.py)
  --summary markdown table (e.g. $GITHUB_STEP_SUMMARY)
  --rows    results.md rows to append
Pass rules (evals/README.md, release bar):
  port:  outcome OK, gates PASS, audit PASS
  serve: outcome UI_OK (the playground lists the model and answers) and the port behind it PASS
Exit 0 only if every expected cell has N passes.
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def passed(r: dict) -> bool:
    if r.get("cmd") == "serve":
        return r.get("outcome") == "UI_OK" and r.get("gates") == "PASS"
    return r.get("outcome") == "OK" and r.get("gates") == "PASS" and r.get("audit") == "PASS"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--models", default="", help="comma-separated models every cell must cover")
    ap.add_argument("--cmds", default="port,serve")
    ap.add_argument("--summary")
    ap.add_argument("--rows")
    a = ap.parse_args()
    cells = defaultdict(list)
    for f in sorted(Path(a.dir).rglob("eval.json")):
        r = json.loads(f.read_text())
        cells[(r["model"].split("://")[-1], r["cmd"])].append(r)
    expected = [(m, c) for m in filter(None, a.models.split(",")) for c in a.cmds.split(",")] or sorted(cells)
    lines = ["| Model | Command | Passed | Trials (outcome, gates/audit, wall min) |", "|---|---|---|---|"]
    ok = True
    for key in expected:
        rs = cells.get(key, [])
        n = sum(passed(r) for r in rs)
        good = n >= a.trials
        ok &= good
        detail = "; ".join(f"{r['outcome']} {r.get('gates')}/{r.get('audit')} {r.get('wall_min')}" for r in rs) or "no results"
        lines.append(f"| {key[0]} | {key[1]} | {'✅' if good else '❌'} {n}/{a.trials} | {detail} |")
    table = "\n".join(lines)
    print(table)
    print("VERDICT", "PASS" if ok else "FAIL")
    if a.summary:
        with open(a.summary, "a") as fh:
            fh.write(f"## Release validation: {'PASS' if ok else 'FAIL'}\n\n{table}\n")
    if a.rows:
        with open(a.rows, "w") as fh:
            for rs in cells.values():
                for r in rs:
                    fh.write(f"| {r['date']} | {r['model'].split('://')[-1]} | {r['cmd']} #{r['trial']} (CI) | "
                             f"{r['outcome']} | {r['wall_min']} | {r.get('gates')} / {r.get('audit')} | "
                             f"{r.get('tool_calls')} / {r.get('output_tokens')} | - | - | {r.get('summary') or ''} |\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
