import { useEffect, useState, useMemo } from 'react';
import {
  Activity,
  AlertOctagon,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Bot,
  CheckCircle2,
  Clock,
  Layers,
  Percent,
  RefreshCw,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  Volume2,
  VolumeX,
  Wallet,
  Zap,
} from 'lucide-react';
import './App.css';

type Stats = {
  total_trades: number;
  closed_trades: number;
  win_rate: number;
  net_pnl: number;
  open_trades: number;
  winning_trades?: number;
  losing_trades?: number;
  profit_factor?: number;
};

type BrokerStatus = {
  status: 'CONNECTED' | 'DISCONNECTED' | 'ERROR';
  client_id: string;
  is_configured: boolean;
  mode: 'PAPER' | 'LIVE' | 'STANDBY';
  available_balance: number;
  sod_limit: number;
  utilized_amount: number;
  collateral_amount: number;
  open_broker_positions: number;
  last_synced: string;
};

type MarketQuote = {
  symbol: string;
  last_price: number;
  change: number;
  pct_change: number;
  high: number;
  low: number;
  trend?: 'BULLISH' | 'BEARISH' | 'SIDEWAYS';
  regime?: string;
  regime_desc?: string;
  confidence?: number;
  buy_votes?: number;
  sell_votes?: number;
};

type Signal = {
  id: number;
  symbol: string;
  signal_type: 'BUY' | 'SELL';
  strategy_name: string;
  confidence: number;
  price: number;
  timestamp: string;
};

type Trade = {
  id: number;
  symbol: string;
  display_symbol?: string;
  trade_type: 'BUY' | 'SELL';
  signal_direction?: 'BUY' | 'SELL';
  option_type?: string | null;
  strike?: number | null;
  quantity?: number;
  entry_price: number;
  exit_price: number | null;
  profit_loss: number | null;
  current_price?: number | null;
  live_profit_loss?: number | null;
  stop_loss?: number | null;
  target_1?: number | null;
  target_2?: number | null;
  status: 'OPEN' | 'CLOSED';
  strategy_used: string;
  entry_time: string;
  exit_time: string | null;
  close_reason?: string | null;
};

type PnlHistoryPoint = {
  trade_id: number;
  trade_num: number;
  symbol: string;
  pnl: number;
  cumulative_pnl: number;
  time: string;
  strategy: string;
};

type StrategyPerformance = {
  strategy: string;
  trades: number;
  wins: number;
  win_rate: number;
  pnl: number;
};

const API_BASE = 'http://127.0.0.1:8000';

const initialStats: Stats = {
  total_trades: 0,
  closed_trades: 0,
  win_rate: 0,
  net_pnl: 0,
  open_trades: 0,
  winning_trades: 0,
  losing_trades: 0,
  profit_factor: 1.0,
};

const initialBroker: BrokerStatus = {
  status: 'DISCONNECTED',
  client_id: 'Not Configured',
  is_configured: false,
  mode: 'PAPER',
  available_balance: 0,
  sod_limit: 0,
  utilized_amount: 0,
  collateral_amount: 0,
  open_broker_positions: 0,
  last_synced: '',
};

