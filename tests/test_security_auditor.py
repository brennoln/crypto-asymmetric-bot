from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import settings
from src.audit.holder_analyzer import SecurityAuditor


def _candidate(**overrides) -> dict:
    """Candidato que passa em todos os filtros; overrides forçam cada rejeição."""
    candidate = {
        "liquidity_usd": settings.MIN_LIQUIDITY_USD * 2,
        "age_minutes": settings.MIN_POOL_AGE_MINUTES * 2,
        "vol_m5_usd": settings.MIN_VOLUME_M5_USD * 2,
        "buys_m5": settings.MIN_BUYS_M5 * 2,
        "sells_m5": settings.MIN_BUYS_M5,
        "chain_id": "solana",
        "token_address": "SoL1111111111111111111111111111111111111111",
    }
    candidate.update(overrides)
    return candidate


def test_approves_a_candidate_that_clears_every_filter():
    passed, reason = SecurityAuditor.audit_token(_candidate())

    assert passed is True
    assert reason


def test_rejects_liquidity_below_the_lower_bound():
    passed, reason = SecurityAuditor.audit_token(
        _candidate(liquidity_usd=settings.MIN_LIQUIDITY_USD - 1)
    )

    assert passed is False
    assert "Liquidez" in reason


def test_rejects_liquidity_above_the_upper_bound():
    passed, reason = SecurityAuditor.audit_token(
        _candidate(liquidity_usd=settings.MAX_LIQUIDITY_USD + 1)
    )

    assert passed is False
    assert "excessiva" in reason


def test_rejects_a_pool_younger_than_the_sniper_window():
    passed, reason = SecurityAuditor.audit_token(
        _candidate(age_minutes=settings.MIN_POOL_AGE_MINUTES - 1)
    )

    assert passed is False
    assert "sniper" in reason


def test_rejects_a_pool_outside_the_age_window():
    passed, reason = SecurityAuditor.audit_token(
        _candidate(age_minutes=settings.MAX_POOL_AGE_HOURS * 60 + 1)
    )

    assert passed is False
    assert "fora da janela" in reason


def test_rejects_a_candidate_without_volume_ignition():
    passed, reason = SecurityAuditor.audit_token(
        _candidate(vol_m5_usd=settings.MIN_VOLUME_M5_USD - 1)
    )

    assert passed is False
    assert "volume" in reason


def test_rejects_a_candidate_with_too_few_buys():
    passed, reason = SecurityAuditor.audit_token(
        _candidate(buys_m5=settings.MIN_BUYS_M5 - 1)
    )

    assert passed is False
    assert "compras" in reason


def test_rejects_heavy_sell_pressure():
    passed, reason = SecurityAuditor.audit_token(
        _candidate(buys_m5=settings.MIN_BUYS_M5 * 2, sells_m5=settings.MIN_BUYS_M5 * 10)
    )

    assert passed is False
    assert "venda" in reason


def test_avoids_division_by_zero_when_there_are_no_sells():
    passed, _ = SecurityAuditor.audit_token(
        _candidate(buys_m5=settings.MIN_BUYS_M5 + 1, sells_m5=0)
    )

    assert passed is True


def test_rejects_with_a_reason_string_for_every_failure():
    _, reason = SecurityAuditor.audit_token(_candidate(liquidity_usd=0.0))

    assert isinstance(reason, str) and reason


# --- Testes de Auditoria On-Chain (v0.4) ---


@pytest.mark.asyncio
async def test_audit_token_onchain_detects_honeypot_zero_sells():
    session = AsyncMock()
    candidate = _candidate(buys_m5=20, sells_m5=0)
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is False
    assert "honeypot" in reason.lower()


@pytest.mark.asyncio
async def test_audit_token_onchain_detects_wash_trading():
    session = AsyncMock()
    candidate = _candidate(
        liquidity_usd=10_000.0,
        vol_m5_usd=200_000.0,  # 20x liquidez
        age_minutes=10.0,
    )
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is False
    assert "wash trading" in reason.lower()


@pytest.mark.asyncio
async def test_audit_token_onchain_rugcheck_approved():
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "score": 450,
            "status": "good",
            "risks": [{"name": "Low liquidity", "level": "warn", "score": 100}],
            "tokenMeta": {"freezeAuthority": None},
        }
    )

    cm = AsyncMock()
    cm.__aenter__.return_value = mock_resp
    cm.__aexit__.return_value = False
    session.get.return_value = cm

    candidate = _candidate(chain_id="solana", token_address="SafeToken111")
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is True
    assert "Score: 450" in reason


