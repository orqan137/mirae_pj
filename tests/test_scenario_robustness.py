from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.evaluate_scenario_robustness import (
    CANONICAL_DEVELOPMENT_CASES,
    DEFAULT_CASES,
    EVALUATOR_VERSION,
    ScenarioObservation,
    ScenarioPack,
    _term_present,
    compute_pack_fingerprint,
    evaluate_pack,
    load_pack,
    render_report,
    summarize_results,
    unsafe_generation_reasons,
    validate_execution_isolation,
    validate_split_isolation,
)
from scripts.run_benchmark_suite import (
    DEFAULT_SUITE,
    RUNNER_VERSION,
    SCENARIO_STAGE_CASES,
    _stage_command,
    _summarize_output,
    load_suite,
)


def _write_pack(tmp_path: Path, payload: dict[str, object]) -> Path:
    payload["pack_fingerprint"] = compute_pack_fingerprint(payload)
    path = tmp_path / "scenario-pack.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _payload() -> dict[str, object]:
    return json.loads(DEFAULT_CASES.read_text(encoding="utf-8"))


def _canonical_development_packs() -> tuple[ScenarioPack, ...]:
    return tuple(load_pack(path) for path in CANONICAL_DEVELOPMENT_CASES)


def test_declared_query_terms_tolerate_spacing_but_not_synonyms() -> None:
    assert _term_present("퇴직연금규약", "퇴직연금 규약 변경")
    assert _term_present("개인회생절차개시결정", "개인회생 절차 개시 결정")
    assert not _term_present("근로자대표", "직원 대표")


def test_development_pack_has_35_explicit_non_holdout_cases() -> None:
    pack = load_pack(DEFAULT_CASES)

    assert pack.split == "development"
    assert pack.panel == "development"
    assert len(pack.cases) == 35
    assert sum(case.test_type == "MFT" for case in pack.cases) == 24
    assert sum(case.test_type == "INV" for case in pack.cases) == 7
    assert sum(case.test_type == "DIR" for case in pack.cases) == 4
    assert all(case.provenance.review_status == "development_only" for case in pack.cases)
    assert sum(
        bool(case.qrels)
        and all(qrel.granularity == "locator_chunk" for qrel in case.qrels)
        for case in pack.cases
    ) == 7


def test_development_expansion_pack_remains_schema_compatible() -> None:
    expansion = load_pack(DEFAULT_CASES.with_name("development-expansion-v1.json"))

    assert expansion.split == "development"
    assert expansion.panel == "development"
    assert len(expansion.cases) == 29
    assert all(
        case.provenance.review_status == "development_only_after_same_system_review"
        for case in expansion.cases
    )


def test_development_pack_passes_without_hcx() -> None:
    pack = load_pack(DEFAULT_CASES)
    results = evaluate_pack(pack, top_k=6)
    report = render_report(pack, results)

    assert report["hcx_calls"] == 0
    assert report["case_count"] == 35
    assert report["failed"] == 0, {
        result["case_id"]: result["failures"]
        for result in results
        if not result["passed"]
    }
    assert report["unsafe_generation_count"] == 0
    assert report["family_all_critical_pass_rate"] == 1.0
    assert report["worst_axis_pass_rate"] == 1.0


def test_pack_fingerprint_is_canonical_and_tamper_evident() -> None:
    payload = _payload()
    assert payload["pack_fingerprint"] == compute_pack_fingerprint(payload)

    reordered = dict(reversed(list(payload.items())))
    assert compute_pack_fingerprint(reordered) == payload["pack_fingerprint"]
    reordered["pack_id"] = "changed"
    assert compute_pack_fingerprint(reordered) != payload["pack_fingerprint"]


def test_loader_rejects_corpus_manifest_mismatch(tmp_path: Path) -> None:
    payload = _payload()
    payload["corpus_manifest_sha256"] = "0" * 64
    path = _write_pack(tmp_path, payload)

    with pytest.raises(ValueError, match="corpus manifest fingerprint mismatch"):
        load_pack(path)


def test_loader_rejects_locator_qrel_content_mismatch(tmp_path: Path) -> None:
    payload = _payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    exact = next(
        case
        for case in cases
        if case["case_id"] == "STRESS-CONTRIBUTION-DELAY-002"
    )
    exact["qrels"][0]["content_sha256"] = "0" * 64
    path = _write_pack(tmp_path, payload)

    with pytest.raises(ValueError, match="content_sha256 mismatches the corpus chunk"):
        load_pack(path)


