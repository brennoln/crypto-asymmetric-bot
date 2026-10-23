import asyncio
import os
import sys

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import aiosqlite
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DB_PATH = os.path.join(PROJECT_ROOT, "data", "trading_history.db")


async def main():
    if not os.path.exists(DB_PATH):
        print("Banco de dados ainda não existe.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT token_symbol, status, entry_price, invested_usd,
                   realized_pnl_usd, COALESCE(gas_fees_usd, 0.0) AS gas_usd,
                   CASE WHEN status = 'OPEN' THEN NULL ELSE (realized_pnl_usd - invested_usd - COALESCE(gas_fees_usd, 0.0)) END AS net_pnl_usd,
                   ROUND(hold_duration_sec / 60.0, 1) AS hold_min, exit_reason
            FROM trade_records
            ORDER BY opened_at DESC
            """
        ) as cursor:
            rows = await cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            trades_df = pd.DataFrame(rows, columns=cols)

        async with db.execute("SELECT COUNT(*), COUNT(DISTINCT token_address) FROM market_ticks") as c:
            total_ticks, unique_tokens = await c.fetchone()

    print("=" * 78)
    print(f"RELATÓRIO QUANTITATIVO (v0.3) - Ticks: {total_ticks:,} | Moedas: {unique_tokens}")
    print("=" * 78)

    if trades_df.empty:
        print("Nenhum trade registrado ainda.")
        return

    closed = trades_df[trades_df["status"] != "OPEN"]
    open_trades = trades_df[trades_df["status"] == "OPEN"]
    total_gas = trades_df["gas_usd"].sum()

    print(f"Posições Abertas agora : {len(open_trades)}")
    print(f"Posições Encerradas    : {len(closed)}")
    print(f"Taxas de Rede Pagas    : ${total_gas:.2f}")


    if not closed.empty:
        total_net_pnl = closed["net_pnl_usd"].sum()
        wins = closed[closed["net_pnl_usd"] > 0]
        win_rate = (len(wins) / len(closed)) * 100.0
        best_trade = closed["net_pnl_usd"].max()
        worst_trade = closed["net_pnl_usd"].min()

        print(f"Win Rate (Fechadas)    : {win_rate:.1f}% ({len(wins)}/{len(closed)})")
        print(f"PnL Líquido Realizado  : ${total_net_pnl:+.2f}")
        print(f"Melhor Trade           : ${best_trade:+.2f} | Pior Trade: ${worst_trade:+.2f}")

    print("\n--- Últimas 15 Operações ---")
    print(trades_df.head(15).to_string(index=False))
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())