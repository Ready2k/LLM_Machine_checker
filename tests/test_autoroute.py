"""Tests for the model:"auto" classifier (gateway/policy/autoroute.py).

The parametrised corpora below double as the tuning set: when a prompt is
misrouted in the wild, add it here with the route it should have taken, then
adjust weights/vocabulary until the whole file passes again.
"""
import asyncio
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import gateway.server as server
from gateway.policy import autoroute
from gateway.policy.autoroute import Decision, classify, refine_with_model, select_auto_route, wants_tiebreak

FAST, QUALITY, PRIVATE = "local-fast", "local-quality", "private-docs"


def user(text):
    return [{"role": "user", "content": text}]


def route(text, **kw):
    return classify(user(text), **kw).route


PY_FUNC = "def total(items):\n    return sum(i.price for i in items)\n"
PY_BLOCK = "```python\n" + PY_FUNC * 12 + "```"          # ~650 chars of code
PY_BIG_BLOCK = "```python\n" + PY_FUNC * 40 + "```"      # ~2200 chars of code
PROSE = "The quarterly roadmap covers the new onboarding flow and the billing migration. "


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def test_select_auto_route_keeps_legacy_signature():
    result = select_auto_route(user("hi"), "personal")
    assert isinstance(result, tuple) and len(result) == 2
    name, reason = result
    assert name == FAST and isinstance(reason, str) and reason
    # positional-only legacy call and default privacy level both still work
    assert select_auto_route(user("hi"))[0] == FAST


def test_classify_returns_structured_decision():
    d = classify(user("Compare the trade-offs of SQL and NoSQL for an audit log"))
    assert isinstance(d, Decision)
    assert d.route == QUALITY and d.stage == "complexity"
    assert d.score >= d.threshold and 0.5 <= d.confidence <= 1.0
    names = {n for n, _, _ in d.signals}
    assert "depth_decisive" in names
    as_dict = d.to_dict()
    assert as_dict["route"] == QUALITY and as_dict["signals"] and as_dict["reason"] == d.reason


@pytest.mark.parametrize("messages", [
    [], None, [{"role": "user"}], [{"role": "user", "content": None}],
    [{"role": "user", "content": ""}], [{"role": "system", "content": "You are helpful."}],
    ["not a dict"], [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}],
    [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
])
def test_degenerate_input_falls_back_to_fast(messages):
    name, reason = select_auto_route(messages)
    assert name == FAST and reason


def test_deterministic():
    msgs = user("Explain how to deploy a FastAPI app to Kubernetes with zero downtime")
    assert {select_auto_route(msgs) for _ in range(20)} == {select_auto_route(msgs)}


# ---------------------------------------------------------------------------
# Clear fast / clear quality
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "hi", "Hello there!", "thanks!", "ok", "Thank you very much",
    "What is the capital of France?",
    "write a function to reverse a string in python",
    "translate 'good morning' to French",
    "fix the typo: teh cat sat",
    "convert 72F to celsius",
    "regex for a UK postcode",
    "why is the sky blue?",
    "what's 17 * 23",
    "rename this variable to something clearer: `x1`",
    "give me a one-liner to count lines in a file",
    "who was the first person on the moon",
    "list the HTTP methods",
])
def test_clear_fast(text):
    assert route(text) == FAST


@pytest.mark.parametrize("text", [
    "Explain the architecture of Kafka and compare it with RabbitMQ",
    "What are the trade-offs between REST and GraphQL?",
    "Analyse the security implications of storing JWTs in localStorage",
    "Design a rate limiter for a multi-tenant API and justify your choices",
    "Review this code for bugs:\n" + PY_BLOCK,
    "Refactor this module:\n" + PY_BIG_BLOCK,
    "Walk me through migrating a monolith to microservices, including a rollback strategy",
    "Write a detailed report comparing three caching strategies",
    "Evaluate whether we should refactor the billing module or rewrite it",
])
def test_clear_quality(text):
    assert route(text) == QUALITY