def test_evaluator_rechecks_runtime_manifest_binding() -> None:
    pack = load_pack(DEFAULT_CASES)
    mismatched = replace(pack, corpus_manifest_sha256="0" * 64)

    with pytest.raises(ValueError, match="evaluation corpus manifest fingerprint mismatch"):
        evaluate_pack(mismatched, top_k=6)


def test_evaluator_rechecks_runtime_locator_qrel_content() -> None:
    pack = load_pack(DEFAULT_CASES)
    target = next(
        case
        for case in pack.cases
        if any(qrel.granularity == "locator_chunk" for qrel in case.qrels)
    )
    qrels = tuple(
        replace(qrel, content_sha256="0" * 64)
        if qrel.granularity == "locator_chunk"
        else qrel
        for qrel in target.qrels
    )
    changed_case = replace(target, qrels=qrels)
    changed_pack = replace(
        pack,
        cases=tuple(
            changed_case if case.case_id == target.case_id else case
            for case in pack.cases
        ),
    )

    with pytest.raises(ValueError, match=r"runtime qrel\[0\] content mismatch"):
        evaluate_pack(changed_pack, top_k=6)


def test_source_qrels_cannot_claim_locator_proof(tmp_path: Path) -> None:
    payload = _payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    legacy = next(case for case in cases if case["case_id"] == "LIFE-CONTRIBUTION-ISA-001")
    legacy["qrels"][0]["locator"] = "page=1"
    path = _write_pack(tmp_path, payload)

    with pytest.raises(ValueError, match="must not claim locator/content proof"):
        load_pack(path)


def test_loader_rejects_required_forbidden_overlap(tmp_path: Path) -> None:
    payload = _payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    cases[0]["forbidden_route_labels"] = [cases[0]["required_route_labels"][0]]
    path = _write_pack(tmp_path, payload)

    with pytest.raises(ValueError, match="required/forbidden route labels overlap"):
        load_pack(path)


def test_loader_rejects_missing_invariance_parent(tmp_path: Path) -> None:
    payload = _payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    case = next(case for case in cases if case["test_type"] == "INV")
    case["parent_case_id"] = "ABSENT"
    path = _write_pack(tmp_path, payload)

    with pytest.raises(ValueError, match="parent is absent"):
        load_pack(path)


def test_loader_rejects_ambiguous_identifiers(tmp_path: Path) -> None:
    payload = _payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    cases[0]["semantic_family_id"] = f" {cases[0]['semantic_family_id']}"
    path = _write_pack(tmp_path, payload)

    with pytest.raises(ValueError, match="leading or trailing whitespace"):
        load_pack(path)


def test_loader_rejects_normalized_duplicate_case_ids(tmp_path: Path) -> None:
    payload = _payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    cases[1]["case_id"] = cases[0]["case_id"].lower()
    path = _write_pack(tmp_path, payload)

    with pytest.raises(ValueError, match="normalized duplicate case_id"):
        load_pack(path)


def test_loader_rejects_inconsistent_normalized_family_spelling(tmp_path: Path) -> None:
    payload = _payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    child = next(
        case for case in cases if case["case_id"] == "STRESS-TRANSFER-REDEMPTION-002"
    )
    child["semantic_family_id"] = child["semantic_family_id"].upper()
    path = _write_pack(tmp_path, payload)

    with pytest.raises(ValueError, match="inconsistent normalized semantic_family_id"):
        load_pack(path)


def test_loader_requires_mft_relation_parent(tmp_path: Path) -> None:
    payload = _payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    child = next(
        case for case in cases if case["case_id"] == "STRESS-NEG-NO-TRANSFER-002"
    )
    child["parent_case_id"] = "STRESS-TRANSFER-REDEMPTION-002"
    path = _write_pack(tmp_path, payload)

    with pytest.raises(ValueError, match="parent must be an MFT case"):
        load_pack(path)


def test_split_lint_rejects_normalized_duplicate_questions() -> None:
    development = load_pack(DEFAULT_CASES)
    base = development.cases[0]
    validation_case = replace(
        base,
        case_id="VALIDATION-DUPLICATE",
        semantic_family_id="new-semantic-family",
        template_family_id="new-template-family",
    )
    validation = ScenarioPack(
        path=Path("validation.json"),
        pack_id="validation",
        split="validation",
        panel="L",
        pack_fingerprint="1" * 64,
        corpus_manifest_sha256=development.corpus_manifest_sha256,
        cases=(validation_case,),
    )

    with pytest.raises(ValueError, match="normalized question leaks"):
        validate_split_isolation((development, validation))


