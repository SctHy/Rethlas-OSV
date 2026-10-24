"""Claude Code transport: isolation flags, structured output, sessions, quota and stops.

A scripted fake ``claude`` emits stream-json events, so these tests pin the transport
contracts. They say nothing about proof or review quality.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import time

import pytest
from osverifier.backend import BackendCancelled
from osverifier.quota import BackendStalled, QuotaExhausted
from osverifier.storage import read_json

from rethlas_osv import cli
from rethlas_osv.claude import (
    CLAUDE_RESUME_MESSAGE,
    DEFAULT_CLAUDE_MODEL,
    ClaudeBackend,
    ClaudeGenerator,
    quota_from_claude_event,
)

FAKE_CLAUDE = """#!{python}
import json, os, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.287 (Claude Code)")
    sys.exit(0)
scenario = os.environ.get("FAKE_CLAUDE", "ok")
sys.stdin.read()
session = args[args.index("--resume") + 1] if "--resume" in args else "session-new"
if scenario == "other-session":
    session = "session-other"
def emit(event):
    print(json.dumps(event), flush=True)
emit({{"type": "system", "subtype": "init", "session_id": session, "model": "fixture"}})
if scenario == "quota":
    emit({{"type": "rate_limit_event", "rate_limit_info": {{"status": "rejected",
          "rateLimitType": "five_hour", "resetsAt": 1, "isUsingOverage": False}}}})
    time.sleep(30)
if scenario == "hang":
    time.sleep(30)
if scenario == "error":
    emit({{"type": "result", "subtype": "success", "is_error": True, "result": "API Error: 500"}})
    sys.exit(1)
output = json.loads(os.environ.get("FAKE_OUTPUT", "{{}}"))
emit({{"type": "result", "subtype": "success", "is_error": False, "session_id": session,
      "structured_output": output, "usage": {{"output_tokens": 1}}}})
