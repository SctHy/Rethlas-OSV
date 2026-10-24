"""The GUI page driven in a real browser: every tab, form and run action.

Needs Playwright and an installed Google Chrome; otherwise skipped. Run with
``uv run --with playwright pytest tests/test_gui_browser.py``. A scripted fake ``codex``
stands in for the CLI, so these tests say nothing about review quality.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import fake_codex, wait_for
from osverifier import cli as verifier_cli

from rethlas_osv_gui.jobs import read_job

sync_api = pytest.importorskip("playwright.sync_api")
CHROME = Path("/Applications/Google Chrome.app")
pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome is not installed")


def until(page, expression, timeout=20):
    """Poll a page expression from here: the page's CSP rightly forbids the in-page
    evaluation that ``wait_for_function`` relies on."""
    wait_for(lambda: page.evaluate(expression), timeout)


@pytest.fixture
def browser_page(served, tmp_path):
    app, client = served
    client.call("PUT", "/api/settings", {"codex_bin": str(fake_codex(tmp_path))})
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context()
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=client.base)
        page = context.new_page()
        problems = []
        # Nothing may leave this machine: every library is bundled with the page.
        context.route(
            lambda url: not url.startswith(client.base),
            lambda route: (problems.append(f"external request: {route.request.url}"), route.abort()),
        )
        page.on("pageerror", lambda error: problems.append(f"page error: {error}"))
        # API errors are expected answers here; static assets and scripts must not fail.
        page.on(
            "console",
            lambda message: problems.append(f"console: {message.text}")
            if message.type == "error" and not message.text.startswith("Failed to load resource")
            else None,
        )
        page.on(
            "response",
            lambda response: problems.append(f"{response.status} {response.url}")
            if response.status >= 400 and "/api/" not in response.url
            else None,
        )
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(client.base + "/")
        until(page, "document.getElementById('codex-pill')?.textContent.includes('signed in')")
        yield app, client, page
        assert problems == []
        browser.close()


def prove_fixture(run: Path):
    """A finished prove run with one reviewed candidate and a handoff."""
    key = "a" * 64
    review = run / "reviews" / key
    review.mkdir(parents=True)
    (run / "inputs").mkdir()
    (run / "inputs/problem.md").write_text("Show that $1 = 1$.")
    (run / "summary.json").write_text(json.dumps({
        "schema_version": "1.0", "status": "ready_for_human", "rounds_completed": 1,
        "generation_branches": 1, "generation_turns_completed": 1, "candidates_generated": 1,
        "winning_branch": 1, "last_decision": "READY_FOR_HUMAN", "human_confirmed": False,
        "handoff_artifact": "blueprint_ready_for_human.md", "error": None,
    }))
    (run / "handoff.json").write_text(json.dumps({"decision": "READY_FOR_HUMAN"}))
    (run / "blueprint_ready_for_human.md").write_text("# Proof\n\nIndeed $1 = 1$.")
    (review / "report.json").write_text(json.dumps(
        {"decision": "READY_FOR_HUMAN", "run_status": "completed", "feedback": []}
    ))
    (review / "report.md").write_text("# Verifier report\n\nNo issue.")
    (review / "candidate-solution.md").write_text("# Proof\n\nIndeed $1 = 1$.")
    (run / "trajectory.jsonl").write_text(
        json.dumps({"at": "2026-01-01T00:00:00+00:00", "event": "ready_for_human", "round": 1}) + "\n"
    )


def test_tabs_theme_and_about(browser_page):
    app, client, page = browser_page
    for tab in ("verify", "prove", "runs", "settings", "about"):
        page.click(f"nav button[data-tab={tab}]")
        assert page.is_visible(f"#tab-{tab}")
        assert page.evaluate("location.hash") == f"#{tab}"
    page.select_option("#theme-select", "dark")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.select_option("#theme-select", "system")
    assert page.evaluate("document.documentElement.dataset.theme") is None
    assert page.is_hidden("#quit-btn")
    assert page.text_content("#about-version").startswith("version ")
    page.click("text=Citation")
    page.click(".cite button")
    until(page, "document.querySelector('.cite button')?.textContent === 'Copied'")
    assert "@misc{2026osverify" in page.evaluate("navigator.clipboard.readText()")


def test_settings_save_reload_and_backend_switch(browser_page):
    app, client, page = browser_page
    page.click("nav button[data-tab=settings]")
    assert page.input_value("#s-prove-generator_model") == "gpt-6-astra"
    page.select_option("#s-prove-generator_backend", "claude")
    assert page.input_value("#s-prove-generator_model") == "claude-opus-5-5"
    page.fill("#s-verify-workers", "7")
    page.check("#s-prove-early_stop")
    page.click("text=Save settings")
    until(page, "document.getElementById('s-saved')?.textContent === 'Saved.'")
    saved = client.call("GET", "/api/settings")[1]
    assert saved["verify"]["workers"] == 7 and saved["prove"]["generator_backend"] == "claude"
    page.reload()
    page.click("nav button[data-tab=settings]")
    until(page, "document.getElementById('s-verify-workers')?.value === '7'")
    assert page.input_value("#s-prove-generator_backend") == "claude"
    # The quick settings on the prove form mirror the defaults and follow a switch too.
    page.click("nav button[data-tab=prove]")
    assert page.input_value("#p-gmodel") == "claude-opus-5-5"
    page.select_option("#p-gbackend", "codex")
    assert page.input_value("#p-gmodel") == "gpt-6-astra"
    page.click("nav button[data-tab=settings]")
    page.fill("#s-verify-workers", "0")
    page.click("text=Save settings")
    until(page, "document.getElementById('s-error')?.textContent.length > 0")
    page.click("text=Check Claude Code")
    until(page, "document.getElementById('claude-detail')?.textContent.length > 0")


def test_verify_run_starts_cancels_and_resumes(browser_page):
    app, client, page = browser_page
    page.click("nav button[data-tab=verify]")
    page.check("input[name=v-mode][value=manual]")
    assert page.is_visible("#v-tags-field")
    page.check("input[name=v-mode][value=smart]")
    assert page.is_hidden("#v-tags-field")
    page.fill("#v-name", "browser run")
    page.click("#v-own summary")
    page.fill("#v-question", "Show that 1 = 1.")
    page.fill("#v-solution", "Clearly 1 = 1.")
    page.click("#v-start")
    until(page, "location.hash === '#run/browser-run'")
    until(page, "document.getElementById('rd-status')?.textContent === 'Running'")
    assert "Show that 1 = 1." in page.text_content("#rd-inputs")
    run_dir = app.runs_root / "browser-run"
    page.click("#rd-actions button")  # Cancel, confirmed by the dialog handler
    wait_for(lambda: read_job(run_dir)["status"] == "exited")
    page.wait_for_selector("#rd-actions button:has-text('Resume')", timeout=20000)
    page.click("#rd-actions button:has-text('Resume')")
    until(page, "document.getElementById('rd-status')?.textContent === 'Running'")
    assert read_job(run_dir)["invocations"] == 2
    page.click("#rd-actions button")
    wait_for(lambda: read_job(run_dir)["status"] == "exited")
    page.click("text=← Runs")
    until(page, "location.hash === '#runs'")
    page.click("text=Refresh")
    page.wait_for_selector("#runs-body tr[data-open-run='browser-run']")


def test_finished_runs_show_reports_reviews_and_copy_sources(browser_page):
    app, client, page = browser_page
    assert verifier_cli.main(["demo", "--run-dir", str(app.runs_root / "demo")]) == 0
    prove_fixture(app.runs_root / "proved")
    page.click("nav button[data-tab=runs]")
    page.click("#runs-body tr[data-open-run='demo']")
    until(page, "document.getElementById('rd-status')?.textContent === 'Completed'")
    assert "Verifier report" in page.text_content("#rd-report")
    assert page.is_enabled("#rd-report-copy")
    page.click("#rd-report-copy")
    until(page, "document.getElementById('rd-report-copy')?.textContent === 'Copied'")
    assert page.evaluate("navigator.clipboard.readText()").startswith("# Verifier report")
    assert page.get_attribute("#rd-export a[download='report.tex']", "href").endswith("/export?format=latex")

    page.click("text=← Runs")
    page.click("#runs-body tr[data-open-run='proved']")
    until(page, "document.getElementById('rd-status')?.textContent === 'Ready for human'")
    assert page.is_visible("#rd-proof-panel") and "Indeed" in page.text_content("#rd-proof")
    # Markdown and math render from the bundled marked and KaTeX, with no network.
    assert page.evaluate("document.querySelector('#rd-proof h1')?.textContent") == "Proof"
    until(page, "!!document.querySelector('#rd-proof .katex')")
    assert page.evaluate("document.fonts.check('16px KaTeX_Main')")
    page.click("#rd-proof-copy")
    until(page, "document.getElementById('rd-proof-copy')?.textContent === 'Copied'")
    assert page.evaluate("navigator.clipboard.readText()") == "# Proof\n\nIndeed $1 = 1$."
    # One section shows at a time: a handoff opens on its proof.
    assert page.is_hidden("#rd-reviews") and page.is_hidden("#rd-events")
    page.click(".seg button[data-section=activity]")
    page.wait_for_selector("#rd-events div:has-text('ready_for_human')")
    assert page.is_hidden("#rd-proof-panel")
    assert page.text_content("#rd-reviews-count") == "1"
    page.click(".seg button[data-section=reviews]")
    page.click("#rd-reviews tr[data-open-review]")
    page.wait_for_selector("#rv-md")
    assert "No issue." in page.text_content("#rv-md")
    page.click("#rd-review-view button:has-text('Copy candidate source')")
    until(page, "[...document.querySelectorAll('#rd-review-view button')].some(b => b.textContent === 'Copied')")
    assert page.evaluate("navigator.clipboard.readText()") == "# Proof\n\nIndeed $1 = 1$."
    assert page.is_visible("#rd-review-view button:has-text('Draft template report')")
    # The sidebar lists recent runs and opens one from any page.
    page.click("nav button[data-tab=settings]")
    page.click("#recent-runs [data-open-run='demo']")
    until(page, "document.getElementById('rd-status')?.textContent === 'Completed'")
    assert page.is_visible("#tab-runs") and page.evaluate("location.hash") == "#run/demo"
    assert "active" in page.get_attribute("#recent-runs [data-open-run='demo']", "class")
    # A verify run without a prepared question has no Question tab and opens on feedback.
    assert page.is_hidden(".seg button[data-section=question]") and page.is_visible("#rd-report")
    page.click("#recent-runs [data-open-run='proved']")
    # A direct link to a run opens it after a reload.
    page.reload()
    until(page, "document.getElementById('rd-status')?.textContent === 'Ready for human'")


def test_codex_sign_in_shows_the_page_link_and_cancels(browser_page):
    app, client, page = browser_page
    page.click("nav button[data-tab=settings]")
    page.click("#login-btn")
    page.wait_for_selector("#login-open:not(.hidden)")
    assert page.get_attribute("#login-open", "data-url") == "https://auth.example/oauth?state=abc"
    page.click("#settings-login button:has-text('Cancel sign-in')")
    until(page, "document.getElementById('login-status')?.textContent.includes('ended')")
    # The same box moves under the About tab's button.
    page.click("nav button[data-tab=about]")
    page.click("#tab-about button:has-text('Sign in with Codex')")
    page.wait_for_selector("#about-login #login-box:not(.hidden)")
    page.click("#about-login button:has-text('Cancel sign-in')")
    until(page, "document.getElementById('login-status')?.textContent.includes('ended')")


def test_file_loading_report_drafts_sign_in_page_and_quit(browser_page, tmp_path):
    app, client, page = browser_page
    # Load file…: a Latin-1 source is decoded without losing its accents.
    source = tmp_path / "paper.tex"
    source.write_bytes("Théorème principal.".encode("latin-1"))
    page.click("nav button[data-tab=prove]")
    with page.expect_file_chooser() as chooser:
        page.click("#tab-prove button:has-text('Load file')")
    chooser.value.set_files(str(source))
    until(page, "document.getElementById('p-problem').value === 'Théorème principal.'")

    # Draft template report: the scripted Codex cannot write one, and the page says so.
    assert verifier_cli.main(["demo", "--run-dir", str(app.runs_root / "demo")]) == 0
    page.click("nav button[data-tab=runs]")
    page.click("#runs-body tr[data-open-run='demo']")
    page.click("#rd-export button:has-text('Draft template report')")
    assert page.is_disabled("#rd-export button:has-text('Draft template report')")
    until(page, "document.getElementById('rd-draft-status')?.textContent.startsWith('Writing report')")
    # The scripted `codex exec` blocks for a minute before failing.
    until(page, "/not released/i.test(document.getElementById('rd-draft-status')?.textContent)", 150)
    assert page.is_enabled("#rd-export button:has-text('Draft template report')")

    # Open the sign-in page: the Codex-provided URL opens in a new window.
    page.click("nav button[data-tab=settings]")
    page.click("#login-btn")
    page.wait_for_selector("#login-open:not(.hidden)")
    page.context.route("https://auth.example/**", lambda route: route.fulfill(body="sign in"))
    with page.context.expect_page() as popup:
        page.click("#login-open")
    popup.value.wait_for_load_state()
    assert popup.value.url == "https://auth.example/oauth?state=abc"
    popup.value.close()
    page.click("#settings-login button:has-text('Cancel sign-in')")

    # Quit (desktop app only): the server is asked to stop; runs keep going on their own.
    app.desktop = True
    page.reload()
    page.wait_for_selector("#quit-btn:not(.hidden)")
    page.click("#quit-btn")
    until(page, "document.body.textContent.includes('has quit')")
    assert app.quit_requested.is_set()
