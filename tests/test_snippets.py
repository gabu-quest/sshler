"""Tests for snippet CRUD operations."""

from __future__ import annotations

import pytest

from sshler import state


@pytest.fixture(autouse=True)
def _init_state(tmp_path):
    """Initialize state with a temporary database for each test."""
    state.reset_state()
    state.initialize(tmp_path)
    yield
    state.reset_state()


class TestSnippetCRUD:
    def test_create_snippet(self):
        s = state.create_snippet(box="mybox", label="Deploy", command="./deploy.sh", category="ops")
        assert s.id
        assert s.box == "mybox"
        assert s.label == "Deploy"
        assert s.command == "./deploy.sh"
        assert s.category == "ops"
        assert s.sort_order == 0
        assert s.created_at > 0

    def test_create_multiple_snippets_increments_sort_order(self):
        s1 = state.create_snippet(box="mybox", label="First", command="echo 1")
        s2 = state.create_snippet(box="mybox", label="Second", command="echo 2")
        s3 = state.create_snippet(box="mybox", label="Third", command="echo 3")
        assert s1.sort_order == 0
        assert s2.sort_order == 1
        assert s3.sort_order == 2

    def test_list_snippets_returns_box_and_global(self):
        state.create_snippet(box="mybox", label="Box cmd", command="echo box")
        state.create_snippet(box="__global__", label="Global cmd", command="echo global")
        state.create_snippet(box="otherbox", label="Other cmd", command="echo other")

        results = state.list_snippets("mybox")
        assert len(results) == 2
        labels = {s.label for s in results}
        assert labels == {"Box cmd", "Global cmd"}

    def test_list_snippets_ordered_by_sort_order(self):
        state.create_snippet(box="mybox", label="A", command="a")
        state.create_snippet(box="mybox", label="B", command="b")
        state.create_snippet(box="mybox", label="C", command="c")

        results = state.list_snippets("mybox")
        assert len(results) == 3
        assert [s.label for s in results] == ["A", "B", "C"]

    def test_list_snippets_empty_box(self):
        results = state.list_snippets("empty")
        assert results == []

    def test_get_snippet_by_id(self):
        created = state.create_snippet(box="mybox", label="Test", command="test")
        found = state.get_snippet_by_id(created.id)
        assert found is not None
        assert found.id == created.id
        assert found.label == "Test"

    def test_get_snippet_by_id_not_found(self):
        result = state.get_snippet_by_id("nonexistent")
        assert result is None

    def test_update_snippet_label(self):
        created = state.create_snippet(box="mybox", label="Old", command="cmd")
        updated = state.update_snippet(created.id, label="New")
        assert updated is not None
        assert updated.label == "New"
        assert updated.command == "cmd"  # unchanged

    def test_update_snippet_command(self):
        created = state.create_snippet(box="mybox", label="Test", command="old cmd")
        updated = state.update_snippet(created.id, command="new cmd")
        assert updated is not None
        assert updated.command == "new cmd"
        assert updated.label == "Test"  # unchanged

    def test_update_snippet_category(self):
        created = state.create_snippet(box="mybox", label="Test", command="cmd", category="old")
        updated = state.update_snippet(created.id, category="new")
        assert updated is not None
        assert updated.category == "new"

    def test_update_snippet_sort_order(self):
        created = state.create_snippet(box="mybox", label="Test", command="cmd")
        updated = state.update_snippet(created.id, sort_order=99)
        assert updated is not None
        assert updated.sort_order == 99

    def test_update_snippet_not_found(self):
        result = state.update_snippet("nonexistent", label="X")
        assert result is None

    def test_delete_snippet(self):
        created = state.create_snippet(box="mybox", label="Test", command="cmd")
        assert state.delete_snippet(created.id) is True
        assert state.get_snippet_by_id(created.id) is None
        assert state.list_snippets("mybox") == []

    def test_delete_snippet_not_found(self):
        assert state.delete_snippet("nonexistent") is False

    def test_global_snippets_visible_to_all_boxes(self):
        state.create_snippet(box="__global__", label="Global", command="global cmd")

        for box in ["box1", "box2", "box3"]:
            results = state.list_snippets(box)
            assert len(results) == 1
            assert results[0].label == "Global"
            assert results[0].box == "__global__"


class TestSnippetAsync:
    @pytest.mark.asyncio
    async def test_create_and_list_async(self):
        await state.create_snippet_async(box="mybox", label="Async", command="echo async")
        results = await state.list_snippets_async("mybox")
        assert len(results) == 1
        assert results[0].label == "Async"

    @pytest.mark.asyncio
    async def test_update_async(self):
        created = await state.create_snippet_async(box="mybox", label="Old", command="old")
        updated = await state.update_snippet_async(created.id, label="New")
        assert updated is not None
        assert updated.label == "New"

    @pytest.mark.asyncio
    async def test_delete_async(self):
        created = await state.create_snippet_async(box="mybox", label="Del", command="del")
        assert await state.delete_snippet_async(created.id) is True
        results = await state.list_snippets_async("mybox")
        assert len(results) == 0
