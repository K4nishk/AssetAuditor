#!/usr/bin/env python3
"""Check ops/queue.template.tsv against mvp.md's declared dependencies.

A queue whose order violates a dependency wastes a whole night: an agent builds
against interfaces that do not exist yet, CodeRabbit flags the mess, and the
stacked PRs above it inherit it. Run this after editing the template.

    python3 ops/validate_queue.py        # exits non-zero on any problem
"""

from __future__ import annotations

import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

OPS = Path(__file__).resolve().parent
seed = SourceFileLoader("seed", str(OPS / "seed_linear.py")).load_module()

issues = {i["aa"]: i for i in seed.parse_mvp()}
template = OPS / "queue.template.tsv"

order: list[tuple[str, str]] = []
seen: set[str] = set()
problems: list[str] = []

for lineno, raw in enumerate(template.read_text().splitlines(), 1):
    line = raw.rstrip()
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    parts = (line.split("\t") + ["no"])[:3]
    tier, aa, _parallel = parts
    if aa not in issues:
        problems.append(f"line {lineno}: {aa} is queued but does not exist in mvp.md")
        continue
    if aa in seen:
        problems.append(f"line {lineno}: {aa} appears twice in the queue")
    for dep in issues[aa]["deps"]:
        if dep not in seen:
            problems.append(f"line {lineno}: {aa} (tier {tier}) depends on {dep}, which has not run yet")
    seen.add(aa)
    order.append((tier, aa))

excluded = sorted(set(issues) - seen, key=lambda a: int(a.split("-")[1]))

print(f"{len(order)} issues queued across tiers {sorted({t for t, _ in order}, key=int)}")
print(f"excluded from the queue: {', '.join(excluded) if excluded else 'none'}")
for aa in excluded:
    print(f"  - {aa}: {issues[aa]['title']}")

if problems:
    print("\nPROBLEMS:")
    for p in problems:
        print(f"  ! {p}")
    sys.exit(1)

print("\nDependency order: OK")
