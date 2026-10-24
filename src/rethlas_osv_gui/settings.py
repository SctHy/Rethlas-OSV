"""Persisted GUI settings and exact CLI argument construction."""

from __future__ import annotations

import copy
from pathlib import Path

from osverifier.backend import DEFAULT_MODEL
from osverifier.frozen import python_module_command
from osverifier.storage import atomic_json, read_json

from rethlas_osv.claude import DEFAULT_CLAUDE_MODEL

BACKENDS = ("codex", "claude")
# Each backend's default model; switching a backend swaps an untouched default.
BACKEND_DEFAULT_MODELS = {"codex": DEFAULT_MODEL, "claude": DEFAULT_CLAUDE_MODEL}
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
WEB_SEARCH_MODES = ("disabled", "cached", "live")
SEARCH_POLICIES = ("alternating", "live", "disabled")

# Every value here maps onto an explicit flag of ``osverify verify`` or
# ``rethlas-osv``. Defaults mirror the CLI defaults so a GUI run and a terminal
# run with no overrides freeze identical configuration, except that a GUI review
# keeps going after the first major mistake unless the user asks it to stop.
DEFAULTS: dict = {
    "codex_bin": "codex",
    "codex_home": "",
    "claude_bin": "claude",
    "claude_config_dir": "",
    "verify": {
        "model": DEFAULT_MODEL,
        "reasoning_effort": "xhigh",
        "web_search": "live",
        "timeout_seconds": 900.0,
        "workers": 16,
        "max_issues": None,
        "max_attempts": 2,
        "decompose": True,
        "early_stop": False,
    },
    "prove": {
        "generator_backend": "codex",
        "verifier_backend": "codex",
        "generator_model": DEFAULT_MODEL,
        "generator_reasoning_effort": "xhigh",
        "verifier_model": DEFAULT_MODEL,
        "verifier_reasoning_effort": "xhigh",
        "max_rounds": 10,
        "generation_branches": 1,
        "search_policy": "alternating",
        "verifier_web_search": "live",
        "generator_timeout": 0.0,
        "verifier_timeout": 900.0,
        "agent_threads": 4,
        "agent_depth": 2,
        "workers": 16,
        "max_issues": None,
        "max_attempts": 2,
        "decompose": True,
        "early_stop": True,
    },
}


def _int(section: str, key: str, value, *, minimum: int) -> int:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError(f"{section}.{key} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{section}.{key} must be an integer") from exc
    if number < minimum:
        raise ValueError(f"{section}.{key} must be at least {minimum}")
    return number


def _optional_int(section: str, key: str, value, *, minimum: int) -> int | None:
    if value is None or value == "" or value == 0 or value == "0":
        return None
    return _int(section, key, value, minimum=minimum)


def _float(section: str, key: str, value, *, minimum: float) -> float:
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError(f"{section}.{key} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{section}.{key} must be a number") from exc
    if number < minimum:
        raise ValueError(f"{section}.{key} must be at least {minimum:g}")
    return number