@pytest.mark.asyncio
async def test_audit_token_onchain_rugcheck_rejects_high_risk():
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "score": 2500,
            "status": "danger",
            "risks": [{"name": "Mint Authority still enabled", "level": "danger", "score": 2000}],
            "tokenMeta": {"freezeAuthority": None},
        }
    )

    cm = AsyncMock()
    cm.__aenter__.return_value = mock_resp
    cm.__aexit__.return_value = False
    session.get.return_value = cm

    candidate = _candidate(chain_id="solana", token_address="RugToken111")
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is False
    assert "RugCheck Score de Risco" in reason
    assert "Mint Authority" in reason


@pytest.mark.asyncio
async def test_audit_token_onchain_rugcheck_rejects_freeze_authority():
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "score": 300,
            "status": "good",
            "risks": [],
            "tokenMeta": {"freezeAuthority": "FreezeAuthKey111"},
        }
    )

    cm = AsyncMock()
    cm.__aenter__.return_value = mock_resp
    cm.__aexit__.return_value = False
    session.get.return_value = cm

    candidate = _candidate(chain_id="solana", token_address="FreezeToken111")
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is False
    assert "Freeze Authority" in reason

def test_rejects_negative_momentum_5m():
    passed, reason = SecurityAuditor.audit_token(
        _candidate(price_change_m5=-2.5)
    )
    assert passed is False
    assert "Momentum negativo" in reason


def test_rejects_hyper_extended_5m():
    passed, reason = SecurityAuditor.audit_token(
        _candidate(price_change_m5=180.0)
    )
    assert passed is False
    assert "hiper-estendido" in reason


@pytest.mark.asyncio
async def test_audit_token_onchain_rejects_dev_holding_exceeded():
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "score": 100,
            "creator": "dev_wallet_123",
            "topHolders": [
                {"address": "dev_wallet_123", "owner": "dev_wallet_123", "pct": 14.5}
            ],
            "tokenMeta": {},
        }
    )
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_resp
    cm.__aexit__.return_value = False
    session.get.return_value = cm

    candidate = _candidate(chain_id="solana", token_address="DevDumpToken")
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is False
    assert "Dev Holding excessivo" in reason
    assert "14.5%" in reason


@pytest.mark.asyncio
async def test_audit_token_onchain_rejects_whale_concentration():
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "score": 100,
            "creator": "dev_other",
            "topHolders": [
                {"address": "whale_wallet_999", "owner": "whale_wallet_999", "pct": 18.2}
            ],
            "tokenMeta": {},
        }
    )
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_resp
    cm.__aexit__.return_value = False
    session.get.return_value = cm

    candidate = _candidate(chain_id="solana", token_address="WhaleToken")
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is False
    assert "Baleia concentrada" in reason
    assert "18.2%" in reason


@pytest.mark.asyncio
async def test_audit_token_onchain_rejects_top10_concentration():
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    # 8 carteiras com 5% cada = 40% acumulado (> 30%)
    holders = [{"address": f"wallet_{i}", "owner": f"owner_{i}", "pct": 5.0} for i in range(8)]
    mock_resp.json = AsyncMock(
        return_value={
            "score": 100,
            "creator": "dev_other",
            "topHolders": holders,
            "tokenMeta": {},
        }
    )
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_resp
    cm.__aexit__.return_value = False
    session.get.return_value = cm

    candidate = _candidate(chain_id="solana", token_address="ConcentratedToken")
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is False
    assert "Top 10 Holders concentrado" in reason
    assert "40.0%" in reason


@pytest.mark.asyncio
async def test_audit_token_onchain_rejects_insiders_cabal():
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "score": 100,
            "creator": "dev_other",
            "topHolders": [
                {"address": "insider_1", "owner": "insider_1", "pct": 9.0, "insider": True},
                {"address": "insider_2", "owner": "insider_2", "pct": 8.0, "insider": True},
            ],
            "tokenMeta": {},
        }
    )
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_resp
    cm.__aexit__.return_value = False
    session.get.return_value = cm

    candidate = _candidate(chain_id="solana", token_address="CabalToken")
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is False
    assert "Alerta Cabal/Insiders" in reason
    assert "17.0%" in reason


@pytest.mark.asyncio
async def test_audit_token_onchain_rejects_rugged_token():
    session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(
        return_value={
            "score": 100,
            "rugged": True,
            "tokenMeta": {},
        }
    )
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_resp
    cm.__aexit__.return_value = False
    session.get.return_value = cm

    candidate = _candidate(chain_id="solana", token_address="DeadRugToken")
    passed, reason = await SecurityAuditor.audit_token_onchain(candidate, session)

    assert passed is False
    assert "Rugged" in reason
