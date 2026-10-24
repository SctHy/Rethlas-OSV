# Rethlas-OSV

**Proof search with a one-sided verifier in the loop.** Rethlas-OSV runs
[Rethlas](https://github.com/frenzymath/Rethlas) proof generation and reviews every draft
with [OSVerify](https://github.com/OSVerify/OSVerify). Only feedback that OSVerify's
auditor approved word for word goes back to the generator, and a run stops at the first
draft handed off as `READY_FOR_HUMAN`. A person still makes the final call.

It also includes a local web GUI, for proving and for plain OSVerify reviews of solutions
and papers. Please report problems in [Issues](https://github.com/OSVerify/Rethlas-OSV/issues).

## Install and run

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and the
[Codex CLI](https://github.com/openai/codex) 0.162 or newer, signed in with your own account
(older CLIs cannot use current models such as gpt-6-astra). `uv sync` installs the
released OSVerify it depends on.

```bash
git clone https://github.com/OSVerify/Rethlas-OSV && cd Rethlas-OSV
uv sync
uv run rethlas-osv --problem problem.md --run-dir runs/my-problem
uv run rethlas-osv-gui   # local web GUI at http://127.0.0.1:8765
```

Without `--problem`, the bundled example (every finite group of prime order is cyclic)
is used. Both the generator and the verifier default to `gpt-6-astra`; choose others
with `--generator-model` and `--verifier-model` (the GUI suggests every model your Codex
account offers). Prove runs can also use [Claude Code](https://code.claude.com) for
generation, review, or both: `--generator-backend claude --verifier-backend claude`
(see the [guide](docs/guide.md#claude-code-backends)). `--generation-branches 4 --max-rounds 10` runs four independent proof attempts
with up to ten revision rounds each. Rerun the same command to resume.

## How a run works

1. Rethlas writes a candidate proof in its own workspace.
2. OSVerify reviews it: a complete draft as a full solution, an unfinished one as
   partial progress.
3. Only the exact audited feedback is passed back for the next round.
4. The run stops at the first `READY_FOR_HUMAN` draft, at the round limit, or when
   quota runs out (then resume with the same command).

## Limitations

- `READY_FOR_HUMAN` is a hand-off for human checking, not a proof certificate.
- Generator isolation is by instruction, not enforced. The generator works in a Codex
  `workspace-write` sandbox inside the run directory, next to the verifier's internal
  review records. It is only given audited feedback, but the sandbox can read any file
  your account can read, so it is able to read those records. A stronger boundary needs
  a read-restricted Codex sandbox.
- Only macOS is supported; there is no Linux or Windows build.

## Documentation

- [Guide](docs/guide.md): GUI, options, decisions, resume and budgets
- [How Rethlas was adapted](docs/rethlas-adaptation.md) and the
  [live smoke test](docs/rethlas-smoke-test.md)
- [Development notes](AGENTS.md)

## License

Apache License 2.0; see [LICENSE](LICENSE). Rethlas is by its contributors, also under
the Apache License 2.0; attribution is in [NOTICE.md](NOTICE.md).
