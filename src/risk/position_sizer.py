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
    ) -> float:
        """Calcula o montante em USD a ser alocado para uma nova compra considerando custos de compra e saldo em caixa."""
        min_required = settings.MIN_POSITION_SIZE_USD + settings.GAS_FEE_USD
        if cash_usd < min_required:
            return 0.0

        if settings.POSITION_SIZING_MODE == "FIXED":
            if cash_usd >= (settings.POSITION_SIZE_USD + settings.GAS_FEE_USD):
                return settings.POSITION_SIZE_USD
            return 0.0

        # Modo DYNAMIC: Aloca uma fração da banca inicial (ex: 25%), limitada pelo caixa disponível menos gás
        available_cash = cash_usd - settings.GAS_FEE_USD
        target_allocation = settings.INITIAL_CAPITAL_USD * settings.MAX_CAPITAL_PER_TRADE_PCT
        allocated = min(available_cash, target_allocation)

        if allocated < settings.MIN_POSITION_SIZE_USD:
            return 0.0

        return round(allocated, 2)

    def calculate_position_size(self, price_usd: float, allocated_usd: float | None = None) -> float:
        """Calcula a quantidade de tokens para alocar o valor determinado por trade."""
        if price_usd <= 0:
            return 0.0
        allocation = allocated_usd if allocated_usd is not None else settings.POSITION_SIZE_USD
        return allocation / price_usd
