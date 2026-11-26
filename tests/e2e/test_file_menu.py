"""Playwright E2E tests: the menu a right-click opens on a file row stays on the screen.

Run with:
    uv run pytest tests/e2e/test_file_menu.py -v
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api",
    reason="Playwright is not installed; run `playwright install chromium`",
)
expect = playwright_async.expect

LAST = "f14.txt"
MENU = ".context-menu"


async def server_home(page, token: str) -> Path:
    """The folder the e2e server calls ``~``; the test process may write there."""
    response = await page.request.get(
        "/api/v1/boxes/local/ls?directory=~", headers={"X-SSHLER-TOKEN": token}
    )
    assert response.ok
    return Path((await response.json())["directory"])


def make_folder(home: Path) -> Path:
    """A fresh folder of 14 small files."""
    base = home / f"menu-{uuid.uuid4().hex[:8]}"
    base.mkdir()
    for number in range(1, 15):
        (base / f"f{number:02d}.txt").write_text("x", encoding="utf-8")
    return base


async def open_files(open_page, app_server, viewport):
    _, token = app_server
    page = await open_page(viewport)
    base = make_folder(await server_home(page, token))
    await page.goto(f"/app/files?box=local&dir={base}", wait_until="load")
    await expect(page.get_by_text(LAST, exact=True)).to_be_attached()
    return page


async def open_menu_on_last_row(page, phone: bool):
    row = page.locator("tr", has_text=LAST)
    if phone:
        await row.get_by_role("button", name="...").click()
    else:
        await row.get_by_text(LAST, exact=True).click(button="right")
    await expect(page.locator(MENU)).to_be_visible()


async def box_of(page, selector: str) -> dict[str, float]:
    box = await page.locator(selector).bounding_box()
    assert box is not None
    return box


async def entry_boxes(page) -> list[dict[str, float]]:
    return [
        await box_of(page, f"{MENU} .context-menu-item >> nth={index}")
        for index in range(await page.locator(f"{MENU} .context-menu-item").count())
    ]


def inside(box: dict[str, float], viewport: dict[str, int]) -> bool:
    return (
        box["x"] >= 0
        and box["y"] >= 0
        and box["x"] + box["width"] <= viewport["width"]
        and box["y"] + box["height"] <= viewport["height"]
    )


async def right_click_at(page, x: int, y: int) -> None:
    """A context-menu event on the last row's name, as the pointer at (x, y) would send it."""
    await page.get_by_text(LAST, exact=True).evaluate(
        """(name, [x, y]) => name.dispatchEvent(new MouseEvent(
            "contextmenu", {clientX: x, clientY: y, bubbles: true, cancelable: true}
        ))""",
        [x, y],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("viewport", "phone"),
    [({"width": 1366, "height": 768}, False), ({"width": 390, "height": 844}, True)],
)
async def test_menu_of_the_last_row_is_wholly_on_the_screen(open_page, app_server, viewport, phone):
    """The menu and every entry of it lie inside the window.

    Mutation: the position is clamped only at mount (the menu's bottom is below the window).
    """
    page = await open_files(open_page, app_server, viewport)
    await open_menu_on_last_row(page, phone)

    menu = await box_of(page, MENU)
    assert inside(menu, viewport), menu
    labels = await page.locator(f"{MENU} .context-menu-item").all_inner_texts()
    assert [labels[0].strip(), labels[-1].strip()] == ["Preview", "Delete"]
    boxes = await entry_boxes(page)
    assert [box for box in boxes if not inside(box, viewport)] == []


@pytest.mark.asyncio
async def test_menu_at_the_bottom_right_corner_is_clamped_on_both_axes(open_page, app_server):
    """A pointer in the corner leaves the menu inside the window on both axes."""
    viewport = {"width": 1366, "height": 768}
    page = await open_files(open_page, app_server, viewport)
    await right_click_at(page, viewport["width"] - 1, viewport["height"] - 1)
    await expect(page.locator(MENU)).to_be_visible()

    menu = await box_of(page, MENU)
    assert inside(menu, viewport), menu


@pytest.mark.asyncio
async def test_menu_taller_than_the_window_scrolls_inside_itself(open_page, app_server):
    """At 390 by 500 the menu is as tall as the window at most and every entry can be clicked."""
    viewport = {"width": 390, "height": 500}
    page = await open_files(open_page, app_server, viewport)
    await open_menu_on_last_row(page, True)

    menu = await box_of(page, MENU)
    assert inside(menu, viewport), menu
    sizes = await page.locator(MENU).evaluate("el => [el.scrollHeight, el.clientHeight]")
    assert sizes[0] > sizes[1]

    await page.locator(f"{MENU} .context-menu-item", has_text="Delete").click()
    await expect(page.locator(MENU)).to_have_count(0)
    await expect(page.get_by_role("dialog")).to_contain_text(LAST)


@pytest.mark.asyncio
async def test_a_second_open_is_placed_for_its_own_point(open_page, app_server):
    """Open, close, open elsewhere: the second menu sits at the second point."""
    viewport = {"width": 1366, "height": 768}
    page = await open_files(open_page, app_server, viewport)

    await right_click_at(page, 100, 100)
    await page.wait_for_timeout(400)  # the pop-in moves the painted box until it ends
    first = await box_of(page, MENU)
    assert (first["x"], first["y"]) == (100, 100)

    await page.keyboard.press("Escape")
    await expect(page.locator(MENU)).to_have_count(0)

    await right_click_at(page, 300, 200)
    await page.wait_for_timeout(400)
    second = await box_of(page, MENU)
    assert (second["x"], second["y"]) == (300, 200)


@pytest.mark.asyncio
async def test_menu_moved_by_a_second_pointer_while_open_is_placed_again(open_page, app_server):
    """A right-click elsewhere while the menu is open moves it, and clamps it again."""
    viewport = {"width": 1366, "height": 768}
    page = await open_files(open_page, app_server, viewport)

    await right_click_at(page, 100, 100)
    await expect(page.locator(MENU)).to_be_visible()
    await right_click_at(page, viewport["width"] - 1, viewport["height"] - 1)

    box = await box_of(page, MENU)
    assert inside(box, viewport), box
    assert box["x"] > 100


@pytest.mark.asyncio
async def test_a_resize_while_open_keeps_the_menu_inside(open_page, app_server):
    """Shrinking the window under an open menu moves the menu inside the new window."""
    page = await open_files(open_page, app_server, {"width": 1366, "height": 768})
    await right_click_at(page, 1000, 300)
    await expect(page.locator(MENU)).to_be_visible()

    smaller = {"width": 700, "height": 600}
    await page.set_viewport_size(smaller)

    # two frames: the page has handled the resize and rendered its answer
    await page.evaluate(
        "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )
    menu = await box_of(page, MENU)
    assert inside(menu, smaller), menu


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [{"width": 760, "height": 700}, {"width": 390, "height": 844}])
async def test_menu_at_the_corner_of_a_narrow_window_is_on_the_screen(open_page, app_server, viewport):
    """A right-click in the bottom-right corner at 760 by 700 and at 390 by 844 keeps the menu inside."""
    page = await open_files(open_page, app_server, viewport)
    await right_click_at(page, viewport["width"] - 1, viewport["height"] - 1)
    await expect(page.locator(MENU)).to_be_visible()
    await page.wait_for_timeout(400)  # let the pop-in finish before measuring

    menu = await box_of(page, MENU)
    assert inside(menu, viewport), menu
