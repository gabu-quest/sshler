"""``fake_tmux`` must cover every module that builds tmux argv.

Each test reaches one module through its own import of the argv builder. With
``fake_tmux`` the stand-in records the call and the PATH tripwire stays empty;
a module the fixture forgot would run the tripwire's ``tmux`` instead.
"""

from pathlib import Path

import pytest

from sshler import snapshot, tmux


@pytest.mark.asyncio
async def test_snapshot_argv_goes_through_the_fake(fake_tmux, tmux_tripwire: Path) -> None:
    await snapshot._cleanup_stale_socket("fakecheck")

    assert fake_tmux.calls() == ["kill-server"]
    assert not tmux_tripwire.exists()


@pytest.mark.asyncio
async def test_default_server_query_goes_through_the_fake(fake_tmux, tmux_tripwire: Path) -> None:
    assert await tmux._query_default_server() == set()

    assert fake_tmux.calls() == ["list-sessions -F #{session_name}"]
    assert not tmux_tripwire.exists()


@pytest.mark.asyncio
async def test_run_local_tmux_goes_through_the_fake(fake_tmux, tmux_tripwire: Path) -> None:
    assert (await tmux.run_local_tmux("fakecheck", ["has-session"]))[0] == 0

    assert fake_tmux.calls() == ["has-session"]
    assert not tmux_tripwire.exists()


@pytest.mark.asyncio
async def test_without_the_fake_the_tripwire_fires(tmux_tripwire: Path) -> None:
    await snapshot._cleanup_stale_socket("fakecheck")

    assert tmux_tripwire.read_text().splitlines() == ["-L ts-fakecheck kill-server"]