@pytest.mark.parametrize("text", [
    "Prove that the square root of 2 is irrational",
    "What's the time complexity of this approach, and why?",
    "Think carefully: is 91 prime?",
    "Is there a race condition here?",
    "Three friends each make one statement. Exactly one of them always lies. If Ann says "
    "Bob never lies and Bob says Cy must be lying, who is the liar?",
    "Design a distributed lock that survives network partitions",
])
def test_short_but_complex_goes_to_quality(text):
    assert len(text) < 200
    assert route(text) == QUALITY


def test_sarcastic_framing_does_not_hide_the_task():
    text = "Oh great, another 'simple' question: design a distributed lock service that survives partitions"
    assert route(text) == QUALITY


# ---------------------------------------------------------------------------
# Length
# ---------------------------------------------------------------------------

def test_long_prompt_with_a_task_goes_to_quality():
    assert route("Summarise the key risks in this plan.\n\n" + PROSE * 8) == QUALITY


def test_very_long_prompt_goes_to_quality_on_length_alone():
    d = classify(user(PROSE * 30))
    assert d.route == QUALITY and "very_long_prompt" in d.reason


def test_long_prompt_with_trivial_transform_stays_fast():
    # Length alone used to force quality; a typo fix on a paragraph doesn't need it,
    # and the paragraph's own vocabulary ("migration") is payload, not the task.
    d = classify(user("Fix the typos in this paragraph:\n\n" + PROSE * 6))
    assert d.route == FAST and "payload_vocab_ignored" in d.reason


def test_payload_rule_needs_a_transform_instruction():
    # Same shape, but the instruction is a reasoning task: payload vocabulary counts.
    assert route("Assess this:\n\n" + PROSE * 3) == QUALITY


def test_brevity_request_pulls_towards_fast():
    assert route("Briefly, what's a mutex?") == FAST
    assert "brevity" in classify(user("tl;dr of HTTP/2 vs HTTP/3?")).reason


def test_short_prompt_stays_fast():
    d = classify(user("ping"))
    assert d.route == FAST and "short_prompt" in d.reason


# ---------------------------------------------------------------------------
# Misroutes reported from real usage
# ---------------------------------------------------------------------------
# Entries are appended between the markers by `scripts/autoroute_feedback.py
# promote` (from the chat UI's "wrong route?" / the dashboard's Auto-Router tab).
# A new entry fails until weights/vocabulary are tuned -- that is the point.

FEEDBACK_CORPUS = [
    # (prompt, expected_route)
    # feedback-corpus-begin
    # feedback-corpus-end
]


@pytest.mark.parametrize("text,expected", FEEDBACK_CORPUS)
def test_reported_misroutes(text, expected):
    assert route(text) == expected


# ---------------------------------------------------------------------------
# Privacy: header override
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", ["confidential", "restricted"])
def test_privacy_header_overrides_content(level):
    name, reason = select_auto_route(user("hi"), level)
    assert name == PRIVATE and reason == f"privacy_header:{level}"


@pytest.mark.parametrize("level", ["public", "personal", "something-unknown"])
def test_content_privacy_applies_at_every_other_tier(level):
    assert select_auto_route(user("this document is confidential"), level)[0] == PRIVATE


# ---------------------------------------------------------------------------
# Privacy: true positives
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    # every keyword of the previous classifier, used in plain prose
    "this is confidential: Q3 numbers are down",
    "here are my private notes from therapy",
    "My salary is 85k, should I ask for a raise?",
    "my password is hunter2, is that strong?",
    "some personal stuff I need help wording",
    "summarise this internal memo for me",
    "can you keep a secret",
    "this spreadsheet has PII in it",
    "the data is sensitive, be careful",
    "we signed an NDA with them last week",
    "it's proprietary to our company",
    "that file is restricted",
    "here are the credentials for the server",
    "my social security number needs updating",
    "her date of birth is on the form",
    # new phrases
    "Private notes (see attached)",
    "Top secret launch plan below",
    "please don't share this with anyone: we're being acquired",
    "attached is my medical record from the GP",
    "my bank account number and sort code are below",
])
def test_privacy_keywords_in_prose(text):
    assert route(text) == PRIVATE


