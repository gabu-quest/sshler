"""Playwright test of the motion tokens and the reduced-motion switch in the built page.

Mutation: drop the ``:root.reduced-motion`` rules from main.css -> the computed ``--dur-base``
stays 220ms with the class set; drop the ``menu-pop`` rules -> the probe's transition
duration is 0s with and without the class.
"""

from __future__ import annotations

import pytest

playwright_async = pytest.importorskip(
    "playwright.async_api", reason="Playwright is not installed; run `playwright install chromium`"
)

pytestmark = pytest.mark.asyncio

DESKTOP = {"width": 1366, "height": 768}

# The duration token as the page computes it, and the transition time of an element that
# holds the entering class of a menu.
PROBE = """() => {
  const probe = document.createElement('div');
  probe.className = 'menu-pop-enter-active';
  document.body.appendChild(probe);
  const seconds = parseFloat(getComputedStyle(probe).transitionDuration.split(',')[0]);
  probe.remove();
  return {
    // the build may write 220ms as .22s: read the token as milliseconds
    tokenMs: (() => {
      const raw = getComputedStyle(document.documentElement).getPropertyValue('--dur-base').trim();
      return raw.endsWith('ms') ? parseFloat(raw) : parseFloat(raw) * 1000;
    })(),
    transitionMs: seconds * 1000,
  };
}"""


async def probe(page) -> dict:
    return await page.evaluate(PROBE)


async def test_motion_runs_by_default(open_page):
    page = await open_page(DESKTOP)
    await page.goto("/app/", wait_until="load")
    result = await probe(page)
    assert result["tokenMs"] == pytest.approx(220)
    assert result["transitionMs"] == pytest.approx(140)


async def test_reduced_motion_class_turns_motion_off(open_page):
    page = await open_page(DESKTOP)
    await page.goto("/app/", wait_until="load")
    await page.evaluate("document.documentElement.classList.add('reduced-motion')")
    result = await probe(page)
    assert result["tokenMs"] <= 0.01 + 1e-6
    assert result["transitionMs"] <= 0.01 + 1e-6


async def test_system_setting_turns_motion_off_with_sshler_setting_off(open_page):
    page = await open_page(DESKTOP)
    await page.emulate_media(reduced_motion="reduce")
    await page.add_init_script("localStorage.setItem('sshler:ui:reduced-motion', 'false')")
    await page.goto("/app/", wait_until="load")
    assert not await page.evaluate("document.documentElement.classList.contains('reduced-motion')")
    result = await probe(page)
    assert result["tokenMs"] <= 0.01 + 1e-6
    assert result["transitionMs"] <= 0.01 + 1e-6
