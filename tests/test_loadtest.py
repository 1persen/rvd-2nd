"""Checks for the standalone staging load-test report and safety boundary."""
import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("loadtest", Path(__file__).parents[1] / "scripts/loadtest.py")
loadtest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loadtest)


def test_nearest_rank_percentiles_stay_within_observations():
    assert loadtest.pct([], 95) == 0
    assert loadtest.pct([11], 95) == 11
    assert loadtest.pct([10, 20], 99) == 20
    assert loadtest.pct(list(range(1, 101)), 95) == 95


def test_separate_sessions_and_legacy_users():
    args = loadtest.parse_args(["--event", "test", "--viewers", "1000", "--voters", "100", "--burst", "--settle"])
    assert (args.viewers, args.voters, args.burst) == (1000, 100, True)
    legacy = loadtest.parse_args(["--event", "test", "--users", "2", "--settle"])
    assert (legacy.viewers, legacy.voters) == (2, 2)


@pytest.mark.parametrize("arguments", [
    ["--voters", "1"],
    ["--viewers", "-1", "--voters", "0"],
    ["--viewers", "0", "--voters", "0"],
    ["--base", "https://example.com", "--voters", "1", "--settle"],
])
def test_rejects_unsafe_or_empty_workloads(arguments):
    with pytest.raises(SystemExit):
        loadtest.parse_args(["--event", "test", *arguments])


def test_integrity_requires_every_stream_to_receive_final_total():
    result = loadtest.integrity(10, 15, 5, [{"last_votes": 15}, {"last_votes": 10}])
    assert result["leaderboard_matches"] is True
    assert result["sse_viewers_at_expected_total"] == 1
    assert result["all_sse_match"] is False
    assert loadtest.integrity(10, 16, 5, [{"last_votes": 15}])["leaderboard_matches"] is False


@pytest.mark.asyncio
async def test_unready_sse_aborts_before_invoice_creation(monkeypatch):
    import httpx

    paths = []

    def respond(request):
        paths.append(request.url.path)
        if '/mock/load-probe-' in request.url.path:
            return httpx.Response(400, json={"message": "Invoice tidak ditemukan"})
        if request.url.path.endswith('/stream'):
            return httpx.Response(503)
        return httpx.Response(200, json={"success": True, "data": {"entries": [{"team_id": "test", "votes": 0}]}})

    original = httpx.AsyncClient
    monkeypatch.setattr(loadtest.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    args = loadtest.parse_args(['--event', 'test', '--viewers', '1', '--voters', '1', '--settle', '--ready-timeout', '0.01'])
    report = await loadtest.run(args)
    assert report['passed'] is False
    assert report['sse']['ready'] == 0
    assert '/api/v1/payments' not in paths


@pytest.mark.skipif(sys.platform == 'win32', reason='resource.getrlimit unavailable on Windows; loadtest.py handles the same way.')
@pytest.mark.asyncio
async def test_low_descriptor_limit_stops_before_any_http(monkeypatch):
    import httpx

    monkeypatch.setattr(loadtest.resource, 'getrlimit', lambda _: (1024, 1048576))

    def unexpected_request(request):
        pytest.fail(f'Low descriptor limit must prevent network requests: {request.url.path}')

    original = httpx.AsyncClient
    monkeypatch.setattr(loadtest.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(unexpected_request), **kwargs))
    args = loadtest.parse_args(['--event', 'test', '--viewers', '1000', '--voters', '100', '--burst', '--settle'])
    report = await loadtest.run(args)
    assert report['passed'] is False
    assert report['file_descriptors'] == {'checked': True, 'soft': 1024, 'hard': 1048576, 'required': 2174, 'sufficient': False}
    assert 'ulimit -n 2174' in report['failure']
    assert report['sse']['ready'] == 0
    assert report['latency_ms'] == {}
