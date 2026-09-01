---
tags: [research, ops]
verified: 2026-09-01
---

# Scheduling the review sweeper — tool survey

Requirement: run a job **hourly on a personal Mac that sleeps overnight**, with retries, no server, no daemon to babysit, no cost ([[../Assumptions|A16]]).

| Option | Verdict | Why |
|---|---|---|
| **launchd user agent** | **leverage** | Native, no install, survives reboot. Decisive: `StartCalendarInterval` **fires on wake** and coalesces intervals missed while asleep |
| cron | **skip** | Deprecated on macOS since 2005. Jobs scheduled while asleep never run. Under TCC it fails *silently* when touching `~/Documents` — where this repo lives |
| launchd `StartInterval` | **skip** | Missed intervals are dropped (kqueue limitation) — the sleeping-laptop case is exactly ours |
| [Cronicle](https://github.com/jhuckaby/Cronicle) | **skip** | Web UI + REST API, but a background service to run and secure for one hourly job |
| [Dkron](https://dkron.io/) | **skip** | Distributed scheduler with retries; cluster machinery for one laptop |
| [Kestra](https://kestra.io/) | **skip** | Full orchestration platform (JVM + DB); wrong order of magnitude |
| Celery / RQ | **skip** | Needs Redis — another always-on dependency the zero-cost contract forbids |
| GitHub Actions `schedule:` | **borrow-idea** | Free on public repos, but the job needs the local worktree, local `.venv` and an authenticated CodeRabbit CLI. Revisit if the build leaves this machine |

## Decision
**launchd user agents**: the review sweeper hourly at :07, the build scheduler every 2 hours at :22 (off-cycle so they never collide). `ProcessType=Background` + `LowPriorityIO` + `Nice 5` keep them out of a foreground build's way.

Mirrors the [[Lineage-OpenLineage|lineage decision]]: keep the spec-shaped behaviour (scheduled retries with state) via a small local ledger plus the platform's own scheduler, rather than standing up a service.

## Gotcha
launchd's environment is minimal and does **not** inherit an interactive shell's PATH, so `claude`, `gh`, `coderabbit` and `uv` are invisible unless the job runs a login shell. Both agents invoke `/bin/bash -lc` and source `ops/.env.local`.

Sources: [Apple — Scheduling Timed Jobs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html) · [launchd rather than cron](https://www.jeremycherfas.net/blog/scheduled-jobs-with-launchd-rather-than-cron) · [Mac crontab: when to switch](https://www.runxbuild.com/blog/mac-crontab/) · [Open-source job schedulers 2026](https://kestra.io/resources/infrastructure/open-source-job-scheduler)
