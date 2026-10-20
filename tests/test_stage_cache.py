"""Validated cache reuse stays separate from the charge-prevention ledger."""

import json
from functools import partial
from types import SimpleNamespace

import pytest

from a_stock_tracker import disclosures as d
from a_stock_tracker import evidence_pipeline as ep
from a_stock_tracker.automation import Budget, read_state, save
from a_stock_tracker.research import ScreenError
from tests.test_evidence_pipeline import evidence_case, supported


def runtime(monkeypatch, tmp_path, answer):
    calls = []
    monkeypatch.delenv("AUTOMATION_PI_RUNTIME", raising=False)
    monkeypatch.setattr(d, "model_command", lambda: (["synthetic"], tmp_path))

    def infer(args, **kwargs):
        request = json.loads(open(args[-1]).read())
        calls.append(request)
        result = answer(request)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "tool_count": 0,
                    "model": "m",
                    "provider": "p",
                    "text": json.dumps(result),
                    "usage": {"input": 10, "output": 10},
                }
            ),
        )

    monkeypatch.setattr(d, "run_bounded", infer)
    return calls


def test_validated_stages_reuse_across_requests_but_still_validate(monkeypatch, tmp_path):
    packet, report = evidence_case()

    def answer(request):
        prompt = request["prompt"]
        if prompt.startswith(ep.SELECT):
            return {k: report[k] for k in ("code", "facts", "gaps")}
        if prompt.startswith(ep.COMPOSE):
            return {k: report[k] for k in ("code", "analysis")}
        return supported(json.loads(prompt[len(ep.REVIEW) :])["subject_report"])

    calls = runtime(monkeypatch, tmp_path, answer)
    root = tmp_path / "cache"
    for index in range(2):
        directory = tmp_path / str(index)
        directory.mkdir(mode=0o700)
        state = {"model_calls": 0, "model": {"provider": "p", "model": "m"}}
        result, review = ep.run(
            packet,
            directory,
            state,
            Budget(100, 1800),
            partial(d.model_call, cache_root=root),
            {},
            lambda label: d.cache_validated_response(directory, label, root),
        )
        assert ep.validate_review(review, result)
        assert state["model_calls"] == (3 if index == 0 else 0)
        if index:
            for usage in directory.glob("*.usage.json"):
                assert read_state(usage)["status"] == "reused_validated_stage"
    assert len(calls) == len(list(root.glob("stage-*.json"))) == 3
    # Every reused stage passes the same validators; no output bypass is cached.
    monkeypatch.setattr(
        ep, "selection_errors", lambda *_: (_ for _ in ()).throw(ScreenError("reject replay"))
    )
    directory = tmp_path / "changed-validator"
    directory.mkdir(mode=0o700)
    with pytest.raises(ScreenError, match="reject replay"):
        ep.run(
            packet,
            directory,
            {"model_calls": 0, "model": {"provider": "p", "model": "m"}},
            Budget(100, 1800),
            partial(d.model_call, cache_root=root),
            {},
        )
    assert len(calls) == 3


def test_invalid_responses_stay_in_ledger_and_never_enter_shared_cache(monkeypatch, tmp_path):
    packet, _ = evidence_case()
    calls = runtime(monkeypatch, tmp_path, lambda _: {"wrong": "schema"})
    root = tmp_path / "cache"
    for index in range(2):
        directory = tmp_path / str(index)
        directory.mkdir(mode=0o700)
        state = {"model_calls": 0, "model": {"provider": "p", "model": "m"}}
        for _ in range(2):
            with pytest.raises(ValueError):
                ep.run(
                    packet,
                    directory,
                    state,
                    Budget(100, 1800),
                    partial(d.model_call, cache_root=root),
                    {},
                    lambda label: d.cache_validated_response(directory, label, root),
                )
        assert state["model_calls"] == 1
        assert len(list(directory.glob("*.response.json"))) == 1
    assert len(calls) == 2
    assert not list(root.glob("stage-*.json"))