def test_split_lint_rejects_normalized_case_id_reuse() -> None:
    development = load_pack(DEFAULT_CASES)
    base = development.cases[0]
    validation_case = replace(
        base,
        case_id=base.case_id.lower(),
        question="case ID 재사용과 무관한 검증 질문",
        semantic_family_id="new-case-id-semantic-family",
        template_family_id="new-case-id-template-family",
    )
    validation = ScenarioPack(
        path=Path("validation-case-id.json"),
        pack_id="validation-case-id",
        split="validation",
        panel="L",
        pack_fingerprint="1" * 64,
        corpus_manifest_sha256=development.corpus_manifest_sha256,
        cases=(validation_case,),
    )

    with pytest.raises(ValueError, match="normalized case_id is reused"):
        validate_split_isolation((development, validation))


def test_split_lint_rejects_semantic_family_leakage() -> None:
    development = load_pack(DEFAULT_CASES)
    base = development.cases[0]
    validation_case = replace(
        base,
        case_id="VALIDATION-FAMILY",
        question="완전히 다른 검증 질문",
        template_family_id="new-template-family",
    )
    validation = ScenarioPack(
        path=Path("validation.json"),
        pack_id="validation",
        split="validation",
        panel="L",
        pack_fingerprint="1" * 64,
        corpus_manifest_sha256=development.corpus_manifest_sha256,
        cases=(validation_case,),
    )

    with pytest.raises(ValueError, match="semantic families leak"):
        validate_split_isolation((development, validation))


def test_split_lint_normalizes_family_identifiers() -> None:
    development = load_pack(DEFAULT_CASES)
    base = development.cases[0]
    validation_case = replace(
        base,
        case_id="VALIDATION-NORMALIZED-FAMILY",
        question="대소문자만 바꾼 family 격리 검증 질문",
        semantic_family_id=base.semantic_family_id.upper(),
        template_family_id="validation-normalized-template",
    )
    validation = ScenarioPack(
        path=Path("validation-normalized.json"),
        pack_id="validation-normalized",
        split="validation",
        panel="L",
        pack_fingerprint="1" * 64,
        corpus_manifest_sha256=development.corpus_manifest_sha256,
        cases=(validation_case,),
    )

    with pytest.raises(ValueError, match="semantic families leak"):
        validate_split_isolation((development, validation))


def test_declared_panel_l_rejects_template_leakage() -> None:
    development = load_pack(DEFAULT_CASES)
    base = development.cases[0]
    validation_case = replace(
        base,
        case_id="VALIDATION-TEMPLATE",
        question="독립 표면형 검증 질문",
        semantic_family_id="new-semantic-family",
    )
    validation = ScenarioPack(
        path=Path("validation.json"),
        pack_id="validation",
        split="validation",
        panel="L",
        pack_fingerprint="1" * 64,
        corpus_manifest_sha256=development.corpus_manifest_sha256,
        cases=(validation_case,),
    )

    with pytest.raises(ValueError, match="panel L template families leak"):
        validate_split_isolation((development, validation))


def test_declared_panel_s_rejects_source_lineage_leakage() -> None:
    development = load_pack(DEFAULT_CASES)
    base = development.cases[0]
    sealed_case = replace(
        base,
        case_id="SEALED-LINEAGE",
        question="봉인된 의미 및 문장형 검증 질문",
        semantic_family_id="sealed-semantic-family",
        template_family_id="sealed-template-family",
    )
    sealed = ScenarioPack(
        path=Path("sealed.json"),
        pack_id="sealed",
        split="sealed",
        panel="S",
        pack_fingerprint="1" * 64,
        corpus_manifest_sha256=development.corpus_manifest_sha256,
        cases=(sealed_case,),
    )

    with pytest.raises(ValueError, match="panel S source lineages leak"):
        validate_split_isolation((development, sealed))


