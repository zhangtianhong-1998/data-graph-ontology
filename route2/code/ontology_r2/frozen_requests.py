"""Bounded model proposals whose inputs are fixed before any callback runs.

Workers return proposals. They do not validate or commit ontology state.
The caller applies results in input order, so concurrency does not change
which proposal is committed first.
"""

from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from dataclasses import dataclass

from .llm import BudgetExceeded


@dataclass
class ProposalResult:
    value: object = None
    error: Exception | None = None
    attempted: bool = False


async def request_frozen(packets, decide, *, max_concurrent_requests=1, stop_on_budget=True):
    """Return input-ordered results.

    ``max_concurrent_requests`` 1 runs the callbacks one by one. A budget
    failure can stop later callbacks when ``stop_on_budget`` is true; those
    later packets are reported as not attempted.
    """
    if type(max_concurrent_requests) is not int or not 1 <= max_concurrent_requests <= 16:
        raise ValueError("max_concurrent_requests must be an integer in 1..16")
    frozen = deepcopy(list(packets))
    if max_concurrent_requests == 1:
        results = []
        exhausted = False
        for packet in frozen:
            if exhausted:
                results.append(ProposalResult(error=BudgetExceeded("Frozen stage budget exhausted")))
                continue
            try:
                value = decide(packet)
                if inspect.isawaitable(value):
                    value = await value
                results.append(ProposalResult(value=value, attempted=True))
            except Exception as exc:
                if stop_on_budget and isinstance(exc, BudgetExceeded):
                    exhausted = True
                results.append(ProposalResult(error=exc, attempted=True))
        return results

    gate = asyncio.Semaphore(max_concurrent_requests)
    exhausted = False

    async def request(packet):
        nonlocal exhausted
        async with gate:
            if exhausted:
                return ProposalResult(error=BudgetExceeded("Frozen stage budget exhausted"))
            try:
                value = decide(packet)
                if inspect.isawaitable(value):
                    value = await value
                return ProposalResult(value=value, attempted=True)
            except Exception as exc:
                if stop_on_budget and isinstance(exc, BudgetExceeded):
                    exhausted = True
                return ProposalResult(error=exc, attempted=True)

    return list(await asyncio.gather(*(request(packet) for packet in frozen)))
