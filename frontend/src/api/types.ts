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
  equity_curve: { t: number; cum: number; drawdown: number }[];
  expectancy_donut: { wins: number; losses: number; win_rate: number };
  long_short: { longs: number; shorts: number; long_pct: number };
  statistics: {
    total_gain_loss: number;
    trade_expectancy: number;
    avg_daily_gain: number;
    avg_hold_ms: number;
    avg_win: number;
    avg_loss: number;
  };
  pnl_asset: string;
  note: string;
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
