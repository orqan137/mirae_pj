from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts import evaluate_external_korquad as evaluator


def test_missing_dataset_is_not_a_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["eval", "--dataset", str(tmp_path / "missing.json")])
    assert evaluator.main() == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False


@pytest.mark.parametrize("flag,value", [("--sample", "0"), ("--sample", "-1"), ("--k", "0")])
def test_invalid_evaluation_size_is_rejected(monkeypatch, flag, value):
    monkeypatch.setattr("sys.argv", ["eval", flag, value])
    with pytest.raises(SystemExit) as error:
        evaluator.main()
    assert error.value.code == 2


def test_empty_dataset_is_rejected(monkeypatch, tmp_path):
    dataset = tmp_path / "empty.json"
    dataset.write_text('{"data": []}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["eval", "--dataset", str(dataset)])
    with pytest.raises(SystemExit) as error:
        evaluator.main()
    assert error.value.code == 2


def test_retrieval_scoring_counts_missing_and_late_gold():
    class Retriever:
        def retrieve(self, question, top_k):
            rows = {"first": ["a", "b"], "late": ["a", "b"], "missing": []}
            return [SimpleNamespace(source_id=value) for value in rows[question][:top_k]]

    result = evaluator.score(Retriever(), [("first", "a"), ("late", "b"), ("missing", "c")], k=2)
    assert result["precision_at_1"] == pytest.approx(1 / 3)
    assert result["recall_at_k"] == pytest.approx(2 / 3)
    assert result["mrr_at_k"] == 0.5
    assert result["empty_result_rate"] == pytest.approx(1 / 3)


def test_report_identifies_retrieval_adaptation_and_reproducible_sample(monkeypatch, tmp_path, capsys):
    dataset = tmp_path / "sample.json"
    payload = {"data": [{"title": "퇴직연금", "paragraphs": [{
        "context": "개인형 퇴직연금은 IRP 계좌입니다.",
        "qas": [{"question": "개인형 퇴직연금은 무엇인가요?"}],
    }]}]}
    dataset.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["eval", "--dataset", str(dataset), "--seed", "7", "--sample", "1"])
    assert evaluator.main() == 0
    first = json.loads(capsys.readouterr().out)
    assert evaluator.main() == 0
    second = json.loads(capsys.readouterr().out)
    assert first == second
    assert first["official_korquad_em_f1"] is False
    assert first["evaluation_task"] == "paragraph_retrieval_adaptation"
    assert first["evaluation_usage"] == "development_diagnostic"
    assert first["dataset_sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert first["seed"] == 7
    assert first["sampled_questions"] == 1
    assert first["hcx_calls"] == 0
    assert all(len(first[key]) == 64 for key in ("sample_sha256", "retriever_sha256", "evaluator_sha256"))
