import pytest

from config.settings import settings
from data.database import db_manager
from src.execution.paper_client import PaperExecutionClient


def _candidate(**overrides) -> dict:
    cand = {
        "symbol": "SOLTOKEN",
        "token_address": "TokenAddr123",
        "chain_id": "solana",
        "pair_address": "PairAddr123",
        "price_usd": 1.0,
        "liquidity_usd": 50_000.0,
    }
    cand.update(overrides)
    return cand


def should_trigger_breakeven_or_early_lock(trade: dict, current_price: float) -> tuple[bool, str]:
    """Função idêntica à regra implementada no position_watcher do main.py."""
    entry = trade["entry_price"]
    peak_price = max(trade.get("peak_price", entry), current_price)
    ratio = current_price / entry if entry > 0 else 0
    peak_ratio = peak_price / entry if entry > 0 else 0
    is_risk_free = 2.0 in trade.get("executed_targets", [])

    if (
        settings.ENABLE_BREAKEVEN_PROTECTION
        and not is_risk_free
        and peak_ratio >= settings.BREAKEVEN_ACTIVATION_RATIO
        and peak_ratio < settings.TRAILING_ACTIVATION_RATIO
    ):
        if ratio <= settings.BREAKEVEN_FLOOR_RATIO:
            return True, "Stop Breakeven ativado"

        drawdown_from_peak = (peak_price - current_price) / peak_price
        if drawdown_from_peak >= settings.BREAKEVEN_PULLBACK_DROP and ratio >= 1.00:
            return True, "Early Profit Lock"

    return False, ""


def test_breakeven_triggers_when_pulling_back_to_floor():
    trade = {"entry_price": 1.0, "peak_price": 1.20, "executed_targets": []}
    # Preço cai de 1.20 para 1.015 (abaixo de 1.02)
    triggered, reason = should_trigger_breakeven_or_early_lock(trade, current_price=1.015)
    assert triggered is True
    assert "Breakeven" in reason


def test_early_profit_lock_triggers_on_15pct_drawdown_from_peak():
    trade = {"entry_price": 1.0, "peak_price": 1.30, "executed_targets": []}
    # Preço cai de 1.30 para 1.10 (queda de 15.38% do topo, mantendo lucro de +10%)
    triggered, reason = should_trigger_breakeven_or_early_lock(trade, current_price=1.10)
    assert triggered is True
    assert "Early Profit Lock" in reason


def test_breakeven_does_not_trigger_if_peak_under_activation():
    trade = {"entry_price": 1.0, "peak_price": 1.10, "executed_targets": []}
    # Subiu apenas até 1.10 (+10%), abaixo do gatilho de 1.15
    triggered, _ = should_trigger_breakeven_or_early_lock(trade, current_price=1.01)
    assert triggered is False


def test_breakeven_does_not_trigger_if_in_trailing_stop_regime():
    trade = {"entry_price": 1.0, "peak_price": 1.45, "executed_targets": []}
    # Subiu até 1.45 (>= 1.40), o regime de Trailing Stop pleno se aplica
    triggered, _ = should_trigger_breakeven_or_early_lock(trade, current_price=1.35)
    assert triggered is False


def should_trigger_monotonic_trailing_stop(trade: dict, current_price: float) -> tuple[bool, str]:
    """Valida o trailing stop monotônico da v0.5."""
    entry = trade["entry_price"]
    peak_price = max(trade.get("peak_price", entry), current_price)
    ratio = current_price / entry if entry > 0 else 0
    peak_ratio = peak_price / entry if entry > 0 else 0
    is_risk_free = 2.0 in trade.get("executed_targets", [])

    if peak_ratio >= settings.TRAILING_ACTIVATION_RATIO:
        drawdown_from_peak = (peak_price - current_price) / peak_price
        drop_threshold = (
            settings.TRAILING_DROP_POST_2X
            if is_risk_free
            else settings.TRAILING_DROP_PRE_2X
        )
        min_ratio_guard = 1.40 if is_risk_free else 1.18

        if drawdown_from_peak >= drop_threshold or ratio <= min_ratio_guard:
            return True, "Trailing Stop Monotônico"

    return False, ""


def should_trigger_invariant_anti_rug(trade: dict, current_price: float, current_liq: float) -> tuple[bool, str]:
    """Valida o detector de invariante Lambda_t < 0.85 da v0.5."""
    entry = trade["entry_price"]
    init_liq = float(trade.get("initial_liquidity_usd", 0.0) or 0.0)
    ratio = current_price / entry if entry > 0 else 0

    if settings.ENABLE_INVARIANT_ANTI_RUG and init_liq > 0 and ratio > 0:
        expected_liq_ratio = ratio ** 0.5
        actual_liq_ratio = current_liq / init_liq
        lambda_invariant = actual_liq_ratio / expected_liq_ratio if expected_liq_ratio > 0 else 1.0

        if lambda_invariant < settings.MIN_INVARIANT_LIQUIDITY_RATIO:
            return True, f"Invariante violada (Λ={lambda_invariant:.2f})"

    return False, ""


def test_monotonic_trailing_stop_prevents_sub_1_18_drop():
    trade = {"entry_price": 1.0, "peak_price": 1.40, "executed_targets": []}
    # Preço cai para 1.17 (abaixo do piso monotônico de 1.18 conquistado no degrau anterior)
    triggered, reason = should_trigger_monotonic_trailing_stop(trade, current_price=1.17)
    assert triggered is True
    assert "Trailing Stop Monotônico" in reason


def test_invariant_anti_rug_detects_silent_liquidity_drain():
    # Preço subiu de 1.0 para 1.44 (esperado: sqrt(1.44) = 1.20x liquidez)
    # Mas liquidez caiu de 20.000 para 16.000 (0.80x do inicial)
    # Lambda = 0.80 / 1.20 = 0.67 < 0.85
    trade = {"entry_price": 1.0, "initial_liquidity_usd": 20_000.0, "executed_targets": []}
    triggered, reason = should_trigger_invariant_anti_rug(
        trade, current_price=1.44, current_liq=16_000.0
    )
    assert triggered is True
    assert "Invariante violada" in reason


def test_invariant_anti_rug_allows_normal_trading_pool():
    # Preço subiu de 1.0 para 1.44 (esperado: sqrt(1.44) = 1.20x)
    # Liquidez acompanhou normalmente em 24.000 (1.20x)
    # Lambda = 1.20 / 1.20 = 1.00 >= 0.85
    trade = {"entry_price": 1.0, "initial_liquidity_usd": 20_000.0, "executed_targets": []}
    triggered, _ = should_trigger_invariant_anti_rug(
        trade, current_price=1.44, current_liq=24_000.0
    )
    assert triggered is False