@pytest.mark.parametrize("text,detector", [
    ("my card is 4111 1111 1111 1111 exp 04/29", "card_number"),
    ("ssn 123-45-6789", "ssn"),
    ("NI number AB 12 34 56 C", "ni_number"),
    ("pay into GB82 WEST 1234 5698 7654 32", "iban"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow...", "private_key"),
    ("why is this rejected? key=sk-abcdefghijklmnopqrstuvwxyz123456", "api_key"),
    ("AKIAIOSFODNN7EXAMPLE keeps getting denied", "api_key"),
    ("decode eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fw", "jwt"),
    ('db = connect(user="admin", password="S3cr3tPass!")', "secret_literal"),
    ("```yaml\napi_key: 9f8e7d6c5b4a\n```", "secret_literal"),
    ("password = hunter", "secret_literal"),
])
def test_sensitive_data_detected_by_shape(text, detector):
    d = classify(user(text))
    assert d.route == PRIVATE and f"hard:{detector}" in d.reason


def test_real_secret_inside_code_fence_is_still_private():
    text = "Why does this fail?\n```python\nPASSWORD = 'correct-horse-battery'\nlogin(PASSWORD)\n```"
    assert route(text) == PRIVATE


@pytest.mark.parametrize("text", [
    "Mon mot de passe est soleil123",
    "Este documento es confidencial",
    "Das ist streng vertraulich",
    "这是机密文件，请帮我总结",
    "我的密码是 abc12345",
    "パスワードを忘れました: tanaka99",
    "Это конфиденциальная информация",
])
def test_privacy_in_other_languages(text):
    assert route(text) == PRIVATE


def test_privacy_scans_system_messages():
    msgs = [{"role": "system", "content": "The user's files are confidential."}] + user("hi")
    assert classify(msgs).route == PRIVATE


def test_privacy_detects_secret_in_assistant_or_tool_turn():
    msgs = user("look up my record") + [
        {"role": "tool", "content": '{"name": "A. Smith", "ssn": "123-45-6789"}'},
        {"role": "user", "content": "thanks, what does it say?"},
    ]
    assert classify(msgs).route == PRIVATE


def test_assistant_vocabulary_alone_does_not_trigger():
    msgs = user("hi") + [{"role": "assistant", "content": "I can't help with confidential data."},
                         {"role": "user", "content": "ok, what is 2+2"}]
    assert classify(msgs).route == FAST


# ---------------------------------------------------------------------------
# Privacy: false positives
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "What does this do?\n```java\npublic class A {\n  private String password;\n"
    "  private void internalReset() { this.secret = null; }\n}\n```",
    "private void foo() {\n  int x = 1;\n}\nwhy won't this compile",
    "what does `private static final` mean in Java?",
    "self.password = password",
    "password = user_input",
    "const secret = getSecret();",
    "token: string;",
    'password = os.environ["DB_PASS"]',
    "def check(password: str, secret: bytes) -> bool:",
    "how do I pass --password to mysqldump safely?",
    "ls /private/tmp shows nothing",
    "user.personal.email is undefined",
    "SELECT name, salary FROM employees WHERE dept = 'eng';",
    "difference between public, protected and private members in C++?",
    "should this be a private method or a helper function?",
])
def test_privacy_words_as_code_are_not_private(text):
    assert route(text) != PRIVATE


@pytest.mark.parametrize("text", [
    "I'm getting an internal server error from nginx",
    "is this comparison case-sensitive?",
    "how does a password manager work?",
    "I forgot my password, how do resets usually work?",
    "what's the secret to good sourdough?",
    "ideas for a secret santa gift under £10",
    "it's just my personal preference, tabs or spaces?",
    "how does an internal combustion engine work",
    "what is a restricted Boltzmann machine",
    "what's the average salary of a data engineer in the UK?",
    "explain credential stuffing",
])
def test_benign_phrases_are_not_private(text):
    d = classify(user(text))
    assert d.route != PRIVATE
    assert "privacy_ignored" in d.reason  # the suppression is visible in the trace


