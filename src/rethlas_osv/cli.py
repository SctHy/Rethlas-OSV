from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from osverifier.backend import DEFAULT_MODEL, CodexBackend
from osverifier.pipeline import RunConfig, Verifier
from osverifier.workflow import DecomposingVerifier, WorkflowConfig

from .claude import DEFAULT_CLAUDE_MODEL, ClaudeBackend, ClaudeGenerator
from .encoding import text_encoding_warning, tolerate_console_encoding
from .generator import CodexGenerator
from .runner import REASONING_EFFORTS, ROOT, Experiment, LoopConfig


def parser():
    p = argparse.ArgumentParser(
        description="Run Rethlas generation with OSVerify feedback and a human handoff."
    )
    p.add_argument("--problem", type=Path, default=ROOT / "vendor/generation/data/example.md")
    p.add_argument(
        "--references",
        type=Path,
        help="Optional directory; defaults to the problem's sibling .refs directory if present",
    )
    p.add_argument("--run-dir", type=Path, required=True)
    backends = ("codex", "claude")
    p.add_argument("--generator-backend", choices=backends, default="codex", help="CLI that runs generation turns (default: codex)")
    p.add_argument("--verifier-backend", choices=backends, default="codex", help="CLI that runs OSVerify review roles (default: codex)")
    p.add_argument("--generator-model", help=f"default {DEFAULT_MODEL} (codex) or {DEFAULT_CLAUDE_MODEL} (claude); `osverify models` lists Codex choices")
    p.add_argument("--verifier-model", help=f"default {DEFAULT_MODEL} (codex) or {DEFAULT_CLAUDE_MODEL} (claude)")
    p.add_argument("--generator-reasoning-effort", choices=REASONING_EFFORTS, default="xhigh")
    p.add_argument(
        "--generator-revision-effort",
        choices=REASONING_EFFORTS,
        help="Effort for turns that answer a REVISE or REJECT review (default: unchanged); "
        "at least --generator-reasoning-effort",
    )
    p.add_argument("--verifier-reasoning-effort", default="xhigh")
    p.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    p.add_argument("--codex-home", type=Path)
    p.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN", "claude"))
    p.add_argument("--claude-config-dir", type=Path, help="Claude Code account folder (CLAUDE_CONFIG_DIR); default: your own")
    p.add_argument(
        "--max-rounds",
        type=int,
        default=10,
        help="Generation rounds per branch; may be increased on resume",
    )
    p.add_argument(
        "--generation-branches",
        type=int,
        default=1,
        help="Independent candidate sessions generated in parallel each round (default: 1)",
    )
    p.add_argument(
        "--generator-timeout",
        type=float,
        default=0,
        help="Optional time limit per generation turn in seconds; 0 disables it (default)",
    )
    p.add_argument("--verifier-idle-timeout", type=float, default=300, help="Retry a verifier call after this many seconds without item progress; 0 disables (default: 300). Silent reasoning can also reach this operational limit.")
    p.add_argument("--verifier-timeout", type=float, default=900, help="Per verifier role/attempt")
    p.add_argument(
        "--search-policy",
        choices=["alternating", "live", "disabled"],
        default="alternating",
        help="Generator search policy; verifier search is independently configured",
    )
    p.add_argument("--verifier-web-search", choices=["disabled", "cached", "live"], default="live")
    p.add_argument("--agent-threads", type=int, default=4)
    p.add_argument("--agent-depth", type=int, default=2, help="0 disables generation subagents")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--max-issues", type=int, default=None)
    p.add_argument("--max-attempts", type=int, default=2)
    p.add_argument(
        "--root-only",
        action="store_true",
        help="Use the legacy whole-solution verifier; default includes decomposition",
    )
    p.add_argument(
        "--no-early-stop",
        action="store_true",
        help="Continue reviewing remaining issues and segments after an audited REJECT; "
        "incompatible with --root-only; READY_FOR_HUMAN still stops the experiment",
    )
    p.add_argument(
        "--prepare-only",
        action="store_true",
        help="Freeze inputs and prepare the workspace without model calls",
    )
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    tolerate_console_encoding()
    warning = text_encoding_warning()
    if warning:
        print(f"warning: {warning}", file=sys.stderr, flush=True)
    try:
        if args.root_only and args.no_early_stop:
            raise ValueError("--no-early-stop cannot be combined with --root-only")
        if args.generator_revision_effort and REASONING_EFFORTS.index(
            args.generator_revision_effort
        ) < REASONING_EFFORTS.index(args.generator_reasoning_effort):
            raise ValueError("--generator-revision-effort cannot be below --generator-reasoning-effort")
        generator_options = dict(
            reasoning_effort=args.generator_reasoning_effort,
            timeout_seconds=args.generator_timeout or None,
            agent_threads=args.agent_threads,
            agent_depth=args.agent_depth,
        )
        if args.generator_backend == "claude":
            generator = ClaudeGenerator(
                model=args.generator_model or DEFAULT_CLAUDE_MODEL,
                claude_bin=args.claude_bin,
                config_dir=args.claude_config_dir,
                **generator_options,
            )
        else:
            generator = CodexGenerator(
                model=args.generator_model or DEFAULT_MODEL,
                codex_bin=args.codex_bin,
                codex_home=args.codex_home,
                **generator_options,
            )
        verifier_options = dict(
            reasoning_effort=args.verifier_reasoning_effort,
            timeout_seconds=args.verifier_timeout,
            idle_timeout_seconds=args.verifier_idle_timeout or None,
            web_search=args.verifier_web_search,
        )
        if args.verifier_backend == "claude":
            backend = ClaudeBackend(
                model=args.verifier_model or DEFAULT_CLAUDE_MODEL,
                claude_bin=args.claude_bin,
                config_dir=args.claude_config_dir,
                **verifier_options,
            )
        else:
            backend = CodexBackend(
                model=args.verifier_model or DEFAULT_MODEL,
                codex_bin=args.codex_bin,
                codex_home=args.codex_home,
                **verifier_options,
            )
        config = dict(
            workers=args.workers, max_issues=args.max_issues, max_attempts=args.max_attempts
        )
        verifier = (
            Verifier(backend, RunConfig(**config))
            if args.root_only
            else DecomposingVerifier(
                backend, WorkflowConfig(**config, early_stop_on_reject=not args.no_early_stop)
            )
        )
        references = args.references
        if references is None and args.problem.with_suffix(".refs").is_dir():
            references = args.problem.with_suffix(".refs")
        experiment = Experiment(
            generator,
            verifier,
            LoopConfig(
                args.max_rounds, args.search_policy, args.generation_branches,
                revision_effort=args.generator_revision_effort,
            ),
            event_sink=lambda row: print(json.dumps(row, ensure_ascii=False), flush=True),
        )
        result = experiment.run(
            args.problem, args.run_dir, references, prepare_only=args.prepare_only
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), flush=True)
        if args.prepare_only and result.status == "running":
            return 0
        return {"ready_for_human": 0, "budget_exhausted": 3}.get(result.status, 2)
    except KeyboardInterrupt:
        print("Interrupted; resume with the same run directory.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
