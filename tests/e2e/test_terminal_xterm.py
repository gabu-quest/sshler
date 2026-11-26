"""Playwright tests of the xterm.js terminal itself: renderer fallback, sizing, the scroll bar,
keyboard input, clipboard and links.

These read the terminal through ``window.term``, the xterm instance ``Terminal.vue`` exposes,
so they check what xterm holds and draws rather than what the socket carried. Each test opens
its terminals in fresh directories, so every page has its own tmux session and its own size.
"""

from __future__ import annotations

import base64
import re
import uuid

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)
expect = playwright_async.expect

TIMEOUT_MS = 15000
DESKTOP = {"width": 1280, "height": 800}
MOBILE = {"width": 390, "height": 844}

NO_WEBGL = """
(() => {
  const getContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, ...rest) {
    if (type === 'webgl2' || type === 'webgl') return null;
    return getContext.call(this, type, ...rest);
  };
})();
"""

# Everything xterm holds, one string per logical line (wrapped rows joined).
BUFFER_TEXT = """() => {
  const b = window.term.buffer.active, lines = [];
  for (let y = 0; y < b.length; y++) {
    const l = b.getLine(y);
    const s = l.translateToString(true);
    if (l.isWrapped && lines.length) lines[lines.length - 1] += s; else lines.push(s);
  }
  return lines.join('\\n');
}"""


def fresh_dir(tmp_path):
    """A new directory for one terminal. sshler names the tmux session after the basename,
    so each terminal gets its own session; the names are digits of one length, so the page
    header (which shows the name) has the same width and height on every page compared."""
    directory = tmp_path / f"t{uuid.uuid4().int % 10**6:06d}"
    directory.mkdir()
    return directory


async def goto_terminal(page, directory, init_script: str | None = None) -> None:
    if init_script:
        await page.context.add_init_script(init_script)
    await page.goto(f"/app/terminal?box=local&dir={directory}", wait_until="load")


async def wait_for_prompt(page) -> None:
    await expect(page.locator(".terminal .xterm-screen")).to_be_visible(timeout=TIMEOUT_MS)
    await page.wait_for_function(
        """() => { const b = window.term && window.term.buffer.active;
          if (!b) return false;
          const line = b.getLine(b.baseY + b.cursorY);
          return !!line && /[$#%>] ?$/.test(line.translateToString(true)); }""",
        timeout=TIMEOUT_MS,
    )


async def open_terminal(page, tmp_path, init_script: str | None = None) -> None:
    """Open the terminal view on the local box in a fresh directory; wait for the prompt."""
    await goto_terminal(page, fresh_dir(tmp_path), init_script)
    await wait_for_prompt(page)


async def wait_for_output(page, pattern: str) -> re.Match[str]:
    """Wait until the terminal's buffer holds a match for *pattern*; return the match."""
    await page.wait_for_function(
        f"(p) => new RegExp(p, 'm').test(({BUFFER_TEXT})())", arg=pattern, timeout=TIMEOUT_MS
    )
    match = re.search(pattern, await page.evaluate(BUFFER_TEXT), re.MULTILINE)
    assert match is not None
    return match


async def type_line(page, text: str) -> None:
    await page.locator(".terminal .xterm").click()
    await page.keyboard.type(text)
    await page.keyboard.press("Enter")


async def stty_size(page) -> tuple[int, int]:
    """Rows and columns the shell sees, as ``stty size`` reports them."""
    tag = f"SZ{uuid.uuid4().hex[:6]}"
    await type_line(page, f"echo {tag}_$(stty size | tr ' ' x)")
    match = await wait_for_output(page, rf"{tag}_(\d+)x(\d+)")
    return int(match.group(1)), int(match.group(2))


async def assert_shell_runs(page, typed=None) -> str:
    """Run a printf whose output only an executed command produces; return that output."""
    marker = f"RUN{uuid.uuid4().hex[:6]}"
    command = f"printf '%s_%s\\n' {marker} 42"
    await (typed or type_line)(page, command)
    await wait_for_output(page, rf"^{marker}_42$")
    return f"{marker}_42"


async def renderer(page) -> str:
    """``dom`` when xterm's DOM renderer draws the rows, else ``webgl``."""
    return await page.evaluate(
        "() => document.querySelector('.terminal .xterm-rows') ? 'dom' : 'webgl'"
    )


async def assert_dom_rows_show(page, text: str) -> None:
    """The DOM renderer draws on the next animation frame, after the buffer has the text:
    wait for the rows to show it rather than reading them once."""
    await expect(page.locator(".terminal .xterm-rows")).to_contain_text(text, timeout=TIMEOUT_MS)