def test_ignored_code_tokens_do_not_mask_real_prose_hit():
    text = "```java\nprivate int x;\n```\nAlso this code is confidential, don't post it."
    assert route(text) == PRIVATE


def test_strict_privacy_mode_restores_any_keyword_rule():
    text = "```java\nprivate int x;\n```"
    assert route(text) != PRIVATE
    assert route(text, config={"autoroute": {"privacy_mode": "strict"}}) == PRIVATE
    assert route("internal server error", config={"privacy_mode": "strict"}) == PRIVATE


LEGACY_KEYWORDS = ["confidential", "private", "salary", "password", "personal", "internal",
                   "secret", "pii", "sensitive", "nda", "proprietary", "restricted",
                   "credentials", "social security", "date of birth"]


@pytest.mark.parametrize("kw", LEGACY_KEYWORDS)
def test_every_legacy_keyword_still_triggers_in_plain_prose(kw):
    assert route(f"I need help with something, it involves {kw} information from work") == PRIVATE


@pytest.mark.parametrize("kw", LEGACY_KEYWORDS)
def test_strict_mode_is_superset_of_legacy(kw):
    assert route(f"x.{kw} = 1;", config={"privacy_mode": "strict"}) == PRIVATE


# ---------------------------------------------------------------------------
# Multi-turn
# ---------------------------------------------------------------------------

def convo(*turns):
    msgs = []
    for i, t in enumerate(turns):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": t})
    return msgs


def test_routes_on_latest_user_turn_not_history():
    msgs = convo("Compare the trade-offs of Raft and Paxos in depth", "Raft is ...",
                 "Unrelated: what is the capital of Peru? Just curious about geography for my quiz night.")
    assert classify(msgs).route == FAST


def test_short_followup_to_complex_turn_stays_on_quality():
    msgs = convo("Compare the trade-offs of Raft and Paxos in depth", "Raft is ...", "and in Go?")
    d = classify(msgs)
    assert d.route == QUALITY and "followup" in d.reason


def test_followup_chain_looks_back_past_other_short_turns():
    msgs = convo("Design a rate limiter and justify the trade-offs", "...", "go on", "...", "and then?")
    assert classify(msgs).route == QUALITY


def test_thanks_after_complex_turn_is_fast():
    msgs = convo("Compare the trade-offs of Raft and Paxos in depth", "Raft is ...", "thanks!")
    assert classify(msgs).route == FAST


def test_short_followup_to_simple_turn_stays_fast():
    msgs = convo("what is the capital of France?", "Paris.", "and Spain?")
    assert classify(msgs).route == FAST


def test_privacy_is_sticky_across_turns():
    msgs = convo("my salary is 85k and my manager won't budge", "That sounds frustrating.",
                 "write a function to reverse a string")
    assert classify(msgs).route == PRIVATE


def test_large_conversation_context_pushes_to_quality():
    msgs = convo("tell me about the history of Rome", "x" * 30000, "and what happened next in the story?")
    d = classify(msgs)
    assert d.route == QUALITY and "huge_context" in d.reason


def test_system_prompt_does_not_skew_complexity():
    system = {"role": "system", "content": "Analyse, compare, evaluate and design. " * 50}
    assert classify([system] + user("hi")).route == FAST
    assert classify([system] + user("what is 2+2")).route == FAST


# ---------------------------------------------------------------------------
# Request shape: tools / structured output
# ---------------------------------------------------------------------------

TOOLS = [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}]
SCHEMA = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}


def test_tools_alone_do_not_force_quality():
    d = classify(user("weather in Leeds?"), tools=TOOLS)
    assert d.route == FAST and "tools" in d.reason


def test_tools_plus_reasoning_goes_to_quality():
    assert classify(user("Plan a 3-city trip and compare the weather in each"), tools=TOOLS).route == QUALITY


