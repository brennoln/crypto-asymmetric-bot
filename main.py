import os

# Limita threads de bibliotecas BLAS/OpenMP para evitar MemoryError/OpenBLAS allocation failure no Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import asyncio
import time
from collections import deque

import aiohttp
import pandas as pd

from config.settings import settings
from data.database import db_manager
from src.audit.holder_analyzer import SecurityAuditor
from src.execution.paper_client import PaperExecutionClient
from src.risk.position_sizer import RiskManager
from src.scanner.dex_scanner import DexScanner
from src.strategy.holding_map import HoldingMap
from src.strategy.stat_strategy import StatisticalStrategy
from src.telemetry.dashboard import TelemetryDashboard
from src.utils.logger import log

MAX_SEEN_TOKENS = 5_000

CIRCUIT_BREAKER_COOLDOWN_SEC = 60.0
FULL_SLOTS_COOLDOWN_SEC = 6.0
SCAN_INTERVAL_SEC = 10.0
PORTFOLIO_SUMMARY_INTERVAL_SEC = 60.0

MAX_PAIRS_PER_REQUEST = 25


async def position_watcher(
    session: aiohttp.ClientSession,
    execution_client: PaperExecutionClient,
    holding_map: HoldingMap,
    stat_strategy: StatisticalStrategy | None = None,
    dashboard: TelemetryDashboard | None = None,
):
    """Monitor de alto desempenho com deduplicação de ticks, Trailing Stop Monotônico, Invariante Anti-Rug e OFI."""
    if stat_strategy is None:
        stat_strategy = StatisticalStrategy(
            window=settings.STAT_WINDOW_TICKS,
            z_score_threshold=settings.STAT_ZSCORE_THRESHOLD,
            imbalance_buy_threshold=settings.STAT_IMBALANCE_BUY,
            imbalance_sell_threshold=settings.STAT_IMBALANCE_SELL,
        )

    tick_buffers: dict[str, deque[dict]] = {}
    last_signals: dict[str, dict] = {}
    stop_confirmation_counts: dict[str, int] = {}
    max_buffer_len = max(stat_strategy.window * 3, 30)
    last_summary_ts = time.time()

    while True:
        execution_client.prune_closed_positions()
        active_trades = [p for p in execution_client.positions if p["status"] == "OPEN"]

        active_addrs = {t["token_address"] for t in active_trades}
        for addr in list(tick_buffers.keys()):
            if addr not in active_addrs:
                tick_buffers.pop(addr, None)
                last_signals.pop(addr, None)
                stop_confirmation_counts.pop(addr, None)

        if active_trades:
            by_chain: dict[str, list[dict]] = {}
            for trade in active_trades:
                by_chain.setdefault(trade["chain_id"], []).append(trade)

            for chain_id, trades in by_chain.items():
                pairs_str = ",".join([t["pair_address"] for t in trades[:MAX_PAIRS_PER_REQUEST]])
                url = f"https://api.dexscreener.com/latest/dex/pairs/{chain_id}/{pairs_str}"

                try:
                    async with session.get(url, timeout=5) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()

                    pairs_info = {p.get("pairAddress"): p for p in data.get("pairs", []) if p}
                    ticks_to_persist: list[dict] = []

                    for trade in trades:
                        if trade["status"] != "OPEN":
                            continue

                        pair_data = pairs_info.get(trade["pair_address"])
                        if not pair_data:
                            continue

                        current_price = float(pair_data.get("priceUsd", 0) or 0)
                        current_liq = float(pair_data.get("liquidity", {}).get("usd", 0) or 0)
                        vol_m5 = float(pair_data.get("volume", {}).get("m5", 0) or 0)
                        txns_m5 = pair_data.get("txns", {}).get("m5", {}) or {}
                        buys = int(txns_m5.get("buys", 0) or 0)
                        sells = int(txns_m5.get("sells", 0) or 0)

                        # 0. Rugpull absoluto: liquidez drenada e sem cotação válida.
                        if current_price <= 0 and current_liq < settings.MIN_RUNNING_LIQUIDITY_USD:
                            await execution_client.close_position_loss(
                                trade=trade,
                                reason=f"Liquidez drenada (${current_liq:,.2f}) sem cotação válida",
                            )
                            continue

                        if current_price <= 0:
                            continue

                        token_addr = trade["token_address"]
                        now_ts = time.time()

                        buf = tick_buffers.setdefault(
                            token_addr, deque(maxlen=max_buffer_len)
                        )

                        # Deduplicação de Tick: Só registra no buffer e no banco se o mercado mudou
                        is_new_market_tick = (
                            not buf
                            or buf[-1]["price_usd"] != current_price
                            or buf[-1]["vol_m5_usd"] != vol_m5
                            or buf[-1]["buys_m5"] != buys
                            or buf[-1]["sells_m5"] != sells
                        )

                        if is_new_market_tick:
                            ticks_to_persist.append(
                                {
                                    "token_address": token_addr,
                                    "symbol": trade["token_symbol"],
                                    "price": current_price,
                                    "liq": current_liq,
                                    "vol_m5": vol_m5,
                                    "buys": buys,
                                    "sells": sells,
                                }
                            )
                            buf.append(
                                {
                                    "timestamp": now_ts,
                                    "price_usd": current_price,
                                    "vol_m5_usd": vol_m5,
                                    "buys_m5": buys,
                                    "sells_m5": sells,
                                }
                            )

                        trade["current_price"] = current_price
                        peak_price = max(trade.get("peak_price", trade["entry_price"]), current_price)
                        trade["peak_price"] = peak_price
                        trade["current_liq"] = current_liq
                        trade["vol_m5"] = vol_m5
                        trade["buys_m5"] = buys
                        trade["sells_m5"] = sells

                        ratio = (
                            current_price / trade["entry_price"]
                            if trade["entry_price"] > 0
                            else 0
                        )
                        peak_ratio = (
                            peak_price / trade["entry_price"]
                            if trade["entry_price"] > 0
                            else 0
                        )
                        trade["current_multiplier"] = ratio
                        trade["peak_multiplier"] = peak_ratio
                        trade["unrealized_pnl_usd"] = (
                            (trade["remaining_amount"] * current_price)
                            + trade.get("realized_pnl_usd", 0.0)
                            - trade["invested_usd"]
                        )
                        trade_age_min = (now_ts - trade["opened_timestamp"]) / 60.0

                        # 1a. Poda por Dreno de Liquidez Absoluto
                        if current_liq < settings.MIN_RUNNING_LIQUIDITY_USD:
                            await execution_client.drop_dead_position(
                                trade=trade,
                                reason=f"Liquidez drenada para ${current_liq:,.2f}",
                                current_price=current_price,
                                current_liq=current_liq,
                            )
                            continue

                        # 1b. Detector Invariante de Remoção Silenciosa de LP (Soft-Rug) (v0.5)
                        init_liq = float(trade.get("initial_liquidity_usd", 0.0) or 0.0)
                        if (
                            settings.ENABLE_INVARIANT_ANTI_RUG
                            and init_liq > 0
                            and trade_age_min >= 0.5
                            and ratio > 0
                        ):
                            expected_liq_ratio = ratio ** 0.5
                            actual_liq_ratio = current_liq / init_liq
                            lambda_invariant = actual_liq_ratio / expected_liq_ratio if expected_liq_ratio > 0 else 1.0

                            if lambda_invariant < settings.MIN_INVARIANT_LIQUIDITY_RATIO:
                                if dashboard:
                                    dashboard.record_blocked_token(
                                        trade.get("token_symbol", "UNKNOWN"),
                                        f"Soft-Rug Invariante AMM (Λ={lambda_invariant:.2f} < {settings.MIN_INVARIANT_LIQUIDITY_RATIO:.2f})"
                                    )
                                await execution_client.drop_dead_position(
                                    trade=trade,
                                    reason=(
                                        f"Detector Invariante: Remoção silenciosa de LP detectada "
                                        f"(Λ={lambda_invariant:.2f} < {settings.MIN_INVARIANT_LIQUIDITY_RATIO:.2f})"
                                    ),
                                    current_price=current_price,
                                    current_liq=current_liq,
                                )
                                continue

                        # 2. Poda por Queda (>35%) com filtro anti-wick
                        if ratio <= (1.0 - settings.HARD_STOP_LOSS_PCT):
                            strikes = stop_confirmation_counts.get(token_addr, 0) + 1
                            stop_confirmation_counts[token_addr] = strikes

                            if ratio < 0.20 and strikes < 2 and current_liq >= settings.MIN_LIQUIDITY_USD:
                                log.warning(
                                    f"⚠️ [ANTI-WICK] {trade['token_symbol']} reportou queda atípica ({(1 - ratio) * 100:.1f}%) "
                                    f"com liquidez intacta (${current_liq:,.0f}). Aguardando confirmação (1/2)..."
                                )
                                continue

                            await execution_client.drop_dead_position(
                                trade=trade,
                                reason=f"Queda abrupta de {(1 - ratio) * 100:.1f}% detectada",
                                current_price=current_price,
                                current_liq=current_liq,
                            )
                            continue
                        else:
                            stop_confirmation_counts[token_addr] = 0

                        # 3. Realização de Alvos Parciais do Holding Map (Checado antes do Trailing Stop)
                        for target in holding_map.targets:
                            if trade["status"] != "OPEN":
                                break
                            if (
                                ratio >= target.multiplier
                                and target.multiplier not in trade["executed_targets"]
                            ):
                                await execution_client.execute_partial_sell(
                                    trade=trade,
                                    multiplier=target.multiplier,
                                    sell_pct=target.sell_percent,
                                    current_price=current_price,
                                    current_liq=current_liq,
                                )

                        if trade["status"] != "OPEN":
                            continue

                        # 4. Avaliação da StatisticalStrategy (Recalculada se chegou tick novo)
                        stat_tag = ""
                        current_zscore = 0.0
                        stat_signal = None
                        if len(buf) >= stat_strategy.window:
                            if is_new_market_tick or token_addr not in last_signals:
                                ticks_df = pd.DataFrame(list(buf))
                                last_signals[token_addr] = stat_strategy.generate_signal(ticks_df)

                            stat_signal = last_signals[token_addr]
                            current_zscore = float(stat_signal.get("z_score", 0.0))
                            trade["z_score"] = current_zscore
                            trade["ofi"] = float(stat_signal.get("imbalance", 0.0))
                            trade["stat_action"] = stat_signal.get("action", "HOLD")
                            stat_tag = (
                                f" | Z: {current_zscore:+.2f} | "
                                f"OFI: {stat_signal['imbalance']:+.2f} ({stat_signal['action']})"
                            )

                        is_risk_free = 2.0 in trade["executed_targets"]

                        # Determina stop_multiplier para exibição na telemetria
                        if peak_ratio >= settings.TRAILING_ACTIVATION_RATIO:
                            drop_th = settings.TRAILING_DROP_POST_2X if is_risk_free else settings.TRAILING_DROP_PRE_2X
                            min_guard = 1.40 if is_risk_free else 1.18
                            trade["stop_multiplier"] = max(peak_ratio * (1.0 - drop_th), min_guard)
                        elif settings.ENABLE_BREAKEVEN_PROTECTION and peak_ratio >= settings.BREAKEVEN_ACTIVATION_RATIO:
                            trade["stop_multiplier"] = max(peak_ratio * (1.0 - settings.BREAKEVEN_PULLBACK_DROP), settings.BREAKEVEN_FLOOR_RATIO)
                        else:
                            trade["stop_multiplier"] = 1.0 - settings.HARD_STOP_LOSS_PCT

                        # 5a. Trailing Stop Dinâmico e Monotônico (Protege lucros após atingir >= 1.40x) (v0.5)
                        if peak_ratio >= settings.TRAILING_ACTIVATION_RATIO:
                            drawdown_from_peak = (peak_price - current_price) / peak_price
                            drop_threshold = (
                                settings.TRAILING_DROP_POST_2X
                                if is_risk_free
                                else settings.TRAILING_DROP_PRE_2X
                            )
                            # Monotonicidade estrita: stop nunca cai abaixo do patamar conquistado no degrau anterior
                            min_ratio_guard = 1.40 if is_risk_free else 1.18

                            if drawdown_from_peak >= drop_threshold or ratio <= min_ratio_guard:
                                phase = "Runner Pós-2x" if is_risk_free else "Pré-2x"
                                await execution_client.drop_dead_position(
                                    trade=trade,
                                    reason=(
                                        f"Trailing Stop Monotônico ({phase}): recuo de {drawdown_from_peak * 100:.1f}% "
                                        f"desde o topo ({peak_ratio:.2f}x -> {ratio:.2f}x | Piso: {min_ratio_guard:.2f}x)"
                                    ),
                                    current_price=current_price,
                                    current_liq=current_liq,
                                )
                                continue

                        # 5b. Breakeven e Proteção Precoce de Lucro (+15% a +40%) (v0.4)
                        if (
                            settings.ENABLE_BREAKEVEN_PROTECTION
                            and not is_risk_free
                            and peak_ratio >= settings.BREAKEVEN_ACTIVATION_RATIO
                            and peak_ratio < settings.TRAILING_ACTIVATION_RATIO
                        ):
                            # Retornou para o piso de segurança (cobre custos de gas de entrada + saída)
                            if ratio <= settings.BREAKEVEN_FLOOR_RATIO:
                                await execution_client.drop_dead_position(
                                    trade=trade,
                                    reason=(
                                        f"Stop Breakeven ativado: pico atingiu {peak_ratio:.2f}x mas recuou "
                                        f"para {ratio:.2f}x (piso protetor de taxas {settings.BREAKEVEN_FLOOR_RATIO:.2f}x)"
                                    ),
                                    current_price=current_price,
                                    current_liq=current_liq,
                                )
                                continue

                            # Retração acentuada do pico intermediário: trava lucro antes de virar prejuízo
                            drawdown_from_peak = (peak_price - current_price) / peak_price
                            if drawdown_from_peak >= settings.BREAKEVEN_PULLBACK_DROP and ratio >= 1.00:
                                await execution_client.drop_dead_position(
                                    trade=trade,
                                    reason=(
                                        f"Early Profit Lock: recuo de {drawdown_from_peak * 100:.1f}% do pico "
                                        f"({peak_ratio:.2f}x -> {ratio:.2f}x), garantindo lucro antecipado"
                                    ),
                                    current_price=current_price,
                                    current_liq=current_liq,
                                )
                                continue

                        # 6. Poda por Inércia e Time-Stop (Não se aplica a posições já Risk-Free / 2.0x)
                        if not is_risk_free:
                            # 6a. Invalidação Precoce aos 10 min: preço negativo e Z-Score de volume zerado/negativo
                            if (
                                trade_age_min >= settings.INERTIA_CHECK_MINUTES
                                and ratio < 1.0
                                and current_zscore <= 0.0
                            ):
                                await execution_client.drop_stagnant_position(
                                    trade=trade,
                                    age_min=trade_age_min,
                                    current_price=current_price,
                                    reason=(
                                        f"Inércia/Estagnação: preço negativo ({ratio:.2f}x) e "
                                        f"Z-Score zerado ({current_zscore:.2f}) após {trade_age_min:.1f}m"
                                    ),
                                    current_liq=current_liq,
                                )
                                continue

                            # 6b. Time-Stop aos 20 min: encerra se estagnou sem atingir o gatilho de sobrevida (+20%)
                            if (
                                trade_age_min >= settings.MAX_HOLD_TIME_MINUTES
                                and ratio < (1.0 + settings.MIN_PROFIT_TO_EXTEND_TIME)
                            ):
                                await execution_client.drop_stagnant_position(
                                    trade=trade,
                                    age_min=trade_age_min,
                                    current_price=current_price,
                                    reason=f"Time-Stop: estagnado após {trade_age_min:.1f}m sem atingir +20%",
                                    current_liq=current_liq,
                                )
                                continue

                        # 7. Saída por Exaustão Estatística (OFI Vendedora)
                        if (
                            stat_signal
                            and stat_signal["action"] == "SELL"
                            and trade_age_min >= settings.STAT_MIN_AGE_FOR_EXIT_MIN
                            and ratio < 1.50
                        ):
                            await execution_client.drop_dead_position(
                                trade=trade,
                                reason=f"Exaustão estatística: {stat_signal['reason']}",
                                current_price=current_price,
                                current_liq=current_liq,
                            )
                            continue

                        # Log enxuto: mostra sempre que o mercado atualizar na API
                        if is_new_market_tick:
                            pnl_pct = (ratio - 1.0) * 100
                            log.info(
                                f"📊 [TRACKING] {trade['token_symbol']} | Preço: ${current_price:.8f} | "
                                f"Variação: {pnl_pct:+.1f}% ({ratio:.2f}x | Topo: {peak_ratio:.2f}x){stat_tag}"
                            )

                    if ticks_to_persist:
                        await db_manager.log_ticks_batch(ticks_to_persist)

                except Exception as e:
                    log.warning(f"Falha na checagem em lote ({chain_id}): {e}")
                    await asyncio.sleep(1.0)

        # Atualização síncrona imediata do dashboard com o mercado em tempo real
        if dashboard:
            dashboard.generate_html_dashboard(execution_client, execution_client.risk_manager)

        now_check = time.time()
        if now_check - last_summary_ts >= PORTFOLIO_SUMMARY_INTERVAL_SEC:
            last_summary_ts = now_check
            if dashboard:
                log.info("\n" + dashboard.format_terminal_view(execution_client, execution_client.risk_manager))
            else:
                open_now = [p for p in execution_client.positions if p["status"] == "OPEN"]
                unrealized_pnl = sum(
                    (p["remaining_amount"] * p["current_price"]) + p["realized_pnl_usd"] - p["invested_usd"]
                    for p in open_now
                )
                realized_day = (
                    execution_client.risk_manager.daily_realized_pnl_usd
                    if execution_client.risk_manager
                    else 0.0
                )
                log.info(
                    f"📈 [RESUMO CARTEIRA] Ativas: {len(open_now)}/{settings.MAX_ACTIVE_POSITIONS} | "
                    f"PnL Aberto (Não-Realizado): ${unrealized_pnl:+.2f} | "
                    f"PnL Fechado (Dia): ${realized_day:+.2f} | "
                    f"Balanço Total: ${(unrealized_pnl + realized_day):+.2f}"
                )

        await asyncio.sleep(settings.WATCHER_INTERVAL_SEC)


