from datetime import date

import pytest

from config.settings import settings
from src.risk.position_sizer import RiskManager

FUTURE_DAY = date(2099, 1, 1)


def test_position_size_allocates_the_fixed_unit_bet():
    expected = settings.POSITION_SIZE_USD / 2.0

    assert RiskManager().calculate_position_size(price_usd=2.0) == pytest.approx(expected)


def test_position_size_is_zero_for_a_non_positive_price():
    risk = RiskManager()

    assert risk.calculate_position_size(price_usd=0.0) == 0.0
    assert risk.calculate_position_size(price_usd=-5.0) == 0.0


def test_circuit_breaker_starts_open():
    assert RiskManager().can_open_position() is True


def test_circuit_breaker_trips_after_the_max_daily_loss():
    risk = RiskManager()

    risk.register_trade_result(-settings.MAX_DAILY_LOSS_USD)

    assert risk.can_open_position() is False
    assert risk.daily_drawdown_usd == pytest.approx(settings.MAX_DAILY_LOSS_USD)


def test_circuit_breaker_stays_open_below_the_max_daily_loss():
    risk = RiskManager()

    risk.register_trade_result(-(settings.MAX_DAILY_LOSS_USD - 1))

    assert risk.can_open_position() is True


def test_profits_offset_realized_losses():
    risk = RiskManager()

    risk.register_trade_result(-60.0)
    risk.register_trade_result(50.0)

    assert risk.daily_drawdown_usd == pytest.approx(10.0)
    assert risk.daily_realized_pnl_usd == pytest.approx(-10.0)


def test_drawdown_is_never_negative_on_a_profitable_day():
    risk = RiskManager()

    risk.register_trade_result(500.0)

    assert risk.daily_drawdown_usd == 0.0


def test_counters_reset_when_the_utc_day_rolls_over(monkeypatch):
    risk = RiskManager()
    risk.register_trade_result(-settings.MAX_DAILY_LOSS_USD)
    assert risk.can_open_position() is False

    monkeypatch.setattr(RiskManager, "_utc_day", staticmethod(lambda: FUTURE_DAY))

    assert risk.can_open_position() is True
    assert risk.daily_realized_pnl_usd == 0.0
    assert risk.daily_drawdown_usd == 0.0



def test_pool_based_optimal_allocation():
    risk = RiskManager()
    cash = 20.0  # $20 disponível em caixa

    alloc_high = risk.calculate_trade_allocation(
        cash_usd=cash,
        pool_liquidity_usd=100_000.0,
        vol_m5_usd=20_000.0,
        ignition_score=95.0,
    )
    alloc_low = risk.calculate_trade_allocation(
        cash_usd=cash,
        pool_liquidity_usd=5_000.0,
        vol_m5_usd=200.0,
        ignition_score=65.0,
    )

    assert alloc_high > alloc_low
    assert alloc_high <= round(settings.INITIAL_CAPITAL_USD * settings.MAX_CAPITAL_PER_TRADE_PCT, 2)
    assert alloc_low >= settings.MIN_POSITION_SIZE_USD


def test_shallow_pool_allocation_penalty():
    """Valida que pools abaixo de $35k sofrem penalidade côncava de dimensionamento de risco."""
    risk = RiskManager()
    cash = 100.0

    alloc_deep = risk.calculate_trade_allocation(
        cash_usd=cash,
        pool_liquidity_usd=50_000.0,
        vol_m5_usd=5_000.0,
        ignition_score=80.0,
    )
    alloc_shallow = risk.calculate_trade_allocation(
        cash_usd=cash,
        pool_liquidity_usd=15_000.0,
        vol_m5_usd=1_500.0,
        ignition_score=80.0,
    )

    assert alloc_deep > alloc_shallow
    # A alocação rasa deve ser expressivamente menor devido ao fator de liquidez
    assert alloc_shallow < alloc_deep * 0.85
