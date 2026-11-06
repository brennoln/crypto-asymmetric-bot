import asyncio
import time
from datetime import datetime, timezone

import aiohttp

from config.settings import settings
from src.utils.logger import log


class MarketRegimeGate:
    """Filtro de Regime de Mercado de Baixa Latência (Zero Inline Latency).

    Opera três camadas complementares de temporização e diagnóstico de mercado:
    1. Macro Health Gate: Monitora a saúde do ativo base ($SOL / $ETH) via thread em background.
    2. Session Liquidity Gate: Identifica a Zona Morta horária (04h-10h UTC) e Janela de Ouro (13h-23h UTC).
    3. Sequential Stagnation Breaker: Detecta quando o mercado entra em regime de lateralidade/chop
       e interrompe novas entradas para preservar caixa.
    """

    def __init__(
        self,
        base_token_address: str | None = None,
        min_sol_1h_return: float | None = None,
        stagnant_threshold: int | None = None,
        stagnant_cooldown_sec: float | None = None,
        dead_zone_start_utc: int | None = None,
        dead_zone_end_utc: int | None = None,
    ):
        self.base_token_address = (
            base_token_address or settings.MACRO_BASE_TOKEN_ADDRESS
        )
        self.min_sol_1h_return = (
            min_sol_1h_return
            if min_sol_1h_return is not None
            else settings.MACRO_MIN_1H_RETURN_PCT
        )
        self.stagnant_threshold = (
            stagnant_threshold
            if stagnant_threshold is not None
            else settings.CONSECUTIVE_STAGNANT_THRESHOLD
        )
        self.stagnant_cooldown_sec = (
            stagnant_cooldown_sec
            if stagnant_cooldown_sec is not None
            else settings.STAGNANT_COOLDOWN_SECONDS
        )
        self.dead_zone_start_utc = (
            dead_zone_start_utc
            if dead_zone_start_utc is not None
            else settings.DEAD_ZONE_START_UTC_HOUR
        )
        self.dead_zone_end_utc = (
            dead_zone_end_utc
            if dead_zone_end_utc is not None
            else settings.DEAD_ZONE_END_UTC_HOUR
        )

        # Estado Macro (Atualizado em segundo plano, leitura síncrona instantânea em memória)
        self.sol_price_usd: float = 0.0
        self.sol_change_1h: float = 0.0
        self.sol_change_24h: float = 0.0
        self.last_macro_update_ts: float = 0.0

        # Estado do Circuit Breaker de Estagnação
        self.consecutive_stagnant_count: int = 0
        self.cooldown_until_ts: float = 0.0
        self.cooldown_reason: str = ""

    def record_trade_exit(
        self,
        exit_multiplier: float,
        peak_multiplier: float,
        exit_reason: str,
        exit_timestamp: float | None = None,
    ) -> None:
        """Registra o fechamento de uma posição e atualiza o estado de estagnação sequencial."""
        now = exit_timestamp if exit_timestamp is not None else time.time()
        reason_lower = exit_reason.lower()

        # Determina se o trade estagnou sem tração
        is_stagnant = (
            peak_multiplier <= settings.STAGNANT_MAX_PEAK_MULTIPLE
            and (
                any(k in reason_lower for k in ["time-stop", "inércia", "inercia", "estagnação", "estagnacao", "stagnant"])
                or exit_multiplier <= 1.02
            )
        )

        if is_stagnant:
            self.consecutive_stagnant_count += 1
            log.warning(
                f"⚠️ [REGIME FEEDBACK] Trade estagnado registrado ({self.consecutive_stagnant_count}/"
                f"{self.stagnant_threshold}) | Pico: {peak_multiplier:.2f}x | Motivo: {exit_reason}"
            )
            if (
                settings.ENABLE_SEQUENTIAL_STAGNATION_BREAKER
                and self.consecutive_stagnant_count >= self.stagnant_threshold
            ):
                self.cooldown_until_ts = now + self.stagnant_cooldown_sec
                cooldown_hours = self.stagnant_cooldown_sec / 3600.0
                self.cooldown_reason = (
                    f"Circuit Breaker Ativo: {self.consecutive_stagnant_count} trades seguidos estagnados "
                    f"(pico <= {settings.STAGNANT_MAX_PEAK_MULTIPLE:.2f}x). Regime CHOP/Dormência detectado."
                )
                log.warning(
                    f"🚨 [CIRCUIT BREAKER REGIME] {self.cooldown_reason} "
                    f"Novas entradas pausadas por {cooldown_hours:.1f}h para preservar caixa."
                )
        elif peak_multiplier >= 1.20 or exit_multiplier >= 1.10:
            if self.consecutive_stagnant_count > 0:
                log.info(
                    f"✨ [REGIME FEEDBACK] Trade com tração saudável ({peak_multiplier:.2f}x) "
                    f"zerou o contador de estagnação."
                )
            self.consecutive_stagnant_count = 0

    def is_in_cooldown(self, current_time: float | None = None) -> tuple[bool, str, float]:
        """Checagem instantânea em memória do Circuit Breaker (<0.0001 ms)."""
        now = current_time if current_time is not None else time.time()
        if now < self.cooldown_until_ts:
            remaining = self.cooldown_until_ts - now
            return True, self.cooldown_reason, remaining
        return False, "OK", 0.0

    def get_session_regime(self, utc_time: datetime | None = None) -> dict:
        """Determina a janela horária de liquidez (Zero network latency)."""
        now_utc = utc_time or datetime.now(timezone.utc)
        hour = now_utc.hour

        if self.dead_zone_start_utc <= hour < self.dead_zone_end_utc:
            return {
                "session": "DEAD_ZONE",
                "is_dead_zone": True,
                "is_golden": False,
                "size_multiplier": settings.DEAD_ZONE_SIZE_MULTIPLIER,
                "min_z_score": settings.DEAD_ZONE_MIN_Z_SCORE,
            }
        elif 13 <= hour < 23:
            return {
                "session": "GOLDEN_HOURS",
                "is_dead_zone": False,
                "is_golden": True,
                "size_multiplier": 1.0,
                "min_z_score": settings.STAT_ZSCORE_THRESHOLD,
            }
        else:
            return {
                "session": "NORMAL",
                "is_dead_zone": False,
                "is_golden": False,
                "size_multiplier": 1.0,
                "min_z_score": settings.STAT_ZSCORE_THRESHOLD,
            }

    def is_macro_safe(self) -> tuple[bool, str]:
        """Checagem instantânea em memória da saúde macro do ecossistema (<0.0001 ms)."""
        if not settings.ENABLE_MACRO_REGIME_GATE:
            return True, "Filtro macro desativado"

        # Se ainda não recebeu o primeiro update da thread, não bloqueia o bot
        if self.last_macro_update_ts == 0.0:
            return True, "Macro warming up (aguardando primeiro ciclo)"

        if self.sol_change_1h < self.min_sol_1h_return:
            return (
                False,
                f"Macro Veto: SOL em queda de {self.sol_change_1h:+.2f}% na última 1h "
                f"(limite de segurança: {self.min_sol_1h_return:+.1f}%)",
            )

        return True, f"Macro Saudável: SOL {self.sol_change_1h:+.2f}% (1h)"

    def audit_candidate(
        self,
        candidate: dict | None = None,
        current_time: float | None = None,
        utc_time: datetime | None = None,
    ) -> tuple[bool, str, float]:
        """Avaliação de regime consolidada em <0.001 ms.

        Retorna:
            (approved: bool, reason: str, size_multiplier: float)
        """
        # 1. Checa Circuit Breaker de Estagnação Sequencial
        cooling, cool_reason, rem_sec = self.is_in_cooldown(current_time)
        if cooling:
            rem_min = rem_sec / 60.0
            return False, f"{cool_reason} ({rem_min:.0f}m restantes de resfriamento)", 0.0

        # 2. Checa Macro Trend ($SOL / $ETH)
        macro_ok, macro_reason = self.is_macro_safe()
        if not macro_ok:
            return False, macro_reason, 0.0

        # 3. Checa Janela Horária de Liquidez
        if settings.ENABLE_SESSION_LIQUIDITY_GATE:
            session_info = self.get_session_regime(utc_time)
            if session_info["is_dead_zone"]:
                if settings.DEAD_ZONE_STRICT_MODE:
                    return (
                        False,
                        f"Zona Morta Horária ({self.dead_zone_start_utc}h-{self.dead_zone_end_utc}h UTC): "
                        f"Mercado DEX sem volume institucional/varejo.",
                        0.0,
                    )
                # Modo adaptativo: exige anomalia extrema ou score altíssimo
                cand = candidate or {}
                z_score = float(cand.get("z_score", 0.0) or 0.0)
                ignition = float(cand.get("ignition_score", 0.0) or 0.0)
                if z_score < session_info["min_z_score"] and ignition < 85.0:
                    return (
                        False,
                        f"Zona Morta Horária UTC: Anomalia moderada (Z={z_score:.2f} < {session_info['min_z_score']:.1f}, "
                        f"Ignition={ignition:.0f} < 85). Exige alta convicção na madrugada.",
                        0.0,
                    )
                return (
                    True,
                    f"Aprovado na Zona Morta com sizing reduzido ({session_info['size_multiplier']*100:.0f}%)",
                    session_info["size_multiplier"],
                )

        return True, "Regime de Mercado Favorável", 1.0

    async def update_macro_data(self, session: aiohttp.ClientSession) -> bool:
        """Consulta assíncrona do ativo base (executada em segundo plano)."""
        url = f"{settings.DEXSCREENER_TOKEN_URL}/{self.base_token_address}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=6.0)) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                pairs = data.get("pairs", [])
                if not pairs:
                    return False

                # Pega a pool principal com maior liquidez
                pairs_sorted = sorted(
                    pairs,
                    key=lambda p: float(p.get("liquidity", {}).get("usd", 0.0) or 0.0),
                    reverse=True,
                )
                top_pair = pairs_sorted[0]
                price = float(top_pair.get("priceUsd", 0.0) or 0.0)
                price_change = top_pair.get("priceChange", {}) or {}
                h1 = float(price_change.get("h1", 0.0) or 0.0)
                h24 = float(price_change.get("h24", 0.0) or 0.0)

                self.sol_price_usd = price
                self.sol_change_1h = h1
                self.sol_change_24h = h24
                self.last_macro_update_ts = time.time()
                return True
        except Exception as e:
            log.debug(f"Falha não-bloqueante na atualização de macro data: {e}")
            return False

    async def run_background_macro_tracker(self, session: aiohttp.ClientSession) -> None:
        """Loop permanente em background que mantém os dados macro atualizados na RAM."""
        log.info("🛰️ [MACRO TRACKER] Inicializando rastreador macro em segundo plano...")
        while True:
            try:
                updated = await self.update_macro_data(session)
                if updated:
                    log.debug(
                        f"🛰️ [MACRO STATE] SOL/USD: ${self.sol_price_usd:.2f} | "
                        f"1h: {self.sol_change_1h:+.2f}% | 24h: {self.sol_change_24h:+.2f}%"
                    )
            except Exception as e:
                log.debug(f"Erro no background macro tracker: {e}")
            await asyncio.sleep(settings.MACRO_POLL_INTERVAL_SEC)


# Instância singleton padrão do Regime Gate
regime_gate = MarketRegimeGate()
