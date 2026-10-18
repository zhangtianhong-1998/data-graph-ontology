"""A shared wire boundary must serialize all callers and space completed calls."""
import asyncio
import contextvars
import time
from types import SimpleNamespace

import pytest

from ontology_r2.llm import StructuredLLM


@pytest.mark.parametrize('value', [0, 17, True, 1.5])
def test_reject_invalid_concurrency_before_request(tmp_path, value):
    with pytest.raises(ValueError, match='max_concurrent_requests'):
        StructuredLLM({'max_concurrent_requests': value}, tmp_path)


def test_live_requests_overlap_unless_mock_or_explicitly_serial(tmp_path):
    live = StructuredLLM({'mode': 'agentscope'}, tmp_path)
    assert live.max_concurrent_requests == 4
    responses = tmp_path / 'responses.yaml'
    responses.write_text('{}\n', encoding='utf-8')
    mock = StructuredLLM({'mode': 'mock', 'responses': str(responses)}, tmp_path)
    assert mock.max_concurrent_requests == 1


def test_higher_concurrency_overlaps_wire_calls(tmp_path, monkeypatch):
    llm = StructuredLLM({'max_concurrent_requests': 2, 'request_interval_seconds': 0,
                         'max_retries': 0}, tmp_path)
    model = SimpleNamespace(wire_observer=contextvars.ContextVar('observer', default=None))
    monkeypatch.setattr(llm, 'get_model', lambda: model)
    active = 0
    peak = 0

    async def transport(model, messages, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return SimpleNamespace(usage=None)

    monkeypatch.setattr('ontology_r2.llm.complete', transport)

    async def run():
        await asyncio.gather(*(llm.complete([], task='concept_bundle') for _ in range(2)))
    asyncio.run(run())
    assert peak == 2
    assert llm.metrics()['request_pacing']['max_concurrent_requests'] == 2


@pytest.mark.parametrize('value', [-0.1, True, '0.2', float('nan'), float('inf'), 61])
def test_reject_invalid_interval_before_request(tmp_path, value):
    with pytest.raises(ValueError, match='request_interval_seconds'):
        StructuredLLM({'request_interval_seconds': value}, tmp_path)


@pytest.mark.parametrize('interval', [0, 0.025])
def test_structured_and_react_share_serial_wire_boundary(tmp_path, monkeypatch, interval):
    llm = StructuredLLM({'request_interval_seconds': interval, 'max_retries': 0,
                         'max_concurrent_requests': 1}, tmp_path)
    model = SimpleNamespace(wire_observer=contextvars.ContextVar('observer', default=None))
    monkeypatch.setattr(llm, 'get_model', lambda: model)
    events, active = [], 0

    async def transport(model, messages, **kwargs):
        nonlocal active
        assert active == 0, 'Concurrent caller bypassed the wire lock'
        active += 1
        events.append(('start', time.monotonic()))
        await asyncio.sleep(0.004)
        active -= 1
        events.append(('end', time.monotonic()))
        return SimpleNamespace(usage=None)

    monkeypatch.setattr('ontology_r2.llm.complete', transport)

    async def run():
        await asyncio.gather(*(llm.complete([], task=task) for task in
                               ('concept_bundle', 'react', 'association_react')))
    asyncio.run(run())
    assert [kind for kind, _ in events] == ['start', 'end'] * 3
    for index in (2, 4):
        assert events[index][1] - events[index - 1][1] >= interval - 0.002


def test_failed_call_also_spaces_the_next_call(tmp_path, monkeypatch):
    llm = StructuredLLM({'request_interval_seconds': 0.025, 'max_retries': 0,
                         'max_concurrent_requests': 1}, tmp_path)
    model = SimpleNamespace(wire_observer=contextvars.ContextVar('observer', default=None))
    monkeypatch.setattr(llm, 'get_model', lambda: model)
    times = []

    async def transport(*args, **kwargs):
        times.append(time.monotonic())
        if len(times) == 1:
            raise TimeoutError('local test')
        return SimpleNamespace(usage=None)
    monkeypatch.setattr('ontology_r2.llm.complete', transport)

    async def run():
        with pytest.raises(TimeoutError):
            await llm.complete([], task='concept_bundle')
        await llm.complete([], task='react')
    asyncio.run(run())
    assert times[1] - times[0] >= 0.023