function formatCurrency(value: number | null | undefined) {
  if (value === null || value === undefined || isNaN(Number(value))) {
    return '₹0.00';
  }
  const num = Number(value);
  const sign = num < 0 ? '-' : '';
  const absNum = Math.abs(num);
  return `${sign}₹${absNum.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

const LOT_SIZES: Record<string, number> = {
  NIFTY: 65,
  BANKNIFTY: 30,
  SENSEX: 20,
  FINNIFTY: 60,
  MIDCPNIFTY: 120,
};

function formatQuantityLots(trade: Trade) {
  const qty = trade.quantity || 65;
  const sym = (trade.symbol || '').toUpperCase();
  const lotSize = LOT_SIZES[sym] || (sym.includes('SENSEX') ? 20 : 65);
  const lots = Math.max(1, Math.round(qty / lotSize));
  return { lots, qty, lotSize };
}

function formatDateTime(value: string | null) {
  if (!value) return '-';
  const normalizedValue = /z|[+-]\d{2}:\d{2}$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalizedValue);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function playNotificationChime() {
  try {
    const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(587.33, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.15);
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.3);
  } catch {
    // Audio context may be restricted before first interaction
  }
}

export function App() {
  const [stats, setStats] = useState<Stats>(initialStats);
  const [broker, setBroker] = useState<BrokerStatus>(initialBroker);
  const [marketQuotes, setMarketQuotes] = useState<MarketQuote[]>([]);
  const [openTrades, setOpenTrades] = useState<Trade[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [pnlHistory, setPnlHistory] = useState<PnlHistoryPoint[]>([]);
  const [strategyStats, setStrategyStats] = useState<StrategyPerformance[]>([]);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [closingTradeId, setClosingTradeId] = useState<number | null>(null);
  const [isClosingAll, setIsClosingAll] = useState(false);
  const [showKillModal, setShowKillModal] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [historyFilter, setHistoryFilter] = useState<'ALL' | 'PROFIT' | 'LOSS'>('ALL');
  const [symbolFilter, setSymbolFilter] = useState<string>('ALL');

  async function loadDashboard(isBackgroundPoll = false) {
    if (!isBackgroundPoll) setRefreshing(true);

    try {
      setError('');
      const [
        statsRes,
        brokerRes,
        marketRes,
        openTradesRes,
        recentTradesRes,
        signalsRes,
        analyticsRes,
      ] = await Promise.allSettled([
        fetch(`${API_BASE}/api/stats`),
        fetch(`${API_BASE}/api/broker/status`),
        fetch(`${API_BASE}/api/market/overview`),
        fetch(`${API_BASE}/api/trades/open`),
        fetch(`${API_BASE}/api/trades/recent`),
        fetch(`${API_BASE}/api/signals/recent`),
        fetch(`${API_BASE}/api/analytics/pnl-history`),
      ]);

      if (statsRes.status === 'fulfilled' && statsRes.value.ok) setStats(await statsRes.value.json());
      if (brokerRes.status === 'fulfilled' && brokerRes.value.ok) setBroker(await brokerRes.value.json());
      if (marketRes.status === 'fulfilled' && marketRes.value.ok) setMarketQuotes(await marketRes.value.json());
      if (openTradesRes.status === 'fulfilled' && openTradesRes.value.ok) setOpenTrades(await openTradesRes.value.json());
      if (recentTradesRes.status === 'fulfilled' && recentTradesRes.value.ok) setTrades(await recentTradesRes.value.json());
      if (signalsRes.status === 'fulfilled' && signalsRes.value.ok) setSignals(await signalsRes.value.json());
      if (analyticsRes.status === 'fulfilled' && analyticsRes.value.ok) {
        const aData = await analyticsRes.value.json();
        setPnlHistory(aData.cumulative_history || []);
        setStrategyStats(aData.strategy_breakdown || []);
      }
    } catch {
      setError('Backend API unreachable. Ensure backend server is running.');
    } finally {
      setLoading(false);
      if (!isBackgroundPoll) setRefreshing(false);
    }
  }

  useEffect(() => {
    const initialLoad = window.setTimeout(() => {
      void loadDashboard(false);
    }, 0);
    const interval = setInterval(() => {
      void loadDashboard(true);
    }, 1000);
    return () => {
      window.clearTimeout(initialLoad);
      clearInterval(interval);
    };
  }, []);

  async function handleCloseTrade(trade: Trade) {
    setClosingTradeId(trade.id);
    setNotice('');
    setError('');

    try {
      const response = await fetch(`${API_BASE}/api/trades/${trade.id}/close`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Manual Exit from Terminal' }),
      });

      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(payload?.detail || 'Trade close failed.');
      }

      if (soundEnabled) playNotificationChime();
      setNotice(`Closed position for ${trade.display_symbol || trade.symbol} at ${formatCurrency(payload?.exit_price)}`);
      await loadDashboard(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Trade close failed');
    } finally {
      setClosingTradeId(null);
    }
  }

  async function handleEmergencyCloseAll() {
    setIsClosingAll(true);
    setShowKillModal(false);
    setNotice('');
    setError('');

    try {
      const response = await fetch(`${API_BASE}/api/trades/close-all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Emergency Kill Switch Activated' }),
      });

      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(payload?.detail || 'Emergency square-off failed.');
      }

      if (soundEnabled) playNotificationChime();
      setNotice(`Emergency Kill Switch: ${payload?.message || 'Positions closed'}`);
      await loadDashboard(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Emergency square-off failed');
    } finally {
      setIsClosingAll(false);
    }
  }

  const filteredHistory = useMemo(() => {
    return trades.filter((t) => {
      if (symbolFilter !== 'ALL' && !t.symbol.toUpperCase().includes(symbolFilter)) return false;
      if (historyFilter === 'PROFIT') return (t.profit_loss ?? 0) > 0;
      if (historyFilter === 'LOSS') return (t.profit_loss ?? 0) < 0;
      return true;
    });
  }, [trades, symbolFilter, historyFilter]);

  // SVG Chart Calculation
  const chartPath = useMemo(() => {
    if (pnlHistory.length < 2) return null;
    const width = 600;
    const height = 140;
    const padding = 20;

    const values = pnlHistory.map((p) => p.cumulative_pnl);
    const minVal = Math.min(...values, 0);
    const maxVal = Math.max(...values, 0);
    const range = maxVal - minVal || 1;

    const points = pnlHistory.map((p, idx) => {
      const x = padding + (idx / (pnlHistory.length - 1)) * (width - 2 * padding);
      const y = height - padding - ((p.cumulative_pnl - minVal) / range) * (height - 2 * padding);
      return { x, y, pnl: p.cumulative_pnl };
    });

    const d = points.reduce((acc, pt, idx) => {
      return idx === 0 ? `M ${pt.x} ${pt.y}` : `${acc} L ${pt.x} ${pt.y}`;
    }, '');

    const zeroY = height - padding - ((0 - minVal) / range) * (height - 2 * padding);
    const areaD = `${d} L ${points[points.length - 1].x} ${zeroY} L ${points[0].x} ${zeroY} Z`;

    return { d, areaD, zeroY, points, isPositive: values[values.length - 1] >= 0 };
  }, [pnlHistory]);

  return (
    <div className="min-h-screen bg-[#070b13] text-slate-100 selection:bg-cyan-500/30 selection:text-cyan-200">
      {/* Top Live Ticker Bar */}
      <div className="border-b border-slate-800/80 bg-slate-950/80 px-4 py-2 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex items-center gap-4 overflow-x-auto py-1">
            <div className="flex items-center gap-2 font-mono font-bold text-cyan-400">
              <Zap className="h-4 w-4 animate-pulse text-cyan-400" />
              <span>DHAN SCANNER PRO</span>
            </div>

            <div className="h-4 w-px bg-slate-800" />

            {marketQuotes.length > 0 ? (
              marketQuotes.map((q) => (
                <div key={q.symbol} className="flex items-center gap-2 font-mono">
                  <span className="font-semibold text-slate-400">{q.symbol}</span>
                  <span className="font-bold text-white">{q.last_price?.toLocaleString('en-IN')}</span>
                  <span
                    className={`flex items-center text-[11px] font-semibold ${
                      q.pct_change >= 0 ? 'text-emerald-400' : 'text-rose-400'
                    }`}
                  >
                    {q.pct_change >= 0 ? (
                      <ArrowUpRight className="h-3 w-3" />
                    ) : (
                      <ArrowDownRight className="h-3 w-3" />
                    )}
                    {q.pct_change >= 0 ? '+' : ''}
                    {q.pct_change?.toFixed(2)}%
                  </span>
                  {q.trend && (
                    <span
                      className={`rounded px-1.5 py-0.5 text-[9px] font-extrabold uppercase tracking-wider ${
                        q.trend === 'BULLISH'
                          ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                          : q.trend === 'BEARISH'
                          ? 'bg-rose-500/20 text-rose-400 border border-rose-500/40'
                          : 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                      }`}
                    >
                      {q.trend === 'BULLISH' ? '● BULL' : q.trend === 'BEARISH' ? '● BEAR' : '● SIDEWAYS'}
                    </span>
                  )}
                </div>
              ))
            ) : (
              <span className="text-slate-500">Connecting to Market Stream...</span>
            )}
          </div>

          <div className="flex items-center gap-3 font-mono">
            <button
              type="button"
              onClick={() => setSoundEnabled(!soundEnabled)}
              title={soundEnabled ? 'Audio alerts active' : 'Audio alerts muted'}
              className="flex items-center gap-1 text-slate-400 hover:text-slate-200"
            >
              {soundEnabled ? (
                <Volume2 className="h-3.5 w-3.5 text-cyan-400" />
              ) : (
                <VolumeX className="h-3.5 w-3.5 text-slate-600" />
              )}
            </button>

            <div className="flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-900/90 px-2.5 py-0.5">
              <span
                className={`h-2 w-2 rounded-full ${
                  broker.status === 'CONNECTED'
                    ? 'bg-emerald-400 animate-pulse-dot'
                    : 'bg-rose-500'
                }`}
              />
              <span className="text-[11px] text-slate-300">
                DHAN: {broker.status}
              </span>
            </div>

            <div
              className={`rounded px-2 py-0.5 text-[11px] font-bold ${
                broker.mode === 'LIVE'
                  ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40 animate-pulse'
                  : 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/30'
              }`}
            >
              {broker.mode === 'LIVE' ? '● LIVE BROKER MODE' : '● PAPER TRADING'}
            </div>
          </div>
        </div>
      </div>

      {/* Main Terminal Body */}
      <div className="mx-auto max-w-7xl p-4 md:p-6 lg:p-8">
        {/* Terminal Header */}
        <header className="mb-6 flex flex-col justify-between gap-4 border-b border-slate-800/80 pb-5 md:flex-row md:items-center">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-black tracking-tight text-white md:text-3xl">
                Algorithmic Trading Dashboard
              </h1>
              <span className="rounded-md border border-cyan-500/30 bg-cyan-950/40 px-2.5 py-0.5 text-xs font-bold text-cyan-400">
                v2.6 Pro
              </span>
            </div>
            <p className="mt-1 flex items-center gap-2 text-xs text-slate-400 md:text-sm">
              <Bot className="h-4 w-4 text-emerald-400" />
              Autonomous F&O strategy scanner with Dhan live execution & real-time risk controls.
            </p>
          </div>

          <div className="flex items-center gap-3">
            {openTrades.length > 0 && (
              <button
                type="button"
                onClick={() => setShowKillModal(true)}
                disabled={isClosingAll}
                className="flex h-10 items-center gap-2 rounded-lg border border-rose-500/40 bg-rose-600/20 px-4 text-xs font-bold text-rose-200 transition hover:bg-rose-600/30 glow-crimson"
              >
                <AlertOctagon className="h-4 w-4" />
                {isClosingAll ? 'Closing All...' : 'EMERGENCY CLOSE ALL'}
              </button>
            )}

            <button
              type="button"
              onClick={() => void loadDashboard(true)}
              disabled={refreshing || loading}
              className="flex h-10 items-center gap-2 rounded-lg border border-slate-700 bg-slate-800/90 px-4 text-xs font-semibold text-slate-200 transition hover:border-slate-600 hover:bg-slate-700"
            >
              <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin text-cyan-400' : ''}`} />
              <span>{refreshing ? 'Refreshing...' : 'Refresh'}</span>
            </button>
          </div>
        </header>

        {/* Status Alerts */}
        {error && (
          <div className="mb-6 flex items-center gap-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200">
            <ShieldAlert className="h-5 w-5 shrink-0 text-rose-400" />
            <span>{error}</span>
          </div>
        )}

        {notice && (
          <div className="mb-6 flex items-center gap-3 rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-4 text-sm text-emerald-200">
            <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-400" />
            <span>{notice}</span>
          </div>
        )}

        {/* Live Market Regime & Trend Confluence Radar */}
        {marketQuotes.length > 0 && (
          <section className="mb-6">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-slate-400">
                <Activity className="h-4 w-4 text-cyan-400" />
                <span>Live Market Regime & AI Confluence Radar</span>
                <span className="rounded-full bg-cyan-950/60 border border-cyan-500/30 px-2 py-0.5 text-[10px] font-semibold text-cyan-300 animate-pulse">
                  ● Real-Time AI Stream
                </span>
              </div>
              <span className="text-[11px] text-slate-500 font-mono">1s Auto-Scan</span>
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              {marketQuotes.map((q) => {
                const isBull = q.trend === 'BULLISH';
                const isBear = q.trend === 'BEARISH';
                const isSide = !isBull && !isBear;

                return (
                  <div
                    key={q.symbol}
                    className={`relative overflow-hidden rounded-xl border p-4 transition-all duration-300 ${
                      isBull
                        ? 'border-emerald-500/40 bg-gradient-to-b from-emerald-950/25 to-slate-900/90 shadow-lg shadow-emerald-950/20'
                        : isBear
                        ? 'border-rose-500/40 bg-gradient-to-b from-rose-950/25 to-slate-900/90 shadow-lg shadow-rose-950/20'
                        : 'border-amber-500/30 bg-gradient-to-b from-amber-950/15 to-slate-900/90 shadow-lg shadow-amber-950/10'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-base font-black tracking-wide text-white">{q.symbol}</span>
                          <span className="rounded bg-slate-800 px-1.5 py-0.2 text-[10px] font-mono text-slate-400">SPOT</span>
                        </div>
                        <div className="mt-1 flex items-baseline gap-2">
                          <span className="text-2xl font-black font-mono tracking-tight text-white">
                            ₹{q.last_price?.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                          </span>
                          <span
                            className={`text-xs font-bold ${
                              q.pct_change >= 0 ? 'text-emerald-400' : 'text-rose-400'
                            }`}
                          >
                            {q.pct_change >= 0 ? '+' : ''}
                            {q.pct_change?.toFixed(2)}%
                          </span>
                        </div>
                      </div>

                      <div
                        className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-extrabold uppercase tracking-wider ${
                          isBull
                            ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/50 shadow-sm shadow-emerald-500/20'
                            : isBear
                            ? 'bg-rose-500/20 text-rose-300 border border-rose-500/50 shadow-sm shadow-rose-500/20'
                            : 'bg-amber-500/20 text-amber-300 border border-amber-500/50 shadow-sm shadow-amber-500/20'
                        }`}
                      >
                        <span
                          className={`h-2 w-2 rounded-full ${
                            isBull ? 'bg-emerald-400 animate-pulse' : isBear ? 'bg-rose-400 animate-pulse' : 'bg-amber-400'
                          }`}
                        />
                        {q.regime || (isBull ? 'BULLISH' : isBear ? 'BEARISH' : 'SIDEWAYS')}
                      </div>
                    </div>

                    <p className="mt-2.5 text-xs leading-relaxed text-slate-300 min-h-[32px]">
                      {q.regime_desc || (isSide ? 'Market is rangebound / consolidating. Bot waiting for 15m breakout.' : 'Trend active.')}
                    </p>

                    <div className="mt-3 flex items-center justify-between border-t border-slate-800/80 pt-2.5 text-[11px] font-mono">
                      <div>
                        <span className="text-slate-500">Day Range: </span>
                        <span className="text-slate-200 font-semibold">₹{q.low?.toLocaleString('en-IN')}</span> - <span className="text-slate-200 font-semibold">₹{q.high?.toLocaleString('en-IN')}</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          q.buy_votes ? 'bg-emerald-950/80 text-emerald-300 border border-emerald-500/40' : 'bg-slate-800 text-slate-500'
                        }`}>
                          Buy: {q.buy_votes || 0}
                        </span>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          q.sell_votes ? 'bg-rose-950/80 text-rose-300 border border-rose-500/40' : 'bg-slate-800 text-slate-500'
                        }`}>
                          Sell: {q.sell_votes || 0}
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* 4 Hero KPI Cards */}
        <section className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {/* Card 1: Net PnL */}
          <div className="glass-card rounded-xl p-5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
                Net Realized P&L
              </span>
              <div
                className={`rounded-full p-2 ${
                  stats.net_pnl >= 0 ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'
                }`}
              >
                {stats.net_pnl >= 0 ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
              </div>
            </div>
            <div className="mt-3">
              <h2
                className={`text-3xl font-extrabold tracking-tight font-mono ${
                  stats.net_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                {formatCurrency(stats.net_pnl)}
              </h2>
              <div className="mt-2 flex items-center gap-2 text-xs text-slate-400 font-mono">
                <span className="text-emerald-400">+{stats.winning_trades || 0} Wins</span>
                <span>/</span>
                <span className="text-rose-400">-{stats.losing_trades || 0} Losses</span>
              </div>
            </div>
          </div>

          {/* Card 2: Dhan Margin / Funds */}
          <div className="glass-card rounded-xl p-5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
                Dhan Available Funds
              </span>
              <div className="rounded-full bg-cyan-500/10 p-2 text-cyan-400">
                <Wallet className="h-4 w-4" />
              </div>
            </div>
            <div className="mt-3">
              <h2 className="text-3xl font-extrabold tracking-tight font-mono text-cyan-300">
                {formatCurrency(broker.available_balance)}
              </h2>
              <div className="mt-2 flex items-center justify-between text-xs text-slate-400 font-mono">
                <span>SOD Limit: {formatCurrency(broker.sod_limit)}</span>
                <span className="text-slate-500">{broker.client_id}</span>
              </div>
            </div>
          </div>

          {/* Card 3: Win Rate */}
          <div className="glass-card rounded-xl p-5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
                Win Rate & Factor
              </span>
              <div className="rounded-full bg-amber-500/10 p-2 text-amber-400">
                <Percent className="h-4 w-4" />
              </div>
            </div>
            <div className="mt-3">
              <div className="flex items-baseline gap-2">
                <h2 className="text-3xl font-extrabold tracking-tight font-mono text-amber-300">
                  {stats.win_rate.toFixed(1)}%
                </h2>
                <span className="text-xs font-semibold text-slate-400 font-mono">
                  (PF: {stats.profit_factor?.toFixed(2) || '1.00'})
                </span>
              </div>
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                <div
                  className="h-full bg-gradient-to-r from-amber-500 to-emerald-400"
                  style={{ width: `${Math.min(100, Math.max(0, stats.win_rate))}%` }}
                />
              </div>
            </div>
          </div>

          {/* Card 4: Active Positions */}
          <div className="glass-card rounded-xl p-5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
                Running Positions
              </span>
              <div className="rounded-full bg-fuchsia-500/10 p-2 text-fuchsia-400">
                <Activity className="h-4 w-4" />
              </div>
            </div>
            <div className="mt-3">
              <h2 className="text-3xl font-extrabold tracking-tight font-mono text-fuchsia-300">
                {stats.open_trades}
              </h2>
              <div className="mt-2 flex items-center justify-between text-xs text-slate-400 font-mono">
                <span>Total Executed: {stats.total_trades}</span>
                <span className="flex items-center gap-1 text-emerald-400">
                  <span className="h-2 w-2 rounded-full bg-emerald-400 animate-ping" />
                  Live
                </span>
              </div>
            </div>
          </div>
        </section>

        {/* Analytics & Signal Radar Section */}
        <section className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* Equity Curve & Strategy Chart (2 Columns) */}
          <div className="glass-panel rounded-xl p-5 lg:col-span-2">
            <div className="mb-4 flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-cyan-400" />
                <h2 className="text-sm font-bold uppercase tracking-wider text-white">
                  Cumulative P&L Curve & Strategies
                </h2>
              </div>
              <span className="text-xs font-mono text-slate-400">
                Closed Trades: {pnlHistory.length}
              </span>
            </div>

            {chartPath ? (
              <div className="mb-4">
                <svg
                  viewBox="0 0 600 140"
                  className="h-36 w-full overflow-visible"
                  preserveAspectRatio="none"
                >
                  <defs>
                    <linearGradient id="pnlAreaGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop
                        offset="0%"
                        stopColor={chartPath.isPositive ? '#10b981' : '#f43f5e'}
                        stopOpacity="0.3"
                      />
                      <stop
                        offset="100%"
                        stopColor={chartPath.isPositive ? '#10b981' : '#f43f5e'}
                        stopOpacity="0.0"
                      />
                    </linearGradient>
                  </defs>

                  {/* Zero Line */}
                  <line
                    x1="20"
                    y1={chartPath.zeroY}
                    x2="580"
                    y2={chartPath.zeroY}
                    stroke="rgba(100, 116, 139, 0.3)"
                    strokeDasharray="4 4"
                  />

                  {/* Area Fill */}
                  <path d={chartPath.areaD} fill="url(#pnlAreaGrad)" />

                  {/* Line Stroke */}
                  <path
                    d={chartPath.d}
                    fill="none"
                    stroke={chartPath.isPositive ? '#10b981' : '#f43f5e'}
                    strokeWidth="2.5"
                    strokeLinecap="round"
                  />

                  {/* Points */}
                  {chartPath.points.map((pt, idx) => (
                    <circle
                      key={idx}
                      cx={pt.x}
                      cy={pt.y}
                      r="3.5"
                      fill={pt.pnl >= 0 ? '#10b981' : '#f43f5e'}
                      stroke="#0f172a"
                      strokeWidth="1.5"
                    />
                  ))}
                </svg>
              </div>
            ) : (
              <div className="flex h-36 items-center justify-center text-xs text-slate-500 font-mono">
                Equity curve will render after completing 2 or more closed trades.
              </div>
            )}

            {/* Strategy Performance Matrix */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {strategyStats.length > 0 ? (
                strategyStats.slice(0, 4).map((strat) => (
                  <div key={strat.strategy} className="rounded-lg bg-slate-900/60 p-2.5 border border-slate-800">
                    <div className="truncate text-[11px] font-medium text-slate-400">
                      {strat.strategy}
                    </div>
                    <div className="mt-1 flex items-baseline justify-between">
                      <span
                        className={`text-xs font-bold font-mono ${
                          strat.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                        }`}
                      >
                        {formatCurrency(strat.pnl)}
                      </span>
                      <span className="text-[10px] text-slate-500 font-mono">
                        {strat.win_rate}% win
                      </span>
                    </div>
                  </div>
                ))
              ) : (
                <div className="col-span-4 text-center text-xs text-slate-500 py-1">
                  No strategy metrics available yet.
                </div>
              )}
            </div>
          </div>

          {/* AI Signal Radar (1 Column) */}
          <div className="glass-panel rounded-xl p-5">
            <div className="mb-4 flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <Bot className="h-4 w-4 text-emerald-400" />
                <h2 className="text-sm font-bold uppercase tracking-wider text-white">
                  Live AI Signal Radar
                </h2>
              </div>
              <span className="flex items-center gap-1.5 text-xs text-emerald-400 font-mono">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-ping" />
                Scanning
              </span>
            </div>

            <div className="flex flex-col gap-2.5 overflow-y-auto max-h-[220px]">
              {signals.length > 0 ? (
                signals.slice(0, 4).map((sig) => (
                  <div
                    key={sig.id}
                    className="flex items-center justify-between rounded-lg border border-slate-800/80 bg-slate-900/80 p-3 hover:border-slate-700"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-white text-xs">{sig.symbol}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                            sig.signal_type === 'BUY'
                              ? 'bg-emerald-500/20 text-emerald-300'
                              : 'bg-rose-500/20 text-rose-300'
                          }`}
                        >
                          {sig.signal_type}
                        </span>
                      </div>
                      <div className="mt-1 text-[11px] text-slate-400">
                        {sig.strategy_name || 'AI Signal Engine'}
                      </div>
                    </div>

                    <div className="text-right">
                      <div className="text-xs font-bold font-mono text-cyan-400">
                        {sig.confidence ? `${sig.confidence}% Conf` : 'Triggered'}
                      </div>
                      <div className="mt-1 flex items-center gap-1 text-[10px] text-slate-500 font-mono justify-end">
                        <Clock className="h-3 w-3" />
                        {formatDateTime(sig.timestamp).split(',')[1] || '-'}
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <div className="flex h-36 flex-col items-center justify-center text-center text-xs text-slate-500">
                  <Activity className="h-6 w-6 text-slate-600 mb-2 animate-pulse" />
                  <span>Waiting for next high-confidence signal trigger...</span>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* Active Running Positions Section */}
        <section className="mb-6 overflow-hidden rounded-xl border border-slate-800/80 glass-panel">
          <div className="flex flex-wrap items-center justify-between border-b border-slate-800 px-5 py-4">
            <div className="flex items-center gap-2">
              <Activity className="h-5 w-5 text-fuchsia-400 animate-pulse" />
              <div>
                <h2 className="text-base font-bold text-white">Active Running Positions</h2>
                <p className="text-xs text-slate-400">
                  Real-time LTP, Trailing Stop-Loss, and Target tracking.
                </p>
              </div>
            </div>
            <span className="rounded-full border border-fuchsia-500/30 bg-fuchsia-500/10 px-3 py-1 text-xs font-mono font-bold text-fuchsia-300">
              {openTrades.length} ACTIVE
            </span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead>
                <tr className="bg-slate-950/60 font-mono uppercase tracking-wider text-slate-400">
                  <th className="px-4 py-3">Contract / Instrument</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Lots / Qty</th>
                  <th className="px-4 py-3">Entry Price</th>
                  <th className="px-4 py-3">Current LTP</th>
                  <th className="px-4 py-3">Stop-Loss</th>
                  <th className="px-4 py-3">Target 1 & 2</th>
                  <th className="px-4 py-3">Live P&L</th>
                  <th className="px-4 py-3">Strategy</th>
                  <th className="px-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono">
                {!loading && openTrades.length === 0 ? (
                  <tr>
                    <td colSpan={10} className="px-4 py-12 text-center text-slate-500">
                      <div className="flex flex-col items-center justify-center gap-2">
                        <Layers className="h-8 w-8 text-slate-700" />
                        <span>No active positions currently running. Scanner is active.</span>
                      </div>
                    </td>
                  </tr>
                ) : null}

                {openTrades.map((trade) => {
                  const pnl = trade.live_profit_loss ?? 0;
                  const isProfitable = pnl >= 0;
                  const { lots, qty } = formatQuantityLots(trade);

                  return (
                    <tr key={`open-${trade.id}`} className="transition hover:bg-slate-800/40">
                      <td className="px-4 py-3.5 font-bold text-white">
                        <div className="flex items-center gap-2">
                          <span>{trade.display_symbol || trade.symbol}</span>
                          {trade.option_type && (
                            <span className="rounded bg-cyan-950 px-1.5 py-0.5 text-[10px] text-cyan-300 border border-cyan-800">
                              {trade.option_type}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <span
                          className={`inline-flex rounded px-2 py-0.5 text-[10px] font-bold ${
                            trade.signal_direction === 'BUY' || trade.trade_type === 'BUY'
                              ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                              : 'bg-rose-500/20 text-rose-300 border border-rose-500/30'
                          }`}
                        >
                          {trade.signal_direction || trade.trade_type}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 text-slate-300">
                        <div className="flex items-center gap-1.5">
                          <span className="rounded bg-indigo-950/80 px-2 py-0.5 font-bold text-indigo-300 border border-indigo-800 text-[11px]">
                            {lots} {lots === 1 ? 'Lot' : 'Lots'}
                          </span>
                          <span className="text-[10px] text-slate-400">({qty})</span>
                        </div>
                      </td>
                      <td className="px-4 py-3.5 text-slate-300">{formatCurrency(trade.entry_price)}</td>
                      <td className="px-4 py-3.5 font-bold text-cyan-300">
                        {formatCurrency(trade.current_price)}
                      </td>
                      <td className="px-4 py-3.5 text-rose-400">
                        {trade.stop_loss ? formatCurrency(trade.stop_loss) : '-'}
                      </td>
                      <td className="px-4 py-3.5 text-emerald-400">
                        {trade.target_1 ? formatCurrency(trade.target_1) : '-'}
                      </td>
                      <td className="px-4 py-3.5 font-bold">
                        <span
                          className={`inline-flex items-center gap-1 rounded px-2 py-1 ${
                            isProfitable
                              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                              : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                          }`}
                        >
                          {isProfitable ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
                          {formatCurrency(pnl)}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 text-slate-400 text-[11px]">
                        {trade.strategy_used || 'General'}
                      </td>
                      <td className="px-4 py-3.5 text-right">
                        <button
                          type="button"
                          onClick={() => void handleCloseTrade(trade)}
                          disabled={closingTradeId === trade.id}
                          className="inline-flex h-8 items-center justify-center rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 text-xs font-bold text-rose-300 transition hover:bg-rose-500/20 disabled:opacity-50"
                        >
                          {closingTradeId === trade.id ? 'Closing...' : 'Close'}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        {/* Trade History & Log Table */}
        <section className="overflow-hidden rounded-xl border border-slate-800/80 glass-panel">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-5 py-4">
            <div>
              <h2 className="text-base font-bold text-white">Execution History & Journal</h2>
              <p className="text-xs text-slate-400">Audit trail of all recent trades and exit outcomes.</p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <div className="flex rounded-lg bg-slate-900 p-1 border border-slate-800 text-xs font-mono">
                {(['ALL', 'NIFTY', 'SENSEX'] as const).map((sym) => (
                  <button
                    key={sym}
                    type="button"
                    onClick={() => setSymbolFilter(sym)}
                    className={`rounded px-2.5 py-1 font-semibold transition ${
                      symbolFilter === sym
                        ? 'bg-cyan-950 text-cyan-300 border border-cyan-800 shadow-sm'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {sym}
                  </button>
                ))}
              </div>

              <div className="flex rounded-lg bg-slate-900 p-1 border border-slate-800 text-xs font-mono">
                {(['ALL', 'PROFIT', 'LOSS'] as const).map((filter) => (
                  <button
                    key={filter}
                    type="button"
                    onClick={() => setHistoryFilter(filter)}
                    className={`rounded px-2.5 py-1 font-semibold transition ${
                      historyFilter === filter
                        ? 'bg-slate-800 text-white shadow-sm'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {filter}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-xs font-mono">
              <thead>
                <tr className="bg-slate-950/60 uppercase tracking-wider text-slate-400">
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Lots / Qty</th>
                  <th className="px-4 py-3">Entry</th>
                  <th className="px-4 py-3">Exit</th>
                  <th className="px-4 py-3">P&L</th>
                  <th className="px-4 py-3">Strategy</th>
                  <th className="px-4 py-3">Timestamp</th>
                  <th className="px-4 py-3">Status / Reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {!loading && filteredHistory.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-10 text-center text-slate-500">
                      No trades match the current filter.
                    </td>
                  </tr>
                ) : null}

                {filteredHistory.map((trade) => {
                  const pnl = trade.profit_loss ?? 0;
                  const isProfit = pnl > 0;
                  const isLoss = pnl < 0;
                  const { lots, qty } = formatQuantityLots(trade);

                  return (
                    <tr key={trade.id} className="transition hover:bg-slate-800/30">
                      <td className="px-4 py-3.5 font-bold text-white">
                        {trade.display_symbol || trade.symbol}
                      </td>
                      <td className="px-4 py-3.5">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                            trade.signal_direction === 'BUY' || trade.trade_type === 'BUY'
                              ? 'bg-emerald-500/20 text-emerald-300'
                              : 'bg-rose-500/20 text-rose-300'
                          }`}
                        >
                          {trade.signal_direction || trade.trade_type}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 text-slate-300">
                        <div className="flex items-center gap-1.5">
                          <span className="rounded bg-slate-800 px-2 py-0.5 text-[11px] font-bold text-slate-300 border border-slate-700">
                            {lots} {lots === 1 ? 'Lot' : 'Lots'}
                          </span>
                          <span className="text-[10px] text-slate-500">({qty})</span>
                        </div>
                      </td>
                      <td className="px-4 py-3.5 text-slate-300">{formatCurrency(trade.entry_price)}</td>
                      <td className="px-4 py-3.5 text-slate-300">{formatCurrency(trade.exit_price)}</td>
                      <td
                        className={`px-4 py-3.5 font-bold ${
                          isProfit ? 'text-emerald-400' : isLoss ? 'text-rose-400' : 'text-slate-400'
                        }`}
                      >
                        {formatCurrency(trade.profit_loss)}
                      </td>
                      <td className="px-4 py-3.5 text-slate-400 text-[11px]">{trade.strategy_used || '-'}</td>
                      <td className="px-4 py-3.5 text-slate-400 text-[11px]">{formatDateTime(trade.entry_time)}</td>
                      <td className="px-4 py-3.5">
                        <span
                          className={`rounded px-2 py-0.5 text-[10px] font-bold ${
                            trade.status === 'OPEN'
                              ? 'bg-amber-500/20 text-amber-300'
                              : 'bg-slate-800 text-slate-300'
                          }`}
                        >
                          {trade.status === 'OPEN' ? 'OPEN' : trade.close_reason || 'CLOSED'}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {/* Emergency Kill Modal */}
      {showKillModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm">
          <div className="glass-panel max-w-md rounded-2xl border border-rose-500/40 p-6 shadow-2xl">
            <div className="flex items-center gap-3 text-rose-400 mb-3">
              <AlertOctagon className="h-6 w-6" />
              <h3 className="text-lg font-bold text-white">Emergency Kill Switch Confirmation</h3>
            </div>
            <p className="text-sm text-slate-300">
              Are you sure you want to instantly square off <strong>all {openTrades.length} open position(s)</strong> at current market prices?
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setShowKillModal(false)}
                className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-700"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleEmergencyCloseAll()}
                className="rounded-lg bg-rose-600 px-4 py-2 text-xs font-bold text-white hover:bg-rose-500 shadow-lg glow-crimson"
              >
                Yes, Close All Now
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
