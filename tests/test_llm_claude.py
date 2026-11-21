"""The headless Claude Code provider in llm_backend (LLM_PROVIDER=claude)."""
import json
import subprocess
from typing import List

import pytest
from pydantic import BaseModel

import llm_backend


class Pick(BaseModel):
    id: str
    score: int


class Picks(BaseModel):
    picks: List[Pick]


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)


def _cli(monkeypatch, payload, returncode=0, stderr=""):
    seen = {}

    def fake_run(cmd, input, **kw):
        seen["cmd"], seen["input"] = cmd, input
        out = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(cmd, returncode, out, stderr)

    monkeypatch.setattr(llm_backend.subprocess, "run", fake_run)
    return seen


def test_claude_is_active_without_a_base_url(claude):
    assert llm_backend.provider() == "claude"
    assert llm_backend.active()
    assert llm_backend.model_name() == "sonnet"
    assert llm_backend.describe()["provider"] == "claude"


def test_structured_output_is_validated_and_prompt_goes_on_stdin(claude, monkeypatch):
    seen = _cli(monkeypatch, {
        "is_error": False,
        "structured_output": {"picks": [{"id": "w3", "score": 88}]},
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1500, "output_tokens": 40},
    })
    parsed, cost = llm_backend.generate_json("TRANSCRIPT", Picks)
    assert parsed == {"picks": [{"id": "w3", "score": 88}]}
    assert cost["input_tokens"] == 1502 and cost["total_cost"] == 0.0
    assert seen["input"] == "TRANSCRIPT"
    cmd = seen["cmd"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == Picks.model_json_schema()


def test_falls_back_to_result_text(claude, monkeypatch):
    _cli(monkeypatch, {"is_error": False,
                       "result": '```json\n{"picks": [{"id": "w1", "score": 5}]}\n```'})
    parsed, _ = llm_backend.generate_json("x", Picks)
    assert parsed["picks"][0]["id"] == "w1"


def test_cli_error_raises_with_its_reason(claude, monkeypatch):
    _cli(monkeypatch, {"is_error": True, "result": "Claude AI usage limit reached"})
    with pytest.raises(RuntimeError, match="usage limit"):
        llm_backend.generate_json("x", Picks)


def test_non_json_output_raises(claude, monkeypatch):
    _cli(monkeypatch, "command not found", returncode=127, stderr="claude: not found")
    with pytest.raises(RuntimeError, match="exit 127"):
        llm_backend.generate_json("x", Picks)


def test_schema_violation_raises(claude, monkeypatch):
    _cli(monkeypatch, {"is_error": False, "structured_output": {"picks": [{"id": "w1"}]}})
    with pytest.raises(Exception, match="validation error"):
        llm_backend.generate_json("x", Picks)


# --- the gta-clipper skill rides the system prompt -------------------------

def test_skill_body_is_appended_without_frontmatter(claude, monkeypatch):
    monkeypatch.delenv("LLM_SKILL", raising=False)
    prompt = llm_backend.system_prompt()
    assert prompt.startswith(llm_backend.SYSTEM_PROMPT)
    assert "# GTA VI clipper" in prompt
    assert "name: gta-clipper" not in prompt
    seen = _cli(monkeypatch, {"is_error": False, "structured_output": {"picks": []}})
    llm_backend.generate_json("x", Picks)
    cmd = seen["cmd"]
    assert cmd[cmd.index("--system-prompt") + 1] == prompt


def test_skill_can_be_turned_off_or_swapped(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_SKILL", "")
    assert llm_backend.system_prompt() == llm_backend.SYSTEM_PROMPT
    other = tmp_path / "SKILL.md"
    other.write_text("---\nname: x\n---\nCLIP CATS ONLY\n")
    monkeypatch.setenv("LLM_SKILL", str(other))
    assert llm_backend.system_prompt().endswith("CLIP CATS ONLY")
