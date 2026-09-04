# Environment gotchas (this machine)

Every entry below cost real debugging time on 2026-08-31/09-01. They are properties of
the host, not of AssetAuditor, and they will bite again on a fresh context.

## Shell & scripting

**macOS ships bash 3.2.** `"${arr[@]}"` on an **empty** array under `set -u` is a fatal
unbound-variable error. Use `${arr[@]+"${arr[@]}"}`. This crashed every agent invocation
until fixed, because "no model override" is the default path.

**`timeout` does not exist** on macOS. **`ps` and the Docker socket may be blocked** for
sandboxed tooling — use file mtimes and `nc -z` to infer process state instead.

## Python

**The active `python3` is the python.org 3.14 framework build**, whose CA bundle at
`…/etc/openssl/cert.pem` does not exist until someone runs *Install Certificates.command*.
`urllib` therefore fails with `CERTIFICATE_VERIFY_FAILED` while `curl` succeeds on the
same host — that is why Aegis's `curl`-based Linear calls worked and the first Python
seeder did not. `ops/seed_linear.py` falls back to `certifi`; never disable verification.

## PostgreSQL — the big one

Three separate failures stacked on top of each other:

1. **A root LaunchDaemon crash-loop.** `/Library/LaunchDaemons/homebrew.mxcl.postgresql@16.plist`
   (created by a `sudo brew services start`) tried to run Postgres as root, which it
   refuses — producing a 900 KB log of *"root execution of the PostgreSQL server is not
   permitted"* and a root-owned log file the user could not write, which then blocked
   `pg_ctl -l`. Remove with
   `sudo launchctl bootout system/homebrew.mxcl.postgresql@16` + `sudo rm` the plist.
2. **Two competing registrations.** A root LaunchDaemon *and* a user LaunchAgent for the
   same label make `brew services start` fail with `Bootstrap failed: 5: Input/output error`.
   Skip `brew services` entirely; use `pg_ctl` as your own user.
3. **SysV shared-memory exhaustion.** This Mac had ~8 months of uptime; macOS allocates a
   small fixed pool of shm identifiers at boot (`shmmni` default 32) and those leak. A
   56-byte `shmget` then fails with *"No space left on device"* even though `ipcs -m`
   shows zero segments. Those `kern.sysv.*` limits are largely read-only after boot, so
   **a reboot is the real fix.**

**Workaround that avoids all three:** run Postgres in Docker — containers get their own
IPC namespace.

```bash
docker run -d --name aa-postgres -e POSTGRES_HOST_AUTH_METHOD=trust \
  -e POSTGRES_USER=$(whoami) -e POSTGRES_DB=$(whoami) -p 5432:5432 postgres:16
```

**`pg_isready` with no host probes the UNIX SOCKET in `/tmp`**, which a container never
creates — it publishes TCP only. Always `pg_isready -h localhost`. The orchestrator's
preflight probes both and exports `PGHOST` when only TCP answers; without that it would
tell every agent "no PostgreSQL" while a healthy container was listening.

Postgres is **optional**: it only affects execution of DB-backed tests (AA-18, AA-21,
lineage). Those agents still write migrations, code and tests and flag them unexecuted.

## Node

Node 18. AA-1 pinned **Vite 5**, which supports it — do not "helpfully" upgrade Vite
without upgrading Node (`source ~/.nvm/nvm.sh && nvm install 22`).

## Agents have no network

Claude Code's own bash sandbox denies egress, so `uv add`, `pip install` and
`npm install` all fail inside an agent. Dependencies are **pre-provisioned**: `.venv`
from `pyproject.toml`/`uv.lock`, plus `frontend/node_modules`. Agents must use them and
name any missing package in their summary instead of working around it. Add new
dependencies yourself, between runs.

## Linear

`LINEAR_TEAM_KEY` is the **issue-id prefix** (`KCH`), not the team name (`Kchaw14`).
AssetAuditor shares Aegis's team; its issues are all titled `AA-n: …`, which is both how
re-seeding detects duplicates and how you filter the shared board.

The API key was never stored anywhere — it lived only in shell history. It now belongs
in `ops/.env.local` (gitignored).

## CodeRabbit CLI

- `coderabbit pullrequest <n>` **requires `--show-prompts`**; `--agent` alone exits with
  a usage error that looks like a review.
- A real finding is `{"type":"finding","severity":"major","fileName":…,"codegenInstructions":…}`
  — there is **no** `message`/`title`/`description` field. A parser keying on those
  matches nothing and silently falls back to grepping for the word "major".
- Plain-text lines are appended after the NDJSON (`Error: Rate limit exceeded`), so
  filter to lines starting `{` before `jq`, and always slurp (`jq -s`).
- Free tier = 8 reviews per replenishing window; `{"errorType":"rate_limit"}` is
  recoverable, not fatal.
- **Only PRs whose base is in `.coderabbit.yaml`'s `base_branches` get auto-reviewed.**
  The default is the repo's default branch only, so `development` and `feature/.*` must
  both be listed, and the config must exist on the **default branch** (`main`).

## macOS TCC and launchd

**A LaunchAgent's child shell cannot read `~/Documents`.** This is the single most
expensive landmine in the build: `com.assetauditor.review-sweeper` fired hourly for a
full day and never once worked, exiting 1 with

```
shell-init: getcwd: cannot access parent directories: Operation not permitted
/bin/bash: .../ops/.env.local: Operation not permitted
```

The tell is the asymmetry: **launchd itself writes the agent's log into `~/Documents`
fine**, because launchd is privileged — but the `bash` it spawns is not. So the error
file grows while nothing runs, and `launchctl print` shows a perfectly healthy agent
(`state = not running`, which means idle, not broken). Check `runs` and
`last exit code` instead.

Not a permissions problem in the unix sense: `ls -l` shows the file readable, and
`~/Documents` shows `drwx------+` — the `+` is the TCC ACL.

Two fixes. **Move the repo out of `~/Documents`** (TCC does not guard arbitrary home
paths) — clean, but every plist hardcodes the path. Or **grant Full Disk Access to
`/bin/bash`** in System Settings → Privacy & Security — fast, but it grants FDA to every
bash script on the machine. Neither has been done; the sweeper is hand-run instead.

Anything run from your own terminal is unaffected — Terminal has TCC access.

## launchd

Prefer `StartCalendarInterval` over `StartInterval`: it fires on **wake** and coalesces
missed slots; `StartInterval` drops them. launchd's PATH is minimal and does not inherit
an interactive shell, so both agents run `/bin/bash -lc` and source `ops/.env.local`.
`Bootstrap failed: 5` means the label is already registered — `bootout` first.
