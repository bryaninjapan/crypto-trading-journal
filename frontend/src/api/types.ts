export type Market = "spot" | "usdm" | "coinm";
export type Direction = "Long" | "Short";

export interface Position {
  id: number;
  market: Market;
  symbol: string;
  direction: Direction;
  open_trade_id: number;
  open_time: number;
  close_time: number | null;
  hold_ms: number | null;
  qty: number | null;
  avg_entry: number | null;
  avg_exit: number | null;
  realized_pnl: number | null;
  pnl_asset: string | null;
  is_estimated: boolean;
  fees: number | null;
  fee_asset: string | null;
  funding: number | null;
  num_fills: number;
  close_price_usd: number | null;
  mae: number | null;
  mfe: number | null;
  entry_quality: number | null;
  opportunity_capture: number | null;
}

export interface Fill {
  trade_id: number;
  side: "BUY" | "SELL";
  price: number;
  qty_base: number;
  realized_pnl: number | null;
  fee: number | null;
  fee_asset: string | null;
  trade_time: number;
}

export interface PositionDetail extends Position {
  fills: Fill[];
  metrics_error?: string;
}

export interface Balance {
  market: Market;
  asset: string;
  free: number | null;
  locked: number | null;
  balance: number | null;
}

export interface Summary {
  balances: Balance[];
  kpi: {
    total_positions: number;
    win_rate: number;
    usdm_realized_pnl: number;
    pnl_asset: string;
  };
  equity_curve: { t: number; cum: number }[];
  note: string;
}

export interface Analytics {
  market: Market;
  kpi: {
    total_trades: number;
    avg_hold_ms: number;
    win_rate: number;
    longs: number;
    shorts: number;
    long_pct: number;
  };
  statistics: {
    total_gain_loss: number;
    trade_expectancy: number;
    avg_daily_gain: number;
    avg_daily_volume: number;
    largest_gain: number;
    total_trades_volume: number;
    avg_trades_per_day: number;
    avg_trade_win: number;
    avg_trade_loss: number;
    max_consecutive_win: number;
    max_consecutive_loss: number;
    largest_losses: number;
  };
  longs: {
    count: number;
    win_ratio: number;
    wins: number;
    losses: number;
    avg_duration_ms: number;
    total_realized_pnl: number;
    avg_win: number;
    avg_loss: number;
  };
  shorts: {
    count: number;
    win_ratio: number;
    wins: number;
    losses: number;
    avg_duration_ms: number;
    total_realized_pnl: number;
    avg_win: number;
    avg_loss: number;
  };
  equity_curve?: { t: number; cum: number; drawdown: number }[];
}

export interface SymbolRow {
  market: Market;
  symbol: string;
  trades: number;
  wins: number;
  longs: number;
  shorts: number;
  total_gain: number;
  avg_hold_ms: number;
  pnl_asset: string;
  is_estimated: boolean;
  win_rate: number;
}

export interface Candle {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface KlinesResponse {
  symbol: string;
  market: Market;
  interval: string;
  candles: Candle[];
  markers: {
    entry: { t: number; price: number | null };
    exit: { t: number | null; price: number | null } | null;
  };
}

export interface MaeMfePoint {
  t: number;
  mae: number;
  mfe: number;
}

export interface MaeMfeTimeline {
  timeline: MaeMfePoint[];
}
