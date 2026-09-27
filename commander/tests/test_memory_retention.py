from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from a2a_protocol.server import A2ABaseAgent
from bounded_cache import BoundedLRUCache
from commander_gateway.store import FileGatewayStore
from idempotency_store import IdempotencyStore
from workflow_state_store import WorkflowStateStore


def test_bounded_lru_enforces_count_and_recency() -> None:
    cache = BoundedLRUCache(max_items=2, max_bytes=10_000)
    cache["first"] = {"value": 1}
    cache["second"] = {"value": 2}
    assert cache.get("first") == {"value": 1}

    cache["third"] = {"value": 3}

    assert cache.get("second") is None
    assert list(cache) == ["first", "third"]
    assert cache.stats()["evictions"] == 1


def test_bounded_lru_enforces_byte_budget() -> None:
    cache = BoundedLRUCache(max_items=10, max_bytes=80)
    cache["first"] = {"blob": "a" * 50}
    cache["second"] = {"blob": "b" * 50}

    assert cache.get("first") is None
    assert cache.get("second") is not None
    assert cache.stats()["bytes"] <= 80


def test_agent_cleanup_releases_workflow_scoped_memory() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        agent = A2ABaseAgent(
            name="Memory_Test_Agent",
            description="memory test",
            role="recon",
            port=19022,
            idempotency_db_path=str(Path(temp_dir) / "agent.db"),
        )
        agent._workflow_work_lists["wf-1"] = [{"activity_id": "a"}]
        agent._task_response_cache["wf-1:a"] = {
            "workflow_id": "wf-1", "status": "completed"
        }
        agent._stream_response_cache["wf-1:a"] = ["event"]

        result = agent.cleanup_workflow("wf-1")

        assert result["removed"] == 3
        assert agent.get_work_list("wf-1") == []
        assert agent.cache_metrics()["task_responses"]["items"] == 0


def test_checkpoint_store_prunes_oldest_records() -> None:
    with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
        os.environ,
        {
            "A2A_CHECKPOINT_RETENTION_COUNT": "2",
            "A2A_CHECKPOINT_RETENTION_BYTES": "1000000",
        },
    ):
        store = WorkflowStateStore(temp_dir)
        for index in range(3):
            store.save(f"wf-{index}", {"status": "completed", "value": index})

        assert not store.exists("wf-0")
        assert store.exists("wf-1")
        assert store.exists("wf-2")
        assert store.stats()["items"] == 2


def test_checkpoint_store_prunes_existing_records_on_open() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        with patch.dict(os.environ, {
            "A2A_CHECKPOINT_RETENTION_COUNT": "3",
            "A2A_CHECKPOINT_RETENTION_BYTES": "1000000",
        }):
            store = WorkflowStateStore(temp_dir)
            for index in range(3):
                store.save(f"wf-{index}", {"status": "completed", "value": index})
        with patch.dict(os.environ, {
            "A2A_CHECKPOINT_RETENTION_COUNT": "2",
            "A2A_CHECKPOINT_RETENTION_BYTES": "1000000",
        }):
            reopened = WorkflowStateStore(temp_dir)

        assert reopened.stats()["items"] == 2
        assert not reopened.exists("wf-0")


def test_idempotency_store_prunes_oldest_records() -> None:
    with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
        os.environ,
        {
            "A2A_IDEMPOTENCY_RETENTION_COUNT": "2",
            "A2A_IDEMPOTENCY_RETENTION_BYTES": "1000000",
            "A2A_IDEMPOTENCY_RETENTION_DAYS": "30",
        },
    ):
        store = IdempotencyStore(Path(temp_dir) / "agent.db", "agent")
        for index in range(3):
            store.put(f"wf:{index}", {"status": "completed", "value": index})

        assert store.get("wf:0") is None
        assert store.get("wf:2")["value"] == 2
        assert store.stats()["items"] == 2


def test_idempotency_store_prunes_existing_records_on_open() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "agent.db"
        with patch.dict(os.environ, {
            "A2A_IDEMPOTENCY_RETENTION_COUNT": "3",
            "A2A_IDEMPOTENCY_RETENTION_BYTES": "1000000",
            "A2A_IDEMPOTENCY_RETENTION_DAYS": "30",
        }):
            store = IdempotencyStore(database_path, "agent")
            for index in range(3):
                store.put(f"wf:{index}", {"status": "completed", "value": index})
        with patch.dict(os.environ, {
            "A2A_IDEMPOTENCY_RETENTION_COUNT": "2",
            "A2A_IDEMPOTENCY_RETENTION_BYTES": "1000000",
            "A2A_IDEMPOTENCY_RETENTION_DAYS": "30",
        }):
            reopened = IdempotencyStore(database_path, "agent")

        assert reopened.stats()["items"] == 2
        assert reopened.get("wf:0") is None


def test_gateway_file_store_prunes_all_record_families() -> None:
    with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
        os.environ,
        {
            "GATEWAY_PACKAGE_RETENTION_COUNT": "2",
            "GATEWAY_PACKAGE_RETENTION_BYTES": "1000000",
            "GATEWAY_WORKFLOW_RETENTION_COUNT": "2",
            "GATEWAY_WORKFLOW_RETENTION_BYTES": "1000000",
            "GATEWAY_IDEMPOTENCY_RETENTION_COUNT": "2",
        },
    ):
        store = FileGatewayStore(temp_dir)
        for index in range(3):
            store.save_package({"index": index})
            store.save_workflow(f"wf-{index}", {"index": index})
            store.save_idempotency(f"digest-{index}", {"index": index})

        stats = store.stats()
        assert stats["packages"]["items"] == 2
        assert stats["workflows"]["items"] == 2
        assert stats["idempotency"]["items"] == 2


def test_gateway_file_store_prunes_existing_records_on_open() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        with patch.dict(os.environ, {
            "GATEWAY_PACKAGE_RETENTION_COUNT": "3",
            "GATEWAY_PACKAGE_RETENTION_BYTES": "1000000",
            "GATEWAY_WORKFLOW_RETENTION_COUNT": "3",
            "GATEWAY_WORKFLOW_RETENTION_BYTES": "1000000",
            "GATEWAY_IDEMPOTENCY_RETENTION_COUNT": "3",
        }):
            store = FileGatewayStore(temp_dir)
            for index in range(3):
                store.save_package({"index": index})
                store.save_workflow(f"wf-{index}", {"index": index})
                store.save_idempotency(f"digest-{index}", {"index": index})
        with patch.dict(os.environ, {
            "GATEWAY_PACKAGE_RETENTION_COUNT": "2",
            "GATEWAY_PACKAGE_RETENTION_BYTES": "1000000",
            "GATEWAY_WORKFLOW_RETENTION_COUNT": "2",
            "GATEWAY_WORKFLOW_RETENTION_BYTES": "1000000",
            "GATEWAY_IDEMPOTENCY_RETENTION_COUNT": "2",
        }):
            reopened = FileGatewayStore(temp_dir)

        stats = reopened.stats()
        assert stats["packages"]["items"] == 2
        assert stats["workflows"]["items"] == 2
        assert stats["idempotency"]["items"] == 2
