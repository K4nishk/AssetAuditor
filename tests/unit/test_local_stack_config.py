"""Static checks on the Box's local stack (AA-60, #43; ADR v1.2.0 §2-3).

Compose is read through `docker compose config` itself, so the checks see what
Compose sees, and the same call proves every profile renders with a dummy env.
The LiteLLM config is read by a narrow parser for its known shape: if the shape
changes, `_litellm_groups` finds nothing and the tests fail rather than pass.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from worker.model_target import ModelTarget

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "worker/docker-compose.yml"
LITELLM_CONFIG = ROOT / "llm/litellm.config.yaml"
IDENTITY = ROOT / "llm/local_model.toml"
EVAL_WORKFLOW = ROOT / ".github/workflows/llm-evals.yml"

DUMMY_ENV = {
    name: "dummy"
    for name in (
        "WORKER_DATABASE_URL",
        "BLOB_READ_WRITE_TOKEN",
        "LITELLM_MASTER_KEY",
        "GROQ_API_KEY",
        "GRAFANA_CLOUD_PROMETHEUS_URL",
        "GRAFANA_CLOUD_PROMETHEUS_USER",
        "GRAFANA_CLOUD_API_KEY",
    )
}
PINNED_IMAGE_RE = re.compile(r"[^\s:@]+:(?P<tag>[^\s:@]+)@sha256:[0-9a-f]{64}")
MOVING_TAGS = {"latest", "main-stable", "main", "stable", "nightly"}
UTILISATION_CEILING = 0.62


@pytest.fixture(scope="module")
def services() -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker compose is not installed here; CI runs these checks")
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "--profile", "*", "config", "--format", "json"],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp"), **DUMMY_ENV},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


@pytest.fixture(scope="module")
def identity() -> dict:
    return tomllib.loads(IDENTITY.read_text())


def _litellm_groups() -> dict[str, dict[str, str]]:
    """`model_name` -> scalar `litellm_params`, for this config's shape only."""
    text = LITELLM_CONFIG.read_text()
    body = text.split("\nmodel_list:\n", 1)[1]
    body = re.split(r"\n(?=\S)", body, maxsplit=1)[0]
    groups: dict[str, dict[str, str]] = {}
    for entry in re.split(r"\n  - ", "\n" + body)[1:]:
        lines = entry.splitlines()
        name = re.fullmatch(r"model_name: (\S+)", lines[0].strip())
        assert name is not None, f"unexpected model_list entry: {lines[0]!r}"
        params = {}
        for line in lines[1:]:
            match = re.fullmatch(r" {6}(\w+): (.*?)(?:\s+#.*)?", line)
            if match:
                params[match.group(1)] = match.group(2)
        groups[name.group(1)] = params
    assert groups, "no model_list entries parsed"
    return groups


def _local_groups() -> dict[str, dict[str, str]]:
    local = {name: p for name, p in _litellm_groups().items() if name.endswith("-local")}
    assert set(local) == {"extractor-local", "commentary-local"}
    return local


def _flag(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_every_image_is_pinned_by_tag_and_digest(services) -> None:
    for name, service in services.items():
        if "image" not in service:
            continue
        match = PINNED_IMAGE_RE.fullmatch(service["image"])
        assert match, f"{name}: image {service['image']!r} is not tag@sha256:digest"
        assert match["tag"] not in MOVING_TAGS, f"{name}: moving tag {match['tag']!r}"


def test_the_eval_workflow_runs_the_same_litellm_image(services) -> None:
    images = re.findall(r"ghcr\.io/berriai/litellm\S*", EVAL_WORKFLOW.read_text())
    assert images == [services["litellm"]["image"]]


def test_vllm_utilisation_stays_under_the_ceiling(services) -> None:
    command = services["vllm"]["command"]
    assert float(_flag(command, "--gpu-memory-utilization")) <= UTILISATION_CEILING
    assert int(_flag(command, "--kv-cache-memory-bytes")) > 0


def test_no_service_shares_the_host_ipc_namespace(services) -> None:
    for name, service in services.items():
        assert service.get("ipc") != "host", name
    assert services["vllm"].get("shm_size")


def test_vllm_serves_offline_without_prompt_logging_or_telemetry(services) -> None:
    vllm = services["vllm"]
    assert "--enable-log-requests" not in vllm["command"]
    assert vllm["environment"]["HF_HUB_OFFLINE"] == "1"
    assert vllm["environment"]["VLLM_NO_USAGE_STATS"] == "1"
    assert "ports" not in vllm


def test_only_the_seed_service_sees_the_hugging_face_token(services) -> None:
    holders = {name for name, s in services.items() if "HF_TOKEN" in (s.get("environment") or {})}
    assert holders == {"vllm-seed"}


def test_local_groups_never_retry_and_carry_no_rate_limits() -> None:
    for name, params in _local_groups().items():
        assert params.get("num_retries") == "0", name
        assert params.get("max_retries") == "0", name
        assert "rpm" not in params and "tpm" not in params, name


def test_no_fallback_names_a_local_group() -> None:
    lines = LITELLM_CONFIG.read_text().splitlines()
    for i, line in enumerate(lines):
        key = re.match(r"(\s*)\w*fallbacks:(.*)", line)
        if not key:
            continue
        block = [key.group(2)]
        for following in lines[i + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= len(key.group(1)):
                break
            block.append(following)
        assert "-local" not in "\n".join(block), line


def test_background_health_checks_are_off() -> None:
    assert re.search(r"^\s+background_health_checks: false\b", LITELLM_CONFIG.read_text(), re.M)


def test_served_model_name_matches_across_files(services, identity) -> None:
    served = identity["served_model_name"]
    assert _flag(services["vllm"]["command"], "--served-model-name") == served
    for name, params in _local_groups().items():
        assert params["model"] == f"hosted_vllm/{served}", name
        assert params["api_base"] == "http://vllm:8000/v1", name


def test_served_name_encodes_family_quantisation_and_revision(services, identity) -> None:
    revision = identity["revision"]
    pinned = re.fullmatch(r"[0-9a-f]{40}", revision) is not None
    assert pinned or revision == "unpinned"
    short = revision[:7] if pinned else revision
    quant = identity["quantization"].split("_")[0]
    assert identity["served_model_name"] == f"{identity['family']}-{quant}-{short}"
    command = services["vllm"]["command"]
    assert _flag(command, "--quantization") == identity["quantization"]
    assert ("--revision" in command) == pinned
    if pinned:
        assert _flag(command, "--revision") == revision


def test_seed_and_serve_name_the_same_repo(services, identity) -> None:
    assert services["vllm"]["command"][0] == identity["repo"]
    assert services["vllm-seed"]["environment"]["LOCAL_MODEL_REPO"] == identity["repo"]


def test_model_targets_are_exactly_the_litellm_groups() -> None:
    assert {target.value for target in ModelTarget} == set(_litellm_groups())