def test_protected_execution_requires_all_development_and_validation_for_sealed() -> None:
    development_packs = _canonical_development_packs()
    development = development_packs[0]
    base = development.cases[0]
    validation_case = replace(
        base,
        case_id="VALIDATION-ISOLATED-001",
        question="독립 validation 격리 확인 질문",
        semantic_family_id="validation_isolated_family",
        template_family_id="validation_isolated_template",
        source_lineage_family="validation_isolated_lineage",
    )
    validation = ScenarioPack(
        path=Path("validation-isolated.json"),
        pack_id="validation-isolated",
        split="validation",
        panel="L",
        pack_fingerprint="1" * 64,
        corpus_manifest_sha256=development.corpus_manifest_sha256,
        cases=(validation_case,),
    )
    with pytest.raises(ValueError, match="requires all canonical development packs"):
        validate_execution_isolation(
            validation,
            development_packs=development_packs[:-1],
        )
    validate_execution_isolation(
        validation,
        development_packs=development_packs,
    )

    sealed_case = replace(
        base,
        case_id="SEALED-ISOLATED-001",
        question="독립 sealed 격리 확인 질문",
        semantic_family_id="sealed_isolated_family",
        template_family_id="sealed_isolated_template",
        source_lineage_family="sealed_isolated_lineage",
    )
    sealed = ScenarioPack(
        path=Path("sealed-isolated.json"),
        pack_id="sealed-isolated",
        split="sealed",
        panel="S",
        pack_fingerprint="2" * 64,
        corpus_manifest_sha256=development.corpus_manifest_sha256,
        cases=(sealed_case,),
    )

    with pytest.raises(ValueError, match="requires a separate validation"):
        validate_execution_isolation(
            sealed,
            development_packs=development_packs,
        )
    with pytest.raises(ValueError, match="requires a separate validation"):
        evaluate_pack(sealed, top_k=6)
    validate_execution_isolation(
        sealed,
        development_packs=development_packs,
        isolation_packs=(validation,),
    )


@pytest.mark.parametrize("development_index", (1, 2))
def test_protected_execution_rejects_expansion_and_safety_leakage(
    development_index: int,
) -> None:
    development_packs = _canonical_development_packs()
    source = next(
        case for case in development_packs[development_index].cases if case.test_type == "MFT"
    )
    leaked_case = replace(
        source,
        case_id=f"VALIDATION-CANONICAL-LEAK-{development_index}",
        question=f"canonical 개발 팩 {development_index} 격리 누출 검증 질문",
        template_family_id=f"validation-canonical-template-{development_index}",
        source_lineage_family=f"validation-canonical-lineage-{development_index}",
    )
    validation = ScenarioPack(
        path=Path(f"validation-canonical-leak-{development_index}.json"),
        pack_id=f"validation-canonical-leak-{development_index}",
        split="validation",
        panel="L",
        pack_fingerprint="3" * 64,
        corpus_manifest_sha256=development_packs[0].corpus_manifest_sha256,
        cases=(leaked_case,),
    )

    with pytest.raises(ValueError, match="semantic families leak"):
        evaluate_pack(validation, top_k=6)


def test_evaluator_version_tracks_complete_canonical_isolation() -> None:
    assert EVALUATOR_VERSION == "pension-scenario-robustness-eval-v3"


def test_protected_report_is_aggregate_only_and_redacted() -> None:
    development = load_pack(DEFAULT_CASES)
    results = evaluate_pack(development, top_k=6)
    protected = replace(development, split="sealed", panel="L", pack_id="sealed-private")

    with pytest.raises(ValueError, match="aggregate-only"):
        render_report(protected, results, report_level="detailed")

    report = render_report(protected, results, report_level="aggregate")
    rendered = json.dumps(report, ensure_ascii=False)
    assert "results" not in report
    assert "pack_id" not in report
    assert "per_axis" not in report
    assert "per_family" not in report
    assert "qrel_granularity_case_counts" not in report
    assert "anchor_control_rejection_rate" not in report
    assert "threshold_failures" not in report
    assert development.cases[0].case_id not in rendered
    assert development.cases[0].question not in rendered
    assert development.cases[0].qrels[0].source_id not in rendered


def test_protected_cli_redacts_schema_failure_details(tmp_path: Path) -> None:
    payload = _payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    secret_case = deepcopy(cases[0])
    secret_case.update(
        {
            "case_id": "SECRET-SEALED-CASE",
            "semantic_family_id": "secret_sealed_family",
            "template_family_id": "secret_sealed_template",
            "source_lineage_family": "secret_sealed_lineage",
            "question": "SECRET-SEALED-QUESTION",
            "test_type": "INV",
            "parent_case_id": "SECRET-MISSING-PARENT",
            "relation": "semantic_invariant",
        }
    )
    payload.update(
        {
            "pack_id": "secret-sealed-pack",
            "split": "sealed",
            "panel": "S",
            "cases": [secret_case],
        }
    )
    path = _write_pack(tmp_path, payload)
    project_root = DEFAULT_CASES.parents[2]

    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "evaluate_scenario_robustness.py"),
            "--cases",
            str(path),
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": "protected_scenario_evaluation_failed",
        "split": "sealed",
    }
    assert "SECRET" not in completed.stderr
    assert "secret_sealed" not in completed.stderr


