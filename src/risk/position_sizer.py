from datetime import date, datetime, timezone

from config.settings import settings


class RiskManager:
    """Guardrails de portfólio: sizing dinâmico de capital e circuit breaker de perda diária."""

    def __init__(self):
        self._current_day: date = self._utc_day()
        self.daily_realized_pnl_usd: float = 0.0

    @staticmethod
    def _utc_day() -> date:
        return datetime.now(timezone.utc).date()

    def _roll_day_if_needed(self) -> None:
        """Zera os acumuladores quando o dia UTC muda."""
        today = self._utc_day()
        if today != self._current_day:
            self._current_day = today
            self.daily_realized_pnl_usd = 0.0

    @property
    def daily_drawdown_usd(self) -> float:
        """Perda líquida acumulada no dia (sempre >= 0)."""
        return max(0.0, -self.daily_realized_pnl_usd)

    def register_trade_result(self, net_pnl_usd: float) -> None:
        """Contabiliza o resultado de uma operação encerrada no acumulado do dia."""
        self._roll_day_if_needed()
        self.daily_realized_pnl_usd += net_pnl_usd

    def can_open_position(self) -> bool:
        """Circuit breaker para não ultrapassar perda máxima diária."""
        self._roll_day_if_needed()
        return self.daily_drawdown_usd < settings.MAX_DAILY_LOSS_USD

    def calculate_trade_allocation(
        self,
        cash_usd: float,
        active_positions_count: int = 0,
        pool_liquidity_usd: float = 0.0,
        vol_m5_usd: float = 0.0,
        ignition_score: float = 0.0,
    ) -> float:
        """Calcula o montante ótimo em USD a ser alocado para compra maximizando retorno com base na liquidez da pool e momentum."""
        min_required = settings.MIN_POSITION_SIZE_USD + settings.GAS_FEE_USD
        if cash_usd < min_required:
            return 0.0

        if settings.POSITION_SIZING_MODE == "FIXED":
            if cash_usd >= (settings.POSITION_SIZE_USD + settings.GAS_FEE_USD):
                return settings.POSITION_SIZE_USD
            return 0.0

        available_cash = cash_usd - settings.GAS_FEE_USD

        # Se não foram fornecidos dados de pool, usa o padrão proporcional de 25% da banca
        if pool_liquidity_usd <= 0:
            target = settings.INITIAL_CAPITAL_USD * 0.25
            return round(min(available_cash, max(settings.MIN_POSITION_SIZE_USD, target)), 2)

        # Penalidade côncava para pools rasas (< $35k) para blindar contra slippage e risco de despejo brutal
        liquidity_penalty = 1.0
        if pool_liquidity_usd > 0 and pool_liquidity_usd < settings.SHALLOW_POOL_THRESHOLD_USD:
            liquidity_penalty = min(
                1.0,
                (pool_liquidity_usd / settings.SHALLOW_POOL_THRESHOLD_USD) ** settings.SHALLOW_POOL_EXPONENT,
            )

        # 1. Teto de impacto de preço na AMM (x * y = k): protege contra slippage excessivo
        quote_reserve = pool_liquidity_usd / 2.0
        max_allowed_by_impact = quote_reserve * settings.MAX_AMM_PRICE_IMPACT_PCT

        # 2. Fator de Convencimento do Ignition Score (Score normalizado entre 60 e 100)
        norm_score = max(0.0, min(1.0, (ignition_score - 60.0) / 40.0))
        score_multiplier = 0.70 + (norm_score * 0.65)  # 0.70x a 1.35x

        # 3. Fator de Profundidade e Velocidade de Volume da Pool (Turnover 5m / Liquidez)
        turnover_ratio = (vol_m5_usd / max(100.0, pool_liquidity_usd)) if pool_liquidity_usd > 0 else 0.0
        depth_multiplier = min(1.25, max(0.85, 0.85 + (turnover_ratio * 4.0)))

        # 4. Dimensionamento Ótimo em USD
        base_target = settings.INITIAL_CAPITAL_USD * 0.25  # ~R$ 25 / $4.35
        optimal_target = base_target * score_multiplier * depth_multiplier * liquidity_penalty

        # Limites de risco da banca: entre 15% e 35% da banca inicial (teto penalizado para pools rasas)
        min_trade = round(max(settings.MIN_POSITION_SIZE_USD, settings.INITIAL_CAPITAL_USD * settings.MIN_CAPITAL_PER_TRADE_PCT), 2)
        max_trade = round(settings.INITIAL_CAPITAL_USD * settings.MAX_CAPITAL_PER_TRADE_PCT * liquidity_penalty, 2)
        max_trade = max(min_trade, max_trade)

        bounded_allocation = min(max_trade, max(min_trade, optimal_target))

        # Restringe pelo limite de impacto de slippage na AMM (se a pool for rasa)
        if max_allowed_by_impact > 0:
            bounded_allocation = min(bounded_allocation, max_allowed_by_impact)

        # Limita estritamente pelo caixa livre disponível
        final_allocation = min(available_cash, bounded_allocation)

        if final_allocation < settings.MIN_POSITION_SIZE_USD:
            if available_cash >= settings.MIN_POSITION_SIZE_USD and max_allowed_by_impact >= settings.MIN_POSITION_SIZE_USD:
                final_allocation = settings.MIN_POSITION_SIZE_USD
            else:
                return 0.0

        return round(final_allocation, 2)

    def calculate_position_size(self, price_usd: float, allocated_usd: float | None = None) -> float:
        """Calcula a quantidade de tokens para alocar o valor determinado por trade."""
        if price_usd <= 0:
            return 0.0
        allocation = allocated_usd if allocated_usd is not None else settings.POSITION_SIZE_USD
        return allocation / price_usd