async def write_to_terminal(page, text: str) -> None:
    """Write *text* into xterm as the socket would, and wait until xterm has taken it.

    tmux redraws its own screen whenever it likes, over whatever a test put there. So from
    the first call on, what the socket sends is dropped: the terminal holds what the test
    wrote and nothing else."""
    await page.evaluate(
        """(s) => new Promise(done => {
          if (!window.__testWrite) {
            window.__testWrite = window.term.write.bind(window.term);
            window.term.write = () => {};
          }
          window.__testWrite(s, done);
        })""",
        text,
    )


async def fill_scrollback(page, count: int) -> None:
    """tmux keeps xterm on its alternate buffer, which has no scrollback: switch xterm to the
    normal buffer and write *count* numbered lines straight into it."""
    lines = "\r\n".join(f"line {n}" for n in range(1, count + 1))
    await write_to_terminal(page, "\x1b[?1049l\x1b[H\x1b[2J" + lines)


@pytest.mark.asyncio
async def test_terminal_works_with_webgl_unavailable(open_page, tmp_path):
    """With no WebGL in the page, xterm draws with its DOM renderer, runs commands and keeps
    the size the WebGL terminal has.

    Mutation: a renderer chain that needs WebGL (or a fallback that throws out of
    createTerminal) leaves no terminal -> the prompt never appears; a fallback that reserves
    a different width -> the two ``stty size`` results differ.
    """
    with_webgl = await open_page(DESKTOP)
    await open_terminal(with_webgl, tmp_path)
    assert await renderer(with_webgl) == "webgl"
    webgl_size = await stty_size(with_webgl)

    without = await open_page(DESKTOP)
    await open_terminal(without, tmp_path, init_script=NO_WEBGL)
    assert await renderer(without) == "dom"
    await assert_dom_rows_show(without, await assert_shell_runs(without))
    assert await stty_size(without) == webgl_size


@pytest.mark.asyncio
async def test_lost_webgl_context_falls_back_to_dom_once(open_page, tmp_path):
    """A WebGL context lost after the terminal opened hands drawing to the DOM renderer once,
    and the terminal goes on showing output.

    Mutation: no onContextLoss handler -> the rows never move to the DOM renderer; a handler
    that loads a new WebGL addon -> a WebGL canvas comes back and the DOM rows go away.
    """
    page = await open_page(DESKTOP)
    lost: list[str] = []
    page.on(
        "console",
        lambda m: lost.append(m.text) if "WebGL context lost" in m.text else None,
    )
    await open_terminal(page, tmp_path)
    assert await renderer(page) == "webgl"

    await page.evaluate(
        "() => document.querySelectorAll('.terminal .xterm-screen canvas')"
        ".forEach(c => c.dispatchEvent(new Event('webglcontextlost', {cancelable: true})))"
    )
    # The addon waits 3 s for a restore before it reports the loss.
    await page.wait_for_selector(".terminal .xterm-rows", timeout=TIMEOUT_MS)
    await assert_dom_rows_show(page, await assert_shell_runs(page))
    assert await renderer(page) == "dom"
    assert len(lost) == 1


@pytest.mark.asyncio
async def test_terminal_opened_hidden_fits_when_shown(open_page, tmp_path):
    """A terminal opened in a container of zero size (a hidden tab) fits once it is shown.

    Mutation: no refit on resize -> the shown terminal keeps the size it was given while
    hidden and ``stty size`` differs from a terminal opened visible.
    """
    reference = await open_page(DESKTOP)
    await open_terminal(reference, tmp_path)
    expected = await stty_size(reference)

    page = await open_page(DESKTOP)
    hide = """document.addEventListener('DOMContentLoaded', () => {
      const s = document.createElement('style'); s.id = 'hide-terminal';
      s.textContent = '.terminal { display: none !important; }';
      document.head.appendChild(s); });"""
    await goto_terminal(page, fresh_dir(tmp_path), hide)
    await page.wait_for_function("() => window.term !== undefined", timeout=TIMEOUT_MS)
    assert await page.evaluate("() => document.querySelector('.terminal').offsetWidth") == 0

    await page.evaluate("() => document.getElementById('hide-terminal').remove()")
    await wait_for_prompt(page)
    assert await stty_size(page) == expected


