"""Módulo de Telemetria e Dashboard em Tempo Real (v0.8).

Responsável por agregar métricas operacionais, exibir o painel TUI de alta fidelidade
no terminal e gerar o dashboard web dinâmico (HTML auto-refresh).
"""

from __future__ import annotations

import datetime
import html
import os
import sys
import time
from typing import Any, Dict, List

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

    def format_terminal_view(
        self,
        execution_client: Any,
        risk_manager: Any | None = None,
    ) -> str:
        """Gera uma visualização formatada em texto/ANSI para o terminal."""
        uptime_sec = int(time.time() - self.start_time)
        uptime_str = f"{uptime_sec // 3600:02d}:{(uptime_sec % 3600) // 60:02d}:{uptime_sec % 60:02d}"

        positions = getattr(execution_client, "positions", [])
        total_open = len(positions)
        max_slots = getattr(risk_manager, "max_active_positions", 10) if risk_manager else 10

        unrealized_pnl = sum(p.get("unrealized_pnl_usd", 0.0) for p in positions)
        realized_pnl = getattr(execution_client, "total_realized_pnl_usd", 0.0)
        gas_paid = getattr(execution_client, "total_gas_paid_usd", 0.0)
        net_balance = realized_pnl + unrealized_pnl - gas_paid

        sep = "=" * 80
        sub_sep = "-" * 80

        lines = [
            sep,
            f" ⚡ ASYMMETRIC MOMENTUM ENGINE (v0.8) | UPTIME: {uptime_str} | SLOTS: {total_open}/{max_slots}",
            sep,
            f" 💼 PORTFÓLIO: Realizado: ${realized_pnl:+.2f} | Não-Realizado: ${unrealized_pnl:+.2f} | Gás: ${gas_paid:.2f} | Net: ${net_balance:+.2f}",
            sub_sep,
        ]

        if not positions:
            lines.append("  [Nenhuma posição aberta no momento - Varredor ativo]")
        else:
            lines.append(
                f"  {'TICKER':<10} {'TEMPO':<7} {'ENTRADA':<12} {'ATUAL':<12} {'MULT':<8} {'TOPO':<8} {'STOP':<8} {'PNL ($)':<10} {'STATUS'}"
            )
            lines.append(f"  {'-'*76}")
            now = time.time()
            for p in positions:
                sym = p.get("symbol", "N/A")[:9]
                hold_min = (now - p.get("opened_at", now)) / 60.0
                entry_p = p.get("entry_price", 0.0)
                curr_p = p.get("current_price", entry_p)
                mult = p.get("current_multiplier", 1.0)
                peak = p.get("peak_multiplier", 1.0)
                stop = p.get("stop_multiplier", 0.65)
                pnl = p.get("unrealized_pnl_usd", 0.0)
                risk_free = "RISK-FREE" if (p.get("is_risk_free") or len(p.get("executed_targets", set())) > 0) else "RUNNING"

                lines.append(
                    f"  {sym:<10} {hold_min:4.1f}m  ${entry_p:<11.6f} ${curr_p:<11.6f} {mult:5.2f}x  {peak:5.2f}x  {stop:5.2f}x  ${pnl:+7.2f}  [{risk_free}]"
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
        """Gera um arquivo HTML completo, autônomo e responsivo com auto-refresh para monitoramento."""
        uptime_sec = int(time.time() - self.start_time)
        uptime_str = f"{uptime_sec // 3600:02d}:{(uptime_sec % 3600) // 60:02d}:{uptime_sec % 60:02d}"

        positions = getattr(execution_client, "positions", [])
        total_open = len(positions)
        max_slots = getattr(risk_manager, "max_active_positions", 10) if risk_manager else 10

        unrealized_pnl = sum(p.get("unrealized_pnl_usd", 0.0) for p in positions)
        realized_pnl = getattr(execution_client, "total_realized_pnl_usd", 0.0)
        gas_paid = getattr(execution_client, "total_gas_paid_usd", 0.0)
        net_balance = realized_pnl + unrealized_pnl - gas_paid

        # Gera linhas da tabela de posições
        now = time.time()
        pos_rows = []
        for p in positions:
            sym = html.escape(str(p.get("symbol", "N/A")))
            hold_min = (now - p.get("opened_at", now)) / 60.0
            entry_p = p.get("entry_price", 0.0)
            curr_p = p.get("current_price", entry_p)
            mult = p.get("current_multiplier", 1.0)
            peak = p.get("peak_multiplier", 1.0)
            stop = p.get("stop_multiplier", 0.65)
            pnl = p.get("unrealized_pnl_usd", 0.0)
            is_rf = p.get("is_risk_free") or len(p.get("executed_targets", set())) > 0
            badge_class = "badge-rf" if is_rf else "badge-open"
            badge_text = "RISK-FREE" if is_rf else "ACTIVE"
            pnl_class = "text-green" if pnl >= 0 else "text-red"

            pos_rows.append(
                f"""<tr>
                    <td><strong>{sym}</strong></td>
                    <td>{hold_min:.1f}m</td>
                    <td>${entry_p:.6f}</td>
                    <td>${curr_p:.6f}</td>
                    <td><strong>{mult:.2f}x</strong></td>
                    <td>{peak:.2f}x</td>
                    <td>{stop:.2f}x</td>
                    <td class="{pnl_class}">${pnl:+.2f}</td>
                    <td><span class="badge {badge_class}">{badge_text}</span></td>
                </tr>"""
            )

        if not pos_rows:
            pos_table_content = '<tr><td colspan="9" class="text-muted text-center">Nenhuma posição aberta no momento. Scanner em busca de assimetria...</td></tr>'
        else:
            pos_table_content = "\n".join(pos_rows)

        # Gera linhas dos bloqueios de segurança
        audit_rows = []
        for b in reversed(self.blocked_events[-8:]):
            audit_rows.append(
                f"""<tr>
                    <td class="text-muted">{b['timestamp']}</td>
                    <td><strong>{html.escape(b['symbol'])}</strong></td>
                    <td><span class="badge badge-blocked">REJEITADO</span></td>
                    <td>{html.escape(b['reason'])}</td>
                </tr>"""
            )

        if not audit_rows:
            audit_table_content = '<tr><td colspan="4" class="text-muted text-center">Nenhum evento malicioso recente detectado.</td></tr>'
        else:
            audit_table_content = "\n".join(audit_rows)

        net_color = "#10b981" if net_balance >= 0 else "#ef4444"

        html_content = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="3">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Asymmetric Momentum Engine - Telemetria v0.8</title>
    <style>
        :root {{
            --bg: #0b0f19;
            --surface: #111827;
            --border: #1f2937;
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
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
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
        .card-value {{ font-size: 22px; font-weight: 700; }}
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
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }}
        th {{ background: #1a2234; color: var(--text-dim); font-weight: 600; font-size: 12px; text-transform: uppercase; }}
        tr:last-child td {{ border-bottom: none; }}
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
        .section-title {{ font-size: 16px; font-weight: 700; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }}
    </style>
</head>
<body>
    <header>
        <div>
            <h1><span class="pulse"></span> Asymmetric Momentum Engine — Telemetria v0.8</h1>
            <div class="header-meta">Convex Holding Map & Anti-Cabal Distribution Monitor</div>
        </div>
        <div class="header-meta">
            Uptime: <strong>{uptime_str}</strong> | Atualização automática: <strong>3s</strong>
        </div>
    </header>

    <div class="grid">
        <div class="card">
            <div class="card-label">Slots de Carteira</div>
            <div class="card-value">{total_open} / {max_slots}</div>
        </div>
        <div class="card">
            <div class="card-label">PnL Não-Realizado</div>
            <div class="card-value {'text-green' if unrealized_pnl >= 0 else 'text-red'}">${unrealized_pnl:+.2f}</div>
        </div>
        <div class="card">
            <div class="card-label">PnL Realizado</div>
            <div class="card-value {'text-green' if realized_pnl >= 0 else 'text-red'}">${realized_pnl:+.2f}</div>
        </div>
        <div class="card">
            <div class="card-label">Taxas de Rede (Gás)</div>
            <div class="card-value">${gas_paid:.2f}</div>
        </div>
        <div class="card">
            <div class="card-label">Balanço Líquido (Net PnL)</div>
            <div class="card-value" style="color: {net_color};">${net_balance:+.2f}</div>
        </div>
    </div>

    <div class="section-title">📊 Posições Ativas em Monitoramento</div>
    <table>
        <thead>
            <tr>
                <th>Ticker</th>
                <th>Duração</th>
                <th>Entrada</th>
                <th>Atual</th>
                <th>Múltiplo</th>
                <th>Topo</th>
                <th>Stop Floor</th>
                <th>PnL Não-Realizado</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            {pos_table_content}
        </tbody>
    </table>

    <div class="section-title">🛡️ Auditoria On-Chain & Proteção Anti-Cabal</div>
    <table>
        <thead>
            <tr>
                <th>Horário</th>
                <th>Token</th>
                <th>Ação</th>
                <th>Motivo da Rejeição / Filtro</th>
            </tr>
        </thead>
        <tbody>
            {audit_table_content}
        </tbody>
    </table>
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
                           (SELECT price_usd FROM market_ticks m WHERE m.token_address = t.token_address ORDER BY m.id DESC LIMIT 1) AS latest_price
                    FROM trade_records t
                    WHERE t.status = 'OPEN'
                    """
                ) as cursor:
                    rows = await cursor.fetchall()
                    for r in rows:
                        sym, opened_at_str, entry, inv, real, curr = r
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
                            "is_risk_free": False,
                            "executed_targets": set(),
                        })

                async with db.execute(
                    "SELECT SUM(realized_pnl_usd - invested_usd), SUM(gas_fees_usd) FROM trade_records WHERE status != 'OPEN'"
                ) as cursor:
                    row = await cursor.fetchone()
                    realized_pnl = (row[0] or 0.0) if row else 0.0
                    gas_paid = (row[1] or 0.0) if row else 0.0

        class StandaloneClient:
            def __init__(self, pos, pnl, gas):
                self.positions = pos
                self.total_realized_pnl_usd = pnl
                self.total_gas_paid_usd = gas

        client = StandaloneClient(positions, realized_pnl, gas_paid)
        print(dashboard.format_terminal_view(client))
        html_file = dashboard.generate_html_dashboard(client)
        print(f"\n[OK] Dashboard HTML atualizado: {os.path.abspath(html_file)}")

    asyncio.run(run_standalone())
