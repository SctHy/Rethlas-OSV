"""The GUI page must never turn paper text or model output into live markup.

Reports, proofs and paper-derived questions are untrusted. These checks pin the page's
defences: a restrictive CSP, integrity-checked libraries, escaping that also covers
single quotes, no inline handlers built from run data, and Markdown that passes the
escaping renderer and an allowlist before reaching the DOM.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src/rethlas_osv_gui/static"
PAGE = STATIC / "index.html"


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def inline_script() -> str:
    """The page's own code, which lives in app.js rather than inline."""
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_no_inline_script_or_handlers_and_csp_forbids_them():
    html = page()
    policy = re.search(r'Content-Security-Policy" content="([^"]+)"', html).group(1)
    script_src = next(d for d in policy.split(";") if d.strip().startswith("script-src")).split()
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
    assert re.findall(r"<script\b[^>]*>(?!</script>)", html) == [], "inline <script> content"
    for source in (html, inline_script()):
        assert not re.search(r"\son[a-z]+\s*=", source), "inline event handler attribute"
    assert '<script src="/app.js"></script>' in html


def test_only_the_page_files_are_served(served):
    app, client = served
    import urllib.error
    import urllib.request

    served_files = (
        ("/", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css"),
        ("/vendor/pico.min.css", "text/css"), ("/vendor/marked/marked.min.js", "text/javascript"),
        ("/vendor/katex/fonts/KaTeX_Main-Regular.woff2", "font/woff2"),
    )
    for path, kind in served_files:
        with urllib.request.urlopen(client.base + path) as response:
            assert response.headers["Content-Type"].startswith(kind)
    for path in ("/server.py", "/static/app.js", "/../server.py", "/app.js.map", "/vendor/PICO-LICENSE.md",
                 "/vendor/katex/LICENSE", "/vendor/katex/fonts/KaTeX_Main-Regular.ttf", "/vendor/../app.js"):
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(client.base + path)
        assert refused.value.code == 404


def test_csp_restricts_requests_and_precedes_every_resource():
    html = page()
    match = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html)
    assert match, "the page needs a Content-Security-Policy"
    policy = {d.split()[0]: d.split()[1:] for d in match.group(1).split(";") if d.strip()}
    assert policy["connect-src"] == ["'self'"]
    assert policy["img-src"] == ["'self'", "data:"]
    assert policy["object-src"] == ["'none'"]
    assert policy["base-uri"] == ["'none'"] and policy["form-action"] == ["'none'"]
    first_resource = min(html.index("<script"), html.index("<link"))
    assert match.start() < first_resource


# The bundled libraries are the exact npm releases; these are their SRI hashes, as the
# page pinned them when it still loaded them from cdnjs.
VENDORED = {
    "katex/katex.min.js": "sha384-7zkQWkzuo3B5mTepMUcHkMB5jZaolc2xDwL6VFqjFALcbeS9Ggm/Yr2r3Dy4lfFg",
    "katex/katex.min.css": "sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+",
    "katex/contrib/auto-render.min.js": "sha384-43gviWU0YVjaDtb/GhzOouOXtZMP/7XUzwPTstBeZFe/+rCMvRwr4yROQP43s0Xk",
    "marked/marked.min.js": "sha384-/TQbtLCAerC3jgaim+N78RZSDYV7ryeoBCVqTuzRrFec2akfBkHS7ACQ3PQhvMVi",
}


def test_page_loads_nothing_from_other_origins_and_vendored_files_are_pinned():
    import base64
    import hashlib

    html = page()
    assert not re.search(r"""(?:src|href)=["']?(?:https?:)?//""", html.split("</head>")[0]), "external resource"
    policy = re.search(r'Content-Security-Policy" content="([^"]+)"', html).group(1)
    assert "http" not in policy and "cdnjs" not in policy
    for name, expected in VENDORED.items():
        data = (STATIC / "vendor" / name).read_bytes()
        assert "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode() == expected, name
        assert f'/vendor/{name}"' in html, f"{name} is not used by the page"
    fonts = sorted(p.name for p in (STATIC / "vendor/katex/fonts").glob("*.woff2"))
    css = (STATIC / "vendor/katex/katex.min.css").read_text()
    assert len(fonts) == 20 and all(f"fonts/{name}" in css for name in fonts)


def test_run_data_never_becomes_inline_code_or_raw_markup():
    script = inline_script()
    assert not re.search(r'on[a-z]+="[^"]*\$\{', script), "inline handler built from data"
    assert "\"'\": \"&#39;\"" in script, "esc() must escape single quotes"
    assert 'option value="${esc(m)}"' in script
    render = script[script.index("function renderMarkdown"):]
    render = render[: render.index("\n}\n")]
    assert "sanitizeInto(el, html)" in render and "innerHTML = html" not in render


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_markdown_hooks_escape_html_and_drop_script_urls(tmp_path):
    script = inline_script()
    esc = re.search(r"^function esc\(s\).*$", script, re.M).group(0)
    block = script[
        script.index("// ---- safe Markdown rendering: begin") : script.index(
            "// ---- safe Markdown rendering: end"
        )
    ]
    probe = tmp_path / "probe.js"
    probe.write_text(
        esc
        + "\n"
        + block
        + r"""
const r = SAFE_MARKED.renderer, tag = SAFE_MARKED.tokenizer.tag;
const rules = { rules: { inline: { tag: /^<[a-z][^>]*>/i } } };
console.log(JSON.stringify({
  inline: r.html('<img src=x onerror=alert(1)>', false),
  block: r.html('<div onclick=x>t</div>', true),
  token: r.html({ text: '<b>t</b>', block: false }),
  js: r.link('javascript:alert(1)', null, 'T'),
  tab: r.link('java\tscript:alert(1)', null, 'T'),
  data: r.link('data:text/html,x', null, 'T'),
  web: r.link('https://a.example/?x=1&y=2', null, 'T'),
  relative: r.link('#sec', null, 'T'),
  image: r.image('https://tracker.example/p.png', null, 'a<b'),
  tag: tag.call(rules, '<pre>rest'),
  title: tag.call(rules, '<img src=x onerror="fetch(1)">'),
}));
"""
    )
    out = json.loads(subprocess.run(["node", str(probe)], capture_output=True, text=True, check=True).stdout)
    assert out["inline"] == "&lt;img src=x onerror=alert(1)&gt;"
    assert out["block"] == "<p>&lt;div onclick=x&gt;t&lt;/div&gt;</p>"
    assert out["token"] == "&lt;b&gt;t&lt;/b&gt;"
    assert out["js"] == out["tab"] == out["data"] == "T"
    assert out["web"] == '<a href="https://a.example/?x=1&amp;y=2">T</a>'
    assert out["relative"] == '<a href="#sec">T</a>'
    assert out["image"] == "a&lt;b"
    assert out["tag"] == {"type": "text", "raw": "<pre>", "text": "&lt;pre&gt;"}
    assert "<" not in out["title"]["text"]