@pytest.mark.asyncio
async def test_scroll_bar_scrolls_5000_lines(open_page, tmp_path):
    """Dragging xterm's scroll bar thumb scrolls 5,000 lines to the top and back, and the
    thumb follows.

    Mutation: a scroll bar hidden or not wired to the buffer -> the drag leaves viewportY
    where it was and the thumb does not reach the track's ends.
    """
    page = await open_page(DESKTOP)
    await open_terminal(page, tmp_path)
    await fill_scrollback(page, 5000)
    state_js = """() => {
      const b = window.term.buffer.active;
      const bar = document.querySelector(
        '.terminal .xterm-scrollable-element > .scrollbar.vertical');
      const r = bar.getBoundingClientRect();
      const t = bar.querySelector('.slider').getBoundingClientRect();
      return {baseY: b.baseY, viewportY: b.viewportY, barTop: r.top, barBottom: r.bottom,
              barX: r.left + r.width / 2, thumbTop: t.top, thumbBottom: t.bottom,
              thumbY: t.top + t.height / 2,
              first: b.getLine(b.viewportY).translateToString(true)};
    }"""
    start = await page.evaluate(state_js)
    assert start["baseY"] > 4000
    assert start["viewportY"] == start["baseY"]
    assert start["thumbBottom"] == pytest.approx(start["barBottom"], abs=1)

    async def press_thumb(x: float, y: float) -> None:
        # The bar shows while the pointer is over the terminal and fades when it leaves;
        # hidden, it takes no clicks. Each drag below ends outside the terminal.
        await page.mouse.move(x, y)
        await expect(page.locator(".terminal .scrollbar.vertical.visible")).to_have_count(1)
        await page.mouse.down()

    await press_thumb(start["barX"], start["thumbY"])
    await page.mouse.move(start["barX"], start["barTop"] - 50, steps=10)
    await page.mouse.up()
    await page.wait_for_function(
        "() => window.term.buffer.active.viewportY === 0", timeout=TIMEOUT_MS
    )
    top = await page.evaluate(state_js)
    assert top["thumbTop"] == pytest.approx(top["barTop"], abs=1)
    assert top["first"] == "line 1"

    await press_thumb(top["barX"], top["thumbY"])
    await page.mouse.move(top["barX"], top["barBottom"] + 50, steps=10)
    await page.mouse.up()
    await page.wait_for_function(
        "() => window.term.buffer.active.viewportY === window.term.buffer.active.baseY",
        timeout=TIMEOUT_MS,
    )
    bottom = await page.evaluate(state_js)
    assert bottom["thumbBottom"] == pytest.approx(bottom["barBottom"], abs=1)
    assert bottom["first"] == f"line {bottom['viewportY'] + 1}"


@pytest.mark.asyncio
async def test_mobile_input_bar_reaches_shell_after_tapping_terminal(
    open_page, app_server, tmp_path
):
    """On a phone the input bar types into the shell, before and after a tap on the terminal,
    and the terminal's own textarea stays read-only, so a tap brings up no keyboard.

    Mutation: ``disableStdin`` not set from ``externalInput`` -> the textarea is writable;
    input bar text dropped -> the printf output never appears.
    """
    base_url, token = app_server
    browser = (await open_page()).context.browser
    context = await browser.new_context(
        base_url=base_url,
        viewport=MOBILE,
        has_touch=True,
        is_mobile=True,
        extra_http_headers={"X-SSHLER-TOKEN": token},
    )
    page = await context.new_page()
    await open_terminal(page, tmp_path)

    input_bar = page.get_by_placeholder(re.compile("command", re.IGNORECASE))

    async def send_from_input_bar(_page, command: str) -> None:
        await input_bar.fill(command)
        await input_bar.press("Enter")

    await assert_shell_runs(page, send_from_input_bar)
    assert await page.evaluate("() => window.term.textarea.readOnly") is True

    screen = await page.locator(".terminal .xterm-screen").bounding_box()
    assert screen is not None
    await page.touchscreen.tap(screen["x"] + screen["width"] / 2, screen["y"] + 40)
    await assert_shell_runs(page, send_from_input_bar)
    assert await page.evaluate("() => window.term.textarea.readOnly") is True
    await context.close()