def _choice(section: str, key: str, value, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise ValueError(f"{section}.{key} must be one of {', '.join(choices)}")
    return value


def _text(section: str, key: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{section}.{key} must be a nonblank string")
    return value.strip()


def _bool(section: str, key: str, value) -> bool:
    if isinstance(value, bool):
        return value
    if value in ("true", "1", 1):
        return True
    if value in ("false", "0", 0):
        return False
    raise ValueError(f"{section}.{key} must be true or false")


def validate_settings(raw: dict) -> dict:
    """Return a normalized copy; raise ValueError naming the first invalid field."""
    if not isinstance(raw, dict):
        raise ValueError("settings must be an object")
    merged = copy.deepcopy(DEFAULTS)
    for key in ("codex_bin", "codex_home", "claude_bin", "claude_config_dir"):
        if key in raw:
            merged[key] = raw[key]
    for section in ("verify", "prove"):
        incoming = raw.get(section, {})
        if not isinstance(incoming, dict):
            raise ValueError(f"{section} settings must be an object")
        unknown = set(incoming) - set(DEFAULTS[section])
        if unknown:
            raise ValueError(f"unknown {section} setting: {sorted(unknown)[0]}")
        merged[section].update(incoming)
    for key in ("codex_bin", "claude_bin"):
        if not isinstance(merged[key], str) or not merged[key].strip():
            raise ValueError(f"{key} must be a nonblank string")
        merged[key] = merged[key].strip()
    for key in ("codex_home", "claude_config_dir"):
        value = "" if merged[key] is None else merged[key]
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        merged[key] = value.strip()

    v = merged["verify"]
    merged["verify"] = {
        "model": _text("verify", "model", v["model"]),
        "reasoning_effort": _choice(
            "verify", "reasoning_effort", v["reasoning_effort"], REASONING_EFFORTS
        ),
        "web_search": _choice("verify", "web_search", v["web_search"], WEB_SEARCH_MODES),
        "timeout_seconds": _float("verify", "timeout_seconds", v["timeout_seconds"], minimum=1),
        "workers": _int("verify", "workers", v["workers"], minimum=1),
        "max_issues": _optional_int("verify", "max_issues", v["max_issues"], minimum=1),
        "max_attempts": _int("verify", "max_attempts", v["max_attempts"], minimum=1),
        "decompose": _bool("verify", "decompose", v["decompose"]),
        "early_stop": _bool("verify", "early_stop", v["early_stop"]),
    }
    p = merged["prove"]
    for role in ("generator", "verifier"):
        backend = _choice("prove", f"{role}_backend", p[f"{role}_backend"], BACKENDS)
        # A model left at the other backend's default follows the backend switch.
        others = {m for b, m in BACKEND_DEFAULT_MODELS.items() if b != backend}
        if p[f"{role}_model"] in others:
            p[f"{role}_model"] = BACKEND_DEFAULT_MODELS[backend]
    merged["prove"] = {
        "generator_backend": p["generator_backend"],
        "verifier_backend": p["verifier_backend"],
        "generator_model": _text("prove", "generator_model", p["generator_model"]),
        "generator_reasoning_effort": _choice(
            "prove",
            "generator_reasoning_effort",
            p["generator_reasoning_effort"],
            REASONING_EFFORTS,
        ),
        "verifier_model": _text("prove", "verifier_model", p["verifier_model"]),
        "verifier_reasoning_effort": _choice(
            "prove", "verifier_reasoning_effort", p["verifier_reasoning_effort"], REASONING_EFFORTS
        ),
        "max_rounds": _int("prove", "max_rounds", p["max_rounds"], minimum=1),
        "generation_branches": _int(
            "prove", "generation_branches", p["generation_branches"], minimum=1
        ),
        "search_policy": _choice("prove", "search_policy", p["search_policy"], SEARCH_POLICIES),
        "verifier_web_search": _choice(
            "prove", "verifier_web_search", p["verifier_web_search"], WEB_SEARCH_MODES
        ),
        "generator_timeout": _float(
            "prove", "generator_timeout", p["generator_timeout"], minimum=0
        ),
        "verifier_timeout": _float("prove", "verifier_timeout", p["verifier_timeout"], minimum=1),
        "agent_threads": _int("prove", "agent_threads", p["agent_threads"], minimum=1),
        "agent_depth": _int("prove", "agent_depth", p["agent_depth"], minimum=0),
        "workers": _int("prove", "workers", p["workers"], minimum=1),
        "max_issues": _optional_int("prove", "max_issues", p["max_issues"], minimum=1),
        "max_attempts": _int("prove", "max_attempts", p["max_attempts"], minimum=1),
        "decompose": _bool("prove", "decompose", p["decompose"]),
        "early_stop": _bool("prove", "early_stop", p["early_stop"]),
    }
    for section in ("verify", "prove"):
        if not merged[section]["decompose"] and not merged[section]["early_stop"]:
            raise ValueError(f"{section}: disabling early stop requires the decomposed workflow")
    return merged


def load_settings(path: Path) -> dict:
    """Validated settings from disk; ValueError names what is wrong with the file."""
    if not path.exists():
        return validate_settings({})
    try:
        raw = read_json(path)
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON") from exc
    return validate_settings(raw)


def save_settings(path: Path, raw: dict) -> dict:
    settings = validate_settings(raw)
    atomic_json(path, settings)
    return settings


def _common(settings: dict) -> list[str]:
    argv = ["--codex-bin", settings["codex_bin"]]
    if settings["codex_home"]:
        argv += ["--codex-home", settings["codex_home"]]
    return argv


def verify_argv(
    run_dir: Path, settings: dict, question: Path, solution: Path, request_id: str
) -> list[str]:
    """The exact ``osverify verify`` command for a GUI run."""
    v = settings["verify"]
    argv = [
        *python_module_command("osverifier.cli"),
        "verify",
        "--question",
        str(question),
        "--solution",
        str(solution),
        "--request-id",
        request_id,
        "--run-dir",
        str(run_dir),
        "--model",
        v["model"],
        *_common(settings),
        "--reasoning-effort",
        v["reasoning_effort"],
        "--timeout-seconds",
        str(v["timeout_seconds"]),
        "--web-search",
        v["web_search"],
        "--workers",
        str(v["workers"]),
        "--max-attempts",
        str(v["max_attempts"]),
    ]
    if v["max_issues"] is not None:
        argv += ["--max-issues", str(v["max_issues"])]
    if v["decompose"]:
        argv.append("--decompose")
        if not v["early_stop"]:
            argv.append("--no-early-stop")
    return argv


def paper_argv(
    run_dir: Path, settings: dict, paper: Path, mode: str, tags: list[str], request_id: str
) -> list[str]:
    """Extract the question from one paper, then exec the exact ``osverify verify`` command."""
    v = settings["verify"]
    inputs = paper.parent
    verify = verify_argv(
        run_dir, settings, inputs / "question.md", inputs / "solution.md", request_id
    )
    verify += ["--review-type", "paper"]
    if "--no-early-stop" not in verify and v["decompose"]:
        verify.append("--no-early-stop")
    argv = [
        *python_module_command("osverifier_reports.paper_prepare"),
        "--run-dir",
        str(run_dir),
        "--paper",
        str(paper),
        "--mode",
        mode,
    ]
    if tags:
        argv += ["--tags", *tags]
    argv += [
        "--model",
        v["model"],
        *_common(settings),
        "--reasoning-effort",
        v["reasoning_effort"],
        "--timeout-seconds",
        str(v["timeout_seconds"]),
        "--max-attempts",
        str(v["max_attempts"]),
        "--",
        *verify,
    ]
    return argv


def prove_argv(run_dir: Path, settings: dict, problem: Path) -> list[str]:
    """The exact ``rethlas-osv`` command for a GUI run."""
    p = settings["prove"]
    argv = [
        *python_module_command("rethlas_osv.cli"),
        "--problem",
        str(problem),
        "--run-dir",
        str(run_dir),
        "--generator-model",
        p["generator_model"],
        "--verifier-model",
        p["verifier_model"],
        "--generator-reasoning-effort",
        p["generator_reasoning_effort"],
        "--verifier-reasoning-effort",
        p["verifier_reasoning_effort"],
        *_common(settings),
        "--max-rounds",
        str(p["max_rounds"]),
        "--generation-branches",
        str(p["generation_branches"]),
        "--generator-timeout",
        str(p["generator_timeout"]),
        "--verifier-timeout",
        str(p["verifier_timeout"]),
        "--search-policy",
        p["search_policy"],
        "--verifier-web-search",
        p["verifier_web_search"],
        "--agent-threads",
        str(p["agent_threads"]),
        "--agent-depth",
        str(p["agent_depth"]),
        "--workers",
        str(p["workers"]),
        "--max-attempts",
        str(p["max_attempts"]),
    ]
    if p["max_issues"] is not None:
        argv += ["--max-issues", str(p["max_issues"])]
    argv += ["--generator-backend", p["generator_backend"], "--verifier-backend", p["verifier_backend"]]
    if "claude" in (p["generator_backend"], p["verifier_backend"]):
        argv += ["--claude-bin", settings["claude_bin"]]
        if settings["claude_config_dir"]:
            argv += ["--claude-config-dir", settings["claude_config_dir"]]
    if not p["decompose"]:
        argv.append("--root-only")
    elif not p["early_stop"]:
        argv.append("--no-early-stop")
    return argv


def with_max_rounds(argv: list[str], max_rounds: int) -> list[str]:
    """Resume a prove run with a deeper round budget; every other argument is frozen."""
    if max_rounds < 1:
        raise ValueError("max_rounds must be positive")
    updated = list(argv)
    if "--max-rounds" not in updated[:-1]:
        raise ValueError("the stored command has no round budget")
    index = updated.index("--max-rounds")
    updated[index + 1] = str(max_rounds)
    return updated
