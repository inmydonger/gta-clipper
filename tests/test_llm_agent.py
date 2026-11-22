"""The file-inbox provider in llm_backend (LLM_PROVIDER=agent)."""
import glob
import json
import os
import threading
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
def agent(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "agent")
    monkeypatch.setenv("LLM_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_TIMEOUT", "5")
    return tmp_path


def _answer(inbox, body):
    """Play the agent: wait for a request, check it, write an answer."""
    def run():
        while not glob.glob(os.path.join(inbox, "*.request.json")):
            pass
        req = glob.glob(os.path.join(inbox, "*.request.json"))[0]
        data = json.load(open(req))
        assert data["prompt"] == "rank these" and "properties" in data["schema"]
        with open(req.replace(".request.", ".response."), "w") as f:
            f.write(body)
    t = threading.Thread(target=run)
    t.start()
    return t


def test_agent_answer_is_validated_and_files_cleaned(agent):
    assert llm_backend.active()
    t = _answer(agent, json.dumps({"picks": [{"id": "w1", "score": 80}]}))
    parsed, cost = llm_backend._agent_json("rank these", Picks, poll=0.01)
    t.join()
    assert parsed == {"picks": [{"id": "w1", "score": 80}]}
    assert cost["total_cost"] == 0.0
    assert os.listdir(agent) == []


def test_agent_wrong_shape_times_out(agent, monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT", "0.3")
    t = _answer(agent, json.dumps({"nope": 1}))
    with pytest.raises(RuntimeError, match="invalid answer"):
        llm_backend._agent_json("rank these", Picks, poll=0.01)
    t.join()
    assert os.listdir(agent) == []
