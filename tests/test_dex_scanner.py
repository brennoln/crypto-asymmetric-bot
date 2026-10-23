from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.scanner.dex_scanner import DexScanner


@pytest.mark.asyncio
async def test_scanner_get_success():
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"pairs": []})

    cm = AsyncMock()
    cm.__aenter__.return_value = mock_resp
    cm.__aexit__.return_value = False
    session.get.return_value = cm

    scanner = DexScanner(session)
    res = await scanner._get("https://test.api")
    assert res == {"pairs": []}


@pytest.mark.asyncio
async def test_scanner_get_retries_on_rate_limit(monkeypatch):
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)

    session = MagicMock()
    resp_429 = AsyncMock()
    resp_429.status = 429

    resp_200 = AsyncMock()
    resp_200.status = 200
    resp_200.json = AsyncMock(return_value={"ok": True})

    cm_429 = AsyncMock()
    cm_429.__aenter__.return_value = resp_429
    cm_429.__aexit__.return_value = False

    cm_200 = AsyncMock()
    cm_200.__aenter__.return_value = resp_200
    cm_200.__aexit__.return_value = False

    session.get.side_effect = [cm_429, cm_200]

    scanner = DexScanner(session)
    res = await scanner._get("https://test.api")

    assert res == {"ok": True}
    assert sleep_mock.await_count == 1


@pytest.mark.asyncio
async def test_scanner_get_retries_on_client_error(monkeypatch):
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)

    session = MagicMock()
    resp_200 = AsyncMock()
    resp_200.status = 200
    resp_200.json = AsyncMock(return_value={"ok": True})

    cm_200 = AsyncMock()
    cm_200.__aenter__.return_value = resp_200
    cm_200.__aexit__.return_value = False

    session.get.side_effect = [aiohttp.ClientError("Connection refused"), cm_200]

    scanner = DexScanner(session)
    res = await scanner._get("https://test.api")

    assert res == {"ok": True}
    assert sleep_mock.await_count == 1


def test_compute_ignition_score_ranking():
    c_weak = {
        "liquidity_usd": 10_000.0,
        "vol_m5_usd": 1_000.0,
        "vol_h1_usd": 12_000.0,
        "buys_m5": 10,
        "sells_m5": 10,
        "age_minutes": 60.0,
        "price_change_m5": 0.0,
    }
    c_strong = {
        "liquidity_usd": 10_000.0,
        "vol_m5_usd": 15_000.0,
        "vol_h1_usd": 20_000.0,
        "buys_m5": 50,
        "sells_m5": 5,
        "age_minutes": 60.0,
        "price_change_m5": 15.0,
    }

    score_weak = DexScanner.compute_ignition_score(c_weak)
    score_strong = DexScanner.compute_ignition_score(c_strong)

    assert score_strong > score_weak
    assert score_strong > 50.0


def test_compute_ignition_score_temporal_normalization():
    # Em uma pool de 15 minutos, baseline_m5 divide por 3 (15/5), evitando inflar artificialmente por 12
    cand_young = {
        "liquidity_usd": 10_000.0,
        "vol_m5_usd": 3_000.0,
        "vol_h1_usd": 9_000.0,  # 9000 em 15m => média é exatamente 3000 por 5m (vol_acceleration = 1.0)
        "buys_m5": 10,
        "sells_m5": 10,
        "age_minutes": 15.0,
    }
    score = DexScanner.compute_ignition_score(cand_young)
    assert score > 0.0


def test_compute_ignition_score_penalizes_falling_knife():
    cand_pumping = {
        "liquidity_usd": 20_000.0,
        "vol_m5_usd": 5_000.0,
        "vol_h1_usd": 15_000.0,
        "buys_m5": 20,
        "sells_m5": 10,
        "price_change_m5": +12.0,
    }
    cand_dumping = {
        "liquidity_usd": 20_000.0,
        "vol_m5_usd": 5_000.0,
        "vol_h1_usd": 15_000.0,
        "buys_m5": 20,
        "sells_m5": 10,
        "price_change_m5": -18.0,
    }

    score_pumping = DexScanner.compute_ignition_score(cand_pumping)
    score_dumping = DexScanner.compute_ignition_score(cand_dumping)

    assert score_pumping > score_dumping