def test_agent_loop_with_tool_results_and_schema_goes_to_quality():
    msgs = user("find flights to Oslo and book the cheapest") + [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": '{"flights": []}'},
        {"role": "user", "content": "try the next day instead"},
    ]
    d = classify(msgs, tools=TOOLS, response_format=SCHEMA)
    assert d.route == QUALITY
    assert {"tools", "tool_results", "json_schema"} <= {n for n, _, _ in d.signals}


def test_json_object_is_a_weak_signal():
    d = classify(user("list three colours as JSON"), response_format={"type": "json_object"})
    assert d.route == FAST and "json_object" in d.reason


# ---------------------------------------------------------------------------
# Ambiguous / borderline
# ---------------------------------------------------------------------------

def test_borderline_cases_report_low_confidence():
    d = classify(user("Explain how to deploy a FastAPI app to Kubernetes with zero downtime"))
    assert d.margin < 1.0 and d.confidence < 0.75
    clear = classify(user("hi"))
    assert clear.confidence == 1.0


def test_single_mild_signal_is_not_enough():
    assert route("explain what a closure is") == FAST
    assert route("how do I reverse a list in python?") == FAST


def test_code_without_a_hard_task_stays_fast():
    assert route("add a docstring:\n```python\n" + PY_FUNC + "```") == FAST


def test_multi_part_question_escalates():
    text = ("How should I structure the repo? Where do the tests go? And how do I configure CI "
            "so it runs them on every push?")
    assert route(text) == QUALITY


def test_enumerated_requirements_escalate():
    text = ("Implement an endpoint that:\n- validates the payload\n- deduplicates by id\n"
            "- retries on conflict\n- emits an audit event")
    assert route(text) == QUALITY


# ---------------------------------------------------------------------------
# Non-English / mixed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Explica paso a paso cómo funciona el algoritmo de Dijkstra", QUALITY),
    ("¿Qué es una API?", FAST),
    ("Analysiere die Vor- und Nachteile von Microservices", QUALITY),
    ("Was ist ein Mutex?", FAST),
    ("Explique pourquoi cette requête est lente et compare les deux index", QUALITY),
    ("Traduis « bonjour » en anglais", FAST),
    ("请分析并比较微服务架构和单体架构的优缺点", QUALITY),
    ("什么是递归？", FAST),
    ("このアーキテクチャの長所と短所を分析してください", QUALITY),
    ("こんにちは", FAST),
    ("Сравни и проанализируй два подхода к кэшированию", QUALITY),
    ("спасибо", FAST),
    # mixed: English instruction, non-English payload and vice versa
    ("Translate to English: 我们需要分析系统架构", FAST),
    ("Por favor review this code and analyse the bugs:\n" + PY_BLOCK, QUALITY),
])
def test_non_english_and_mixed(text, expected):
    assert route(text) == expected


def test_cjk_length_is_scaled():
    # ~170 CJK characters carry as much as a 500-char English prompt.
    text = "我们的团队正在讨论下一个季度的产品路线图以及相关的资源安排问题" * 6
    assert "long_prompt" in classify(user(text)).reason


def test_non_latin_script_is_noted_in_trace():
    assert "script:non_latin" in classify(user("什么是递归？")).reason


# ---------------------------------------------------------------------------
# Reason string: useful, header-safe, and free of prompt content
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "hi", "这是机密文件 — 请帮我总结", "Compare A and B in depth " * 40,
    "my password is Zx9!quux-hunter2", "ssn 123-45-6789", "¿Qué es una API?",
])
def test_reason_is_ascii_bounded_and_single_line(text):
    _, reason = select_auto_route(user(text), "personal")
    assert reason.isascii() and "\n" not in reason and "\r" not in reason
    assert 0 < len(reason) <= 240
    reason.encode("latin-1")  # must be a legal HTTP header value


def test_reason_never_contains_prompt_content():
    for text, leak in [("my password is Zx9quux, keep it secret", "zx9quux"),
                       ("ssn 123-45-6789", "6789"),
                       ("my salary is 85k", "salary"),
                       ("key sk-abcdefghijklmnopqrstuvwxyz123456", "abcdef")]:
        d = classify(user(text))
        assert d.route == PRIVATE
        assert leak not in d.reason.lower()
        assert leak not in str(d.to_dict()).lower()


