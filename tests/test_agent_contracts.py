from __future__ import annotations

from pathlib import Path

import yaml
import pytest

from app.answer_validation import ANSWER_VALIDATOR_VERSION
from app.calculations import CALCULATOR_VERSION
from app.config import Settings
from app.planning import PLANNER_VERSION, QUERY_ANCHOR_VERSION
from app.product_identity import PRODUCT_IDENTITY_RESOLVER_VERSION
from app.retrieval import RETRIEVER_VERSION
from app.routing import ROUTER_VERSION
from app.semantic_coverage import SEMANTIC_COVERAGE_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "agents" / "contracts" / "tool-registry.yaml"
SKILL_ROOTS = (
    PROJECT_ROOT / "agents" / "skills",
    PROJECT_ROOT / "agents" / "workbench" / "skills",
)


def _registry() -> dict[str, object]:
    payload = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_tool_registry_uses_orchestrator_control_and_matches_time_budgets() -> None:
    registry = _registry()
    assert registry["schema_version"] == 1
    assert registry["policy"] == "orchestrator_controlled"
    assert registry["model_direct_tool_calls"] is False

    budgets = registry["budgets"]
    assert isinstance(budgets, dict)
    settings = Settings()
    assert budgets["request_total_seconds"] == settings.request_timeout_seconds
    assert budgets["hcx_upstream_seconds"] == settings.upstream_timeout_seconds
    assert budgets["max_hcx_calls"] == 1


def test_tool_registry_has_unique_tools_and_explicit_implementation_status() -> None:
    registry = _registry()
    tools = registry["tools"]
    assert isinstance(tools, list)
    assert tools

    ids: list[str] = []
    for tool in tools:
        assert isinstance(tool, dict)
        assert tool["status"] in {"implemented", "planned"}
        assert tool["executor"] in {
            "deterministic",
            "deterministic_preferred",
            "hyperclova_x",
        }
        assert tool["side_effects"] in {"none", "read_only", "network_hcx_only"}
        assert isinstance(tool["implementation"], str)
        assert str(tool["implementation"]).startswith("app.")
        ids.append(str(tool["id"]))

    assert len(ids) == len(set(ids))
    assert {
        "classify_question",
        "plan_question",
        "resolve_product_identity",
        "retrieve_general_evidence",
        "retrieve_product_evidence",
        "calculate_pension_tax_credit",
        "assess_suitability",
        "assess_evidence",
        "compare_product_facts",
        "generate_answer",
    }.issubset(set(ids))

    statuses = {str(tool["id"]): tool["status"] for tool in tools}
    assert statuses["plan_question"] == "implemented"
    assert statuses["assess_evidence"] == "implemented"
    assert statuses["resolve_product_identity"] == "implemented"
    assert statuses["compare_product_facts"] == "implemented"


def test_tool_registry_versions_match_runtime_components() -> None:
    tools = _registry()["tools"]
    assert isinstance(tools, list)
    by_id = {str(tool["id"]): tool for tool in tools}

    assert by_id["classify_question"]["version"] == ROUTER_VERSION
    assert by_id["plan_question"]["version"] == PLANNER_VERSION
    assert by_id["plan_question"]["query_anchor_version"] == QUERY_ANCHOR_VERSION
    assert by_id["resolve_product_identity"]["version"] == PRODUCT_IDENTITY_RESOLVER_VERSION
    assert by_id["retrieve_general_evidence"]["version"] == RETRIEVER_VERSION
    assert by_id["validate_answer"]["version"] == ANSWER_VALIDATOR_VERSION
    assert (
        by_id["validate_answer"]["semantic_coverage_version"]
        == SEMANTIC_COVERAGE_VERSION
    )
    assert by_id["calculate_pension_tax_credit"]["version"] == CALCULATOR_VERSION


def test_forbidden_tools_cannot_be_registered_as_agent_tools() -> None:
    registry = _registry()
    tools = registry["tools"]
    forbidden = registry["forbidden_tools"]
    assert isinstance(tools, list)
    assert isinstance(forbidden, list)
    assert set(forbidden).isdisjoint({str(tool["id"]) for tool in tools})
    assert "non_hyperclova_llm" in forbidden
    assert "financial_transaction" in forbidden
    assert "shell" in forbidden


def test_workbench_is_not_part_of_runtime_docker_image() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY agents ./agents" in dockerfile
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "agents/workbench" in dockerignore.splitlines()


def test_repository_has_one_agent_charter() -> None:
    if not (PROJECT_ROOT / "AGENTS.md").exists():
        pytest.skip("Internal authoring instructions are excluded from the shared repository")
    charters = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in PROJECT_ROOT.rglob("AGENT*.md")
        if ".venv" not in path.parts and ".git" not in path.parts
    )
    assert charters == ["AGENTS.md"]


def test_every_skill_has_valid_frontmatter_and_ui_metadata() -> None:
    if not all(root.is_dir() for root in SKILL_ROOTS):
        pytest.skip("Internal authoring skills are excluded from the shared repository")
    skill_dirs = sorted(
        path
        for root in SKILL_ROOTS
        for path in root.iterdir()
        if path.is_dir()
    )
    assert skill_dirs

    for skill_dir in skill_dirs:
        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert skill_text.startswith("---\n")
        _, frontmatter_text, _ = skill_text.split("---", maxsplit=2)
        frontmatter = yaml.safe_load(frontmatter_text)
        assert set(frontmatter) == {"name", "description"}
        assert frontmatter["name"] == skill_dir.name
        assert isinstance(frontmatter["description"], str)
        assert frontmatter["description"].strip()

        metadata = yaml.safe_load(
            (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )
        interface = metadata["interface"]
        assert isinstance(interface["display_name"], str)
        assert 25 <= len(interface["short_description"]) <= 64
        assert f"${skill_dir.name}" in interface["default_prompt"]
