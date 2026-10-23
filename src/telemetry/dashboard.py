"""Módulo de Telemetria e Dashboard em Tempo Real (v0.8.5).

Responsável por agregar métricas operacionais, exibir o painel TUI de alta fidelidade
no terminal e gerar o dashboard web dinâmico simulando uma conta real de corretora (BRL e USD).
"""

from __future__ import annotations

import datetime
import html
import os
import sys
import time
from typing import Any, Dict, List

from config.settings import settings

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class TelemetryDashboard:
    """Agregador de métricas e gerador de visualizações para o motor quantitativo."""

    def __init__(self, max_audit_logs: int = 50):
        self.max_audit_logs = max_audit_logs
        self.blocked_events: List[Dict[str, Any]] = []
        self.start_time = time.time()

    def record_blocked_token(
        self,
        symbol: str,
        reason: str,
        score: float = 0.0,
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        """Registra uma intervenção do sistema de auditoria de segurança (Anti-Cabal/Anti-Rug)."""
        event = {
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
            "symbol": symbol,
            "reason": reason,
            "score": round(score, 1),
            "metadata": metadata or {},
        }
        self.blocked_events.append(event)
        if len(self.blocked_events) > self.max_audit_logs:
            self.blocked_events.pop(0)

    @staticmethod
    def _calc_hold_min(p: Dict[str, Any], now: float) -> float:
        """Calcula o tempo em carteira suportando float epoch ou string ISO/UTC."""
        opened_ts = p.get("opened_timestamp")
        if opened_ts is not None and isinstance(opened_ts, (int, float)):
            return max(0.0, (now - float(opened_ts)) / 60.0)
        opened_at = p.get("opened_at")
        if isinstance(opened_at, (int, float)):
            return max(0.0, (now - float(opened_at)) / 60.0)
        if isinstance(opened_at, str):
            try:
                clean_iso = opened_at.replace("Z", "+00:00")
                dt = datetime.datetime.fromisoformat(clean_iso)
                return max(0.0, (now - dt.timestamp()) / 60.0)
            except Exception:
                pass
        return 0.0

    @staticmethod
    def _calc_pnl(p: Dict[str, Any]) -> float:
        """Calcula o PnL não-realizado de forma segura."""
        if "unrealized_pnl_usd" in p and p["unrealized_pnl_usd"] is not None:
            return float(p["unrealized_pnl_usd"])
        rem = float(p.get("remaining_amount", 0.0) or 0.0)
        entry = float(p.get("entry_price", 0.0) or 0.0)
        curr = float(p.get("current_price", entry) or entry)
        real = float(p.get("realized_pnl_usd", 0.0) or 0.0)
        inv = float(p.get("invested_usd", 0.0) or 0.0)
        return (rem * curr) + real - inv

    def format_terminal_view(
        self,
        execution_client: Any,
        risk_manager: Any | None = None,
    ) -> str:
        """Gera uma visualização formatada em texto/ANSI para o terminal com modelo de conta real."""
        uptime_sec = int(time.time() - self.start_time)
        uptime_str = f"{uptime_sec // 3600:02d}:{(uptime_sec % 3600) // 60:02d}:{uptime_sec % 60:02d}"

        positions = getattr(execution_client, "positions", [])
        open_positions = [p for p in positions if p.get("status", "OPEN") == "OPEN"]
        total_open = len(open_positions)
        max_slots = getattr(risk_manager, "max_active_positions", 10) if risk_manager else 10

        initial_cap_usd = getattr(execution_client, "initial_capital_usd", settings.INITIAL_CAPITAL_USD)
        initial_cap_brl = initial_cap_usd * settings.USD_BRL_RATE

        cash_usd = getattr(execution_client, "cash_usd", initial_cap_usd)
        cash_brl = cash_usd * settings.USD_BRL_RATE

        allocated_usd = getattr(execution_client, "allocated_usd", 0.0)
        allocated_brl = allocated_usd * settings.USD_BRL_RATE

        equity_usd = getattr(execution_client, "equity_usd", cash_usd)
        equity_brl = equity_usd * settings.USD_BRL_RATE

        virtual_equity_usd = getattr(execution_client, "virtual_equity_usd", equity_usd)
        virtual_equity_brl = virtual_equity_usd * settings.USD_BRL_RATE

        realized_pnl_usd = getattr(execution_client, "total_realized_pnl_usd", 0.0)
        realized_pnl_brl = realized_pnl_usd * settings.USD_BRL_RATE

        total_pnl_usd = getattr(execution_client, "total_pnl_usd", equity_usd - initial_cap_usd)
        total_pnl_brl = total_pnl_usd * settings.USD_BRL_RATE
        total_pnl_pct = (total_pnl_usd / initial_cap_usd * 100.0) if initial_cap_usd > 0 else 0.0

        gas_paid = getattr(execution_client, "total_gas_paid_usd", 0.0)
        gas_brl = gas_paid * settings.USD_BRL_RATE

        sep = "=" * 80
        sub_sep = "-" * 80

        lines = [
            sep,
            f" ⚡ ASYMMETRIC MOMENTUM ENGINE | BANCA INICIAL: R$ {initial_cap_brl:.2f} (${initial_cap_usd:.2f}) | UPTIME: {uptime_str} | SLOTS: {total_open}/{max_slots}",
            sep,
            f" 💵 CAIXA REAL: R$ {cash_brl:.2f} (${cash_usd:.2f}) | 🔮 VIRTUAL (LIQ.): R$ {virtual_equity_brl:.2f} (${virtual_equity_usd:.2f})",
            f" 💼 PATRIMÔNIO: R$ {equity_brl:.2f} | ALOCADO: R$ {allocated_brl:.2f} | PNL REALIZADO: R$ {realized_pnl_brl:+.2f} | GÁS: R$ {gas_brl:.2f}",
            sub_sep,
        ]

        if not open_positions:
            lines.append("  [Nenhuma posição aberta no momento - Varredor ativo com capital pronto]")
        else:
            lines.append(
                f"  {'TICKER':<10} {'TEMPO':<7} {'ALOCADO':<16} {'ATUAL':<12} {'MULT':<8} {'TOPO':<8} {'STOP':<8} {'PNL (R$)':<12} {'STATUS'}"
            )
            lines.append(f"  {'-'*84}")
            now = time.time()
            for p in open_positions:
                sym = str(p.get("token_symbol") or p.get("symbol") or "N/A")[:9]
                hold_min = self._calc_hold_min(p, now)
                inv_usd = float(p.get("invested_usd", 0.0) or 0.0)
                inv_brl = inv_usd * settings.USD_BRL_RATE
                curr_p = float(p.get("current_price", p.get("entry_price", 0.0)) or 0.0)
                mult = p.get("current_multiplier")
                entry_p = float(p.get("entry_price", 0.0) or 0.0)
                if mult is None:
                    mult = (curr_p / entry_p) if entry_p > 0 else 1.0
                peak = p.get("peak_multiplier")
                if peak is None:
                    peak_p = float(p.get("peak_price", entry_p) or entry_p)
                    peak = (peak_p / entry_p) if entry_p > 0 else 1.0
                stop = float(p.get("stop_multiplier", 0.65) or 0.65)
                pnl_usd = self._calc_pnl(p)
                pnl_brl = pnl_usd * settings.USD_BRL_RATE
                risk_free = "RISK-FREE" if (p.get("is_risk_free") or len(p.get("executed_targets", set())) > 0) else "RUNNING"

                lines.append(
                    f"  {sym:<10} {hold_min:4.1f}m  R$ {inv_brl:<6.2f} (${inv_usd:<4.2f})  ${curr_p:<11.6f} {mult:5.2f}x  {peak:5.2f}x  {stop:5.2f}x  R$ {pnl_brl:+7.2f}  [{risk_free}]"
                )

        lines.append(sub_sep)
        lines.append(" 🛡️ AUDITORIA ON-CHAIN RECENTE (Últimos bloqueios preventivos):")
        if not self.blocked_events:
            lines.append("  [Nenhum token fraudulento detectado recentemente]")
        else:
            recent_blocks = self.blocked_events[-4:]
            for b in reversed(recent_blocks):
                lines.append(f"  [{b['timestamp']}] {b['symbol']:<10} -> {b['reason']}")

        lines.append(sep)
        return "\n".join(lines)

    def generate_html_dashboard(
        self,
        execution_client: Any,
        risk_manager: Any | None = None,
        output_path: str = "data/dashboard.html",
    ) -> str:
        """Gera um arquivo HTML completo com métricas financeiras reais de corretora (BRL e USD)."""
        uptime_sec = int(time.time() - self.start_time)
        uptime_str = f"{uptime_sec // 3600:02d}:{(uptime_sec % 3600) // 60:02d}:{uptime_sec % 60:02d}"

        positions = getattr(execution_client, "positions", [])
        open_positions = [p for p in positions if p.get("status", "OPEN") == "OPEN"]
        total_open = len(open_positions)
        max_slots = getattr(risk_manager, "max_active_positions", 10) if risk_manager else 10

        # Métricas de Conta / Finanças (Modelo Exchange)
        initial_cap_usd = getattr(execution_client, "initial_capital_usd", settings.INITIAL_CAPITAL_USD)
        initial_cap_brl = initial_cap_usd * settings.USD_BRL_RATE

        cash_usd = getattr(execution_client, "cash_usd", initial_cap_usd)
        cash_brl = cash_usd * settings.USD_BRL_RATE

        allocated_usd = getattr(execution_client, "allocated_usd", 0.0)
        allocated_brl = allocated_usd * settings.USD_BRL_RATE

        equity_usd = getattr(execution_client, "equity_usd", cash_usd)
        equity_brl = equity_usd * settings.USD_BRL_RATE

        virtual_equity_usd = getattr(execution_client, "virtual_equity_usd", None)
        if virtual_equity_usd is None:
            virtual_equity_usd = cash_usd
            for p in open_positions:
                rem = float(p.get("remaining_amount", 0.0) or 0.0)
                curr_p = float(p.get("current_price", p.get("entry_price", 0.0)) or 0.0)
                gross = rem * curr_p
                net_val = max(0.0, (gross * 0.999) - 0.15)
                virtual_equity_usd += net_val
        virtual_equity_brl = virtual_equity_usd * settings.USD_BRL_RATE

        realized_pnl_usd = getattr(execution_client, "total_realized_pnl_usd", 0.0)
        realized_pnl_brl = realized_pnl_usd * settings.USD_BRL_RATE

        total_pnl_usd = getattr(execution_client, "total_pnl_usd", equity_usd - initial_cap_usd)
        total_pnl_brl = total_pnl_usd * settings.USD_BRL_RATE
        total_pnl_pct = (total_pnl_usd / initial_cap_usd * 100.0) if initial_cap_usd > 0 else 0.0

        gas_paid = getattr(execution_client, "total_gas_paid_usd", 0.0)
        gas_brl = gas_paid * settings.USD_BRL_RATE

        pnl_color = "#10b981" if total_pnl_usd >= 0 else "#ef4444"
        realized_color = "#10b981" if realized_pnl_usd >= 0 else "#ef4444"

        # Gera linhas da tabela de posições com gaveta expansível
        now = time.time()
        pos_rows = []
        for idx, p in enumerate(open_positions):
            sym = html.escape(str(p.get("token_symbol") or p.get("symbol") or "N/A"))
            sym_id = f"coin_{idx}_{sym.replace(' ', '_').replace('.', '_')}"
            hold_min = self._calc_hold_min(p, now)
            inv_usd = float(p.get("invested_usd", 0.0) or 0.0)
            inv_brl = inv_usd * settings.USD_BRL_RATE
            entry_p = float(p.get("entry_price", 0.0) or 0.0)
            curr_p = float(p.get("current_price", entry_p) or entry_p)
            mult = p.get("current_multiplier")
            if mult is None:
                mult = (curr_p / entry_p) if entry_p > 0 else 1.0
            peak = p.get("peak_multiplier")
            if peak is None:
                peak_p = float(p.get("peak_price", entry_p) or entry_p)
                peak = (peak_p / entry_p) if entry_p > 0 else 1.0
            stop = float(p.get("stop_multiplier", 0.65) or 0.65)
            pnl_usd = self._calc_pnl(p)
            pnl_brl = pnl_usd * settings.USD_BRL_RATE
            executed_targets = set(p.get("executed_targets", set()) or set())
            is_rf = p.get("is_risk_free") or len(executed_targets) > 0
            badge_class = "badge-rf" if is_rf else "badge-open"
            badge_text = "RISK-FREE" if is_rf else "ACTIVE"
            pnl_class = "text-green" if pnl_usd >= 0 else "text-red"

            # Detalhes da pool
            chain_id = html.escape(str(p.get("chain_id", "solana")).lower())
            pair_addr = html.escape(str(p.get("pair_address", "")))
            token_addr = html.escape(str(p.get("token_address", "")))
            liq = float(p.get("current_liq", p.get("initial_liquidity_usd", 0.0)) or 0.0)
            vol5m = float(p.get("vol_m5", 0.0) or 0.0)
            buys = int(p.get("buys_m5", 0) or 0)
            sells = int(p.get("sells_m5", 0) or 0)
            ofi = float(p.get("ofi", 0.0) or 0.0)
            z_score = float(p.get("z_score", 0.0) or 0.0)
            ignition = float(p.get("ignition_score", 0.0) or 0.0)
            impact = float(p.get("entry_price_impact", 0.0) or 0.0)
            gas_fee = float(p.get("gas_fees_usd", 0.15) or 0.15)
            dex_url = f"https://dexscreener.com/{chain_id}/{pair_addr}" if pair_addr else f"https://dexscreener.com/search?q={token_addr or sym}"

            ofi_label = "Pressão Compradora (Agressores Ativos)" if ofi > 0 else ("Pressão Vendedora" if ofi < 0 else "Neutro")
            ofi_color = "var(--green)" if ofi > 0 else ("var(--red)" if ofi < 0 else "var(--text-dim)")

            t2 = "✅ ATINGIDO" if 2.0 in executed_targets else "⏳ PENDENTE"
            t5 = "✅ ATINGIDO" if 5.0 in executed_targets else "⏳ PENDENTE"
            t15 = "✅ ATINGIDO" if 15.0 in executed_targets else "⏳ PENDENTE"
            t50 = "✅ ATINGIDO" if 50.0 in executed_targets else "⏳ PENDENTE"

            pos_rows.append(
                f"""<tr class="pos-row" onclick="toggleDetails('{sym_id}')" title="Clique para expandir métricas e abrir no DexScreener">
                    <td><strong class="token-title">{sym}</strong> <span class="expand-indicator" id="arrow-{sym_id}">▼</span></td>
                    <td>{hold_min:.1f}m</td>
                    <td>R$ {inv_brl:.2f} <span class="subtext">(${inv_usd:.2f})</span></td>
                    <td>${entry_p:.6f}</td>
                    <td>${curr_p:.6f}</td>
                    <td><strong>{mult:.2f}x</strong></td>
                    <td>{peak:.2f}x</td>
                    <td>{stop:.2f}x</td>
                    <td class="{pnl_class}"><strong>R$ {pnl_brl:+.2f}</strong> <span class="subtext">(${pnl_usd:+.2f})</span></td>
                    <td><span class="badge {badge_class}">{badge_text}</span></td>
                </tr>
                <tr id="details-{sym_id}" class="details-row" style="display: none;">
                    <td colspan="10">
                        <div class="pool-detail-box">
                            <div class="pool-header">
                                <div>
                                    <span class="pool-title">Métricas de Microestrutura & Pool ao Vivo — {sym}</span>
                                    <span class="pool-chain">[{chain_id.upper()}]</span>
                                </div>
                                <a href="{dex_url}" target="_blank" class="btn-dex" onclick="event.stopPropagation()">
                                    📈 Abrir Gráfico & Pool no DexScreener ↗
                                </a>
                            </div>

                            <div class="metrics-subgrid">
                                <div class="subcard">
                                    <div class="subcard-label">Liquidez Atual da Pool</div>
                                    <div class="subcard-val">${liq:,.2f}</div>
                                </div>
                                <div class="subcard">
                                    <div class="subcard-label">Volume 5 Minutos</div>
                                    <div class="subcard-val">${vol5m:,.2f} <span class="subtext">({buys}B / {sells}S)</span></div>
                                </div>
                                <div class="subcard">
                                    <div class="subcard-label">Order Flow Imbalance (OFI)</div>
                                    <div class="subcard-val" style="color: {ofi_color};">{ofi:+.2f} <span class="subtext">({ofi_label})</span></div>
                                </div>
                                <div class="subcard">
                                    <div class="subcard-label">Momentum Z-Score</div>
                                    <div class="subcard-val">{z_score:+.2f}</div>
                                </div>
                                <div class="subcard">
                                    <div class="subcard-label">Ignition Score de Entrada</div>
                                    <div class="subcard-val">{ignition:.1f} pts</div>
                                </div>
                                <div class="subcard">
                                    <div class="subcard-label">Impacto AMM / Slippage</div>
                                    <div class="subcard-val">+{impact*100:.2f}% <span class="subtext">(Gás: ${gas_fee:.2f})</span></div>
                                </div>
                            </div>

                            <div class="ladder-title">🎯 Escada Convexa de Desrisco & Saídas (Holding Map)</div>
                            <div class="ladder-grid">
                                <div class="ladder-step {'step-done' if '✅' in t2 else ''}">
                                    <span class="step-target">2.0x (Vender 35%)</span>
                                    <span class="step-desc">Recupera 70% Principal</span>
                                    <span class="step-status">{t2}</span>
                                </div>
                                <div class="ladder-step {'step-done' if '✅' in t5 else ''}">
                                    <span class="step-target">5.0x (Vender 30%)</span>
                                    <span class="step-desc">Trava +120% Lucro Líquido</span>
                                    <span class="step-status">{t5}</span>
                                </div>
                                <div class="ladder-step {'step-done' if '✅' in t15 else ''}">
                                    <span class="step-target">15.0x (Vender 20%)</span>
                                    <span class="step-desc">Expansão de Convexidade</span>
                                    <span class="step-status">{t15}</span>
                                </div>
                                <div class="ladder-step {'step-done' if '✅' in t50 else ''}">
                                    <span class="step-target">50.0x (Vender 15%)</span>
                                    <span class="step-desc">Moonbag (+1170% Lucro)</span>
                                    <span class="step-status">{t50}</span>
                                </div>
                            </div>
                        </div>
                    </td>
                </tr>"""
            )

        if not pos_rows:
            pos_table_content = '<tr><td colspan="10" class="text-muted text-center" style="padding: 24px;">Nenhuma posição aberta no momento. Scanner em busca de assimetria...</td></tr>'
        else:
            pos_table_content = "\n".join(pos_rows)

        # Gera linhas do histórico de posições encerradas (vendas, stops, alvos realizados)
        closed_positions = [p for p in positions if p.get("status", "OPEN") != "OPEN"]
        if not closed_positions:
            closed_table_content = '<tr><td colspan="8" class="text-muted text-center" style="padding: 24px;">Nenhuma moeda encerrada ainda nesta sessão. Conforme ocorrerem vendas, trailing stops e take-profits, o histórico completo aparecerá aqui.</td></tr>'
        else:
            closed_rows = []
            for p in reversed(closed_positions):
                sym = html.escape(str(p.get("token_symbol") or p.get("symbol") or "N/A"))
                reason = html.escape(str(p.get("exit_reason") or "Saída a Mercado"))
                dur_sec = float(p.get("duration_sec") or p.get("hold_duration_sec", 0.0) or 0.0)
                dur_str = f"{dur_sec / 60.0:.1f}m" if dur_sec > 0 else "< 1m"
                entry_p = float(p.get("entry_price", 0.0) or 0.0)
                exit_p = float(p.get("exit_price", entry_p) or entry_p)
                inv_usd = float(p.get("invested_usd", 0.0) or 0.0)
                inv_brl = inv_usd * settings.USD_BRL_RATE
                rec_usd = float(p.get("recovered_usd", 0.0) or p.get("realized_pnl_usd", 0.0) or 0.0)
                rec_brl = rec_usd * settings.USD_BRL_RATE
                net_pnl = float(p.get("net_pnl_usd", rec_usd - inv_usd - 0.30))
                net_pnl_brl = net_pnl * settings.USD_BRL_RATE
                pnl_class = "text-green" if net_pnl >= 0 else "text-red"

                peak_mult = p.get("peak_multiplier")
                if peak_mult is None:
                    peak_mult = (exit_p / entry_p) if entry_p > 0 else 1.0
                exit_mult = p.get("exit_multiplier")
                if exit_mult is None:
                    exit_mult = (exit_p / entry_p) if entry_p > 0 else 1.0

                status = str(p.get("status", "CLOSED")).upper()
                if net_pnl > 0:
                    badge_style = "badge-rf"
                    status_label = "LUCRO"
                elif status == "DROPPED":
                    badge_style = "badge-blocked"
                    status_label = "STOP"
                elif status == "STAGNANT":
                    badge_style = "badge-open"
                    status_label = "ROTAÇÃO"
                else:
                    badge_style = "badge-blocked"
                    status_label = status

                chain_id = html.escape(str(p.get("chain_id", "solana")).lower())
                pair_addr = html.escape(str(p.get("pair_address", "")))
                token_addr = html.escape(str(p.get("token_address", "")))
                dex_url = f"https://dexscreener.com/{chain_id}/{pair_addr}" if pair_addr else f"https://dexscreener.com/search?q={token_addr or sym}"

                closed_rows.append(
                    f"""<tr>
                        <td><a href="{dex_url}" target="_blank" style="color: #60a5fa; text-decoration: none; font-weight: 700;">{sym} ↗</a></td>
                        <td style="max-width: 280px; font-size: 12px; color: var(--text-dim);">{reason}</td>
                        <td>{dur_str}</td>
                        <td>${entry_p:.6f} &rarr; ${exit_p:.6f}</td>
                        <td>Topo: <strong>{peak_mult:.2f}x</strong> | Saída: {exit_mult:.2f}x</td>
                        <td>R$ {inv_brl:.2f} &rarr; R$ {rec_brl:.2f} <span class="subtext">(${rec_usd:.2f})</span></td>
                        <td class="{pnl_class}"><strong>R$ {net_pnl_brl:+.2f}</strong> <span class="subtext">(${net_pnl:+.2f})</span></td>
                        <td><span class="badge {badge_style}">{status_label}</span></td>
                    </tr>"""
                )
            closed_table_content = "\n".join(closed_rows)

        html_content = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="2">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Asymmetric Momentum Engine — Telemetria v0.8.5</title>
    <style>
        :root {{
            --bg: #0b0f19;
            --surface: #111827;
            --surface-hover: #1f293d;
            --border: #1f2937;
            --border-highlight: #374151;
            --text: #f9fafb;
            --text-dim: #9ca3af;
            --accent: #3b82f6;
            --green: #10b981;
            --red: #ef4444;
            --yellow: #f59e0b;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            padding: 24px;
            font-size: 14px;
        }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
        }}
        h1 {{ font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }}
        .header-meta {{ color: var(--text-dim); font-size: 13px; }}
        .pulse {{
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--green);
            margin-right: 6px;
            box-shadow: 0 0 8px var(--green);
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }}
        .card-label {{ color: var(--text-dim); font-size: 12px; text-transform: uppercase; margin-bottom: 6px; font-weight: 600; }}
        .card-value {{ font-size: 20px; font-weight: 700; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            overflow: hidden;
            margin-bottom: 24px;
        }}
        th, td {{
            padding: 12px 14px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        th {{ background: #1a2234; color: var(--text-dim); font-weight: 600; font-size: 12px; text-transform: uppercase; }}
        tr.pos-row {{
            cursor: pointer;
            transition: background 0.15s ease;
        }}
        tr.pos-row:hover {{
            background: var(--surface-hover);
        }}
        .token-title {{ color: #60a5fa; }}
        .expand-indicator {{ font-size: 10px; color: var(--text-dim); margin-left: 4px; display: inline-block; transition: transform 0.2s; }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
        }}
        .badge-rf {{ background: rgba(16, 185, 129, 0.2); color: var(--green); border: 1px solid var(--green); }}
        .badge-open {{ background: rgba(59, 130, 246, 0.2); color: var(--accent); }}
        .badge-blocked {{ background: rgba(239, 68, 68, 0.2); color: var(--red); border: 1px solid var(--red); }}
        .text-green {{ color: var(--green); }}
        .text-red {{ color: var(--red); }}
        .text-muted {{ color: var(--text-dim); }}
        .text-center {{ text-align: center; }}
        .section-title {{ font-size: 16px; font-weight: 700; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between; }}
        .section-hint {{ font-size: 12px; font-weight: normal; color: var(--text-dim); }}

        /* Painel detalhado de análise da moeda */
        .details-row td {{
            background: #0d1322;
            padding: 18px 24px;
            border-top: 1px dashed var(--border-highlight);
        }}
        .pool-detail-box {{
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}
        .pool-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .pool-title {{ font-size: 15px; font-weight: 700; color: #93c5fd; }}
        .pool-chain {{ font-size: 12px; color: var(--text-dim); margin-left: 8px; font-weight: 600; }}
        .btn-dex {{
            background: #2563eb;
            color: #ffffff;
            text-decoration: none;
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: background 0.2s;
        }}
        .btn-dex:hover {{ background: #1d4ed8; }}
        .metrics-subgrid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
        }}
        .subcard {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 10px 14px;
        }}
        .subcard-label {{ font-size: 11px; color: var(--text-dim); text-transform: uppercase; margin-bottom: 4px; font-weight: 600; }}
        .subcard-val {{ font-size: 15px; font-weight: 700; }}
        .subtext {{ font-size: 11px; font-weight: normal; color: var(--text-dim); }}

        .ladder-title {{ font-size: 13px; font-weight: 700; color: var(--text); margin-top: 4px; }}
        .ladder-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 10px;
        }}
        .ladder-step {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 10px 12px;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        .ladder-step.step-done {{
            border-color: var(--green);
            background: rgba(16, 185, 129, 0.08);
        }}
        .step-target {{ font-size: 13px; font-weight: 700; color: #e5e7eb; }}
        .step-desc {{ font-size: 11px; color: var(--text-dim); }}
        .step-status {{ font-size: 11px; font-weight: 700; color: var(--yellow); margin-top: 4px; }}
        .step-done .step-status {{ color: var(--green); }}
    </style>
</head>
<body>
    <header>
        <div>
            <h1><span class="pulse"></span> Asymmetric Momentum Engine — Telemetria v0.8.5</h1>
            <div class="header-meta">Simulação de Conta Real com Controle de Saldo em Caixa (BRL / USD)</div>
        </div>
        <div class="header-meta" style="display: flex; align-items: center; gap: 10px;">
            <span id="bot-status-pill" class="badge badge-rf">VERIFICANDO...</span>
            <span>Uptime: <strong>{uptime_str}</strong></span>
            <span>Taxa: <strong>2s</strong></span>
        </div>
    </header>

    <div class="grid">
        <div class="card">
            <div class="card-label">💵 Dinheiro Real (Caixa Livre)</div>
            <div class="card-value" style="color: #60a5fa;">R$ {cash_brl:.2f} <span class="subtext">(${cash_usd:.2f})</span></div>
            <div class="subtext" style="margin-top: 4px; color: var(--text-dim);">Saldo disponível no bolso para novas compras</div>
        </div>
        <div class="card">
            <div class="card-label">🔮 Dinheiro Virtual (Liq. Estimada)</div>
            <div class="card-value" style="color: #a78bfa;">R$ {virtual_equity_brl:.2f} <span class="subtext">(${virtual_equity_usd:.2f})</span></div>
            <div class="subtext" style="margin-top: 4px; color: var(--text-dim);">Caixa + Moedas a mercado (menos taxas de saída)</div>
        </div>
        <div class="card">
            <div class="card-label">💼 Patrimônio Total (Mark-to-Market)</div>
            <div class="card-value" style="color: {pnl_color};">R$ {equity_brl:.2f} <span class="subtext">(${equity_usd:.2f})</span></div>
            <div class="subtext" style="margin-top: 4px; color: {pnl_color}; font-weight: 600;">{total_pnl_pct:+.2f}% desde a banca (R$ {initial_cap_brl:.2f})</div>
        </div>
        <div class="card">
            <div class="card-label">🪙 Capital Alocado em Moedas</div>
            <div class="card-value">R$ {allocated_brl:.2f} <span class="subtext">(${allocated_usd:.2f})</span></div>
            <div class="subtext" style="margin-top: 4px;">{total_open}/{max_slots} slots em aberto</div>
        </div>
        <div class="card">
            <div class="card-label">📈 Lucro Realizado (No Bolso)</div>
            <div class="card-value" style="color: {realized_color};">R$ {realized_pnl_brl:+.2f} <span class="subtext">(${realized_pnl_usd:+.2f})</span></div>
            <div class="subtext" style="margin-top: 4px;">Consolidado de saídas e take-profits</div>
        </div>
        <div class="card">
            <div class="card-label">⛽ Taxas de Rede Pagas (Gás)</div>
            <div class="card-value">R$ {gas_brl:.2f} <span class="subtext">(${gas_paid:.2f})</span></div>
            <div class="subtext" style="margin-top: 4px;">Simulação de Priority & Base Fees</div>
        </div>
    </div>

    <div class="section-title">
        <span>📊 Posições Ativas em Carteira</span>
        <span class="section-hint">💡 Dica: Clique em qualquer moeda para ver a análise profunda da pool e abrir no DexScreener</span>
    </div>
    <table>
        <thead>
            <tr>
                <th>Ticker</th>
                <th>Duração</th>
                <th>Alocação (R$)</th>
                <th>Entrada</th>
                <th>Atual</th>
                <th>Múltiplo</th>
                <th>Topo</th>
                <th>Stop Floor</th>
                <th>PnL Atual (R$)</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            {pos_table_content}
        </tbody>
    </table>

    <div class="section-title" style="margin-top: 32px;">
        <span>📜 Histórico de Tokens Vendidos & Saídas Finalizadas</span>
        <span class="section-hint">Trades liquidados por Take-Profit, Trailing Stop ou Time-Stop</span>
    </div>
    <table>
        <thead>
            <tr>
                <th>Token</th>
                <th>Motivo da Saída</th>
                <th>Duração</th>
                <th>Entrada &rarr; Saída</th>
                <th>Múltiplo (Topo / Saída)</th>
                <th>Alocado &rarr; Recuperado</th>
                <th>PnL Realizado Líquido</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            {closed_table_content}
        </tbody>
    </table>

    <script>
        function toggleDetails(id) {{
            const row = document.getElementById('details-' + id);
            const arrow = document.getElementById('arrow-' + id);
            if (!row) return;
            const isHidden = row.style.display === 'none';
            row.style.display = isHidden ? 'table-row' : 'none';
            if (arrow) arrow.style.transform = isHidden ? 'rotate(180deg)' : 'rotate(0deg)';

            let openTokens = [];
            try {{
                openTokens = JSON.parse(localStorage.getItem('open_pool_rows') || '[]');
            }} catch(e) {{}}
            if (isHidden) {{
                if (!openTokens.includes(id)) openTokens.push(id);
            }} else {{
                openTokens = openTokens.filter(t => t !== id);
            }}
            localStorage.setItem('open_pool_rows', JSON.stringify(openTokens));
        }}

        // Restaura gavetas abertas e checa se o bot está online
        const generatedTs = {int(now)};
        function updateHeartbeat() {{
            const ageSec = Math.floor(Date.now() / 1000 - generatedTs);
            const statusPill = document.getElementById('bot-status-pill');
            const pulse = document.querySelector('.pulse');
            if (ageSec > 6) {{
                if (statusPill) {{
                    statusPill.textContent = '🔴 BOT OFFLINE / PAUSADO';
                    statusPill.className = 'badge badge-blocked';
                }}
                if (pulse) {{
                    pulse.style.background = 'var(--red)';
                    pulse.style.boxShadow = '0 0 8px var(--red)';
                }}
            }} else {{
                if (statusPill) {{
                    statusPill.textContent = '🟢 BOT ATIVO & CONECTADO';
                    statusPill.className = 'badge badge-rf';
                }}
                if (pulse) {{
                    pulse.style.background = 'var(--green)';
                    pulse.style.boxShadow = '0 0 8px var(--green)';
                }}
            }}
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            updateHeartbeat();
            try {{
                const openTokens = JSON.parse(localStorage.getItem('open_pool_rows') || '[]');
                openTokens.forEach(id => {{
                    const row = document.getElementById('details-' + id);
                    const arrow = document.getElementById('arrow-' + id);
                    if (row) {{
                        row.style.display = 'table-row';
                        if (arrow) arrow.style.transform = 'rotate(180deg)';
                    }}
                }});
            }} catch(e) {{}}
        }});
    </script>
</body>
</html>"""

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return output_path


if __name__ == "__main__":
    import asyncio
    import aiosqlite

    async def run_standalone():
        db_path = "data/trading_history.db"
        dashboard = TelemetryDashboard()

        positions = []
        realized_pnl = 0.0
        gas_paid = 0.0

        if os.path.exists(db_path):
            async with aiosqlite.connect(db_path) as db:
                async with db.execute(
                    """
                    SELECT t.token_symbol, t.opened_at, t.entry_price,
                           t.invested_usd, t.realized_pnl_usd,
                           (SELECT price_usd FROM market_ticks m WHERE m.token_address = t.token_address ORDER BY m.id DESC LIMIT 1) AS latest_price,
                           t.chain_id, t.pair_address, t.entry_price_impact, t.gas_fees_usd
                    FROM trade_records t
                    WHERE t.status = 'OPEN'
                    """
                ) as cursor:
                    rows = await cursor.fetchall()
                    for r in rows:
                        sym, opened_at_str, entry, inv, real, curr, chain, pair, impact, gas = r
                        curr = curr or entry
                        peak = curr
                        mult = curr / entry if entry > 0 else 1.0
                        peak_mult = mult
                        unreal = (inv * mult) + (real or 0.0) - inv
                        positions.append({
                            "symbol": sym,
                            "opened_at": time.time() - 300,
                            "entry_price": entry,
                            "current_price": curr,
                            "current_multiplier": mult,
                            "peak_multiplier": peak_mult,
                            "stop_multiplier": 0.65,
                            "unrealized_pnl_usd": unreal,
                            "invested_usd": inv,
                            "remaining_amount": inv / entry if entry > 0 else 0.0,
                            "chain_id": chain or "solana",
                            "pair_address": pair or "",
                            "entry_price_impact": impact or 0.0,
                            "gas_fees_usd": gas or 0.15,
                            "is_risk_free": False,
                            "executed_targets": set(),
                        })

                async with db.execute(
                    "SELECT SUM(realized_pnl_usd - invested_usd), SUM(gas_fees_usd) FROM trade_records WHERE status != 'OPEN'"
                ) as cursor:
                    row = await cursor.fetchone()
                    realized_pnl = (row[0] or 0.0) if row else 0.0
                    gas_paid = (row[1] or 0.0) if row else 0.0

                async with db.execute(
                    """
                    SELECT token_symbol, opened_at, closed_at, hold_duration_sec,
                           entry_price, invested_usd, realized_pnl_usd, net_pnl_usd,
                           gas_fees_usd, exit_reason, status, chain_id, pair_address
                    FROM trade_records
                    WHERE status != 'OPEN'
                    ORDER BY rowid DESC
                    LIMIT 25
                    """
                ) as cursor:
                    crows = await cursor.fetchall()
                    for cr in crows:
                        csym, copened, cclosed, cdur, centry, cinv, creal, cnet, cgas, creason, cstatus, cchain, cpair = cr
                        positions.append({
                            "token_symbol": csym,
                            "symbol": csym,
                            "opened_at": copened,
                            "closed_at": cclosed,
                            "duration_sec": cdur or 0.0,
                            "entry_price": centry or 0.0,
                            "exit_price": centry or 0.0,
                            "invested_usd": cinv or 0.0,
                            "recovered_usd": creal or 0.0,
                            "net_pnl_usd": cnet or 0.0,
                            "gas_fees_usd": cgas or 0.30,
                            "exit_reason": creason or "Finalizado",
                            "status": cstatus or "CLOSED",
                            "chain_id": cchain or "solana",
                            "pair_address": cpair or "",
                            "peak_multiplier": 1.0,
                            "exit_multiplier": 1.0,
                            "remaining_amount": 0.0,
                        })

        initial_cap_usd = float(settings.INITIAL_CAPITAL_USD)
        open_pos = [p for p in positions if p.get("status", "OPEN") == "OPEN"]
        allocated_usd = sum(p["invested_usd"] for p in open_pos)
        cash_usd = max(0.0, initial_cap_usd - allocated_usd)

        class StandaloneClient:
            def __init__(self, pos, pnl, gas, init_cap, cash):
                self.positions = pos
                self.total_realized_pnl_usd = pnl
                self.total_gas_paid_usd = gas
                self.initial_capital_usd = init_cap
                self.cash_usd = cash

            @property
            def cash_brl(self) -> float:
                return self.cash_usd * settings.USD_BRL_RATE

            @property
            def equity_usd(self) -> float:
                open_trades = [p for p in self.positions if p.get("status", "OPEN") == "OPEN"]
                open_val = sum(p["invested_usd"] * p.get("current_multiplier", 1.0) for p in open_trades)
                return self.cash_usd + open_val

            @property
            def equity_brl(self) -> float:
                return self.equity_usd * settings.USD_BRL_RATE

            @property
            def virtual_equity_usd(self) -> float:
                open_trades = [p for p in self.positions if p.get("status", "OPEN") == "OPEN"]
                return max(0.0, self.equity_usd - (len(open_trades) * 0.15))

            @property
            def virtual_equity_brl(self) -> float:
                return self.virtual_equity_usd * settings.USD_BRL_RATE

            @property
            def total_realized_pnl_brl(self) -> float:
                return self.total_realized_pnl_usd * settings.USD_BRL_RATE

            @property
            def allocated_usd(self) -> float:
                open_trades = [p for p in self.positions if p.get("status", "OPEN") == "OPEN"]
                return sum(p["invested_usd"] for p in open_trades)

            @property
            def allocated_brl(self) -> float:
                return self.allocated_usd * settings.USD_BRL_RATE

            @property
            def total_pnl_usd(self) -> float:
                return self.equity_usd - self.initial_capital_usd

            @property
            def total_pnl_brl(self) -> float:
                return self.total_pnl_usd * settings.USD_BRL_RATE

            @property
            def total_pnl_pct(self) -> float:
                if self.initial_capital_usd <= 0:
                    return 0.0
                return (self.total_pnl_usd / self.initial_capital_usd) * 100.0

        client = StandaloneClient(positions, realized_pnl, gas_paid, initial_cap_usd, cash_usd)
        print(dashboard.format_terminal_view(client))
        html_file = dashboard.generate_html_dashboard(client)
        print(f"\n[OK] Dashboard HTML atualizado: {os.path.abspath(html_file)}")

    asyncio.run(run_standalone())
