"""Python for the generator's computational checks: ``tools/python`` in its workspace.

Runs a script (``tools/python check.py ARGS``), inline code (``tools/python -c CODE``) or
standard input (``tools/python -``) in this interpreter, where sympy, networkx and
numpy are installed. Results are evidence for the generator, never part of the
verifier's input.
"""

from __future__ import annotations

import runpy
import sys
from importlib.metadata import PackageNotFoundError, version

LIBRARIES = ("sympy", "networkx", "numpy")
USAGE = "usage: tools/python SCRIPT [ARGS...] | -c CODE [ARGS...] | - (code on stdin)"


def environment() -> dict:
    """Library versions, recorded in the generator identity (None when missing)."""
    found = {}
    for name in LIBRARIES:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            found[name] = None
    return found


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(USAGE, file=sys.stderr)
        return 0 if args else 2
    sys.dont_write_bytecode = True  # the sandbox may not write outside the workspace
    if args[0] == "-c":
        if len(args) < 2:
            print(USAGE, file=sys.stderr)
            return 2
        sys.argv = ["-c", *args[2:]]
        exec(compile(args[1], "<tools/python -c>", "exec"), {"__name__": "__main__"})
        return 0
    if args[0] == "-":
        sys.argv = ["-", *args[1:]]
        exec(compile(sys.stdin.read(), "<stdin>", "exec"), {"__name__": "__main__"})
        return 0
    sys.argv = args
    runpy.run_path(args[0], run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
