"""Screenshots of sshler's views at chosen widths, languages and themes.

    uv run python scripts/capture_views.py --out DIR [--views overview,files,...]
        [--widths 390,760,1366] [--langs en,ja] [--themes dark,light]
        [--platform posix|windows] [--dist FOLDER]

Writes one PNG per combination, named ``<view>-<width>-<lang>-<theme>.png``. It checks
nothing: it is for looking at the page.

``--platform windows`` shows the page a Windows host would get: the browser rewrites the
bootstrap answer to say the platform is Windows, with its four shells, so the views that
decide on it (the terminal's shell choice and tabs) draw their Windows form. File paths
do not: they come from the server, which is unchanged and still runs here on Linux, so
they stay in Linux form (no drive letters). A Windows shell does not open; the page
around it renders. Those files end in ``-windows.png``.

``--dist FOLDER`` serves the page from another built frontend (``vite build --outDir
FOLDER``), as the code of an earlier commit, while the API is still this server's.

The app starts the way the browser suites start it: this script runs pytest on itself
with ``tests/e2e/conftest.py`` loaded as a plugin, so the ``app_server`` fixture there
(its isolated config, HOME and tmux directory, and their teardown) is the one server
start. The page is the built frontend in ``sshler/static/dist``: run
``pnpm --prefix frontend build`` first, and ``uv run playwright install chromium`` once.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
CONFTEST = ROOT / "tests" / "e2e" / "conftest.py"
ARGS_ENV = "SSHLER_CAPTURE_ARGS"

# The window height that goes with each common width: a phone, a narrow window, a laptop.
HEIGHTS = {390: 844, 760: 700, 1366: 768}
DEFAULT_HEIGHT = 900

# Storage keys the page reads at start (stores/app.ts, i18n.ts).
COLOR_KEY = "sshler:ui:color-mode"
LOCALE_KEY = "sshler:locale"

# Each view: its route. ``{dir}`` is a small known folder made for the run.
VIEWS: dict[str, str] = {
    "overview": "/app/",
    "files": "/app/files?box=local&dir={dir}",
    "terminal": "/app/terminal?box=local&dir={dir}",
    "multi-terminal": "/app/multi-terminal?box=local",
    "commander": "/app/commander?box=local&dir={dir}",
    "boxes": "/app/boxes",
    "settings": "/app/settings",
    "login": "/app/login",
    "progress": "/app/progress",
    "claude": "/app/claude",
    "diff": "/app/diff",
    "artifacts": "/app/artifacts",
    "notfound": "/app/no-such-page",
}
DEFAULT_VIEWS = [
    "overview",
    "files",
    "terminal",
    "multi-terminal",
    "commander",
    "boxes",
    "settings",
    "login",
]

# What a Windows host's bootstrap adds (sshler/api/helpers.py `available_windows_shells`).
WINDOWS_BOOTSTRAP = {
    "platform": "windows",
    "windows_shells": [
        {"id": "pwsh", "label": "PowerShell 7", "available": True},
        {"id": "powershell", "label": "Windows PowerShell", "available": True},
        {"id": "cmd", "label": "Command Prompt", "available": True},
        {"id": "wsl", "label": "WSL (Ubuntu)", "available": True},
    ],
    "default_shell": "pwsh",
}

# How long to wait for a view's own content before taking the picture anyway.
READY_TIMEOUT_MS = 15_000


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path, help="folder for the PNG files")
    parser.add_argument(
        "--views", type=_csv, default=DEFAULT_VIEWS, help=f"any of: {', '.join(VIEWS)}"
    )
    parser.add_argument("--widths", type=_csv, default=["390", "760", "1366"])
    parser.add_argument("--langs", type=_csv, default=["en", "ja"])
    parser.add_argument("--themes", type=_csv, default=["dark", "light"])
    parser.add_argument("--platform", choices=["posix", "windows"], default="posix")
    parser.add_argument("--dist", type=Path, default=None, help="a built frontend to serve instead")
    args = parser.parse_args(argv)
    unknown = [view for view in args.views if view not in VIEWS]
    if unknown:
        parser.error(f"unknown view(s): {', '.join(unknown)}; known: {', '.join(VIEWS)}")
    bad_langs = [lang for lang in args.langs if lang not in ("en", "ja")]
    if bad_langs:
        parser.error(f"unknown language(s): {', '.join(bad_langs)}")
    bad_themes = [theme for theme in args.themes if theme not in ("dark", "light")]
    if bad_themes:
        parser.error(f"unknown theme(s): {', '.join(bad_themes)}")
    if args.dist is not None and not (args.dist / "index.html").is_file():
        parser.error(f"--dist {args.dist} has no index.html")
    try:
        args.widths = [int(width) for width in args.widths]
    except ValueError:
        parser.error("--widths takes whole numbers, as 390,760,1366")
    return args


def make_fixture_dir() -> Path:
    """A folder with a few known entries, so the file views show rows."""
    folder = Path(tempfile.mkdtemp(prefix="capture"))
    (folder / "docs").mkdir()
    (folder / "src").mkdir()
    readme = "# Capture\n\nA folder made for the screenshots.\n"
    (folder / "README.md").write_text(readme, encoding="utf-8")
    (folder / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (folder / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return folder


def _wait_for_view(page, view: str) -> None:
    """Wait for the view's own content; take the picture anyway when it does not come."""
    try:
        # Sockets do not count here, so the terminal's and the progress strip's stay open.
        page.wait_for_load_state("networkidle", timeout=READY_TIMEOUT_MS)
        if view == "terminal":
            page.locator(".xterm-screen").first.wait_for(state="visible", timeout=READY_TIMEOUT_MS)
        elif view == "files":
            page.get_by_text("README.md").first.wait_for(state="visible", timeout=READY_TIMEOUT_MS)
        # No loading spinner left on the page.
        page.wait_for_function(
            "() => ![...document.querySelectorAll('.n-spin')].some(el => el.offsetParent !== null)",
            timeout=READY_TIMEOUT_MS,
        )
    except Exception as exc:  # noqa: BLE001 - a missing landmark still gets its picture
        print(f"  {view}: content did not settle ({type(exc).__name__}); capturing as is")
    page.evaluate(
        "document.fonts.ready.then(() => new Promise("
        "r => requestAnimationFrame(() => requestAnimationFrame(r))))"
    )