def test_reason_explains_the_score():
    d = classify(user("Review this code for bugs:\n" + PY_BLOCK))
    assert d.reason.startswith("score=")
    for part in ("depth_strong", "code_task", "code_block", "conf="):
        assert part in d.reason
    total = sum(w for _, w, _ in d.signals)
    assert abs(total - d.score) < 1e-9


# ---------------------------------------------------------------------------
# Config-driven tuning
# ---------------------------------------------------------------------------

def test_threshold_is_tunable():
    text = "Explain how to deploy a FastAPI app to Kubernetes with zero downtime"
    assert route(text) == QUALITY
    assert route(text, config={"autoroute": {"quality_threshold": 5}}) == FAST


def test_weights_are_tunable():
    text = "weather in Leeds?"
    assert classify(user(text), tools=TOOLS).route == FAST
    assert classify(user(text), tools=TOOLS, config={"weights": {"tools": 5}}).route == QUALITY


def test_extra_terms_are_tunable():
    cfg = {"autoroute": {"extra_terms": {
        "privacy_strong": ["project aurora"], "depth_decisive": ["rca"],
        "privacy_benign": ["private eye"], "fast": ["lookup"]}}}
    assert route("status of Project Aurora?") == FAST
    assert route("status of Project Aurora?", config=cfg) == PRIVATE
    assert route("write the RCA for last night", config=cfg) == QUALITY
    assert route("a novel about a private eye") == PRIVATE
    assert route("a novel about a private eye", config=cfg) == FAST


def test_route_names_are_configurable():
    cfg = {"routes": {"fast": "f", "quality": "q", "private": "p"}}
    assert route("hi", config=cfg) == "f"
    assert route("prove P != NP", config=cfg) == "q"
    assert route("this is confidential", config=cfg) == "p"
    assert select_auto_route(user("hi"), "restricted", config=cfg)[0] == "p"


@pytest.mark.parametrize("bad", [
    {"autoroute": {"weights": {"tools": "not-a-number"}}},
    {"autoroute": {"quality_threshold": "high"}},
    {"autoroute": None}, {"autoroute": []}, {"weights": None, "extra_terms": None},
])
def test_broken_config_never_breaks_routing(bad):
    assert route("hi", config=bad) == FAST
    assert route("this is confidential", config=bad) == PRIVATE


def test_shipped_config_matches_builtin_defaults():
    import yaml
    shipped = yaml.safe_load((server.PROJECT_ROOT / "config" / "autoroute.yaml").read_text())
    for text in ["hi", "prove it step by step", "this is confidential", "private void f() {"]:
        assert classify(user(text), config=shipped).to_dict() == classify(user(text)).to_dict()
    assert shipped["autoroute"]["llm_tiebreak"]["enabled"] is False


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------

def test_common_case_is_well_under_5ms():
    prompts = [user(t) for t in (
        "hi", "write a function to reverse a string in python",
        "Explain the architecture of Kafka and compare it with RabbitMQ",
        "Review this code for bugs:\n" + PY_BLOCK, "my salary is 85k",
        "请分析并比较微服务架构和单体架构的优缺点", "Summarise:\n" + PROSE * 8,
    )]
    for p in prompts:
        classify(p)  # warm
    start = time.perf_counter()
    rounds = 50
    for _ in range(rounds):
        for p in prompts:
            classify(p)
    avg_ms = (time.perf_counter() - start) * 1000 / (rounds * len(prompts))
    assert avg_ms < 1.0, f"average {avg_ms:.3f}ms"


def test_large_paste_stays_bounded():
    big = user("Summarise this log.\n" + ("2026-01-01 12:00:00 INFO worker=7 handled request in 12ms\n" * 4000))
    start = time.perf_counter()
    d = classify(big)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert d.route == QUALITY
    assert elapsed_ms < 500, f"{elapsed_ms:.0f}ms for a 230KB prompt"