async def market_scanner(
    session: aiohttp.ClientSession,
    execution_client: PaperExecutionClient,
    risk_engine: RiskManager,
    dashboard: TelemetryDashboard | None = None,
):
    """Varredura multi-fonte contínua com auditoria on-chain anti-rug e reavaliação dinâmica."""
    scanner = DexScanner(session)
    seen_tokens: set[str] = set()
    seen_order: deque[str] = deque()

    def _mark_seen(addr_key: str) -> None:
        if addr_key not in seen_tokens:
            seen_tokens.add(addr_key)
            seen_order.append(addr_key)
            if len(seen_order) > MAX_SEEN_TOKENS:
                seen_tokens.discard(seen_order.popleft())

    while True:
        if not risk_engine.can_open_position():
            log.warning("Circuit breaker ativado. Pausando entradas...")
            await asyncio.sleep(CIRCUIT_BREAKER_COOLDOWN_SEC)
            continue

        open_positions = [p for p in execution_client.positions if p["status"] == "OPEN"]
        if len(open_positions) >= settings.MAX_ACTIVE_POSITIONS:
            log.info(
                f"Slots cheios ({len(open_positions)}/{settings.MAX_ACTIVE_POSITIONS}). "
                f"Aguardando saídas..."
            )
            await asyncio.sleep(FULL_SLOTS_COOLDOWN_SEC)
            continue

        open_addresses = {p["token_address"].lower() for p in open_positions}
        open_symbols = {p["token_symbol"].upper() for p in open_positions}
        available_slots = settings.MAX_ACTIVE_POSITIONS - len(open_positions)
        log.info(f"Buscando novos tokens em múltiplas fontes... ({available_slots} slots livres)")

        try:
            candidates = await scanner.fetch_recent_pairs()
        except Exception as e:
            log.warning(f"Falha na varredura de pares: {e}")
            await asyncio.sleep(SCAN_INTERVAL_SEC)
            continue

        approved_batch: list[tuple[dict, str]] = []
        for candidate in candidates:
            token_addr = (candidate.get("token_address") or "").strip()
            addr_key = token_addr.lower()
            sym_key = (candidate.get("symbol") or "").strip().upper()

            if (
                not token_addr
                or addr_key in seen_tokens
                or addr_key in open_addresses
                or sym_key in open_symbols
            ):
                continue

            # Se a pool já passou da idade máxima (>8h), nunca mais será válida: descarta em definitivo
            if candidate.get("age_minutes", 0.0) > (settings.MAX_POOL_AGE_HOURS * 60):
                _mark_seen(addr_key)
                continue

            passed, reason = SecurityAuditor.audit_token(candidate)
            if not passed:
                continue

            # Auditoria On-Chain e Anti-Rug / Anti-Honeypot (v0.4)
            if settings.ENABLE_RUGCHECK_AUDIT:
                passed_onchain, onchain_reason = await SecurityAuditor.audit_token_onchain(candidate, session)
                if not passed_onchain:
                    log.warning(f"🛡️ [ANTI-RUG] {candidate.get('symbol')} rejeitado: {onchain_reason}")
                    if dashboard:
                        dashboard.record_blocked_token(
                            candidate.get("symbol", "UNKNOWN"),
                            onchain_reason,
                            candidate.get("ignition_score", 0.0),
                        )
                    _mark_seen(addr_key)
                    continue
                reason = f"{reason} | {onchain_reason}"

            approved_batch.append((candidate, reason))

        approved_batch.sort(
            key=lambda item: item[0].get("ignition_score", 0.0), reverse=True
        )

        for candidate, reason in approved_batch:
            if len(open_positions) >= settings.MAX_ACTIVE_POSITIONS:
                break

            sym_key = (candidate.get("symbol") or "").strip().upper()
            addr_key = candidate["token_address"].lower()
            if sym_key in open_symbols or addr_key in open_addresses:
                continue

            score = candidate.get("ignition_score", 0.0)
            log.info(
                f"✅ Token aprovado: {candidate['name']} ({candidate['symbol']}) | "
                f"Ignition Score: {score:.1f} - {reason}"
            )

            allocated_usd = risk_engine.calculate_trade_allocation(
                cash_usd=execution_client.cash_usd,
                active_positions_count=len(open_positions),
            )
            if allocated_usd <= 0:
                log.warning(
                    f"⛔ [SEM SALDO] Saldo livre em caixa: ${execution_client.cash_usd:.2f} "
                    f"(R$ {execution_client.cash_brl:.2f}) insuficiente para alocação mínima. "
                    f"Aguardando vendas/take-profits para liberar capital."
                )
                break

            token_amount = risk_engine.calculate_position_size(
                price_usd=candidate["price_usd"],
                allocated_usd=allocated_usd,
            )
            if token_amount <= 0:
                log.warning(f"Preço inválido para {candidate['symbol']}. Entrada ignorada.")
                _mark_seen(addr_key)
                continue

            trade = await execution_client.open_position(
                candidate=candidate,
                token_amount=token_amount,
                invested_usd=allocated_usd,
            )
            if trade is None:
                continue

            _mark_seen(addr_key)
            new_trade = execution_client.positions[-1]
            open_positions.append(new_trade)
            open_addresses.add(addr_key)
            open_symbols.add(sym_key)

            if dashboard:
                dashboard.generate_html_dashboard(execution_client, risk_engine)

        await asyncio.sleep(SCAN_INTERVAL_SEC)


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Crypto Asymmetric Bot")
    parser.add_argument("--duration", type=int, default=int(os.environ.get("BOT_RUN_SECONDS", 0)), help="Duração de execução em segundos (0 = infinito)")
    args, _ = parser.parse_known_args()

    log.info("Iniciando Bot Assimétrico Multi-Fonte com Estratégia Estatística Ativa...")
    if args.duration > 0:
        log.info(f"Modo com limite de tempo ativo: {args.duration} segundos.")
    await db_manager.init_db()

    risk_engine = RiskManager()
    execution_client = PaperExecutionClient(
        risk_manager=risk_engine,
        initial_capital_usd=settings.INITIAL_CAPITAL_USD,
    )
    holding_map = HoldingMap()
    stat_strategy = StatisticalStrategy(
        window=settings.STAT_WINDOW_TICKS,
        z_score_threshold=settings.STAT_ZSCORE_THRESHOLD,
        imbalance_buy_threshold=settings.STAT_IMBALANCE_BUY,
        imbalance_sell_threshold=settings.STAT_IMBALANCE_SELL,
    )
    dashboard = TelemetryDashboard()
    # Gera visualização web inicial
    dashboard.generate_html_dashboard(execution_client, risk_engine)

    async with aiohttp.ClientSession() as session:
        tasks = asyncio.gather(
            market_scanner(session, execution_client, risk_engine, dashboard=dashboard),
            position_watcher(session, execution_client, holding_map, stat_strategy, dashboard=dashboard),
        )
        if args.duration > 0:
            try:
                await asyncio.wait_for(tasks, timeout=args.duration)
            except asyncio.TimeoutError:
                log.info(f"Duração programada de {args.duration}s finalizada com sucesso.")
        else:
            await tasks


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot finalizado pelo usuário.")