def test_summary_exposes_worst_group_and_unsafe_count() -> None:
    pack = load_pack(DEFAULT_CASES)
    results = list(evaluate_pack(pack, top_k=6))
    baseline_unsafe = int(summarize_results(pack, tuple(results))["unsafe_generation_count"])
    changed = deepcopy(results[0])
    changed["passed"] = False
    changed["unsafe_reasons"] = ["qrel_alignment"]
    results[0] = changed

    summary = summarize_results(pack, tuple(results))
    assert summary["unsafe_generation_count"] == baseline_unsafe + 1
    assert summary["worst_axis_pass_rate"] < 1.0
    assert summary["worst_family_pass_rate"] == 0.0


def test_unsafe_generation_uses_only_explicit_false_or_qrel_mismatch() -> None:
    pack = load_pack(DEFAULT_CASES)
    supported = next(case for case in pack.cases if case.requires_qrel_support)
    observation = ScenarioObservation(
        route_labels=(),
        source_groups=("general",),
        combined_query="",
        query_anchors=(),
        documents=(),
        evidence_status="answerable",
        should_generate=True,
        selected_documents=(),
    )
    assert unsafe_generation_reasons(
        supported, observation, evidence_qrel_aligned=False
    ) == ("qrel_alignment",)
    expected_false = replace(
        supported,
        expected_evidence_status="out_of_corpus",
        expected_should_generate=False,
        requires_qrel_support=False,
    )
    assert unsafe_generation_reasons(
        expected_false, observation, evidence_qrel_aligned=True
    ) == ("expected_false",)


def test_regular_profiles_run_all_development_scenario_stages() -> None:
    suite = load_suite(DEFAULT_SUITE)
    expected = list(SCENARIO_STAGE_CASES)
    for profile in ("quick", "local", "release", "submission"):
        stages = suite["profiles"][profile]
        scenario_offset = stages.index("scenario_robustness")
        assert stages[scenario_offset : scenario_offset + len(expected)] == expected
    assert "validation" not in suite["profiles"]
    assert "sealed" not in suite["profiles"]
    assert suite["scenario_robustness"]["maximum_hcx_calls"] == 0
    assert RUNNER_VERSION == "pension-benchmark-suite-v14"


@pytest.mark.parametrize(
    ("stage_name", "case_name"),
    [
        ("scenario_robustness", "development-v1.json"),
        ("scenario_robustness_expansion", "development-expansion-v1.json"),
        ("scenario_safety", "development-safety-v1.json"),
    ],
)
def test_runner_scenario_stage_targets_declared_development_pack(
    stage_name: str,
    case_name: str,
) -> None:
    command, timeout = _stage_command(
        stage_name,
        suite_path=DEFAULT_SUITE,
        endpoint=None,
        allow_live_hcx=False,
    )

    cases_flag = command.index("--cases")
    assert Path(command[cases_flag + 1]).name == case_name
    assert command[command.index("--suite") + 1] == str(DEFAULT_SUITE)
    assert timeout == 120.0


@pytest.mark.parametrize("stage_name", tuple(SCENARIO_STAGE_CASES))
def test_runner_summary_keeps_scenario_gate_metrics(stage_name: str) -> None:
    summary = json.loads(
        _summarize_output(
            stage_name,
            json.dumps(
                {
                    "case_count": 35,
                    "passed": 35,
                    "failed": 0,
                    "case_pass_rate": 1.0,
                    "family_all_critical_pass_rate": 1.0,
                    "worst_axis_pass_rate": 1.0,
                    "unsafe_generation_count": 0,
                    "hcx_calls": 0,
                    "results": [{"case_id": "must-not-leak"}],
                }
            ),
        )
    )
    assert summary == {
        "case_count": 35,
        "passed": 35,
        "failed": 0,
        "case_pass_rate": 1.0,
        "family_all_critical_pass_rate": 1.0,
        "worst_axis_pass_rate": 1.0,
        "unsafe_generation_count": 0,
        "hcx_calls": 0,
    }
