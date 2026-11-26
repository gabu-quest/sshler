"""Playwright E2E tests: renaming, moving and copying onto an existing name asks "Replace?".

Each test builds real folders under the server's home, drives the real UI, and asserts
the files' contents on disk afterwards.

Run with:
    uv run pytest tests/e2e/test_replace_prompt.py -v
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api",
    reason="Playwright is not installed; run `playwright install chromium`",
)
expect = playwright_async.expect

# tall enough for the context menu to open below the first rows
DESKTOP_VIEWPORT = {"width": 1280, "height": 1400}


async def server_home(page, token: str) -> Path:
    """The folder the e2e server calls ``~``; the test process may write there."""
    response = await page.request.get(
        "/api/v1/boxes/local/ls?directory=~", headers={"X-SSHLER-TOKEN": token}
    )
    assert response.ok
    return Path((await response.json())["directory"])


def make_tree(home: Path, files: dict[str, str]) -> Path:
    """A fresh folder under ``home`` holding ``files`` (relative path -> content)."""
    base = home / f"replace-{uuid.uuid4().hex[:8]}"
    for rel, text in files.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return base


def contents(base: Path) -> dict[str, str]:
    return {
        str(p.relative_to(base)): p.read_text(encoding="utf-8")
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


async def replace_prompt(page, name: str):
    # By the name: while one question fades out and the next fades in, both are in the page.
    dialog = page.get_by_role("dialog").filter(
        has_text=f'"{name}" already exists here. Replace it?'
    )
    await expect(dialog).to_contain_text("File already exists")
    return dialog


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["Replace", "Keep both", "No, keep it"])
async def test_rename_onto_an_existing_name_asks(open_page, app_server, answer):
    """A rename whose new name exists asks; Replace overwrites it, Keep both lands the
    renamed file beside it under a free name and says that name, No leaves both files.

    Mutation: the rename is sent once without asking (the error toast and both files
    unchanged), or Replace re-sends it without `overwrite` (b.txt keeps "B"), or Keep
    both re-sends it with `overwrite` (b.txt turns into "A" and no "b (2).txt"), or No
    sends it anyway (b.txt turns into "A").
    """
    _, token = app_server
    page = await open_page(DESKTOP_VIEWPORT)
    home = await server_home(page, token)
    base = make_tree(home, {"a.txt": "A", "b.txt": "B"})

    await page.goto(f"/app/files?box=local&dir={base}", wait_until="load")
    await page.get_by_text("a.txt", exact=True).click(button="right")
    await page.locator(".context-menu-item", has_text="Rename").click()
    await page.get_by_placeholder("New name").fill("b.txt")
    await page.get_by_role("button", name="Rename", exact=True).click()

    dialog = await replace_prompt(page, "b.txt")
    await dialog.get_by_role("button", name=answer, exact=True).click()

    if answer == "Replace":
        await expect(page.get_by_text("renamed", exact=True)).to_be_visible()
        assert contents(base) == {"b.txt": "A"}
    elif answer == "Keep both":
        await expect(page.get_by_text("Kept both: saved as b (2).txt")).to_be_visible()
        await expect(page.get_by_text("b (2).txt", exact=True)).to_be_visible()
        assert contents(base) == {"b.txt": "B", "b (2).txt": "A"}
    else:
        await expect(dialog).to_be_hidden()
        # let the list behind the dialog settle before looking for a late error toast
        await page.wait_for_load_state("networkidle")
        await expect(page.get_by_text("a.txt", exact=True)).to_be_visible()
        assert contents(base) == {"a.txt": "A", "b.txt": "B"}
        await expect(page.get_by_text("File already exists")).to_have_count(0)


@pytest.mark.asyncio
async def test_rename_onto_a_folder_shows_the_servers_text_and_asks_nothing(open_page, app_server):
    """A folder in the way can never be replaced, so the user is never asked.

    Mutation: the rename asks "Replace?" for any 409 (a dialog opens, and Replace would
    only fail with a second error).
    """
    _, token = app_server
    page = await open_page(DESKTOP_VIEWPORT)
    home = await server_home(page, token)
    base = make_tree(home, {"a.txt": "A", "b/inner.txt": "inner"})

    await page.goto(f"/app/files?box=local&dir={base}", wait_until="load")
    await page.get_by_text("a.txt", exact=True).click(button="right")
    await page.locator(".context-menu-item", has_text="Rename").click()
    await page.get_by_placeholder("New name").fill("b")
    await page.get_by_role("button", name="Rename", exact=True).click()

    await expect(page.get_by_text("File already exists")).to_be_visible()
    await expect(page.get_by_role("dialog").filter(has_text="Replace it?")).to_have_count(0)
    assert contents(base) == {"a.txt": "A", "b/inner.txt": "inner"}


async def select_rows_in_files_view(page, names: list[str]):
    for name in names:
        row = page.locator(".n-data-table-tr", has_text=name).first
        await row.locator(".n-checkbox").click()


async def start_batch(page, base: Path, dest: str, names: list[str], mode: str):
    """Select ``names`` in ``base/src`` and send them to ``dest`` with Move or Copy."""
    await page.goto(f"/app/files?box=local&dir={base / 'src'}", wait_until="load")
    await select_rows_in_files_view(page, names)
    await page.get_by_role("button", name=f"{mode} To...").click()
    await page.get_by_placeholder("Search dirs & files (* wildcards)...").last.fill(dest)
    await page.locator(".search-result", has_text=str(base / dest)).click()
    await page.get_by_role("button", name=f"{mode} Here").click()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["Move", "Copy"])
@pytest.mark.parametrize("answer", ["Replace", "Keep both", "No, keep it"])
async def test_batch_onto_an_existing_name_asks(open_page, app_server, mode, answer):
    """A batch move/copy asks for the one name that exists; the others go through.

    Mutation: the retry is sent without `overwrite` (dst/y.txt keeps "old-y"), Keep both
    is sent with `overwrite` (no "y (2).txt"), the refused file is sent anyway after No
    (dst/y.txt turns into "new-y"), or the files that went through are sent twice.
    """
    _, token = app_server
    page = await open_page(DESKTOP_VIEWPORT)
    home = await server_home(page, token)
    dest = f"dest-{uuid.uuid4().hex[:8]}"
    base = make_tree(
        home,
        {
            "src/x.txt": "new-x",
            "src/y.txt": "new-y",
            "src/z.txt": "new-z",
            f"{dest}/y.txt": "old-y",
        },
    )

    await page.goto(f"/app/files?box=local&dir={base / 'src'}", wait_until="load")
    await select_rows_in_files_view(page, ["x.txt", "y.txt", "z.txt"])
    await page.get_by_role("button", name=f"{mode} To...").click()
    await page.get_by_placeholder("Search dirs & files (* wildcards)...").last.fill(dest)
    await page.locator(".search-result", has_text=str(base / dest)).click()
    await page.get_by_role("button", name=f"{mode} Here").click()

    dialog = await replace_prompt(page, "y.txt")
    await dialog.get_by_role("button", name=answer, exact=True).click()
    await expect(dialog).to_be_hidden()

    keep_source = mode == "Copy"
    expected = {
        f"{dest}/x.txt": "new-x",
        f"{dest}/y.txt": "new-y" if answer == "Replace" else "old-y",
        f"{dest}/z.txt": "new-z",
    }
    if answer == "Keep both":
        expected[f"{dest}/y (2).txt"] = "new-y"
    if keep_source:
        expected.update({"src/x.txt": "new-x", "src/y.txt": "new-y", "src/z.txt": "new-z"})
    elif answer == "No, keep it":
        expected["src/y.txt"] = "new-y"
    verb = "Moved" if mode == "Move" else "Copied"
    if answer == "No, keep it":
        await expect(page.get_by_text(f"{verb} 2, skipped 1, failed 0")).to_be_visible()
    else:
        await expect(page.get_by_text(f"{verb} 3 item(s)")).to_be_visible()
    if answer == "Keep both":
        await expect(page.get_by_text("Kept both: saved as y (2).txt")).to_be_visible()
    assert contents(base) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["F5", "F6"])
@pytest.mark.parametrize("answer", ["Replace", "Keep both", "No, keep it"])
async def test_two_pane_transfer_onto_an_existing_name_asks(open_page, app_server, mode, answer):
    """A same-box transfer in the two-pane view asks for the one name that exists.

    After No the progress dialog shows that file as kept, a move leaves it in the source
    pane, and the other two files arrive exactly once. After Keep both the file arrives
    beside the old one under a free name, and the page says that name.

    Mutation: the retry is sent without `overwrite`, Keep both is sent with `overwrite`
    (no "b (2).txt"), the progress dialog shows the kept file as done, or a move removes
    the kept file from the source.
    """
    _, token = app_server
    page = await open_page(DESKTOP_VIEWPORT)
    home = await server_home(page, token)
    base = make_tree(
        home,
        {
            "src/a.txt": "new-a",
            "src/b.txt": "new-b",
            "src/c.txt": "new-c",
            "dst/b.txt": "old-b",
        },
    )
    config = json.dumps({"rightBox": "local", "rightDir": str(base / "dst")})
    await page.add_init_script(
        f"localStorage.setItem('sshler:commander:config', {json.dumps(config)})"
    )

    await page.goto(f"/app/commander?box=local&dir={base / 'src'}", wait_until="load")
    left = page.locator(".commander-pane").first
    for name in ["a.txt", "b.txt", "c.txt"]:
        await left.locator(".file-row", has_text=name).click(modifiers=["Control"])
    await expect(left.locator(".file-row.selected")).to_have_count(3)
    await page.locator(".hotbar-key", has_text=mode).click()

    dialog = await replace_prompt(page, "b.txt")
    await dialog.get_by_role("button", name=answer, exact=True).click()

    rows = page.locator(".transfer-file-row")
    await expect(rows).to_have_count(3)
    await expect(page.get_by_role("button", name="Close")).to_be_visible()
    if answer == "Keep both":
        await expect(page.locator(".transfer-file-row.done")).to_have_count(3)
        await expect(page.get_by_text("Kept both: saved as b (2).txt")).to_be_visible()
    elif answer == "Replace":
        await expect(page.locator(".transfer-file-row.done")).to_have_count(3)
    else:
        await expect(page.locator(".transfer-file-row.done")).to_have_count(2)
        kept = page.locator(".transfer-file-row.skipped")
        await expect(kept).to_have_count(1)
        await expect(kept).to_contain_text("b.txt")
        await expect(kept).to_contain_text("skipped, the existing file was kept")

    await page.get_by_role("button", name="Close").click()
    expected = {
        "dst/a.txt": "new-a",
        "dst/b.txt": "new-b" if answer == "Replace" else "old-b",
        "dst/c.txt": "new-c",
    }
    if answer == "Keep both":
        expected["dst/b (2).txt"] = "new-b"
    if mode == "F5":
        expected.update({"src/a.txt": "new-a", "src/b.txt": "new-b", "src/c.txt": "new-c"})
    elif answer == "No, keep it":
        expected["src/b.txt"] = "new-b"
    assert contents(base) == expected

    if mode == "F6":
        names = left.locator(".file-row .name")
        if answer == "No, keep it":
            await expect(names).to_have_text(["..", "b.txt"])
        else:
            await expect(names).to_have_text([".."])


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["Replace", "Keep both", "Skip"])
async def test_batch_with_three_taken_names_can_be_answered_for_all(open_page, app_server, answer):
    """With "Do the same for the other 2" ticked, one answer covers every name still to
    ask; "Keep both" lands each copy beside the old file and the page names all three.

    Mutation: the answer covers only the name on screen (a second dialog opens), or
    "Skip" replaces, or "Replace" keeps, or "Keep both" replaces.
    """
    _, token = app_server
    page = await open_page(DESKTOP_VIEWPORT)
    home = await server_home(page, token)
    dest = f"dest-{uuid.uuid4().hex[:8]}"
    base = make_tree(
        home,
        {
            "src/a.txt": "new-a",
            "src/b.txt": "new-b",
            "src/c.txt": "new-c",
            f"{dest}/a.txt": "old-a",
            f"{dest}/b.txt": "old-b",
            f"{dest}/c.txt": "old-c",
        },
    )
    await start_batch(page, base, dest, ["a.txt", "b.txt", "c.txt"], "Copy")

    dialog = await replace_prompt(page, "a.txt")
    await dialog.get_by_role("checkbox", name="Do the same for the other 2").click()
    await dialog.get_by_role("button", name=answer, exact=True).click()

    if answer == "Skip":
        await expect(page.get_by_text("Copied 0, skipped 3, failed 0")).to_be_visible()
    else:
        await expect(page.get_by_text("Copied 3 item(s)")).to_be_visible()
    if answer == "Keep both":
        await expect(
            page.get_by_text("Kept both: saved as a (2).txt, b (2).txt, c (2).txt")
        ).to_be_visible()
    await expect(page.get_by_role("dialog").filter(has_text="Replace it?")).to_have_count(0)
    prefix = "new" if answer == "Replace" else "old"
    expected = {
        "src/a.txt": "new-a",
        "src/b.txt": "new-b",
        "src/c.txt": "new-c",
        f"{dest}/a.txt": f"{prefix}-a",
        f"{dest}/b.txt": f"{prefix}-b",
        f"{dest}/c.txt": f"{prefix}-c",
    }
    if answer == "Keep both":
        expected.update(
            {
                f"{dest}/a (2).txt": "new-a",
                f"{dest}/b (2).txt": "new-b",
                f"{dest}/c (2).txt": "new-c",
            }
        )
    assert contents(base) == expected


@pytest.mark.asyncio
async def test_an_answer_without_the_tick_box_covers_one_name(open_page, app_server):
    """Without the tick box each name is asked: Keep both for the first, Replace for the
    second, and the last question, with no name left after it, has no tick box.

    Mutation: the first answer is applied to the rest (no second dialog), or the two
    answers are sent in one request with both words (the server refuses it).
    """
    _, token = app_server
    page = await open_page(DESKTOP_VIEWPORT)
    home = await server_home(page, token)
    dest = f"dest-{uuid.uuid4().hex[:8]}"
    base = make_tree(
        home,
        {
            "src/a.txt": "new-a",
            "src/b.txt": "new-b",
            f"{dest}/a.txt": "old-a",
            f"{dest}/b.txt": "old-b",
        },
    )
    await start_batch(page, base, dest, ["a.txt", "b.txt"], "Copy")

    first = await replace_prompt(page, "a.txt")
    await expect(first.get_by_role("checkbox", name="Do the same for the other 1")).to_be_visible()
    await first.get_by_role("button", name="Keep both", exact=True).click()
    second = await replace_prompt(page, "b.txt")
    await expect(second.get_by_role("checkbox")).to_have_count(0)
    await second.get_by_role("button", name="Replace", exact=True).click()

    await expect(page.get_by_text("Kept both: saved as a (2).txt")).to_be_visible()
    assert contents(base) == {
        "src/a.txt": "new-a",
        "src/b.txt": "new-b",
        f"{dest}/a.txt": "old-a",
        f"{dest}/a (2).txt": "new-a",
        f"{dest}/b.txt": "new-b",
    }


@pytest.mark.asyncio
async def test_enter_on_a_question_with_more_names_left_skips_that_name_only(open_page, app_server):
    """The question opens with its first button, "Skip", in focus, so Enter keeps the
    file and goes on to the next name; it never ticks the box or replaces.

    Mutation: the tick box takes the first focus (Enter ticks it and the first question
    stays open), or Enter presses "Replace" or "Keep both".
    """
    _, token = app_server
    page = await open_page(DESKTOP_VIEWPORT)
    home = await server_home(page, token)
    dest = f"dest-{uuid.uuid4().hex[:8]}"
    files = {
        "src/a.txt": "new-a",
        "src/b.txt": "new-b",
        f"{dest}/a.txt": "old-a",
        f"{dest}/b.txt": "old-b",
    }
    base = make_tree(home, files)
    await start_batch(page, base, dest, ["a.txt", "b.txt"], "Copy")

    first = await replace_prompt(page, "a.txt")
    await expect(first.get_by_role("button", name="Skip", exact=True)).to_be_focused()
    await page.keyboard.press("Enter")
    second = await replace_prompt(page, "b.txt")
    await expect(second.get_by_role("button", name="No, keep it", exact=True)).to_be_focused()
    await page.keyboard.press("Enter")

    await expect(page.get_by_text("Copied 0, skipped 2, failed 0")).to_be_visible()
    assert contents(base) == files


PHONE_NARROW = {"width": 360, "height": 780}
# locale -> (the menu's "Rename", the question's three buttons in their order); the rename dialog's
# own placeholder is a text key too (`files.rename_placeholder`).
NEW_NAME_PLACEHOLDER = {"en": "New name", "ja": "新しい名前"}
NARROW_TEXTS = {
    "en": ("Rename", ["No, keep it", "Keep both", "Replace"]),
    "ja": ("名前変更", ["いいえ（残す）", "両方残す", "置き換える"]),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("locale", ["en", "ja"])
async def test_the_three_answers_fit_one_row_on_a_narrow_phone(open_page, app_server, locale):
    """At 360 px wide the three buttons sit on one row, side by side, inside the dialog,
    and the dialog inside the window, in both languages.

    Mutation: the row wraps (the buttons have different tops), or a button runs past
    the dialog's edge or over its neighbour.
    """
    _, token = app_server
    page = await open_page(PHONE_NARROW)
    await page.add_init_script(f"localStorage.setItem('sshler:locale', '{locale}')")
    home = await server_home(page, token)
    base = make_tree(home, {"a.txt": "A", "b.txt": "B"})
    rename, answers = NARROW_TEXTS[locale]

    await page.goto(f"/app/files?box=local&dir={base}", wait_until="load")
    await page.get_by_text("a.txt", exact=True).click(button="right")
    await page.locator(".context-menu-item", has_text=rename).click()
    await page.get_by_placeholder(NEW_NAME_PLACEHOLDER[locale]).fill("b.txt")
    await page.get_by_role("button", name=rename, exact=True).click()

    dialog = page.get_by_role("dialog").filter(has=page.locator(".replace-actions"))
    buttons = dialog.locator(".replace-actions button")
    await expect(buttons).to_have_text(answers)
    # The dialog scales in: measure once it is at rest, and every box in one read.
    await expect(dialog).not_to_have_class(re.compile("transition"))
    frame, *boxes = await dialog.evaluate(
        """(el) => {
            const rect = (node) => {
                const r = node.getBoundingClientRect();
                return { x: r.x, y: r.y, width: r.width, height: r.height };
            };
            return [el, ...el.querySelectorAll(".replace-actions button")].map(rect);
        }"""
    )
    assert len(boxes) == 3
    assert frame["x"] >= 0
    assert frame["x"] + frame["width"] <= 360
    assert len({round(box["y"]) for box in boxes}) == 1
    assert boxes[0]["x"] >= frame["x"]
    assert boxes[2]["x"] + boxes[2]["width"] <= frame["x"] + frame["width"]
    assert boxes[0]["x"] + boxes[0]["width"] <= boxes[1]["x"]
    assert boxes[1]["x"] + boxes[1]["width"] <= boxes[2]["x"]


@pytest.mark.asyncio
async def test_a_double_click_on_replace_answers_only_the_first_name(open_page, app_server):
    """The second click of a double click lands on the next dialog, where Replace is still
    disabled: the second name stays untouched and its prompt stays open.

    Mutation: Replace is enabled the moment a prompt appears (the second file turns
    into "new-b" and no second prompt is left).
    """
    _, token = app_server
    page = await open_page(DESKTOP_VIEWPORT)
    home = await server_home(page, token)
    dest = f"dest-{uuid.uuid4().hex[:8]}"
    base = make_tree(
        home,
        {
            "src/a.txt": "new-a",
            "src/b.txt": "new-b",
            "src/c.txt": "new-c",
            f"{dest}/a.txt": "old-a",
            f"{dest}/b.txt": "old-b",
        },
    )
    await start_batch(page, base, dest, ["a.txt", "b.txt", "c.txt"], "Copy")

    first = await replace_prompt(page, "a.txt")
    # Playwright waits for the button to be enabled, then clicks twice in a row.
    await first.get_by_role("button", name="Replace", exact=True).dblclick(delay=0)

    second = page.get_by_role("dialog").filter(has_text='"b.txt" already exists here')
    await expect(second).to_be_visible()
    await expect(second.get_by_role("button", name="Replace", exact=True)).to_be_disabled()
    # give a wrongly accepted click time to show up
    await page.wait_for_timeout(300)
    await expect(second).to_be_visible()
    # nothing is replaced before every name has been answered
    assert contents(base)[f"{dest}/b.txt"] == "old-b"

    await second.get_by_role("button", name="No, keep it", exact=True).click()
    await expect(page.get_by_text("Copied 2, skipped 1, failed 0")).to_be_visible()
    assert contents(base)[f"{dest}/a.txt"] == "new-a"
    assert contents(base)[f"{dest}/b.txt"] == "old-b"
    assert contents(base)[f"{dest}/c.txt"] == "new-c"
