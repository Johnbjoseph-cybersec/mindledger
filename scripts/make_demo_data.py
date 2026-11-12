#!/usr/bin/env python3
"""Generate SYNTHETIC Claude Code-style transcripts for demos and screenshots.

Deterministic (fixed seed). Every project, prompt and number here is invented.

    python3 scripts/make_demo_data.py /tmp/mindledger-demo --days 120
    python3 -m mindledger report --projects /tmp/mindledger-demo --out demo.html
"""
import argparse
import datetime
import json
import os
import random

PROJECTS = ["/work/demo-api", "/work/notes-site", "/work/data-pipeline", "/work/mobile-app", "/work/infra-scripts"]
MODELS = [("claude-sonnet-4-6", 0.55), ("claude-opus-4-8", 0.35), ("claude-haiku-4-5", 0.10)]
PROMPTS = ["Add pagination to the list endpoint", "Write tests for the date parser",
           "Refactor the config loader", "Fix the flaky upload test", "Document the deploy steps",
           "Speed up the nightly export job", "Add dark mode to the settings page"]


def pick_model(rng):
    r, acc = rng.random(), 0.0
    for m, w in MODELS:
        acc += w
        if r <= acc:
            return m
    return MODELS[0][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    end = datetime.date.fromisoformat(a.end)
    n = 0
    for back in range(a.days - 1, -1, -1):
        day = end - datetime.timedelta(days=back)
        weekend = day.weekday() >= 5
        if rng.random() < (0.45 if weekend else 0.08):
            continue  # some days off
        growth = 0.6 + 0.8 * (a.days - back) / a.days
        for s in range(rng.randint(1, 3 if weekend else 6)):
            n += 1
            sid = "demo-%05d" % n
            cwd = rng.choice(PROJECTS)
            folder = os.path.join(a.out, cwd.replace("/", "-"))
            os.makedirs(folder, exist_ok=True)
            t = datetime.datetime.combine(day, datetime.time(8 + rng.randint(0, 10), rng.randint(0, 59)),
                                          tzinfo=datetime.timezone.utc)
            rows = [{"type": "user", "uuid": sid + "-u0", "sessionId": sid, "cwd": cwd,
                     "timestamp": t.isoformat().replace("+00:00", "Z"),
                     "message": {"role": "user", "content": rng.choice(PROMPTS)}}]
            ctx = rng.randint(15000, 40000)
            for turn in range(int(rng.randint(8, 60) * growth)):
                t += datetime.timedelta(seconds=rng.randint(5, 90))
                ctx += rng.randint(500, 4000)
                created = rng.randint(200, 6000)
                rows.append({"type": "assistant", "uuid": "%s-a%d" % (sid, turn), "sessionId": sid, "cwd": cwd,
                             "timestamp": t.isoformat().replace("+00:00", "Z"),
                             "message": {"id": "msg_%s_%d" % (sid, turn), "model": pick_model(rng), "role": "assistant",
                                         "content": [{"type": "text", "text": "Step %d done." % turn}],
                                         "usage": {"input_tokens": rng.randint(2, 40),
                                                   "output_tokens": rng.randint(80, 2500),
                                                   "cache_read_input_tokens": ctx,
                                                   "cache_creation_input_tokens": created}}})
                rows.append({"type": "user", "uuid": "%s-r%d" % (sid, turn), "sessionId": sid, "cwd": cwd,
                             "timestamp": t.isoformat().replace("+00:00", "Z"),
                             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x",
                                                                      "content": "ok"}]}})
            with open(os.path.join(folder, sid + ".jsonl"), "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
    print("wrote %d synthetic sessions to %s" % (n, a.out))


if __name__ == "__main__":
    main()
