#!/usr/bin/env python3
"""Seed AssetAuditor's mvp.md issues into Linear, then generate ops/queue.tsv.

mvp.md stays the human-authored source of truth for *what* each issue is; Linear
becomes the audited record of *state* (In Progress / In Review) and the home for
CodeRabbit escalations the overnight agents could not service.

Idempotent: an issue whose title already starts with "AA-n:" in the target team is
left alone and reused, so re-running after a partial seed is safe.

Usage:
    export LINEAR_API_KEY=lin_api_...
    export LINEAR_TEAM_KEY=ASA
    python3 ops/seed_linear.py            # dry run: parse + show what would be created
    python3 ops/seed_linear.py --apply    # create issues, write .issue_map.tsv + queue.tsv
"""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

OPS = Path(__file__).resolve().parent
REPO = OPS.parent
MVP = REPO / "mvp.md"
TEMPLATE = OPS / "queue.template.tsv"
QUEUE = OPS / "queue.tsv"
ISSUE_MAP = OPS / ".issue_map.tsv"
API = "https://api.linear.app/graphql"

ISSUE_RE = re.compile(r"^- \*\*(AA-\d+)\s+(.+?)\*\*\s+—\s+(.*)$")
MILESTONE_RE = re.compile(r"^## (M\d+)\s+—\s+(.+)$")
WHY_RE = re.compile(r"^\*Why:\s*(.+?)\*$")
DEPS_RE = re.compile(r"deps:\s*([^.]*)", re.IGNORECASE)


def _ssl_context() -> ssl.SSLContext:
    """A verifying TLS context that still works on stock python.org builds.

    Those installers ship an empty `etc/openssl/cert.pem` until someone runs
    "Install Certificates.command", so `urllib` fails with CERTIFICATE_VERIFY_FAILED
    while `curl` (system keychain) succeeds on the same machine. Fall back to
    certifi's bundle when the interpreter's own store is empty. Verification is
    never disabled — an API key travels in these request headers.
    """
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs():
        try:
            import certifi

            ctx.load_verify_locations(certifi.where())
        except ImportError:
            sys.exit(
                "This Python has no root certificates and certifi is not installed.\n"
                "Fix either way:\n"
                '  /Applications/Python\\ 3.14/Install\\ Certificates.command   # system-wide\n'
                "  python3 -m pip install certifi                              # this script only"
            )
    return ctx