def test_unknown_own_attempt_cannot_be_replaced_with_cached_response(monkeypatch, tmp_path):
    calls = runtime(monkeypatch, tmp_path, lambda _: {"ok": True})
    root = tmp_path / "cache"
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(mode=0o700)
    second.mkdir(mode=0o700)
    state = {"model_calls": 0}
    model = {"provider": "p", "model": "m"}
    label = "600001.SH-v4-select"
    d.model_call(first, label, "synthetic prompt", model, state, Budget(100, 1800), cache_root=root)
    d.cache_validated_response(first, label, root)
    save(second / f"{label}.request.json", calls[0], immutable=True)
    with pytest.raises(ScreenError, match="refusing another charge"):
        d.model_call(
            second, label, "synthetic prompt", model, state, Budget(100, 1800), cache_root=root
        )
    assert len(calls) == 1 and not (second / f"{label}.response.json").exists()


def test_cache_root_symlink_is_rejected_before_inference(monkeypatch, tmp_path):
    calls = runtime(monkeypatch, tmp_path, lambda _: {"ok": True})
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    root = tmp_path / "linked"
    root.symlink_to(target, target_is_directory=True)
    with pytest.raises(ScreenError, match="Symlink automation root"):
        d.model_call(
            tmp_path,
            "600001.SH-v4-select",
            "prompt",
            {"provider": "p", "model": "m"},
            {"model_calls": 0},
            Budget(100, 1800),
            cache_root=root,
        )
    assert not calls


def test_validation_version_invalidates_whole_report_cache(monkeypatch):
    from a_stock_tracker import evidence_blocks as eb

    packet, _ = evidence_case()
    packet["citation_format"] = eb.VERSION
    model = {"provider": "p", "model": "m"}
    before = d.stable_evidence(packet, model)
    monkeypatch.setattr(ep, "CACHE_VERSION", "changed-validation")
    assert d.stable_evidence(packet, model) != before


def test_semantically_rejected_phases_never_poison_later_requests(monkeypatch, tmp_path):
    packet, report = evidence_case()

    def answer(request):
        prompt = request["prompt"]
        if prompt.startswith(ep.SELECT):
            return {k: report[k] for k in ("code", "facts", "gaps")}
        if prompt.startswith(ep.COMPOSE):
            return {k: report[k] for k in ("code", "analysis")}
        if prompt.startswith(ep.REPAIR):
            return {"facts": {}, "sentences": {}}
        review = supported(json.loads(prompt[len(ep.REVIEW) :])["subject_report"])
        review["facts"][0].update(supported=False, reason="不受原文支持")
        return review

    calls = runtime(monkeypatch, tmp_path, answer)
    root = tmp_path / "cache"
    # Force a terminal failure without another charged repair to isolate cache publication.
    monkeypatch.setattr(
        ep, "repair_input", lambda *_: (_ for _ in ()).throw(ScreenError("repair unavailable"))
    )
    for index in range(2):
        directory = tmp_path / str(index)
        directory.mkdir(mode=0o700)
        with pytest.raises(ScreenError, match="repair unavailable"):
            ep.run(
                packet,
                directory,
                {"model_calls": 0, "model": {"provider": "p", "model": "m"}},
                Budget(100, 1800),
                partial(d.model_call, cache_root=root),
                {},
                lambda label: d.cache_validated_response(directory, label, root),
            )
    assert len(calls) == 6 and not list(root.glob("stage-*.json"))


def test_pdf_parse_cache_is_hash_and_version_bound(monkeypatch, tmp_path):
    calls = []

    def convert(args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(
            stdout="Pages: 1\n"
            if args[0] == "pdfinfo"
            else "仅供离线测试的合成财报正文，长度充分。\f"
        )

    monkeypatch.setattr(d, "run_bounded", convert)
    cache = tmp_path / "cache"
    pdf = tmp_path / "source.pdf"
    first = d.parsed_pages(pdf, "a" * 64, cache)
    assert d.parsed_pages(pdf, "a" * 64, cache) == first
    assert calls == ["pdfinfo", "pdftotext"]
    monkeypatch.setattr(d, "PARSER_VERSION", "new-parser-version")
    assert d.parsed_pages(pdf, "a" * 64, cache) == first
    assert len(calls) == 4 and len(list(cache.glob("parsed-*.json"))) == 2