# ---------------------------------------------------------------------------
# Optional model tiebreak (stage 3)
# ---------------------------------------------------------------------------

BORDERLINE = "Explain how to deploy a FastAPI app to Kubernetes with zero downtime"
TB_ON = {"autoroute": {"llm_tiebreak": {"enabled": True}}}


def _refine(decision, messages, answer, config=TB_ON):
    calls = []

    async def ask(judge_messages):
        calls.append(judge_messages)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return asyncio.run(refine_with_model(decision, messages, ask, config)), calls


def test_tiebreak_is_off_by_default():
    d = classify(user(BORDERLINE))
    assert not wants_tiebreak(d)
    refined, calls = _refine(d, user(BORDERLINE), "SIMPLE", config=None)
    assert refined is d and not calls


def test_tiebreak_only_for_close_calls():
    assert wants_tiebreak(classify(user(BORDERLINE), config=TB_ON), TB_ON)
    assert not wants_tiebreak(classify(user("hi"), config=TB_ON), TB_ON)
    assert not wants_tiebreak(classify(user("prove it and compare the trade-offs"), config=TB_ON), TB_ON)


def test_tiebreak_never_runs_for_private_decisions():
    for d in (classify(user("my salary is 85k"), config=TB_ON),
              classify(user(BORDERLINE), "restricted", config=TB_ON)):
        assert d.route == PRIVATE and not wants_tiebreak(d, TB_ON)
        refined, calls = _refine(d, user("x"), "SIMPLE")
        assert refined.route == PRIVATE and not calls


@pytest.mark.parametrize("answer,expected", [
    ("SIMPLE", FAST), ("simple.", FAST), (" Complex\n", QUALITY), ("COMPLEX - needs steps", QUALITY),
])
def test_tiebreak_verdict_decides(answer, expected):
    d = classify(user(BORDERLINE), config=TB_ON)
    refined, calls = _refine(d, user(BORDERLINE), answer)
    assert refined.route == expected and refined.stage == "model"
    assert "tiebreak:" in refined.reason and refined.reason.isascii()
    assert len(calls) == 1 and BORDERLINE in calls[0][-1]["content"]


@pytest.mark.parametrize("answer", ["", "maybe", "I think it depends", None,
                                    RuntimeError("down"), asyncio.TimeoutError()])
def test_tiebreak_failure_keeps_rule_based_decision(answer):
    d = classify(user(BORDERLINE), config=TB_ON)
    before = d.route
    refined, _ = _refine(d, user(BORDERLINE), answer)
    assert refined.route == before and refined.stage == "complexity"
    assert "tiebreak_" in refined.reason


def test_tiebreak_prompt_is_truncated():
    long_borderline = "explain " + "word " * 2000
    cfg = {"autoroute": {"llm_tiebreak": {"enabled": True, "max_margin": 100, "max_chars": 200}}}
    d = classify(user(long_borderline), config=cfg)
    _, calls = _refine(d, user(long_borderline), "COMPLEX", config=cfg)
    assert len(calls[0][-1]["content"]) < 400


# ---------------------------------------------------------------------------
# Gateway integration
# ---------------------------------------------------------------------------

client = TestClient(server.app)
BACKENDS = r".*/v1/chat/completions"


def _completion(content="ok"):
    return httpx.Response(200, json={
        "id": "c1", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"message": {"role": "assistant", "content": content}}],
    })


def _post(text, **extra):
    body = {"model": "auto", "messages": user(text), "stream": False, **extra}
    return client.post("/v1/chat/completions", json=body, headers=extra.pop("headers", None))


@respx.mock
@pytest.mark.parametrize("text,expected", [
    ("hi", FAST),
    ("Compare the trade-offs of Raft and Paxos in depth", QUALITY),
    ("my salary is 85k", PRIVATE),
])
def test_gateway_auto_routes_and_reports_reason(text, expected):
    respx.post(url__regex=BACKENDS).mock(return_value=_completion())
    resp = _post(text)
    assert resp.status_code == 200
    assert resp.headers["x-ai-route"] == expected
    assert resp.headers["x-ai-auto-reason"] == select_auto_route(user(text))[1]


