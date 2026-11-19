"""Exercise the actual manual terminal loop, including attachment input during a wait."""
import io
import json
import sqlite3
import sys
from contextlib import closing, redirect_stdout

from backend.agent.factory import build_runner
from backend.agent.model.llm import ScriptedLLM, tool_call
from scripts.agent import interactive as cli


def test_defendant_can_upload_while_answering_and_record_feedback(tmp_path, monkeypatch):
    material = tmp_path / "收到的材料.txt"
    material.write_text("对方主张维修费800元，附维修清单。", encoding="utf-8")
    def respond(messages, tools):
        assert any(m["role"] == "tool" and "维修费800元" in m["content"] for m in messages)
        assert any(m["role"] == "system" and "defendant" in m["content"] for m in messages)
        return tool_call("finish", answer="先逐项核对对方诉求和收到的清单。",
                         task_result={"delivered_text": "先逐项核对对方诉求和收到的清单。", "missing": []})
    model = ScriptedLLM([respond, tool_call("finish", answer="已记录实际提交，等待反馈。")])
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["interactive.py"])
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test")
    monkeypatch.setattr(cli, "make_chat_model", lambda **kwargs: model)
    monkeypatch.setattr(cli.CorpusSourceResolver, "from_directory", lambda: None)
    monkeypatch.setattr(cli.RetrievalService, "from_config", lambda: None)
    monkeypatch.setattr(cli, "build_runner", lambda **kwargs: build_runner(**kwargs, real_statutes=False, dotenv=None))
    inputs = iter(["/route litigation", f'/upload "{material}"', "我是被告，希望回应对方索赔。",
                   "/state", "/feedback submitted 我已递交答辩材料", "/state", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    capture = io.StringIO()
    with redirect_stdout(capture):
        cli.main()
    assert "运行异常" not in capture.getvalue()
    db_path, = tmp_path.rglob("cases.sqlite3")
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM case_files").fetchone()[0] == 1
        rows = db.execute("SELECT state_json,thread_id FROM agent_runs ORDER BY rowid").fetchall()
        assert len(rows) == 2 and rows[0][1] == rows[1][1]
        assert all(json.loads(row[0])["status"] == "completed" for row in rows)
    assert '"role": "defendant"' in capture.getvalue()
    assert "已记录实际提交，等待受理或补正反馈" in capture.getvalue()
