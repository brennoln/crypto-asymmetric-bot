from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PAPER_TRADING: bool = True
    POSITION_SIZE_USD: float = 15.0
    MAX_DAILY_LOSS_USD: float = 100.0

    # Gestão de Banca e Capital Simulado (v0.8.5)
    INITIAL_CAPITAL_BRL: float = 100.0      # Saldo inicial simulado da conta em Reais (R$)
    USD_BRL_RATE: float = 5.75              # Cotação de conversão USD/BRL
    POSITION_SIZING_MODE: str = "DYNAMIC"   # "DYNAMIC" (proporcional à banca e slots) ou "FIXED"
    MIN_CAPITAL_PER_TRADE_PCT: float = 0.15 # Alocação mínima de 15% da banca em pools rasas (~R$ 15 / ~$2.60)
    MAX_CAPITAL_PER_TRADE_PCT: float = 0.35 # Alocação máxima de 35% da banca em pools profundas e alto ignition (~R$ 35 / ~$6.00)
    MIN_POSITION_SIZE_USD: float = 3.50     # Alocação mínima viável por operação para diluir taxas de gás
    MAX_AMM_PRICE_IMPACT_PCT: float = 0.0030 # Impacto máximo de preço tolerado na AMM (0.30% da reserva de cotação)
    SHALLOW_POOL_THRESHOLD_USD: float = 35_000.0 # Limiar abaixo do qual pools sofrem penalização côncava de risco
    SHALLOW_POOL_EXPONENT: float = 0.75          # Coeficiente côncavo de penalidade de profundidade de liquidez

    @property
    def INITIAL_CAPITAL_USD(self) -> float:
        return round(self.INITIAL_CAPITAL_BRL / self.USD_BRL_RATE, 2)

    # Fricção Realista de Mercado (v0.3 / v0.5)
    ENABLE_PRICE_IMPACT: bool = True        # Modela impacto de preço na curva AMM x * y = k
    GAS_FEE_USD: float = 0.15               # Taxa de rede estimada por swap (Solana priority + base fee = ~$0.15)
    LP_SWAP_FEE_PCT: float = 0.003          # Taxa de swap da pool AMM (0.30% padrão Uniswap/Raydium)

    # Capacidade do Portfólio e Rotação
    MAX_ACTIVE_POSITIONS: int = 10          # Limite de moedas simultâneas
    MAX_HOLD_TIME_MINUTES: float = 35.0     # Tempo máximo de inércia antes de podar (35 min)
    INERTIA_CHECK_MINUTES: float = 10.0     # Checagem de inércia: estagnação se Z-Score <= 0 e preço negativo por 10 min
    ENABLE_FAST_CUT_INERTIA: bool = True    # Fast-cut de inércia negativa precoce (v0.9.1)
    INERTIA_FAST_CUT_MINUTES: float = 15.0  # Janela de checagem do fast-cut (15 minutos)
    FAST_CUT_RATIO_THRESHOLD: float = 0.95  # Se ratio < 0.95x aos 15m sem volume comprador, encerra posição
    MIN_PROFIT_TO_EXTEND_TIME: float = 0.20 # +20% de lucro concede sobrevida à moeda
    WATCHER_INTERVAL_SEC: float = 4.0

    # Parâmetros de Trailing Stop Monotônico e Contínuo Ratchet (v0.5 / v0.9 / v0.9.1)
    TRAILING_ACTIVATION_RATIO: float = 1.40 # Ativa regime pleno após +40% de alta
    TRAILING_DROP_PRE_2X: float = 0.20      # Antes de dobrar: recuo de 20% do topo protege lucro (monotônico com Breakeven)
    TRAILING_DROP_POST_1_5X: float = 0.25   # Recuo tolerado pós-1.5x (25% dá respiro para runners sem violar breakeven)
    TRAILING_FLOOR_POST_1_5X: float = 1.05  # Piso mínimo garantido de 1.05x após 1.5x (Breakeven com taxas cobertas)
    TRAILING_DROP_POST_2X: float = 0.35     # Após bater 2x (Risk-Free): recuo tolerado de 35%
    TRAILING_DROP_POST_3X: float = 0.25     # Após bater 3x: recuo de apenas 25% para proteger ganho expressivo
    TRAILING_FLOOR_POST_2X: float = 1.50    # Piso mínimo garantido de 1.50x após conquistar 2.0x
    TRAILING_FLOOR_POST_3X: float = 2.20    # Piso mínimo garantido de 2.20x após conquistar 3.0x

    # Proteção de Breakeven e Trava Precoce de Lucro (v0.4)
    ENABLE_BREAKEVEN_PROTECTION: bool = True
    BREAKEVEN_ACTIVATION_RATIO: float = 1.15 # Ativa proteção quando o pico atinge +15%
    BREAKEVEN_PULLBACK_DROP: float = 0.15    # Recuo de 15% a partir do pico intermediário trava lucro
    BREAKEVEN_FLOOR_RATIO: float = 1.02      # Stop no Breakeven (+2% cobre custos de taxas de entrada e saída)

    # Cobertura Automática de Custos de Taxa e Alvos Antecipados (v0.8.6)
    ENABLE_FEE_COVER_PROFIT: bool = True       # Vende fatia para cobrir taxas ao bater meta de lucro
    FEE_COVER_PROFIT_TRIGGER_BRL: float = 6.00 # Dispara quando lucro não-realizado da moeda atinge R$ 6,00
    FEE_COVER_AMOUNT_BRL: float = 0.60         # Montante em R$ a recuperar para cobrir custos de compra/venda
    FEE_COVER_MAX_SELL_PCT: float = 0.20       # Limite de no máximo 20% da moeda para cobertura de taxas

    # Detecção Invariante de Remoção de Liquidez (Soft-Rug) (v0.5)
    ENABLE_INVARIANT_ANTI_RUG: bool = True
    MIN_INVARIANT_LIQUIDITY_RATIO: float = 0.85 # Queda > 15% na constante k acusa remoção silenciosa de LP

    # Auditoria de Contrato e Segurança On-Chain (RugCheck + Anti-Honeypot + Anti-Cabal) (v0.4 / v0.6)
    ENABLE_RUGCHECK_AUDIT: bool = True
    RUGCHECK_API_URL: str = "https://api.rugcheck.xyz/v1/tokens"
    MAX_RUGCHECK_SCORE: int = 2000           # Scores RugCheck acima de 2000 são considerados perigosos
    MAX_DEV_HOLDING_PCT: float = 10.0        # Máximo % de suprimento na carteira criadora (dev)
    MAX_SINGLE_HOLDER_PCT: float = 15.0      # Máximo % de suprimento em qualquer carteira individual não-pool
    MAX_TOP10_HOLDERS_PCT: float = 30.0      # Máximo % de suprimento acumulado no Top 10 (excluindo pool de liquidez)
    MAX_INSIDERS_PCT: float = 15.0           # Máximo % de suprimento acumulado em carteiras identificadas como insiders

    # Critérios de Entrada e Microestrutura
    MIN_LIQUIDITY_USD: float = 8_000.0
    MAX_LIQUIDITY_USD: float = 120_000.0
    MIN_POOL_AGE_MINUTES: float = 15.0
    MAX_POOL_AGE_HOURS: float = 8.0
    MIN_VOLUME_M5_USD: float = 1_000.0
    MIN_BUYS_M5: int = 8
    MIN_BUY_SELL_RATIO: float = 1.35
    MIN_PRICE_CHANGE_M5_PCT: float = 0.0     # Rejeita candles negativos de 5m (evita queda em faca caindo)
    MAX_PRICE_CHANGE_M5_PCT: float = 150.0   # Rejeita topos parabólicos hiper-estendidos

    # Travas de Invalidação Rápida (Anti-Ruína)
    HARD_STOP_LOSS_PCT: float = 0.35        # Drop > 35% descarta imediatamente
    MIN_RUNNING_LIQUIDITY_USD: float = 6_000.0

    # Parâmetros da Estratégia Estatística em Tempo Real
    STAT_WINDOW_TICKS: int = 10
    STAT_ZSCORE_THRESHOLD: float = 1.2
    STAT_IMBALANCE_BUY: float = 0.50
    STAT_IMBALANCE_SELL: float = -0.35
    STAT_MIN_AGE_FOR_EXIT_MIN: float = 1.5  # Tempo mínimo antes de aceitar saída estatística por fluxo
    ENABLE_MICROSTRUCTURAL_EXHAUSTION: bool = True # Saída preventiva por exaustão de microestrutura
    CRITICAL_SELL_IMBALANCE: float = -0.40         # Desequilíbrio severo de fluxo vendedor (vendas > 2.3x compras)
    CRITICAL_SELL_PRICE_RATIO: float = 0.92        # Preço abaixo de 0.92x com OFI crítico ativa ejeção imediata

    # Endpoints Multi-Fonte do DexScreener
    DEXSCREENER_PROFILES_URL: str = "https://api.dexscreener.com/token-profiles/latest/v1"
    DEXSCREENER_BOOSTS_LATEST_URL: str = "https://api.dexscreener.com/token-boosts/latest/v1"
    DEXSCREENER_BOOSTS_TOP_URL: str = "https://api.dexscreener.com/token-boosts/top/v1"
    DEXSCREENER_TOKEN_URL: str = "https://api.dexscreener.com/latest/dex/tokens"

    # Filtro de Regime de Mercado, Timing Dinâmico e Circuit Breakers (v0.9.2)
    ENABLE_MACRO_REGIME_GATE: bool = True
    MACRO_BASE_TOKEN_ADDRESS: str = "So11111111111111111111111111111111111111112" # WSOL na Solana
    MACRO_MIN_1H_RETURN_PCT: float = -2.0  # Veto se ativo base (SOL) cair > 2.0% na última hora
    MACRO_POLL_INTERVAL_SEC: float = 45.0  # Frequência da thread assíncrona em segundo plano

    ENABLE_SESSION_LIQUIDITY_GATE: bool = True
    DEAD_ZONE_START_UTC_HOUR: int = 4      # 04:00 UTC (01h Brasília)
    DEAD_ZONE_END_UTC_HOUR: int = 10       # 10:00 UTC (07h Brasília)
    DEAD_ZONE_STRICT_MODE: bool = False    # Se True veta 100%; se False exige super-anomalia e reduz sizing
    DEAD_ZONE_MIN_Z_SCORE: float = 2.2     # Z-Score mínimo exigido durante a Zona Morta
    DEAD_ZONE_SIZE_MULTIPLIER: float = 0.50 # Reduz o sizing em 50% na Zona Morta

    ENABLE_SEQUENTIAL_STAGNATION_BREAKER: bool = True
    CONSECUTIVE_STAGNANT_THRESHOLD: int = 2     # Quantidade de time-stops seguidos para armar o disjuntor
    STAGNANT_MAX_PEAK_MULTIPLE: float = 1.05    # Considera estagnado se o pico do trade não passou de 1.05x
    STAGNANT_COOLDOWN_SECONDS: float = 10800.0  # 3 horas de cooldown para estancar perdas em regime de chop


settings = Settings()
