import aiohttp

from config.settings import settings
from src.utils.logger import log

KNOWN_POOL_IDENTIFIERS = {
    "5q544fkrfoe6tsebxd7s8emxgtjyakttvhaw5q5pge4j1",  # Raydium AMM Authority
    "raydium",
    "meteora",
    "orca",
    "pump.fun",
    "11111111111111111111111111111111",  # Burn / System Program
}


class SecurityAuditor:
    @staticmethod
    def audit_token(candidate: dict) -> tuple[bool, str]:
        liq = candidate["liquidity_usd"]
        age_min = candidate["age_minutes"]
        vol_m5 = candidate["vol_m5_usd"]
        buys = candidate["buys_m5"]
        sells = candidate["sells_m5"]
        price_change_m5 = float(candidate.get("price_change_m5", 0.0) or 0.0)

        # 1. Filtros de Liquidez
        if liq < settings.MIN_LIQUIDITY_USD:
            return False, f"Liquidez vulnerável a rugpull (${liq:,.2f} < ${settings.MIN_LIQUIDITY_USD:,.2f})"

        if liq > settings.MAX_LIQUIDITY_USD:
            return False, f"Liquidez excessiva para alvo 100x (${liq:,.2f})"

        # 2. Janela de Idade (Fuga dos primeiros minutos de Snipers)
        if age_min < settings.MIN_POOL_AGE_MINUTES:
            return False, f"Muito novo, sob fogo de sniper bots ({age_min:.1f}m < {settings.MIN_POOL_AGE_MINUTES}m)"

        if age_min > (settings.MAX_POOL_AGE_HOURS * 60):
            return False, f"Pool fora da janela inicial ({age_min / 60:.1f}h)"

        # 3. Ignição Imediata de Volume
        if vol_m5 < settings.MIN_VOLUME_M5_USD:
            return False, f"Sem volume acelerado nos últimos 5m (${vol_m5:,.2f})"

        if buys < settings.MIN_BUYS_M5:
            return False, f"Pouca quantidade de compras em 5m ({buys})"

        # 4. Pressão Compradora
        ratio = buys / max(sells, 1)
        if ratio < settings.MIN_BUY_SELL_RATIO:
            return False, f"Pressão de venda alta (Ratio Compras/Vendas: {ratio:.2f})"

        # 5. Momentum Direcional e Proteção contra Topo Hiper-Estendido (v0.5)
        if price_change_m5 < settings.MIN_PRICE_CHANGE_M5_PCT:
            return False, f"Momentum negativo no candle de 5m ({price_change_m5:+.1f}%)"

        if price_change_m5 > settings.MAX_PRICE_CHANGE_M5_PCT:
            return False, f"Ativo hiper-estendido em 5m ({price_change_m5:+.1f}% > {settings.MAX_PRICE_CHANGE_M5_PCT:.0f}%)"

        return True, "Aprovado no filtro de ignição assimétrica"

    @staticmethod
    async def audit_token_onchain(
        candidate: dict,
        session: aiohttp.ClientSession,
    ) -> tuple[bool, str]:
        """Auditoria aprofundada de segurança on-chain (RugCheck completo + Dev Holding + Anti-Cabal) (v0.6)."""
        chain_id = (candidate.get("chain_id") or "").lower()
        token_addr = (candidate.get("token_address") or "").strip()
        buys = int(candidate.get("buys_m5", 0) or 0)
        sells = int(candidate.get("sells_m5", 0) or 0)
        liq = float(candidate.get("liquidity_usd", 0.0) or 0.0)
        vol_m5 = float(candidate.get("vol_m5_usd", 0.0) or 0.0)
        age_min = float(candidate.get("age_minutes", 0.0) or 0.0)

        # 1. Heurística Anti-Honeypot e Wash Trading
        if sells == 0 and buys >= 15:
            return False, f"Possível honeypot: {buys} compras e 0 vendas nos últimos 5m"

        if liq > 0 and (vol_m5 / liq) > 15.0 and age_min < 20.0:
            return False, f"Volume anômalo de wash trading (Vol/Liq: {vol_m5/liq:.1f}x em {age_min:.0f}m)"

        if not settings.ENABLE_RUGCHECK_AUDIT:
            return True, "Auditoria RugCheck desabilitada via configurações"

        # 2. Verificação On-Chain via RugCheck API para tokens Solana
        if chain_id == "solana" and token_addr:
            url = f"{settings.RUGCHECK_API_URL.rstrip('/')}/{token_addr}/report"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                    report = None
                    if resp.status == 200:
                        report = await resp.json()
                    elif resp.status == 404:
                        # Tenta fallback para endpoint summary
                        summary_url = f"{settings.RUGCHECK_API_URL.rstrip('/')}/{token_addr}/report/summary"
                        async with session.get(summary_url, timeout=aiohttp.ClientTimeout(total=3.0)) as s_resp:
                            if s_resp.status == 200:
                                report = await s_resp.json()
                            elif s_resp.status == 404:
                                return True, "RugCheck: Token não indexado (aprovado na heurística)"

                    if isinstance(report, dict):
                        # 2a. Verificação de Rugged ativo
                        if report.get("rugged") is True:
                            return False, "RugCheck: Contrato sinalizado como Rugged no histórico"

                        # 2b. Score de Risco Global e Riscos Críticos
                        score = report.get("score", 0) or 0
                        status_str = str(report.get("status", "")).lower()

                        risks = report.get("risks", []) or []
                        danger_risks = [
                            r.get("name", "Risco Crítico")
                            for r in risks
                            if isinstance(r, dict) and r.get("level") == "danger"
                        ]

                        if score > settings.MAX_RUGCHECK_SCORE or "danger" in status_str or danger_risks:
                            risk_summary = ", ".join(danger_risks[:3]) if danger_risks else f"Score {score}"
                            return False, f"RugCheck Score de Risco ({score} > {settings.MAX_RUGCHECK_SCORE} | {risk_summary})"

                        # 2c. Autoridades Perigosas Ativas
                        token_meta = report.get("tokenMeta", {}) or {}
                        if token_meta.get("freezeAuthority") or report.get("freezeAuthority"):
                            return False, "RugCheck: Freeze Authority ativa no contrato"

                        if token_meta.get("mintAuthority") or report.get("mintAuthority"):
                            return False, "RugCheck: Mint Authority ainda ativa no contrato"

                        # 2d. Auditoria de Dev Holding (Carteira Criadora) (v0.6)
                        creator = str(report.get("creator") or "").strip().lower()
                        creator_balance_pct = float(report.get("creatorBalance", 0.0) or 0.0)

                        top_holders = report.get("topHolders", []) or []
                        for holder in top_holders:
                            owner_addr = str(holder.get("owner") or holder.get("address") or "").strip().lower()
                            pct = float(holder.get("pct", 0.0) or 0.0)
                            if creator and owner_addr == creator:
                                creator_balance_pct = max(creator_balance_pct, pct)

                        if creator_balance_pct > settings.MAX_DEV_HOLDING_PCT:
                            return False, f"Dev Holding excessivo: Criador detém {creator_balance_pct:.1f}% do suprimento (máx: {settings.MAX_DEV_HOLDING_PCT:.1f}%)"

                        # 2e. Auditoria de Concentração de Top Holders & Baleias (v0.6)
                        non_pool_holders = []
                        for holder in top_holders:
                            addr = str(holder.get("address") or "").strip().lower()
                            owner = str(holder.get("owner") or "").strip().lower()

                            # Ignora endereços de queima conhecidos
                            if addr.startswith("11111111") or owner.startswith("11111111") or "burn" in owner or "dead" in owner:
                                continue
                            # Ignora pools AMM conhecidas
                            if any(pool_id in addr or pool_id in owner for pool_id in KNOWN_POOL_IDENTIFIERS):
                                continue
                            # Ignora lockers legítimos
                            if holder.get("isLocker") is True:
                                continue

                            non_pool_holders.append(holder)

                        if non_pool_holders:
                            # Checagem de baleia individual
                            max_single_holder = max(non_pool_holders, key=lambda h: float(h.get("pct", 0.0) or 0.0))
                            max_single_pct = float(max_single_holder.get("pct", 0.0) or 0.0)
                            if max_single_pct > settings.MAX_SINGLE_HOLDER_PCT:
                                return False, f"Baleia concentrada: Maior detentor detém {max_single_pct:.1f}% do suprimento (máx: {settings.MAX_SINGLE_HOLDER_PCT:.1f}%)"

                            # Checagem de concentração do Top 10
                            top10_sum = sum(float(h.get("pct", 0.0) or 0.0) for h in non_pool_holders[:10])
                            if top10_sum > settings.MAX_TOP10_HOLDERS_PCT:
                                return False, f"Top 10 Holders concentrado: Top 10 detêm {top10_sum:.1f}% do suprimento (máx: {settings.MAX_TOP10_HOLDERS_PCT:.1f}%)"

                            # Checagem de Insiders / Cabal
                            insiders_sum = sum(
                                float(h.get("pct", 0.0) or 0.0)
                                for h in non_pool_holders
                                if h.get("insider") is True
                            )
                            if insiders_sum > settings.MAX_INSIDERS_PCT:
                                return False, f"Alerta Cabal/Insiders: Carteiras marcadas como insiders detêm {insiders_sum:.1f}% do suprimento (máx: {settings.MAX_INSIDERS_PCT:.1f}%)"

                        return True, f"RugCheck aprovado (Score: {score})"

                    else:
                        log.debug(f"RugCheck status não-200 para {token_addr}")
                        return True, "RugCheck indisponível - aprovado na heurística"

            except Exception as e:
                log.debug(f"RugCheck timeout ou erro de rede para {token_addr}: {e}")
                return True, "RugCheck timeout - aprovado na heurística"

        return True, "Aprovado na auditoria de segurança"
