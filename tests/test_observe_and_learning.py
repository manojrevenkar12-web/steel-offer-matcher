"""Langfuse traces and prompt fallback; corrections that become aliases only after a replay."""

import pytest

from steelmatch.observe.langfuse_bridge import Observer, local_prompt
from steelmatch.review.learning import AliasLearner


@pytest.fixture()
def langfuse_capture():
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    client = Langfuse(public_key="pk-lf-test", secret_key="sk-lf-test", base_url="http://127.0.0.1:9", span_exporter=exporter,
                      flush_at=1)
    yield client, exporter
    client.shutdown()


def test_runs_are_traced_with_generations(langfuse_capture):
    client, exporter = langfuse_capture
    obs = Observer(client)
    with obs.span("rfq", as_type="agent", input={"subject": "Anfrage S235JR"}, metadata={"prompt.extractor": "local-1"}) as span:
        obs.generation("llm.extractor", "sim/fast-commercial", 1200, 300, 0.0008, {"route": "extract"})
        span.update(output={"status": "ready"})
    obs.flush()
    names = [s.name for s in exporter.get_finished_spans()]
    assert "rfq" in names and "llm.extractor" in names
    gen = next(s for s in exporter.get_finished_spans() if s.name == "llm.extractor")
    assert gen.parent is not None  # nested under the RFQ run


def test_prompt_falls_back_to_the_repository_copy(langfuse_capture):
    client, _ = langfuse_capture
    p = Observer(client).prompt("extractor")
    assert p.source == "local" and p.text == local_prompt("extractor").text


def test_no_langfuse_means_no_op():
    obs = Observer(None)
    with obs.span("rfq") as s:
        s.update(output={})
    obs.generation("x", "m", 1, 1, 0.0)
    assert obs.prompt("matcher").source == "local"


def test_alias_is_learned_after_repeated_corrections_and_a_clean_replay(kb):
    learner = AliasLearner(min_corrections=2)
    learner.confirm("c0", "S235JR", "S235JR")
    learner.record("c1", "St37", "S235JR")
    assert learner.candidates(kb) == []
    learner.record("c2", "ST 37", "S235JR")
    new_kb = learner.learn(kb)
    assert new_kb.resolve("st37").grade_id == "S235JR"
    assert learner.decisions[-1].accepted


def test_contradicting_corrections_do_not_become_an_alias(kb):
    learner = AliasLearner(min_corrections=2)
    for i, gid in enumerate(["S355J2", "S355JR", "S355J2", "S355JR"]):
        learner.record(f"c{i}", "S355", gid)
    assert learner.candidates(kb) == []
    assert learner.learn(kb).resolve("S355").grade_id is None


def test_replay_rejects_an_alias_that_breaks_a_confirmed_case(kb):
    learner = AliasLearner(min_corrections=2)
    learner.record("c1", "S355", "S355J2")
    learner.record("c2", "S355", "S355J2")
    learner.confirm("c3", "S355", "S355JR")  # a person confirmed a different reading elsewhere
    learner.learn(kb)
    assert learner.decisions and not learner.decisions[-1].accepted
