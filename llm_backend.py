"""Text-only LLM backend for the moment picker: any OpenAI-compatible server.

Ollama, LM Studio, vLLM, llama.cpp's server, LocalAI and OpenRouter all speak
``POST {base}/chat/completions``. When ``LLM_BASE_URL`` is set (or
``LLM_PROVIDER=openai``), the transcript scoring and detail passes in
``main.get_viral_clips`` go here instead of Gemini, so a self-hosted install
can run the whole pipeline without a Google key.

What stays on Gemini, because it needs a model that can look at frames or a
video file: the layout picker (``layout_picker.py``), the on-screen content
detector (``screencast_layout.py``) and the silent-video path
(``main.get_visual_clips``). Without a Gemini key those degrade the way they
already did: the first two return "none", the third fails the job with a clear
message. Nothing in this module is imported by them.

Structured output: the prompts already spell out the exact JSON shape, so a
plain ``json_object`` mode is enough for most models. The request first asks
for ``json_schema`` (Ollama, vLLM, llama.cpp and LM Studio enforce it); a
server that rejects that field gets the same request again with
``json_object``, then with no ``response_format`` at all. Whatever comes back
is validated with the same pydantic model Gemini's ``response_schema`` uses,
so ``main.py`` sees one shape regardless of provider.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Optional, Tuple, Type

import httpx
from pydantic import BaseModel

DEFAULT_MODEL = "llama3.1:8b"
CLAUDE_DEFAULT_MODEL = "sonnet"
DEFAULT_TIMEOUT = 600.0  # local models on CPU are slow; a scoring batch can take minutes
SYSTEM_PROMPT = "You answer with a single JSON object and nothing else."
SKILL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "skills", "gta-clipper", "SKILL.md")


def system_prompt() -> str:
    """SYSTEM_PROMPT plus the clipping skill's body (frontmatter stripped), so
    the niche rules ride every scoring/detail call on top of the generic
    prompt. ``LLM_SKILL`` points at another skill file; ``LLM_SKILL=`` turns
    it off."""
    path = os.environ.get("LLM_SKILL", SKILL_PATH)
    if not path or not os.path.isfile(path):
        return SYSTEM_PROMPT
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return f"{SYSTEM_PROMPT}\n\n{text.strip()}"


def provider() -> str:
    """``"claude"`` (headless Claude Code CLI, ``LLM_PROVIDER=claude``),
    ``"openai"`` when a compatible endpoint is configured, else ``"gemini"``."""
    explicit = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if explicit == "claude":
        return "claude"
    if explicit in ("openai", "ollama", "local", "openai-compatible"):
        return "openai"
    if explicit == "gemini":
        return "gemini"
    return "openai" if base_url() else "gemini"


def base_url() -> str:
    return (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")


def model_name() -> str:
    default = CLAUDE_DEFAULT_MODEL if provider() == "claude" else DEFAULT_MODEL
    return (os.environ.get("LLM_MODEL") or "").strip() or default


def active() -> bool:
    """True when the moment picker should call this module instead of Gemini."""
    return provider() == "claude" or (provider() == "openai" and bool(base_url()))


def describe() -> Optional[dict]:
    """What ``/api/config`` tells the dashboard, or ``None`` when inactive."""
    if not active():
        return None
    if provider() == "claude":
        return {"provider": "claude", "model": model_name(), "baseUrl": "claude CLI"}
    return {"provider": "openai", "model": model_name(), "baseUrl": base_url()}


def _timeout() -> float:
    try:
        return float(os.environ.get("LLM_TIMEOUT") or DEFAULT_TIMEOUT)
    except ValueError:
        return DEFAULT_TIMEOUT


def _headers() -> dict:
    # Ollama ignores the key but the OpenAI client convention (and vLLM with
    # --api-key) wants the header present; "ollama" is the documented placeholder.
    key = (os.environ.get("LLM_API_KEY") or "ollama").strip()
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _client(**kwargs) -> httpx.Client:
    """Factory so tests can swap in ``httpx.MockTransport``."""
    return httpx.Client(timeout=_timeout(), **kwargs)


def _response_formats(schema: Type[BaseModel]):
    name = getattr(schema, "__name__", "response").lower()
    yield {"type": "json_schema",
           "json_schema": {"name": name, "schema": schema.model_json_schema()}}
    yield {"type": "json_object"}
    yield None


def _is_format_rejection(resp: httpx.Response) -> bool:
    if resp.status_code not in (400, 422):
        return False
    body = resp.text.lower()
    return "response_format" in body or "json_schema" in body or "json_object" in body \
        or "format" in body


def generate_json(prompt: str, schema: Type[BaseModel], model: Optional[str] = None,
                  ) -> Tuple[dict, Optional[dict]]:
    """One chat completion that must come back as JSON matching ``schema``.

    Returns ``(parsed_dict, cost_analysis)`` in the exact shape
    ``main._run_gemini_stage`` returns, so the caller does not branch on the
    provider. Raises on HTTP errors, empty bodies and schema violations; the
    retry policy lives in the caller, same as for Gemini.
    """
    import gemini_worker  # local import: keeps this module free of the google SDK

    if provider() == "claude":
        return _claude_json(prompt, schema, model or model_name())

    url = f"{base_url()}/chat/completions"
    model = model or model_name()
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": prompt},
    ]
    last_rejection: Optional[str] = None
    with _client() as client:
        for fmt in _response_formats(schema):
            body = {"model": model, "messages": messages, "temperature": 0.2, "stream": False}
            if fmt is not None:
                body["response_format"] = fmt
            resp = client.post(url, json=body, headers=_headers())
            if fmt is not None and _is_format_rejection(resp):
                # The server does not know this response_format flavour; the
                # next loop iteration asks for a looser one.
                last_rejection = resp.text[:200]
                continue
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"LLM server {resp.status_code} from {url}: {resp.text[:300]}")
            data = resp.json()
            break
        else:
            raise RuntimeError(
                f"LLM server rejected every response_format variant: {last_rejection}")

    choices = data.get("choices") or []
    text = ""
    if choices:
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        if isinstance(text, list):  # some servers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    parsed = gemini_worker._parse_json_response_text(text)
    # Validate against the same schema Gemini enforces server-side, so a
    # local model that drops a field fails here with a readable error
    # (retried by the caller) instead of deep inside the clip pipeline.
    validated = schema.model_validate(parsed).model_dump()

    usage = data.get("usage") or {}
    cost = {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "thinking_tokens": 0,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
        "model": model,
        "price_estimated": False,
        "local": True,
    }
    return validated, cost


def _claude_cmd(schema: Type[BaseModel], model: str) -> list:
    """Headless Claude Code on the user's own plan: no tools, no MCP, no
    user settings or hooks, no saved session. The prompt goes on stdin
    (a long transcript would overflow argv)."""
    return [os.environ.get("CLAUDE_BIN") or "claude", "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema.model_json_schema()),
            "--model", model,
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--setting-sources", "",
            "--system-prompt", system_prompt()]


def _claude_json(prompt: str, schema: Type[BaseModel], model: str
                 ) -> Tuple[dict, Optional[dict]]:
    """``generate_json`` over ``claude -p``. Errors raise with the CLI's own
    message, so main's retry policy sees 'overloaded' / '529' / '500' as
    transient. A usage-limit hit is not transient: the job fails with the
    CLI's reason, and the transcript checkpoint makes the rerun cheap.
    ponytail: no wait-for-reset loop; add one if limit hits become routine."""
    import gemini_worker

    proc = subprocess.run(_claude_cmd(schema, model), input=prompt,
                          capture_output=True, text=True, timeout=_timeout())
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f"claude CLI exit {proc.returncode}: "
                           f"{(proc.stderr or proc.stdout)[:300]}")
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(data.get('result'))[:300]}")
    parsed = data.get("structured_output")
    if parsed is None:
        parsed = gemini_worker._parse_json_response_text(data.get("result") or "")
    validated = schema.model_validate(parsed).model_dump()

    usage = data.get("usage") or {}
    cost = {
        "input_tokens": int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "thinking_tokens": 0,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,  # billed to the Claude plan, not per token
        "model": model,
        "price_estimated": False,
        "local": True,
    }
    return validated, cost