def _serve_dist(context, base_url: str, dist: Path) -> None:
    """Answer every /app/ request from *dist*, as sshler/spa.py does from its own folder."""
    root = dist.resolve()
    index = root / "index.html"

    def handle(route) -> None:
        path = urlparse(route.request.url).path.removeprefix("/app").lstrip("/")
        target = (root / unquote(path)).resolve() if path else index
        if not (target.is_relative_to(root) and target.is_file()):
            target = index
        route.fulfill(path=str(target))

    context.route(f"{base_url}/app", handle)
    context.route(f"{base_url}/app/**", handle)


def _answer_as_windows(context, base_url: str) -> None:
    def handle(route) -> None:
        response = route.fetch()
        body = response.json()
        body.update(WINDOWS_BOOTSTRAP)
        route.fulfill(response=response, json=body)

    context.route(f"{base_url}/api/v1/bootstrap*", handle)


def capture(base_url: str, token: str, args: dict) -> list[Path]:
    folder = make_fixture_dir()
    try:
        return _capture(base_url, token, args, folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _capture(base_url: str, token: str, args: dict, folder: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright

    out = Path(args["out"])
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    suffix = "-windows" if args["platform"] == "windows" else ""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for lang in args["langs"]:
                for theme in args["themes"]:
                    context = browser.new_context(base_url=base_url, color_scheme=theme)
                    context.set_extra_http_headers({"X-SSHLER-TOKEN": token})
                    if args["dist"]:
                        _serve_dist(context, base_url, Path(args["dist"]))
                    if args["platform"] == "windows":
                        _answer_as_windows(context, base_url)
                    context.add_init_script(
                        f"localStorage.setItem({json.dumps(COLOR_KEY)}, {json.dumps(theme)});"
                        f"localStorage.setItem({json.dumps(LOCALE_KEY)}, {json.dumps(lang)});"
                    )
                    page = context.new_page()
                    for view in args["views"]:
                        url = VIEWS[view].replace("{dir}", quote(str(folder)))
                        for width in args["widths"]:
                            height = HEIGHTS.get(width, DEFAULT_HEIGHT)
                            page.set_viewport_size({"width": width, "height": height})
                            page.goto(url, wait_until="load")
                            _wait_for_view(page, view)
                            path = out / f"{view}-{width}-{lang}-{theme}{suffix}.png"
                            page.screenshot(path=str(path))
                            written.append(path)
                            print(f"  wrote {path.name}")
                    context.close()
        finally:
            browser.close()
    return written


def test_capture_views(app_server) -> None:
    """The pytest entry this script runs on itself: the conftest's server, then the pictures."""
    base_url, token = app_server
    args = json.loads(os.environ[ARGS_ENV])
    written = capture(base_url, token, args)
    print(f"{len(written)} screenshot(s) in {args['out']}")


def _load_conftest():
    spec = importlib.util.spec_from_file_location("sshler_e2e_conftest", CONFTEST)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {CONFTEST}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    import pytest

    args = parse_args(sys.argv[1:] if argv is None else argv)
    os.environ[ARGS_ENV] = json.dumps(
        {
            "out": str(args.out.resolve()),
            "views": args.views,
            "widths": args.widths,
            "langs": args.langs,
            "themes": args.themes,
            "platform": args.platform,
            "dist": str(args.dist.resolve()) if args.dist else None,
        }
    )
    return int(
        pytest.main(
            [
                str(Path(__file__).resolve()),
                "--rootdir",
                str(ROOT),
                "-p",
                "no:cacheprovider",
                "-o",
                "timeout=0",
                "-q",
                "-s",
            ],
            plugins=[_load_conftest()],
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