@pytest.mark.asyncio
async def test_desktop_paste_shift_enter_and_ctrl_c_reach_the_shell(open_page, tmp_path):
    """Ctrl+V pastes the clipboard into the shell, Shift+Enter sends ESC CR and Ctrl+C with
    no selection interrupts the foreground program.

    Mutation: Ctrl+V not pasting -> the pasted printf never runs; Shift+Enter sent as a bare
    CR -> ``cat -v`` echoes ``SE`` without ``^[``; Ctrl+C swallowed -> ``cat`` keeps
    reading and the next printf is echoed, not run.
    """
    page = await open_page(DESKTOP)
    await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    await open_terminal(page, tmp_path)

    async def paste(_page, command: str) -> None:
        await page.evaluate("(t) => navigator.clipboard.writeText(t)", command)
        await page.locator(".terminal .xterm").click()
        await page.keyboard.press("Control+v")
        await page.wait_for_function(
            f"(c) => ({BUFFER_TEXT})().includes(c)", arg=command, timeout=TIMEOUT_MS
        )
        await page.keyboard.press("Enter")

    await assert_shell_runs(page, paste)

    await type_line(page, "cat -v")
    await page.keyboard.type("SE")
    await page.keyboard.press("Shift+Enter")
    await wait_for_output(page, r"^SE\^\[$")
    await page.keyboard.press("Control+c")
    await assert_shell_runs(page)


@pytest.mark.asyncio
async def test_one_osc52_sequence_writes_the_clipboard_once(open_page, tmp_path):
    """An OSC 52 copy reaches the browser clipboard exactly once, whether it names the
    clipboard (``c``) or, as tmux sends it, no selection.

    Mutation: xterm and addon-clipboard both handling OSC 52 -> two writes per sequence;
    the provider honouring only ``c`` -> the tmux-style sequence writes nothing.
    """
    page = await open_page(DESKTOP)
    await page.context.add_init_script(
        """window.__clipboardWrites = [];
        Object.defineProperty(navigator, 'clipboard', {value: {
          writeText: (t) => { window.__clipboardWrites.push(t); return Promise.resolve(); },
          readText: () => Promise.resolve(''),
        }});"""
    )
    await open_terminal(page, tmp_path)
    for selection, text in (("c", "copied with c"), ("", "copied by tmux")):
        payload = base64.b64encode(text.encode()).decode()
        await write_to_terminal(page, f"\x1b]52;{selection};{payload}\x07")
    await page.wait_for_function("() => window.__clipboardWrites.length >= 2")
    assert await page.evaluate("() => window.__clipboardWrites") == [
        "copied with c",
        "copied by tmux",
    ]


@pytest.mark.asyncio
async def test_osc8_and_plain_links_open_on_click(open_page, tmp_path):
    """An OSC 8 hyperlink and a plain URL in the output both open when clicked.

    Mutation: the OSC 8 link handler or the web-links addon not loaded -> the click opens
    nothing and the recorded list stays short.
    """
    page = await open_page(DESKTOP)
    await page.context.add_init_script(
        """window.__opened = [];
        window.open = (url) => {
          const w = {opener: null, location: {}};
          Object.defineProperty(w.location, 'href', {set: (v) => window.__opened.push(v)});
          if (url) window.__opened.push(url);
          return w;
        };"""
    )
    await open_terminal(page, tmp_path)
    await write_to_terminal(
        page,
        "\x1b[H\x1b[2J"
        "\x1b]8;;https://example.com/osc8\x1b\\OSC8LINK\x1b]8;;\x1b\\\r\n"
        "see https://example.com/plain here\r\n",
    )
    cell = await page.evaluate(
        """() => {
          const s = document.querySelector('.terminal .xterm-screen').getBoundingClientRect();
          return {x: s.left, y: s.top, w: s.width / window.term.cols,
                  h: s.height / window.term.rows};
        }"""
    )
    for column, row, url in (
        (3, 0, "https://example.com/osc8"),
        (10, 1, "https://example.com/plain"),
    ):
        x = cell["x"] + (column + 0.5) * cell["w"]
        y = cell["y"] + (row + 0.5) * cell["h"]
        await page.mouse.move(x - cell["w"], y)
        await page.mouse.move(x, y)
        await page.mouse.click(x, y)
        await page.wait_for_function("(u) => window.__opened.includes(u)", arg=url)
    assert await page.evaluate("() => window.__opened") == [
        "https://example.com/osc8",
        "https://example.com/plain",
    ]


