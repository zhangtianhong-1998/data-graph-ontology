"""Frozen proposals keep input order and do not commit shared state."""
import asyncio

import pytest

from ontology_r2.frozen_requests import request_frozen
from ontology_r2.llm import BudgetExceeded


def test_concurrent_results_stay_in_input_order():
    async def decide(packet):
        await asyncio.sleep(packet["delay"])
        return packet["id"]

    packets = [{"id": "slow", "delay": 0.03}, {"id": "fast", "delay": 0.0}]
    results = asyncio.run(request_frozen(packets, decide, max_concurrent_requests=2))
    assert [item.value for item in results] == ["slow", "fast"]
    assert all(item.attempted and item.error is None for item in results)
    assert packets[0]["id"] == "slow"


def test_budget_failure_does_not_start_later_packets_when_serial():
    seen = []

    async def decide(packet):
        seen.append(packet)
        if packet == "stop":
            raise BudgetExceeded("budget")
        return packet

    results = asyncio.run(request_frozen(["ok", "stop", "later"], decide, stop_on_budget=True))
    assert seen == ["ok", "stop"]
    assert results[0].value == "ok"
    assert isinstance(results[1].error, BudgetExceeded) and results[1].attempted
    assert isinstance(results[2].error, BudgetExceeded) and not results[2].attempted


def test_concurrency_must_be_a_small_integer():
    with pytest.raises(ValueError, match="max_concurrent_requests"):
        asyncio.run(request_frozen([], lambda packet: packet, max_concurrent_requests=0))