def gql(query: str, variables: dict | None = None) -> dict:
    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        sys.exit("LINEAR_API_KEY is not set.")
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API, data=body, headers={"Authorization": key, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:  # surface Linear's own error text
        hint = "  (401/403 usually means the key is wrong or revoked)" if exc.code in (401, 403) else ""
        sys.exit(f"Linear API HTTP {exc.code}: {exc.read().decode()[:500]}{hint}")
    except urllib.error.URLError as exc:  # no network / DNS / proxy refusal
        sys.exit(f"Could not reach {API}: {exc.reason}\n"
                 "Check your connection — and note this cannot run inside a sandboxed agent shell.")
    if "errors" in payload:
        sys.exit(f"Linear API error: {json.dumps(payload['errors'])[:500]}")
    return payload["data"]


def parse_mvp() -> list[dict]:
    """Extract every AA-n issue with its milestone context and declared deps."""
    issues: list[dict] = []
    milestone = why = ""
    for line in MVP.read_text().splitlines():
        if m := MILESTONE_RE.match(line):
            milestone, why = f"{m.group(1)} — {m.group(2)}", ""
            continue
        if m := WHY_RE.match(line):
            why = m.group(1)
            continue
        if m := ISSUE_RE.match(line):
            aa, title, body = m.group(1), m.group(2), m.group(3)
            deps = []
            if d := DEPS_RE.search(body):
                deps = re.findall(r"AA-\d+", d.group(1))
            issues.append(
                {"aa": aa, "title": title, "body": body, "milestone": milestone, "why": why, "deps": deps}
            )
    return issues


def description_for(issue: dict) -> str:
    deps = ", ".join(issue["deps"]) if issue["deps"] else "none"
    return f"""{issue['body']}

**Milestone:** {issue['milestone']}
_Why this milestone:_ {issue['why']}

**Dependencies:** {deps}

---
Spec source: `mvp.md` ({issue['aa']}) — mvp.md is authoritative; this issue mirrors it for state tracking.
Architecture of record: `docs/adr/ADR_v1.1.0.md`. Binding conventions: `CLAUDE.md`.
Seeded by `ops/seed_linear.py`."""


def main() -> None:
    apply = "--apply" in sys.argv
    team_key = os.environ.get("LINEAR_TEAM_KEY", "")
    issues = parse_mvp()
    print(f"Parsed {len(issues)} issues from mvp.md")
    if not issues:
        sys.exit("Parsed nothing — has mvp.md's issue format changed?")

    if not apply:
        for i in issues:
            print(f"  {i['aa']:6} {i['title'][:60]:60} deps={','.join(i['deps']) or '-'}")
        print("\nDry run. Re-run with --apply to create these in Linear.")
        return

    if not team_key:
        teams = gql("{ teams { nodes { key name } } }")["teams"]["nodes"]
        sys.exit("LINEAR_TEAM_KEY is not set. Available teams: "
                 + ", ".join(f"{t['key']} ({t['name']})" for t in teams))

    data = gql('{ teams(filter: { key: { eq: "%s" } }) { nodes { id key name } } }' % team_key)
    nodes = data["teams"]["nodes"]
    if not nodes:
        # LINEAR_TEAM_KEY is the short KEY that prefixes issue ids (the "KCH" in
        # KCH-9), not the team's display name — an easy and unhelpful thing to get
        # wrong, so show what actually exists rather than just refusing.
        available = gql("{ teams { nodes { key name } } }")["teams"]["nodes"]
        listing = "\n".join(f"  LINEAR_TEAM_KEY={t['key']:<8} # {t['name']}" for t in available)
        sys.exit(
            f"No Linear team with key {team_key!r}.\n"
            "LINEAR_TEAM_KEY must be the team KEY that prefixes issue ids (e.g. KCH in KCH-9),\n"
            f"not the team name. Your teams:\n{listing}"
        )
    team_id, team_name = nodes[0]["id"], nodes[0]["name"]
    print(f"Target team: {team_key} ({team_name})")

    # Existing issues, so a re-run reuses rather than duplicates.
    existing: dict[str, str] = {}
    cursor, page = None, 0
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        res = gql('{ issues(filter: { team: { key: { eq: "%s" } } }, first: 100%s) '
                  "{ nodes { id identifier title } pageInfo { hasNextPage endCursor } } }"
                  % (team_key, after))["issues"]
        for n in res["nodes"]:
            if m := re.match(r"^(AA-\d+):", n["title"]):
                existing[m.group(1)] = n["identifier"]
        if not res["pageInfo"]["hasNextPage"] or page > 20:
            break
        cursor, page = res["pageInfo"]["endCursor"], page + 1

    mapping: dict[str, str] = {}
    for issue in issues:
        aa = issue["aa"]
        if aa in existing:
            print(f"  = {aa} already seeded as {existing[aa]}")
            mapping[aa] = existing[aa]
            continue
        created = gql(
            "mutation($t: String!, $d: String!, $team: String!) { "
            "issueCreate(input: { teamId: $team, title: $t, description: $d }) "
            "{ success issue { identifier url } } }",
            {"t": f"{aa}: {issue['title']}", "d": description_for(issue), "team": team_id},
        )["issueCreate"]
        ident = created["issue"]["identifier"]
        mapping[aa] = ident
        print(f"  + {aa} -> {ident}  {created['issue']['url']}")

    ISSUE_MAP.write_text("".join(f"{aa}\t{lin}\n" for aa, lin in sorted(
        mapping.items(), key=lambda kv: int(kv[0].split("-")[1]))))
    print(f"\nWrote {ISSUE_MAP.relative_to(REPO)} ({len(mapping)} entries)")

    # Translate the tier template (AA-n) into the runnable queue (Linear ids).
    if not TEMPLATE.exists():
        sys.exit(f"Missing {TEMPLATE}; cannot generate queue.tsv")
    out, missing = [], []
    for line in TEMPLATE.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            out.append(line)
            continue
        tier, aa, parallel = (line.split("\t") + ["no"])[:3]
        if aa not in mapping:
            missing.append(aa)
            out.append(f"# UNSEEDED {aa} — not created in Linear, skipped")
            continue
        out.append(f"{tier}\t{mapping[aa]}\t{parallel}")
    QUEUE.write_text("\n".join(out) + "\n")
    print(f"Wrote {QUEUE.relative_to(REPO)}")
    if missing:
        print(f"WARNING: {len(missing)} queue entries had no Linear issue: {', '.join(missing)}")

    gate = mapping.get("AA-15")
    if gate:
        print(f"\nKill gate is AA-15 -> {gate}. Export it before the run:\n  export KILL_GATE_ISSUE={gate}")


if __name__ == "__main__":
    main()
