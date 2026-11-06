import time
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock

from config.settings import settings
from data.database import db_manager
from src.audit.regime_gate import MarketRegimeGate
from src.execution.paper_client import PaperExecutionClient
from src.risk.position_sizer import RiskManager


def test_sequential_stagnation_circuit_breaker():
    """Valida se 2 saídas consecutivas por estagnação ativam o disjuntor de cooldown."""
    gate = MarketRegimeGate(
        stagnant_threshold=2,
        stagnant_cooldown_sec=3600.0,
    )
    now = 1000000.0

    # Trade 1: Saiu por Time-Stop com pico de 1.02x
    gate.record_trade_exit(
        exit_multiplier=0.99,
        peak_multiplier=1.02,
        exit_reason="Time-Stop: estagnado após 35m",
        exit_timestamp=now,
    )
    assert gate.consecutive_stagnant_count == 1
    in_cool, _, _ = gate.is_in_cooldown(current_time=now)
    assert in_cool is False

    # Trade 2: Saiu por Inércia com pico de 1.04x
    gate.record_trade_exit(
        exit_multiplier=0.98,
        peak_multiplier=1.04,
        exit_reason="Inércia/Estagnação: preço negativo",
        exit_timestamp=now + 60,
    )
    assert gate.consecutive_stagnant_count == 2
    in_cool, reason, rem = gate.is_in_cooldown(current_time=now + 60)
    assert in_cool is True
    assert "Circuit Breaker" in reason
    assert rem > 0

    # Auditoria de candidato durante o cooldown deve vetar
    approved, audit_reason, mult = gate.audit_candidate(
        candidate={"symbol": "TEST", "z_score": 3.0},
        current_time=now + 100,
    )
    assert approved is False
    assert "Circuit Breaker" in audit_reason
    assert mult == 0.0

    # Trade com alta saudável (1.35x) deve resetar o contador
    gate.record_trade_exit(
        exit_multiplier=1.25,
        peak_multiplier=1.35,
        exit_reason="Take-Profit",
        exit_timestamp=now + 4000,
    )
    assert gate.consecutive_stagnant_count == 0


def test_macro_health_gate():
    """Valida bloqueio quando o ativo base (SOL) está em sangria abrupta de 1h."""
    gate = MarketRegimeGate(min_sol_1h_return=-2.0)
    gate.last_macro_update_ts = time.time()

    # SOL caindo -3.2% na última hora -> VETO MACRO
    gate.sol_change_1h = -3.2
    is_safe, reason = gate.is_macro_safe()
    assert is_safe is False
    assert "Macro Veto" in reason

    # SOL subindo +1.5% -> SEGURO
    gate.sol_change_1h = 1.5
    is_safe, reason = gate.is_macro_safe()
    assert is_safe is True
    assert "Macro Saudável" in reason


def test_session_liquidity_gate():
    """Valida diferenciação entre a Zona Morta da madrugada e o Horário Nobre."""
    gate = MarketRegimeGate(dead_zone_start_utc=4, dead_zone_end_utc=10)

    # 06:00 UTC -> Zona Morta
    t_dead = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)
    session_dead = gate.get_session_regime(t_dead)
    assert session_dead["is_dead_zone"] is True
    assert session_dead["size_multiplier"] == settings.DEAD_ZONE_SIZE_MULTIPLIER

    # Candidato com anomalia fraca (Z=1.5) na Zona Morta deve ser barrado
    gate.last_macro_update_ts = 0.0  # Warmup
    approved, reason, mult = gate.audit_candidate(
        candidate={"symbol": "LATE", "z_score": 1.5, "ignition_score": 70.0},
        utc_time=t_dead,
    )
    assert approved is False
    assert "Zona Morta" in reason

    # Candidato com super-anomalia (Z=2.6) na Zona Morta deve ser aprovado com sizing reduzido
    approved, reason, mult = gate.audit_candidate(
        candidate={"symbol": "SUPER", "z_score": 2.6, "ignition_score": 80.0},
        utc_time=t_dead,
    )
    assert approved is True
    assert mult == settings.DEAD_ZONE_SIZE_MULTIPLIER

    # 16:00 UTC -> Horário Nobre
    t_golden = datetime(2026, 11, 5, 16, 0, tzinfo=timezone.utc)
    session_golden = gate.get_session_regime(t_golden)
    assert session_golden["is_golden"] is True
    assert session_golden["size_multiplier"] == 1.0


def test_position_sizer_regime_multiplier():
    """Garante que o position sizer honra o multiplicador de regime da Zona Morta."""
    rm = RiskManager()
    base_alloc = rm.calculate_trade_allocation(
        cash_usd=100.0,
        pool_liquidity_usd=50_000.0,
        vol_m5_usd=5_000.0,
        ignition_score=80.0,
        regime_size_multiplier=1.0,
    )
    dead_alloc = rm.calculate_trade_allocation(
        cash_usd=100.0,
        pool_liquidity_usd=50_000.0,
        vol_m5_usd=5_000.0,
        ignition_score=80.0,
        regime_size_multiplier=0.50,
    )
    assert dead_alloc < base_alloc
    assert dead_alloc >= settings.MIN_POSITION_SIZE_USD


@pytest.mark.asyncio
async def test_paper_client_integration_with_regime_gate(temp_db):
    """Garante que fechamentos no PaperExecutionClient notificam o Regime Gate."""
    await db_manager.init_db()
    gate = MarketRegimeGate(stagnant_threshold=2, stagnant_cooldown_sec=1800.0)
    client = PaperExecutionClient(initial_capital_usd=100.0, regime_gate=gate)

    fake_trade = {
        "token_symbol": "DORMANT",
        "token_address": "DORMANT_ADDR_123",
        "pair_address": "PAIR_123",
        "chain_id": "solana",
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "entry_price": 1.0,
        "remaining_amount": 10.0,
        "invested_usd": 10.0,
        "realized_pnl_usd": 0.0,
        "opened_timestamp": time.time() - 2100,
        "peak_multiplier": 1.03,
        "gas_fees_usd": 0.15,
        "status": "OPEN",
    }
    client.positions.append(fake_trade)
    await db_manager.insert_trade(fake_trade)

    await client.drop_stagnant_position(
        trade=fake_trade,
        age_min=35.0,
        current_price=0.99,
        reason="Time-Stop: estagnado após 35m",
        current_liq=20_000.0,
    )

    assert gate.consecutive_stagnant_count == 1
