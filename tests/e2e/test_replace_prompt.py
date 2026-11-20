"""Playwright E2E tests: renaming, moving and copying onto an existing name asks "Replace?".

Each test builds real folders under the server's home, drives the real UI, and asserts
the files' contents on disk afterwards.

Run with:
    uv run pytest tests/e2e/test_replace_prompt.py -v
"""

from __future__ import annotations

import json
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
    dialog = page.get_by_role("dialog").filter(has_text="File already exists")
    await expect(dialog).to_contain_text(f'"{name}" already exists here. Replace it?')
    return dialog


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["Replace", "No, keep it"])
async def test_rename_onto_an_existing_name_asks(open_page, app_server, answer):
    """A rename whose new name exists asks; Replace overwrites it, No leaves both files.

    Mutation: the rename is sent once without asking (the error toast and both files
    unchanged), or Replace re-sends it without `overwrite` (b.txt keeps "B"), or No
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
@pytest.mark.parametrize("answer", ["Replace", "No, keep it"])
async def test_batch_onto_an_existing_name_asks(open_page, app_server, mode, answer):
    """A batch move/copy asks for the one name that exists; the others go through.

    Mutation: the retry is sent without `overwrite` (dst/y.txt keeps "old-y"), the
    refused file is sent anyway after No (dst/y.txt turns into "new-y"), or the files
    that went through are sent twice.
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
    if keep_source:
        expected.update({"src/x.txt": "new-x", "src/y.txt": "new-y", "src/z.txt": "new-z"})
    elif answer != "Replace":
        expected["src/y.txt"] = "new-y"
    summary = {"Replace": "3, skipped 0", "No, keep it": "2, skipped 1"}[answer]
    verb = "Moved" if mode == "Move" else "Copied"
    if answer == "Replace":
        await expect(page.get_by_text(f"{verb} 3 item(s)")).to_be_visible()
    else:
        await expect(page.get_by_text(f"{verb} {summary}, failed 0")).to_be_visible()
    assert contents(base) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["F5", "F6"])
@pytest.mark.parametrize("answer", ["Replace", "No, keep it"])
async def test_two_pane_transfer_onto_an_existing_name_asks(open_page, app_server, mode, answer):
    """A same-box transfer in the two-pane view asks for the one name that exists.

    After No the progress dialog shows that file as kept, a move leaves it in the source
    pane, and the other two files arrive exactly once.

    Mutation: the retry is sent without `overwrite`, the progress dialog shows the kept
    file as done, or a move removes the kept file from the source.
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
    if answer == "Replace":
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
    if mode == "F5":
        expected.update({"src/a.txt": "new-a", "src/b.txt": "new-b", "src/c.txt": "new-c"})
    elif answer != "Replace":
        expected["src/b.txt"] = "new-b"
    assert contents(base) == expected

    if mode == "F6":
        names = left.locator(".file-row .name")
        if answer == "Replace":
            await expect(names).to_have_text([".."])
        else:
            await expect(names).to_have_text(["..", "b.txt"])


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["Replace all", "Skip all"])
async def test_batch_with_three_taken_names_can_be_answered_for_all(open_page, app_server, answer):
    """One click on "Replace all" or "Skip all" answers for every name still to ask.

    Mutation: the answer covers only the name on screen (a second dialog opens), or
    "Skip all" replaces, or "Replace all" keeps.
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
    await dialog.get_by_role("button", name=answer, exact=True).click()

    replaced = answer == "Replace all"
    if replaced:
        await expect(page.get_by_text("Copied 3 item(s)")).to_be_visible()
    else:
        await expect(page.get_by_text("Copied 0, skipped 3, failed 0")).to_be_visible()
    await expect(page.get_by_role("dialog").filter(has_text="Replace it?")).to_have_count(0)
    prefix = "new" if replaced else "old"
    assert contents(base) == {
        "src/a.txt": "new-a",
        "src/b.txt": "new-b",
        "src/c.txt": "new-c",
        f"{dest}/a.txt": f"{prefix}-a",
        f"{dest}/b.txt": f"{prefix}-b",
        f"{dest}/c.txt": f"{prefix}-c",
    }


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
