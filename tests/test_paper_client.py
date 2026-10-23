import aiosqlite
import pytest

from data.database import db_manager
from src.execution.paper_client import PaperExecutionClient
from src.risk.position_sizer import RiskManager

# Escada do HoldingMap: (multiplier, sell_pct, preço no gatilho)
LADDER = ((2.0, 0.50, 2.0), (5.0, 0.30, 5.0), (25.0, 0.20, 25.0))
UNIT_SIZE = 10.0


def _candidate(**overrides) -> dict:
    candidate = {
        "symbol": "TEST",
        "token_address": "0xabc",
        "chain_id": "solana",
        "pair_address": "PAIR1",
        "price_usd": 1.0,
    }
    candidate.update(overrides)
    return candidate


async def _trade_row():
    async with aiosqlite.connect(db_manager.db_path) as db:
        async with db.execute(
            "SELECT status, exit_reason, closed_at FROM trade_records"
        ) as cursor:
            return await cursor.fetchone()


async def test_open_position_records_the_trade_in_the_database(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient()

    trade = await client.open_position(
        _candidate(), token_amount=UNIT_SIZE, invested_usd=UNIT_SIZE
    )

    assert trade["status"] == "OPEN"
    assert trade["remaining_amount"] == pytest.approx(UNIT_SIZE)
    assert await _trade_row() == ("OPEN", None, None)


async def test_opened_at_carries_the_full_utc_timestamp(temp_db):
    await db_manager.init_db()

    trade = await PaperExecutionClient().open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    assert "T" in trade["opened_at"]
    assert trade["opened_at"].endswith("+00:00")


async def test_partial_sell_keeps_the_position_open_while_size_remains(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient()
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    await client.execute_partial_sell(trade, multiplier=2.0, sell_pct=0.50, current_price=2.0)

    assert trade["status"] == "OPEN"
    assert trade["remaining_amount"] == pytest.approx(5.0)
    assert await _trade_row() == ("OPEN", None, None)


async def test_the_two_x_step_makes_the_position_risk_free(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient()
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    await client.execute_partial_sell(trade, multiplier=2.0, sell_pct=0.50, current_price=2.0)

    assert trade["realized_pnl_usd"] == pytest.approx(UNIT_SIZE)
    assert trade["realized_pnl_usd"] - trade["invested_usd"] == pytest.approx(0.0)


async def test_full_ladder_persists_the_closed_status(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient()
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    for multiplier, sell_pct, price in LADDER:
        await client.execute_partial_sell(trade, multiplier, sell_pct, current_price=price)

    assert trade["status"] == "CLOSED"
    assert trade["remaining_amount"] == 0.0

    status, exit_reason, closed_at = await _trade_row()
    assert status == "CLOSED"
    assert exit_reason
    assert closed_at is not None


async def test_a_fully_realized_position_settles_exactly_once(temp_db):
    await db_manager.init_db()
    risk = RiskManager()
    client = PaperExecutionClient(risk_manager=risk)
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    for multiplier, sell_pct, price in LADDER:
        await client.execute_partial_sell(trade, multiplier, sell_pct, current_price=price)

    # Receita: 10 (2x) + 15 (5x) + 50 (25x) = 75 -> bruto 65.
    # Taxas de rede (v0.3): 4 swaps * $0.15 = $0.60 -> líquido real: $64.40.
    assert risk.daily_realized_pnl_usd == pytest.approx(64.40)


async def test_drop_dead_position_persists_the_stop(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient()
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    await client.drop_dead_position(trade, reason="stop técnico", current_price=0.5)

    assert trade["status"] == "DROPPED"
    assert trade["realized_pnl_usd"] == pytest.approx(5.0)

    status, exit_reason, closed_at = await _trade_row()
    assert status == "DROPPED"
    assert exit_reason == "stop técnico"
    assert closed_at is not None


async def test_drop_stagnant_position_persists_the_rotation(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient()
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    await client.drop_stagnant_position(trade, age_min=6.0, current_price=1.2)

    assert trade["status"] == "STAGNANT"

    status, exit_reason, closed_at = await _trade_row()
    assert status == "STAGNANT"
    assert exit_reason == "Time-Stop / Inércia"
    assert closed_at is not None


async def test_close_position_loss_persists_a_rugpull(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient()
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    await client.close_position_loss(trade, reason="Liquidez drenada")

    assert trade["status"] == "DEAD"
    assert trade["remaining_amount"] == 0.0

    status, exit_reason, closed_at = await _trade_row()
    assert status == "DEAD"
    assert exit_reason == "Liquidez drenada"
    assert closed_at is not None


async def test_a_losing_close_is_reported_to_the_risk_manager(temp_db):
    await db_manager.init_db()
    risk = RiskManager()
    client = PaperExecutionClient(risk_manager=risk)
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    await client.drop_dead_position(trade, reason="stop técnico", current_price=0.5)

    # Perda bruta: -$5.00 | Taxas de rede (1 compra + 1 venda): -$0.30 -> Líquido: -$5.30
    assert risk.daily_realized_pnl_usd == pytest.approx(-5.30)
    assert risk.daily_drawdown_usd == pytest.approx(5.30)


async def test_a_winning_close_does_not_add_to_the_drawdown(temp_db):
    await db_manager.init_db()
    risk = RiskManager()
    client = PaperExecutionClient(risk_manager=risk)
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    await client.drop_dead_position(trade, reason="alvo atingido", current_price=3.0)

    # Ganho bruto: +$20.00 | Taxas de rede (1 compra + 1 venda): -$0.30 -> Líquido: +$19.70
    assert risk.daily_realized_pnl_usd == pytest.approx(19.70)
    assert risk.daily_drawdown_usd == 0.0



async def test_execution_client_works_without_a_risk_manager(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient()
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    await client.drop_dead_position(trade, reason="stop técnico", current_price=0.5)

    assert trade["status"] == "DROPPED"


async def test_drop_stagnant_position_with_custom_reason(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient()
    trade = await client.open_position(_candidate(), UNIT_SIZE, UNIT_SIZE)

    custom_reason = "Inércia: retorno negativo e Z-Score zerado após 10m"
    await client.drop_stagnant_position(
        trade, age_min=10.5, current_price=0.9, reason=custom_reason
    )

    assert trade["status"] == "STAGNANT"
    status, exit_reason, closed_at = await _trade_row()
    assert status == "STAGNANT"
    assert exit_reason == custom_reason
    assert closed_at is not None


def test_compute_price_impact_formula():
    client = PaperExecutionClient(enable_price_impact=True)

    # Ordem de $15 em pool de $10.000 (reserva de cotação = $5.000)
    # Impacto = 15 / (5000 + 15) = 15 / 5015 ≈ 0.002991 (0.30%)
    impact_small = client.compute_price_impact(order_usd=15.0, pool_liquidity_usd=10_000.0)
    assert impact_small == pytest.approx(15.0 / 5015.0, rel=1e-4)

    # Ordem grande: $1.000 em pool de $10.000 (reserva = $5.000)
    # Impacto = 1000 / 6000 ≈ 16.67%
    impact_large = client.compute_price_impact(order_usd=1000.0, pool_liquidity_usd=10_000.0)
    assert impact_large == pytest.approx(1000.0 / 6000.0, rel=1e-4)

    # Sem liquidez ou ordem zerada
    assert client.compute_price_impact(order_usd=0.0, pool_liquidity_usd=10_000.0) == 0.0
    assert client.compute_price_impact(order_usd=15.0, pool_liquidity_usd=0.0) == 0.0


async def test_buy_executes_with_price_impact_and_debits_gas(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient(gas_fee_usd=0.20, enable_price_impact=True)

    # Pool de $10.000 (reserva = $5.000), entrada de $100
    # Impacto = 100 / 5100 ≈ 0.0196 (1.96%)
    # Preço spot = 1.0 -> Preço executado = 1.0 * (1 + 0.0196) = 1.0196
    cand = _candidate(price_usd=1.0, liquidity_usd=10_000.0)
    trade = await client.open_position(cand, token_amount=100.0, invested_usd=100.0)

    expected_impact = 100.0 / 5100.0
    expected_exec_price = 1.0 * (1.0 + expected_impact)
    expected_tokens = 100.0 / expected_exec_price

    assert trade["entry_price_impact"] == pytest.approx(expected_impact, rel=1e-4)
    assert trade["entry_price"] == pytest.approx(expected_exec_price, rel=1e-4)
    assert trade["initial_amount"] == pytest.approx(expected_tokens, rel=1e-4)
    assert trade["gas_fees_usd"] == pytest.approx(0.20)


async def test_partial_sell_applies_sell_impact_and_gas(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient(gas_fee_usd=0.10, enable_price_impact=True)
    cand = _candidate(price_usd=1.0, liquidity_usd=20_000.0)
    trade = await client.open_position(cand, token_amount=10.0, invested_usd=10.0)

    # Vende 50% (5 tokens) a preço 2.0 (ordem bruta = $10.00) em pool de $20.000 (reserva = $10.000)
    # Impacto venda = 10 / (10000 + 10) = 10 / 10010 ≈ 0.000999 (0.10%)
    # Preço efetivo = 2.0 * (1 - 0.000999) ≈ 1.998
    await client.execute_partial_sell(
        trade, multiplier=2.0, sell_pct=0.50, current_price=2.0, current_liq=20_000.0
    )

    tokens_sold = trade["initial_amount"] * 0.50
    gross_value = tokens_sold * 2.0
    expected_sell_impact = client.compute_price_impact(gross_value, 20_000.0)
    expected_revenue = tokens_sold * (2.0 * (1.0 - expected_sell_impact))

    assert trade["realized_pnl_usd"] == pytest.approx(expected_revenue, rel=1e-4)
    assert trade["gas_fees_usd"] == pytest.approx(0.20)  # 1 compra ($0.10) + 1 venda ($0.10)





async def test_execution_applies_lp_swap_fee_when_configured(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient(
        gas_fee_usd=0.0,
        enable_price_impact=True,
        lp_swap_fee_pct=0.003,  # 0.30%
    )
    cand = _candidate(price_usd=1.0, liquidity_usd=100_000.0)
    # Impacto de $10 em $100k = 10 / (50000 + 10) ≈ 0.00019996
    # Exec price = 1.0 * (1 + impact) * (1 + 0.003)
    trade = await client.open_position(cand, token_amount=10.0, invested_usd=10.0)

    expected_impact = client.compute_price_impact(10.0, 100_000.0)
    expected_exec = 1.0 * (1.0 + expected_impact) * 1.003
    assert trade["entry_price"] == pytest.approx(expected_exec, rel=1e-4)
    assert trade["initial_liquidity_usd"] == 100_000.0


async def test_realized_pnl_exact_accounting_matches_cash(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient(
        initial_capital_usd=100.0,
        gas_fee_usd=0.15,
        enable_price_impact=False,
    )
    # Abre posição de $10 -> Caixa deve cair para 100 - (10 + 0.15) = 89.85
    trade = await client.open_position(_candidate(price_usd=1.0), token_amount=10.0, invested_usd=10.0)
    assert client.cash_usd == pytest.approx(89.85)

    # Vende com prejuízo a $0.80 (recupera $8.00 - $0.15 gás = $7.85)
    await client.drop_dead_position(trade, reason="Stop Loss", current_price=0.80)
    # Caixa após venda: 89.85 + 7.85 = 97.70
    assert client.cash_usd == pytest.approx(97.70)
    # PnL líquido realizado: $7.85 - $10.15 = -$2.30
    assert client.total_realized_pnl_usd == pytest.approx(-2.30)
    # Saldo em caixa = inicial ($100) + PnL Líquido (-$2.30) = $97.70
    assert client.cash_usd == pytest.approx(client.initial_capital_usd + client.total_realized_pnl_usd)


async def test_partial_sell_custom_label(temp_db):
    await db_manager.init_db()
    client = PaperExecutionClient(initial_capital_usd=50.0, gas_fee_usd=0.10, enable_price_impact=False)
    trade = await client.open_position(_candidate(price_usd=1.0), token_amount=10.0, invested_usd=10.0)

    # Venda parcial de 10% com custom_label
    await client.execute_partial_sell(
        trade=trade,
        multiplier=1.5,
        sell_pct=0.10,
        current_price=1.5,
        custom_label="COBERTURA DE TAXAS",
    )
    assert trade["remaining_amount"] == pytest.approx(9.0)
    assert 1.5 in trade["executed_targets"]