@pytest.mark.asyncio
async def test_light_theme_colours_the_part_row_below_the_last_row(open_page, tmp_path):
    """The terminal is rarely a whole number of rows high. xterm 6 paints the theme background
    on its scrollable element, not on the viewport (whose stylesheet colour is the dark
    default), so the strip under the last row has the theme's colour only while that element
    fills the terminal."""
    page = await open_page(DESKTOP)
    await page.emulate_media(color_scheme="light")
    await open_terminal(page, tmp_path)
    # The connecting overlay now fades out (80 ms); the strip is read once it has gone.
    await expect(page.locator(".terminal-overlay")).to_have_count(0)
    strip = await page.evaluate(
        """() => {
          const xterm = document.querySelector('.terminal .xterm');
          xterm.scrollIntoView({ block: 'end' });
          const box = xterm.getBoundingClientRect();
          const rows = document.querySelector('.terminal .xterm-screen').getBoundingClientRect();
          let el = document.elementFromPoint(box.left + box.width / 2, box.bottom - 1);
          let colour = 'rgba(0, 0, 0, 0)';
          while (el && colour === 'rgba(0, 0, 0, 0)') {
            colour = getComputedStyle(el).backgroundColor;
            el = el.parentElement;
          }
          return { height: box.bottom - rows.bottom, colour };
        }"""
    )
    assert strip["height"] > 1, "this size leaves no part-row to check; pick another viewport"
    # A box with no saved theme has the calm theme; on a light page its background is the
    # palette's surface, #ffffff (the viewport behind it is xterm's dark default).
    assert strip["colour"] == "rgb(255, 255, 255)"


LAPTOP = {"width": 1366, "height": 768}
FULL_HD = {"width": 1920, "height": 1080}


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [LAPTOP, FULL_HD], ids=["1366x768", "1920x1080"])
async def test_terminal_fills_the_window_height(open_page, tmp_path, viewport):
    """The terminal's frame ends at the bottom of the window, less the page's few px of
    padding: its bottom border is on screen, and no empty band sits below it.

    Mutation: the view's own `100vh - 96px` height back -> at 1366 x 768 the frame runs
    past the window (bottom border cut), at 1920 x 1080 it ends well above the bottom.
    """
    page = await open_page(viewport)
    await open_terminal(page, tmp_path)
    box = await page.locator(".terminal-wrapper").bounding_box()
    assert box is not None
    bottom = box["y"] + box["height"]
    assert viewport["height"] - 8 <= bottom <= viewport["height"], f"the frame ends at {bottom}px"


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["en", "ja"])
@pytest.mark.parametrize("width", [768, 1024, 1366])
async def test_terminal_header_is_one_slim_row(open_page, tmp_path, width, lang):
    """On a desktop the view's header is one row at most 32 px high, and nothing in it is
    cut off, from 768 px (the narrowest desktop) up, in English and Japanese.

    Mutation: the 24 px title and the wrapping controls back -> the header is two or three
    rows high at 768 and 1024 px.
    """
    page = await open_page({"width": width, "height": 768})
    if lang == "ja":
        await page.context.add_init_script("localStorage.setItem('sshler:locale', 'ja')")
    await open_terminal(page, tmp_path)
    header = page.locator(".page-header")
    box = await header.bounding_box()
    assert box is not None
    assert box["height"] <= 32, f"the header is {box['height']}px high"
    overflow = await header.evaluate("el => el.scrollWidth - el.clientWidth")
    assert overflow == 0, f"{overflow}px of the header is cut off"


@pytest.mark.asyncio
async def test_rail_keys_reach_the_rail_from_inside_the_terminal(open_page, tmp_path):
    """With the keyboard in the terminal, Alt+\\ opens the rail and Escape closes it, and
    neither key is typed into the shell.

    Mutation: no rail keys in the terminal's key handler -> the rail stays closed (the
    page's own listener skips keys typed into xterm) and Alt+\\ is sent as ESC \\.
    """
    page = await open_page(LAPTOP)
    keystrokes: list[bytes] = []

    def on_socket(ws) -> None:
        if "/ws/term" in ws.url:
            ws.on("framesent", lambda payload: keystrokes.append(payload) if isinstance(payload, bytes) else None)

    page.on("websocket", on_socket)
    await open_terminal(page, tmp_path)
    await page.locator(".terminal .xterm").click()
    toggle = page.get_by_test_id("rail-toggle")
    await expect(toggle).to_have_attribute("aria-expanded", "false")
    keystrokes.clear()

    await page.keyboard.press("Alt+Backslash")
    await expect(toggle).to_have_attribute("aria-expanded", "true")
    await page.keyboard.press("Escape")
    await expect(toggle).to_have_attribute("aria-expanded", "false")
    assert keystrokes == []

    # With the rail closed, Escape is the shell's again.
    await page.keyboard.press("Escape")
    for _ in range(50):
        if keystrokes:
            break
        await page.wait_for_timeout(50)
    assert keystrokes == [b"\x1b"]
