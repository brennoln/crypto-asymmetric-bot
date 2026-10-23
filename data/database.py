import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

# Caminho absoluto: independe do diretório de trabalho de quem executa o bot.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading_history.db")

DB_BUSY_TIMEOUT_SEC = 30.0
DB_BUSY_TIMEOUT_MS = int(DB_BUSY_TIMEOUT_SEC * 1000)


class DatabaseManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        """Abre uma conexão com WAL e espera por locks concorrentes."""
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SEC) as db:
            await db.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS};")
            await db.execute("PRAGMA journal_mode = WAL;")
            yield db

    @staticmethod
    def _trade_id(trade: dict) -> str:
        return f"{trade['token_address']}_{trade['opened_at']}"

    async def init_db(self, clear_history: bool = True):
        """Inicializa as tabelas de séries temporais e execução de trades."""
        async with self._connect() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS market_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    iso_time TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    token_symbol TEXT NOT NULL,
                    price_usd REAL NOT NULL,
                    liquidity_usd REAL NOT NULL,
                    vol_m5_usd REAL NOT NULL,
                    buys_m5 INTEGER NOT NULL,
                    sells_m5 INTEGER NOT NULL
                )
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_market_ticks_token
                ON market_ticks (token_address, id)
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS trade_records (
                    trade_id TEXT PRIMARY KEY,
                    token_symbol TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    pair_address TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    invested_usd REAL NOT NULL,
                    realized_pnl_usd REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    hold_duration_sec REAL,
                    exit_reason TEXT,
                    status TEXT NOT NULL,
                    gas_fees_usd REAL DEFAULT 0.0,
                    entry_price_impact REAL DEFAULT 0.0,
                    net_pnl_usd REAL DEFAULT 0.0
                )
            """)

            # Migração automática e segura para tabelas pré-existentes
            for col, col_def in [
                ("gas_fees_usd", "REAL DEFAULT 0.0"),
                ("entry_price_impact", "REAL DEFAULT 0.0"),
                ("net_pnl_usd", "REAL DEFAULT 0.0"),
            ]:
                try:
                    await db.execute(f"ALTER TABLE trade_records ADD COLUMN {col} {col_def};")
                except Exception:
                    pass

            await db.commit()
        if clear_history:
            async with self._connect() as db:
                await db.execute("DELETE FROM trade_records;")
                await db.execute("DELETE FROM market_ticks;")
                await db.commit()
        else:
            await self.cleanup_stale_open_trades()

    async def cleanup_stale_open_trades(self):
        """Encerra com segurança ordens órfãs deixadas com status OPEN de sessões anteriores."""
        async with self._connect() as db:
            now_iso = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """
                UPDATE trade_records
                SET status = 'DROPPED',
                    closed_at = ?,
                    exit_reason = 'Sessão anterior finalizada (Reinicialização)',
                    hold_duration_sec = COALESCE(hold_duration_sec, 1200.0)
                WHERE status = 'OPEN'
                """,
                (now_iso,),
            )
            await db.commit()

    async def log_tick(
        self,
        token_address: str,
        symbol: str,
        price: float,
        liq: float,
        vol_m5: float,
        buys: int,
        sells: int,
    ):
        """Grava uma observação pontual de mercado."""
        await self.log_ticks_batch(
            [
                {
                    "token_address": token_address,
                    "symbol": symbol,
                    "price": price,
                    "liq": liq,
                    "vol_m5": vol_m5,
                    "buys": buys,
                    "sells": sells,
                }
            ]
        )

    async def log_ticks_batch(self, ticks: list[dict]):
        """Grava múltiplos ticks de um único ciclo em lote (uma única transação SQLite)."""
        if not ticks:
            return
        now = datetime.now(timezone.utc)
        ts = now.timestamp()
        iso = now.isoformat()

        rows = [
            (
                ts,
                iso,
                t["token_address"],
                t["symbol"],
                t["price"],
                t["liq"],
                t["vol_m5"],
                t["buys"],
                t["sells"],
            )
            for t in ticks
        ]

        async with self._connect() as db:
            await db.executemany(
                """
                INSERT INTO market_ticks (
                    timestamp, iso_time, token_address, token_symbol,
                    price_usd, liquidity_usd, vol_m5_usd, buys_m5, sells_m5
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await db.commit()

    async def fetch_token_ticks(self, token_address: str, limit: int = 30) -> list[dict]:
        """Retorna os últimos ticks de UM token específico em ordem cronológica."""
        async with self._connect() as db:
            query = """
                SELECT timestamp, price_usd, vol_m5_usd, buys_m5, sells_m5
                FROM market_ticks
                WHERE token_address = ?
                ORDER BY id DESC
                LIMIT ?
            """
            async with db.execute(query, (token_address, limit)) as cursor:
                rows = await cursor.fetchall()

        rows.reverse()
        return [
            {
                "timestamp": r[0],
                "price_usd": r[1],
                "vol_m5_usd": r[2],
                "buys_m5": r[3],
                "sells_m5": r[4],
            }
            for r in rows
        ]

    async def insert_trade(self, trade: dict):
        """Grava a abertura de uma nova posição."""
        gas_fees = float(trade.get("gas_fees_usd", 0.0) or 0.0)
        impact = float(trade.get("entry_price_impact", 0.0) or 0.0)
        net_pnl = float(trade.get("realized_pnl_usd", 0.0) - trade["invested_usd"] - gas_fees)

        async with self._connect() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO trade_records (
                    trade_id, token_symbol, token_address, chain_id, pair_address,
                    entry_price, invested_usd, realized_pnl_usd, opened_at,
                    closed_at, hold_duration_sec, exit_reason, status,
                    gas_fees_usd, entry_price_impact, net_pnl_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._trade_id(trade),
                    trade["token_symbol"],
                    trade["token_address"],
                    trade["chain_id"],
                    trade["pair_address"],
                    trade["entry_price"],
                    trade["invested_usd"],
                    trade["realized_pnl_usd"],
                    trade["opened_at"],
                    None,
                    None,
                    None,
                    "OPEN",
                    gas_fees,
                    impact,
                    net_pnl,
                ),
            )
            await db.commit()

    async def update_trade_partial(self, trade: dict):
        """Atualiza o PnL realizado no banco imediatamente após uma venda parcial."""
        gas_fees = float(trade.get("gas_fees_usd", 0.0) or 0.0)
        net_pnl = float(trade.get("realized_pnl_usd", 0.0) - trade["invested_usd"] - gas_fees)

        async with self._connect() as db:
            await db.execute(
                """
                UPDATE trade_records
                SET realized_pnl_usd = ?,
                    gas_fees_usd = ?,
                    net_pnl_usd = ?
                WHERE trade_id = ?
                """,
                (trade["realized_pnl_usd"], gas_fees, net_pnl, self._trade_id(trade)),
            )
            await db.commit()

    async def close_trade(self, trade: dict, exit_reason: str, duration_sec: float):
        """Atualiza a liquidação final e o PnL fechado do trade."""
        now = datetime.now(timezone.utc).isoformat()
        gas_fees = float(trade.get("gas_fees_usd", 0.0) or 0.0)
        net_pnl = float(trade.get("realized_pnl_usd", 0.0) - trade["invested_usd"] - gas_fees)

        async with self._connect() as db:
            await db.execute(
                """
                UPDATE trade_records
                SET closed_at = ?,
                    hold_duration_sec = ?,
                    realized_pnl_usd = ?,
                    exit_reason = ?,
                    status = ?,
                    gas_fees_usd = ?,
                    net_pnl_usd = ?
                WHERE trade_id = ?
                """,
                (
                    now,
                    duration_sec,
                    trade["realized_pnl_usd"],
                    exit_reason,
                    trade["status"],
                    gas_fees,
                    net_pnl,
                    self._trade_id(trade),
                ),
            )
            await db.commit()



db_manager = DatabaseManager()