@respx.mock
def test_gateway_privacy_header_forces_private_route():
    respx.post(url__regex=BACKENDS).mock(return_value=_completion())
    resp = client.post("/v1/chat/completions", headers={"x-ai-privacy": "restricted"},
                       json={"model": "auto", "messages": user("hi"), "stream": False})
    assert resp.status_code == 200
    assert resp.headers["x-ai-route"] == PRIVATE
    assert resp.headers["x-ai-auto-reason"] == "privacy_header:restricted"


@respx.mock
def test_gateway_passes_tools_to_classifier():
    respx.post(url__regex=BACKENDS).mock(return_value=_completion())
    resp = _post("Plan a 3-city trip and compare the weather in each", tools=TOOLS)
    assert resp.headers["x-ai-route"] == QUALITY and "tools" in resp.headers["x-ai-auto-reason"]


@respx.mock
def test_gateway_logs_decision_trace_without_prompt_content(monkeypatch):
    respx.post(url__regex=BACKENDS).mock(return_value=_completion())
    events = []
    monkeypatch.setattr(server, "log_llm_event", lambda cid, kind, msg, meta=None: events.append((kind, msg, meta)))
    _post("my password is Zx9quux77")
    kind, msg, meta = next(e for e in events if e[0] == "router.auto")
    assert meta["route"] == PRIVATE and meta["signals"]
    assert "zx9quux77" not in (msg + str(meta)).lower()


@respx.mock
def test_gateway_reads_autoroute_config(monkeypatch):
    respx.post(url__regex=BACKENDS).mock(return_value=_completion())
    monkeypatch.setattr(server, "load_autoroute_config", lambda: {"autoroute": {"quality_threshold": -10}})
    assert _post("hi").headers["x-ai-route"] == QUALITY
    monkeypatch.setattr(server, "load_autoroute_config", lambda: {"autoroute": {"quality_threshold": 99}})
    assert _post("prove it step by step").headers["x-ai-route"] == FAST


@respx.mock
def test_gateway_tiebreak_asks_fast_model_once(monkeypatch):
    monkeypatch.setattr(server, "load_autoroute_config", lambda: TB_ON)
    calls = []

    def backend(request):
        import json as _json
        body = _json.loads(request.content)
        calls.append(body)
        is_judge = body.get("max_tokens") == 4
        return _completion("COMPLEX" if is_judge else "answer")

    respx.post(url__regex=BACKENDS).mock(side_effect=backend)
    resp = _post("Explain how to deploy a FastAPI app to Kubernetes with zero downtime "
                 "but keep it simple")
    d = classify(user("Explain how to deploy a FastAPI app to Kubernetes with zero downtime "
                      "but keep it simple"), config=TB_ON)
    assert wants_tiebreak(d, TB_ON)
    assert resp.status_code == 200
    assert resp.headers["x-ai-route"] == QUALITY
    assert "tiebreak:complex" in resp.headers["x-ai-auto-reason"]
    assert len(calls) == 2 and calls[0]["max_tokens"] == 4 and calls[0]["temperature"] == 0

    # A clear-cut request makes no extra model call.
    calls.clear()
    assert _post("hi").headers["x-ai-route"] == FAST
    assert len(calls) == 1


@respx.mock
def test_gateway_tiebreak_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(server, "load_autoroute_config", lambda: TB_ON)

    def backend(request):
        import json as _json
        if _json.loads(request.content).get("max_tokens") == 4:
            return httpx.Response(500, text="boom")
        return _completion("answer")

    respx.post(url__regex=BACKENDS).mock(side_effect=backend)
    resp = _post(BORDERLINE)
    assert resp.status_code == 200
    assert resp.headers["x-ai-route"] == classify(user(BORDERLINE)).route
    assert "tiebreak_failed" in resp.headers["x-ai-auto-reason"]