"""


@pytest.fixture
def claude_bin(tmp_path):
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    path.write_text(FAKE_CLAUDE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def flag(argv, name):
    return argv[argv.index(name) + 1]


def test_generator_command_is_isolated_and_follows_the_turn_search_setting(tmp_path, claude_bin):
    generator = ClaudeGenerator(model="fixture", claude_bin=str(claude_bin))
    workspace, attempt = tmp_path / "ws", tmp_path / "attempt"
    attempt.mkdir()
    live = generator.command(workspace, attempt, None, "live")
    for isolation in ("--restricted", "--strict-mcp-config", "--disable-slash-commands"):
        assert isolation in live
    assert flag(live, "--setting-sources") == "" and flag(live, "--permission-mode") == "dontAsk"
    settings = json.loads(flag(live, "--settings"))
    assert settings["autoMemoryEnabled"] is False and settings["sandbox"]["enabled"] is True
    assert settings["sandbox"]["allowUnsandboxedCommands"] is False
    assert settings["sandbox"]["network"]["allowedDomains"] == ["*"]
    assert "WebSearch" in flag(live, "--tools") and "Task" in flag(live, "--tools")
    assert "--resume" not in live and "subgoal-prover" in flag(live, "--agents")
    mcp = read_json(attempt / "mcp.json")["mcpServers"]["rethlas"]
    assert mcp["args"][-2:] == ["--workspace", str(workspace)]
    assert mcp["env"] == {"RETHLAS_ALLOW_SEARCH": "1", "PYTHONUTF8": "1"}
    assert "--dangerously-skip-permissions" not in live

    # Independent-thinking turns lose web tools, theorem search and shell network.
    quiet = ClaudeGenerator(model="fixture", claude_bin=str(claude_bin), agent_depth=0)
    offline = quiet.command(workspace, attempt, "session-1", "disabled")
    assert json.loads(flag(offline, "--settings"))["sandbox"]["network"]["allowedDomains"] == []
    assert "Web" not in flag(offline, "--tools") and "Task" not in flag(offline, "--tools")
    assert "--agents" not in offline and flag(offline, "--resume") == "session-1"
    assert flag(offline, "--effort") == "xhigh"
    (tmp_path / "revising").mkdir()
    assert flag(quiet.command(workspace, tmp_path / "revising", "session-1", "live", "max"), "--effort") == "max"
    assert read_json(attempt / "mcp.json")["mcpServers"]["rethlas"]["env"]["RETHLAS_ALLOW_SEARCH"] == "0"


def test_generator_turn_returns_structured_output_and_checks_the_session(
    tmp_path, claude_bin, monkeypatch
):
    monkeypatch.setenv("FAKE_OUTPUT", json.dumps({"candidate_ready": True, "progress": "done"}))
    generator = ClaudeGenerator(model="fixture", claude_bin=str(claude_bin))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    turn, session = generator.generate(workspace, tmp_path / "a1", "prompt", None, "live")
    assert turn.candidate_ready and session == "session-new"
    assert read_json(tmp_path / "a1/raw-output.json")["progress"] == "done"
    turn, session = generator.generate(workspace, tmp_path / "a2", "prompt", "session-new", "live")
    assert session == "session-new"
    monkeypatch.setenv("FAKE_CLAUDE", "other-session")
    with pytest.raises(ValueError, match="session"):
        generator.generate(workspace, tmp_path / "a3", "prompt", "session-new", "live")


def test_explicit_usage_limit_stops_the_call_and_latches(tmp_path, claude_bin, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "quota")
    backend = ClaudeBackend(model="fixture", claude_bin=str(claude_bin), web_search="disabled")
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    started = time.monotonic()
    with pytest.raises(QuotaExhausted) as raised:
        backend.generate("proposer", {"request": {}}, {"type": "object"}, attempt)
    assert time.monotonic() - started < 20
    assert str(raised.value) == CLAUDE_RESUME_MESSAGE
    assert read_json(attempt / "interruption.json")["reason"] == "quota_exhausted"
    # No later call starts on this backend once quota has latched.
    second = tmp_path / "second"
    second.mkdir()
    with pytest.raises(QuotaExhausted):
        backend.generate("proposer", {"request": {}}, {"type": "object"}, second)
    assert not (second / "process-start.json").exists()


def test_only_explicit_signals_count_as_quota():
    assert quota_from_claude_event({"type": "assistant", "message": "usage limit reached"}) is None
    assert quota_from_claude_event(
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}}
    ) is None
    assert quota_from_claude_event(
        {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "isUsingOverage": True}}
    ) is None
    assert quota_from_claude_event({"type": "result", "is_error": True, "result": "Overloaded"}) is None
    assert quota_from_claude_event(
        {"type": "result", "is_error": True, "result": "You've hit your limit · resets 3pm"}
    ) is not None


def test_review_role_runs_read_only_in_a_fresh_workspace(tmp_path, claude_bin, monkeypatch):
    monkeypatch.setenv("FAKE_OUTPUT", json.dumps({"issues": []}))
    backend = ClaudeBackend(model="fixture", claude_bin=str(claude_bin), web_search="cached")
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    payload = {"request": {"question": "Q", "candidate_solution": "S"}, "numbered_solution": "1 S"}
    assert backend.generate("proposer", payload, {"type": "object"}, attempt) == {"issues": []}
    workspace = attempt / "workspace"
    assert (workspace / ".agents/skills/propose-solution-issues/SKILL.md").is_file()
    assert (workspace / "question.md").read_text() == "Q"
    argv = read_json(attempt / "command.json")["argv"]
    assert flag(argv, "--tools") == "Read,Glob,Grep,Bash,WebSearch"
    assert "--no-session-persistence" in argv
    assert json.loads(flag(argv, "--settings"))["sandbox"]["network"]["allowedDomains"] == []
    assert backend.identity["kind"] == "claude_code"

    monkeypatch.setenv("FAKE_CLAUDE", "error")
    failed = tmp_path / "failed"
    failed.mkdir()
    with pytest.raises(RuntimeError, match="API Error: 500"):
        backend.generate("proposer", payload, {"type": "object"}, failed)


def test_cancel_stops_the_call_and_a_fresh_instance_still_works(tmp_path, claude_bin, monkeypatch):
    import threading
    from dataclasses import replace

    monkeypatch.setenv("FAKE_CLAUDE", "hang")
    backend = ClaudeBackend(model="fixture", claude_bin=str(claude_bin))
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    threading.Timer(1.0, backend.cancel).start()
    with pytest.raises(BackendCancelled):
        backend.generate("proposer", {"request": {}}, {"type": "object"}, attempt)
    pid = read_json(attempt / "process-start.json")["pid"]
    assert subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode != 0

    monkeypatch.setenv("FAKE_CLAUDE", "ok")
    fresh = replace(backend)
    other = tmp_path / "other"
    other.mkdir()
    assert fresh.generate("proposer", {"request": {}}, {"type": "object"}, other) == {}
    assert fresh.identity == backend.identity


def test_idle_review_call_is_reported_as_stalled(tmp_path, claude_bin, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "hang")
    backend = ClaudeBackend(model="fixture", claude_bin=str(claude_bin), idle_timeout_seconds=1)
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    with pytest.raises(BackendStalled):
        backend.generate("proposer", {"request": {}}, {"type": "object"}, attempt)
    assert read_json(attempt / "stall.json")["reason"] == "no_progress_timeout"


def test_cli_freezes_the_claude_backends_with_claude_default_models(tmp_path, claude_bin, capsys):
    problem = tmp_path / "problem.md"
    problem.write_text("Show that 1 = 1.")
    code = cli.main(
        [
            "--problem", str(problem), "--run-dir", str(tmp_path / "run"), "--prepare-only",
            "--generator-backend", "claude", "--verifier-backend", "claude",
            "--claude-bin", str(claude_bin),
        ]
    )
    assert code == 0, capsys.readouterr().err
    provenance = read_json(tmp_path / "run/manifest.json")["provenance"]
    assert provenance["generator"]["kind"] == "claude_code_generator"
    assert provenance["generator"]["model"] == DEFAULT_CLAUDE_MODEL
    assert provenance["verifier"]["backend"]["kind"] == "claude_code"
    assert provenance["verifier"]["backend"]["model"] == DEFAULT_CLAUDE_MODEL
    # A revision effort below the base effort is refused before any run starts.
    assert cli.main([
        "--problem", str(problem), "--run-dir", str(tmp_path / "other"), "--prepare-only",
        "--generator-backend", "claude", "--claude-bin", str(claude_bin),
        "--generator-reasoning-effort", "high", "--generator-revision-effort", "low",
    ]) == 1
    assert "cannot be below" in capsys.readouterr().err


def test_review_prompt_inlines_each_document_once_and_shares_a_role_system_prompt(
    tmp_path, claude_bin, monkeypatch
):
    monkeypatch.setenv("FAKE_OUTPUT", json.dumps({"issues": []}))
    backend = ClaudeBackend(model="fixture", claude_bin=str(claude_bin), web_search="disabled")
    solution = "Line one of the proof.\nLine two, QED."
    payload = {
        "request": {"question": "Show Q.", "candidate_solution": solution},
        "numbered_solution": "1: Line one of the proof.\n2: Line two, QED.",
        "focus_text": solution,
        "scope": {"scope_id": "root"},
    }
    prompts = []
    for name in ("first", "second"):
        attempt = tmp_path / name
        attempt.mkdir()
        backend.generate("proposer", payload, {"type": "object"}, attempt)
        argv = read_json(attempt / "command.json")["argv"]
        assert flag(argv, "--system-prompt-file") == str(attempt / "system-prompt.md")
        prompts.append(((attempt / "system-prompt.md").read_text(), (attempt / "prompt.txt").read_text()))
    (system, prompt), (system_again, _) = prompts
    # Same role, same system prompt: later calls read it from the prompt cache.
    assert system == system_again and "<policy>" in system and "propose-solution-issues" in system
    assert prompt.count("Line two, QED.") == 1, "each document once"
    inlined = json.loads(prompt.split('<file name="input.json">\n')[1].split("\n</file>")[0])
    assert inlined["request"]["candidate_solution"] == "<the text of candidate-solution.md>"
    assert inlined["scope"] == {"scope_id": "root"}
    assert "(identical to candidate-solution.md)" in prompt
    # The files stay on disk as the call's record.
    assert (tmp_path / "first/workspace/candidate-solution.md").read_text() == solution


def test_compute_launcher_runs_scripts_inline_code_and_stdin(tmp_path):
    from rethlas_osv import compute
    from rethlas_osv.runner import Experiment

    class Generator:
        identity = {"model": "fixture", "reasoning_effort": "low"}

    workspace = tmp_path / "ws"
    Experiment(Generator(), None).prepare_workspace(workspace, tmp_path, "Q", {"references": {}})
    launcher = workspace / "tools/python"
    assert launcher.stat().st_mode & stat.S_IXUSR and (workspace / "scratch").is_dir()
    script = workspace / "scratch/check.py"
    script.write_text("import sys, sympy\nx = sympy.symbols('x')\nprint(sympy.factor(x**2 - 1), sys.argv[1:])\n")
    def run(*args, **options):
        return subprocess.run([str(launcher), *args], cwd=workspace, capture_output=True, text=True, **options)

    assert run("scratch/check.py", "a").stdout.strip() == "(x - 1)*(x + 1) ['a']"
    assert run("-c", "import networkx as nx; print(nx.cycle_graph(5).number_of_edges())").stdout.strip() == "5"
    assert run("-", input="import numpy; print(int(numpy.eye(3).trace()))").stdout.strip() == "3"
    assert run().returncode == 2
    assert all(compute.environment().values()), "sympy, networkx and numpy are installed"
