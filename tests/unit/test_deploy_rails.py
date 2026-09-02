"""Structural checks for the deploy rails (KCH-39 / AA-4).

Text/JSON-structure assertions, not a real Vercel/Docker deployment — this
sandbox has no cloud credentials or Docker daemon (see CLAUDE.md). Mirrors
tests/unit/test_ci_workflow.py's approach: lock in the shape of the config so
a later edit can't silently drop a required piece.
"""

import json
import re
from pathlib import Path

VERCEL_CONFIG_PATH = Path("vercel.json")
ENV_EXAMPLE_PATH = Path(".env.example")
DOCKER_COMPOSE_PATH = Path("worker/docker-compose.yml")
BRING_UP_PATH = Path("worker/bring-up.md")


def test_vercel_config_is_valid_json():
    config = json.loads(VERCEL_CONFIG_PATH.read_text())
    assert "rewrites" in config


def test_vercel_config_builds_the_frontend():
    config = json.loads(VERCEL_CONFIG_PATH.read_text())
    assert "frontend" in config["buildCommand"]
    assert config["outputDirectory"] == "frontend/dist"


def test_vercel_config_routes_api_before_the_spa_catch_all():
    config = json.loads(VERCEL_CONFIG_PATH.read_text())
    sources = [rule["source"] for rule in config["rewrites"]]
    assert sources.index("/api/:path*") < sources.index("/:path*")
    api_rule = next(r for r in config["rewrites"] if r["source"] == "/api/:path*")
    assert api_rule["destination"] == "/api/index"


def test_env_example_exists_and_documents_names_only():
    text = ENV_EXAMPLE_PATH.read_text()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        # "documents names only" (Security-Model.md) — every var line is
        # `NAME=` or `NAME=placeholder-that-is-not-a-secret`, never a real
        # credential-shaped value.
        assert "=" in line
        name, _, value = line.partition("=")
        assert name.isupper()
        assert "://" not in value


def test_env_example_documents_the_worker_service_role_boundary():
    text = ENV_EXAMPLE_PATH.read_text()
    assert "WORKER_DATABASE_URL" in text
    assert "DATABASE_URL" in text
    assert "never be exposed to the frontend" in text


def test_env_example_documents_the_ci_branch_db_secrets():
    # Names must match .github/workflows/ci.yml's migration-dry-run job exactly.
    text = ENV_EXAMPLE_PATH.read_text()
    assert "SUPABASE_ACCESS_TOKEN" in text
    assert "SUPABASE_PROJECT_ID" in text
    assert "SUPABASE_DB_PASSWORD" in text


def test_docker_compose_defines_worker_and_litellm_unconditionally():
    text = DOCKER_COMPOSE_PATH.read_text()
    assert "worker:" in text
    assert "litellm:" in text


def test_docker_compose_gates_vllm_behind_the_gpu_profile():
    text = DOCKER_COMPOSE_PATH.read_text()
    assert "vllm:" in text
    assert 'profiles: ["gpu"]' in text


def test_docker_compose_worker_has_no_published_ports():
    # ADR v1.1.0 §2: outbound-only, no inbound ports, nothing exposed. Isolate
    # just the top-level `worker:` service block (2-space indent) so nested
    # keys under other services don't leak into the match.
    text = DOCKER_COMPOSE_PATH.read_text()
    match = re.search(r"\n  worker:\n(.*?)(?=\n  \S)", text, re.DOTALL)
    assert match, "could not isolate the worker service block"
    assert "ports:" not in match.group(1)


def test_bring_up_documents_env_setup_and_heartbeat_check():
    text = BRING_UP_PATH.read_text()
    assert "docker compose up" in text
    assert "--profile gpu" in text
    assert "worker_heartbeat" in text
    assert "WORKER_DATABASE_URL" in text
