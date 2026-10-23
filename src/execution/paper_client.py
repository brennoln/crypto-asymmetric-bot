import time
from datetime import datetime, timezone

from config.settings import settings
from data.database import db_manager
from src.utils.logger import log


class PaperExecutionClient:
    """Simulated order management system with realistic market friction (v0.3 / v0.5).

    Models Automated Market Maker (AMM x * y = k) price impact, pool swap fees
    (LP fee) and network transaction fees (gas/priority fees) so simulated performance
    matches real decentralized exchange dynamics.
    """

    def __init__(
        self,
        risk_manager=None,
        gas_fee_usd: float | None = None,
        enable_price_impact: bool | None = None,
        lp_swap_fee_pct: float = 0.0,
    ):
        self.risk_manager = risk_manager
        self.gas_fee_usd = settings.GAS_FEE_USD if gas_fee_usd is None else gas_fee_usd
        self.enable_price_impact = (
            settings.ENABLE_PRICE_IMPACT if enable_price_impact is None else enable_price_impact
        )
        self.lp_swap_fee_pct = lp_swap_fee_pct
        self.positions: list[dict] = []

    def compute_price_impact(self, order_usd: float, pool_liquidity_usd: float) -> float:
        """Calcula o impacto de preço estimado em uma AMM de produto constante (x * y = k).

        Em pools simétricas, a reserva da moeda de cotação (USD) é R = L_total / 2.
        O impacto de preço médio de uma ordem delta_x é delta_x / (R + delta_x).
        """
        if not self.enable_price_impact or pool_liquidity_usd <= 0 or order_usd <= 0:
            return 0.0
        quote_reserve = pool_liquidity_usd / 2.0
        impact = order_usd / (quote_reserve + order_usd)
        return min(impact, 0.80)

    def _net_pnl(self, trade: dict) -> float:
        """Calcula o PnL líquido real após dedução de investimento e taxas de rede."""
        return trade["realized_pnl_usd"] - trade["invested_usd"] - trade.get("gas_fees_usd", 0.0)

    def _settle(self, trade: dict) -> float:
        """Registra o resultado financeiro no RiskManager e devolve o PnL líquido."""
        net_pnl = self._net_pnl(trade)
        if self.risk_manager is not None:
            self.risk_manager.register_trade_result(net_pnl)
        return net_pnl

    def prune_closed_positions(self, max_closed_history: int = 100) -> None:
        """Evita vazamento de memória mantendo todas as OPEN e apenas as últimas fechadas."""
        open_trades = [p for p in self.positions if p["status"] == "OPEN"]
        closed_trades = [p for p in self.positions if p["status"] != "OPEN"]
        if len(closed_trades) > max_closed_history:
            closed_trades = closed_trades[-max_closed_history:]
        self.positions = open_trades + closed_trades

    async def open_position(
        self, candidate: dict, token_amount: float, invested_usd: float
    ) -> dict:
        spot_price = float(candidate.get("price_usd", 0.0) or 0.0)
        pool_liq = float(candidate.get("liquidity_usd", 0.0) or 0.0)

        # 1. Price Impact de Compra e Taxa da Pool (LP Fee)
        buy_impact = self.compute_price_impact(invested_usd, pool_liq)
        fee_factor = 1.0 + self.lp_swap_fee_pct if (self.enable_price_impact and self.lp_swap_fee_pct > 0) else 1.0
        effective_entry_price = spot_price * (1.0 + buy_impact) * fee_factor if spot_price > 0 else spot_price

        # Quantidade real de tokens recebidos considerando o impacto
        if effective_entry_price > 0:
            actual_tokens = invested_usd / effective_entry_price
        else:
            actual_tokens = token_amount

        # 2. Taxa de rede da transação de compra
        initial_gas = self.gas_fee_usd

        trade = {
            "token_symbol": candidate["symbol"],
            "token_address": candidate["token_address"],
            "chain_id": candidate["chain_id"],
            "pair_address": candidate["pair_address"],
            "entry_price": effective_entry_price,
            "spot_entry_price": spot_price,
            "entry_price_impact": buy_impact,
            "initial_liquidity_usd": pool_liq,
            "current_price": effective_entry_price,
            "initial_amount": actual_tokens,
            "remaining_amount": actual_tokens,
            "invested_usd": invested_usd,
            "realized_pnl_usd": 0.0,
            "gas_fees_usd": initial_gas,
            "executed_targets": set(),
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "opened_timestamp": time.time(),
            "status": "OPEN",
        }

        self.positions.append(trade)
        await db_manager.insert_trade(trade)

        score_info = (
            f" | Score: {candidate['ignition_score']:.1f}"
            if "ignition_score" in candidate
            else ""
        )
        impact_info = (
            f" | Impacto: +{buy_impact * 100:.2f}% (LP Fee: {self.lp_swap_fee_pct*100:.2f}% | Gás: ${initial_gas:.2f})"
            if buy_impact > 0 or initial_gas > 0 or self.lp_swap_fee_pct > 0
            else ""
        )
        log.info(
            f"🚀 [COMPRA PAPER] {candidate['symbol']} | "
            f"Preço Spot: ${spot_price:.8f} -> Exec: ${effective_entry_price:.8f} | "
            f"Alocação: ${invested_usd:.2f} | "
            f"Tokens: {actual_tokens:,.2f}{score_info}{impact_info}"
        )
        return trade

    async def execute_partial_sell(
        self,
        trade: dict,
        multiplier: float,
        sell_pct: float,
        current_price: float,
        current_liq: float = 0.0,
    ):
        """Executa a liquidação parcial programada pelo Holding Map considerando fricção."""
        tokens_to_sell = min(trade["initial_amount"] * sell_pct, trade["remaining_amount"])
        gross_value = tokens_to_sell * current_price

        # Price Impact de Venda e Taxa de LP: empurra o preço líquido para baixo
        sell_impact = self.compute_price_impact(gross_value, current_liq)
        fee_factor = 1.0 - self.lp_swap_fee_pct if (self.enable_price_impact and self.lp_swap_fee_pct > 0) else 1.0
        effective_sell_price = current_price * (1.0 - sell_impact) * fee_factor
        revenue_usd = tokens_to_sell * effective_sell_price

        trade["remaining_amount"] -= tokens_to_sell
        trade["realized_pnl_usd"] += revenue_usd
        trade["gas_fees_usd"] = trade.get("gas_fees_usd", 0.0) + self.gas_fee_usd
        trade["executed_targets"].add(multiplier)

        net_so_far = self._net_pnl(trade)
        log.info(
            f"💰 [TAKE PROFIT {multiplier:.1f}x] {trade['token_symbol']} atingiu {multiplier:.1f}x! "
            f"Vendido {sell_pct * 100:.0f}% ({tokens_to_sell:,.2f} tokens) por ${revenue_usd:.2f} "
            f"(Impacto: -{sell_impact * 100:.2f}% | Gás total: ${trade['gas_fees_usd']:.2f}) | "
            f"PnL Líquido até agora: ${net_so_far:+.2f}"
        )

        if trade["remaining_amount"] <= 1e-6:
            trade["remaining_amount"] = 0.0
            trade["status"] = "CLOSED"
            duration_sec = time.time() - trade["opened_timestamp"]
            await db_manager.close_trade(
                trade,
                exit_reason="Todos os alvos do Holding Map realizados",
                duration_sec=duration_sec,
            )
            net_pnl = self._settle(trade)
            log.info(
                f"🏁 [POSIÇÃO ENCERRADA] {trade['token_symbol']} realizou todos os alvos. "
                f"PnL Líquido Real: {net_pnl:+.2f} (Taxas totais: ${trade['gas_fees_usd']:.2f})"
            )
        else:
            await db_manager.update_trade_partial(trade)

    async def drop_stagnant_position(
        self,
        trade: dict,
        age_min: float,
        current_price: float,
        reason: str = "Time-Stop / Inércia",
        current_liq: float = 0.0,
    ):
        """Vende a mercado o saldo restante de moedas que estagnaram para liberar o slot."""
        remaining = trade["remaining_amount"]
        gross_value = remaining * current_price

        sell_impact = self.compute_price_impact(gross_value, current_liq)
        fee_factor = 1.0 - self.lp_swap_fee_pct if (self.enable_price_impact and self.lp_swap_fee_pct > 0) else 1.0
        effective_sell_price = current_price * (1.0 - sell_impact) * fee_factor
        recovered_usd = remaining * effective_sell_price

        trade["realized_pnl_usd"] += recovered_usd
        trade["gas_fees_usd"] = trade.get("gas_fees_usd", 0.0) + self.gas_fee_usd
        trade["remaining_amount"] = 0.0
        trade["status"] = "STAGNANT"

        duration_sec = time.time() - trade["opened_timestamp"]
        await db_manager.close_trade(
            trade, exit_reason=reason, duration_sec=duration_sec
        )

        net_pnl = self._settle(trade)
        ratio = current_price / trade["entry_price"] if trade["entry_price"] > 0 else 0
        var_pct = (ratio - 1.0) * 100

        log_fn = log.info if net_pnl >= 0 else log.warning
        pnl_label = "Lucro Líquido" if net_pnl >= 0 else "Prejuízo Líquido"
        log_fn(
            f"⏳ [ROTAÇÃO / VENDA] {trade['token_symbol']} liquidado a mercado após {age_min:.1f}m. "
            f"Variação: {var_pct:+.1f}% | Recuperado: ${recovered_usd:.2f} (Impacto: -{sell_impact * 100:.2f}%) | "
            f"{pnl_label}: ${net_pnl:+.2f} (Gás total: ${trade['gas_fees_usd']:.2f})"
        )

    async def drop_dead_position(
        self,
        trade: dict,
        reason: str,
        current_price: float,
        current_liq: float = 0.0,
    ):
        """Vende a mercado o saldo restante ao disparar stop técnico, fluxo ou dreno de liquidez."""
        remaining = trade["remaining_amount"]
        gross_value = remaining * current_price

        sell_impact = self.compute_price_impact(gross_value, current_liq)
        fee_factor = 1.0 - self.lp_swap_fee_pct if (self.enable_price_impact and self.lp_swap_fee_pct > 0) else 1.0
        effective_sell_price = current_price * (1.0 - sell_impact) * fee_factor
        recovered_usd = remaining * effective_sell_price

        trade["realized_pnl_usd"] += recovered_usd
        trade["gas_fees_usd"] = trade.get("gas_fees_usd", 0.0) + self.gas_fee_usd
        trade["remaining_amount"] = 0.0
        trade["status"] = "DROPPED"

        duration_sec = time.time() - trade["opened_timestamp"]
        await db_manager.close_trade(trade, exit_reason=reason, duration_sec=duration_sec)

        net_pnl = self._settle(trade)
        log.warning(
            f"🚫 [STOP A MERCADO] {trade['token_symbol']} liquidado. "
            f"Motivo: {reason} | Recuperado: ${recovered_usd:.2f} (Impacto: -{sell_impact * 100:.2f}%) | "
            f"PnL Líquido Real: {net_pnl:+.2f} (Gás total: ${trade['gas_fees_usd']:.2f})"
        )

    async def close_position_loss(self, trade: dict, reason: str):
        """Encerra a posição em caso de rugpull absoluto (onde a liquidez zerou por completo)."""
        trade["status"] = "DEAD"
        trade["remaining_amount"] = 0.0

        duration_sec = time.time() - trade["opened_timestamp"]
        await db_manager.close_trade(trade, exit_reason=reason, duration_sec=duration_sec)

        net_pnl = self._settle(trade)
        log.warning(
            f"💀 [RUGPULL / PERDA TOTAL] {trade['token_symbol']} zerado. "
            f"Motivo: {reason} | Prejuízo Líquido: ${net_pnl:+.2f} (Gás pago na compra: ${trade.get('gas_fees_usd', 0.0):.2f})"
        )
