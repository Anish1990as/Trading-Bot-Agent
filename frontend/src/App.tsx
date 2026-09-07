import { useEffect, useState } from 'react';

type Stats = {
  total_trades: number;
  closed_trades: number;
  win_rate: number;
  net_pnl: number;
  open_trades: number;
};

type Trade = {
  id: number;
  symbol: string;
  display_symbol?: string;
  trade_type: 'BUY' | 'SELL';
  signal_direction?: 'BUY' | 'SELL';
  option_type?: string | null;
  strike?: number | null;
  entry_price: number;
  exit_price: number | null;
  profit_loss: number | null;
  current_price?: number | null;
  live_profit_loss?: number | null;
  status: 'OPEN' | 'CLOSED';
  strategy_used: string;
  entry_time: string;
  exit_time: string | null;
};

const API_BASE = 'http://localhost:8000';

const initialStats: Stats = {
  total_trades: 0,
  closed_trades: 0,
  win_rate: 0,
  net_pnl: 0,
  open_trades: 0,
};

function formatCurrency(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return '-';
  }
  return `Rs ${Number(value).toFixed(2)}`;
}

function getTradeLabel(trade: Trade) {
  return trade.display_symbol || trade.symbol;
}

function getTradeDirection(trade: Trade) {
  return trade.signal_direction || trade.trade_type;
}

function formatDateTime(value: string | null) {
  if (!value) {
    return '-';
  }

  const normalizedValue =
    /z|[+-]\d{2}:\d{2}$/i.test(value) ? value : `${value}Z`;

  const date = new Date(normalizedValue);
  if (Number.isNaN(date.getTime())) {
    return '-';
  }

  return date.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata',
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getApiErrorMessage(err: unknown) {
  if (err instanceof TypeError) {
    return 'Backend API is not running on localhost:8000.';
  }

  if (err instanceof Error) {
    return err.message;
  }

  return 'Unexpected API error.';
}

function App() {
  const [stats, setStats] = useState<Stats>(initialStats);
  const [openTrades, setOpenTrades] = useState<Trade[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [closingTradeId, setClosingTradeId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  async function loadDashboard(showRefreshState = false) {
    if (showRefreshState) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      setError('');

      const [statsResponse, openTradesResponse, recentTradesResponse] = await Promise.all([
        fetch(`${API_BASE}/api/stats`),
        fetch(`${API_BASE}/api/trades/open`),
        fetch(`${API_BASE}/api/trades/recent`),
      ]);

      if (!statsResponse.ok || !openTradesResponse.ok || !recentTradesResponse.ok) {
        throw new Error('Dashboard data could not be loaded.');
      }

      const statsData: Stats = await statsResponse.json();
      const openTradesData: Trade[] = await openTradesResponse.json();
      const tradesData: Trade[] = await recentTradesResponse.json();

      setStats(statsData);
      setOpenTrades(openTradesData);
      setTrades(tradesData);
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    const initialLoadId = window.setTimeout(() => {
      void loadDashboard();
    }, 0);

    const intervalId = window.setInterval(() => {
      void loadDashboard(true);
    }, 5000);

    return () => {
      window.clearTimeout(initialLoadId);
      window.clearInterval(intervalId);
    };
  }, []);

  async function handleCloseTrade(trade: Trade) {
    setClosingTradeId(trade.id);
    setNotice('');
    setError('');

    try {
      const response = await fetch(`${API_BASE}/api/trades/${trade.id}/close`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          reason: 'Manual Close from Dashboard',
        }),
      });

      const payload = await response.json().catch(() => null);

      if (!response.ok) {
        throw new Error(payload?.detail || 'Trade close failed.');
      }

      setNotice(`Trade ${trade.symbol} closed at ${formatCurrency(payload?.exit_price)}.`);
      await loadDashboard(true);
    } catch (err) {
      setError(getApiErrorMessage(err));
    } finally {
      setClosingTradeId(null);
    }
  }

  return (
    <div className="min-h-screen bg-slate-900 text-slate-50 p-4 md:p-8">
      <div className="mx-auto max-w-7xl">
        <header className="mb-8 flex flex-col gap-4 border-b border-slate-800 pb-6 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white md:text-4xl">
              Local Algorithmic Trading Platform
            </h1>
            <p className="mt-2 text-sm text-slate-400 md:text-base">
              Manual control for active paper trades from the dashboard.
            </p>
          </div>

          <button
            type="button"
            onClick={() => void loadDashboard(true)}
            disabled={refreshing || loading}
            className="inline-flex h-11 items-center justify-center rounded-lg border border-slate-700 bg-slate-800 px-4 text-sm font-semibold text-slate-200 transition hover:border-slate-600 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {refreshing ? 'Refreshing...' : 'Refresh Data'}
          </button>
        </header>

        {error ? (
          <div className="mb-6 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        ) : null}

        {notice ? (
          <div className="mb-6 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
            {notice}
          </div>
        ) : null}

        <section className="mb-8 grid grid-cols-1 gap-4 md:grid-cols-4">
          <div className="rounded-xl border border-slate-800 bg-slate-800 p-5 shadow-lg">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Net PnL</h2>
            <p className={`mt-2 text-3xl font-bold ${stats.net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {formatCurrency(stats.net_pnl)}
            </p>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-800 p-5 shadow-lg">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Win Rate</h2>
            <p className="mt-2 text-3xl font-bold text-sky-400">{stats.win_rate.toFixed(2)}%</p>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-800 p-5 shadow-lg">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Open Trades</h2>
            <p className="mt-2 text-3xl font-bold text-amber-400">{stats.open_trades}</p>
          </div>
          <div className="rounded-xl border border-slate-800 bg-slate-800 p-5 shadow-lg">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">Total Trades</h2>
            <p className="mt-2 text-3xl font-bold text-fuchsia-400">{stats.total_trades}</p>
          </div>
        </section>

        <section className="mb-8 overflow-hidden rounded-xl border border-slate-800 bg-slate-800 shadow-lg">
          <div className="border-b border-slate-700 px-5 py-4">
            <h2 className="text-lg font-bold text-white">Running Trades</h2>
            <p className="mt-1 text-sm text-slate-400">
              Active positions are shown here until they are closed.
            </p>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left">
              <thead>
                <tr className="bg-slate-900/60 text-xs uppercase tracking-wide text-slate-400">
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Entry</th>
                  <th className="px-4 py-3">Current</th>
                  <th className="px-4 py-3">Live PnL</th>
                  <th className="px-4 py-3">Strategy</th>
                  <th className="px-4 py-3">Opened</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/60">
                {!loading && openTrades.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-10 text-center text-slate-500">
                      No running trades right now.
                    </td>
                  </tr>
                ) : null}

                {openTrades.map((trade) => (
                  <tr key={`open-${trade.id}`} className="transition hover:bg-slate-700/20">
                    <td className="px-4 py-4 font-medium text-white">{getTradeLabel(trade)}</td>
                    <td className="px-4 py-4">
                      <div className="flex items-center gap-2">
                        <span
                          className={`inline-flex min-w-16 justify-center rounded-md px-2 py-1 text-xs font-bold ${
                            getTradeDirection(trade) === 'BUY'
                              ? 'bg-emerald-500/20 text-emerald-300'
                              : 'bg-red-500/20 text-red-300'
                          }`}
                        >
                          {getTradeDirection(trade)}
                        </span>
                        {trade.option_type ? (
                          <span className="inline-flex min-w-12 justify-center rounded-md bg-slate-700 px-2 py-1 text-xs font-bold text-slate-200">
                            {trade.option_type}
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td className="px-4 py-4 text-slate-300">{formatCurrency(trade.entry_price)}</td>
                    <td className="px-4 py-4 text-slate-300">{formatCurrency(trade.current_price)}</td>
                    <td
                      className={`px-4 py-4 font-semibold ${
                        (trade.live_profit_loss ?? 0) > 0
                          ? 'text-emerald-400'
                          : (trade.live_profit_loss ?? 0) < 0
                            ? 'text-red-400'
                            : 'text-slate-400'
                      }`}
                    >
                      {formatCurrency(trade.live_profit_loss)}
                    </td>
                    <td className="px-4 py-4 text-sm text-slate-400">{trade.strategy_used || '-'}</td>
                    <td className="px-4 py-4 text-sm text-slate-400">{formatDateTime(trade.entry_time)}</td>
                    <td className="px-4 py-4">
                      <span className="inline-flex min-w-16 justify-center rounded-md bg-amber-500/20 px-2 py-1 text-xs font-bold text-amber-300">
                        {trade.status}
                      </span>
                    </td>
                    <td className="px-4 py-4 text-right">
                      <button
                        type="button"
                        onClick={() => void handleCloseTrade(trade)}
                        disabled={closingTradeId === trade.id}
                        className="inline-flex h-9 items-center justify-center rounded-lg border border-red-500/30 bg-red-500/10 px-3 text-sm font-semibold text-red-200 transition hover:border-red-400/40 hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {closingTradeId === trade.id ? 'Closing...' : 'Close Trade'}
                      </button>
                    </td>
                  </tr>
                ))}

                {loading ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-10 text-center text-slate-500">
                      Loading running trades...
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>

        <section className="overflow-hidden rounded-xl border border-slate-800 bg-slate-800 shadow-lg">
          <div className="flex items-center justify-between border-b border-slate-700 px-5 py-4">
            <div>
              <h2 className="text-lg font-bold text-white">Recent Trades</h2>
              <p className="mt-1 text-sm text-slate-400">Open trades can be closed directly from this table.</p>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[960px] text-left">
              <thead>
                <tr className="bg-slate-900/60 text-xs uppercase tracking-wide text-slate-400">
                  <th className="px-4 py-3">Symbol</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Entry</th>
                  <th className="px-4 py-3">Exit</th>
                  <th className="px-4 py-3">PnL</th>
                  <th className="px-4 py-3">Opened</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/60">
                {!loading && trades.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-slate-500">
                      No trades recorded yet.
                    </td>
                  </tr>
                ) : null}

                {trades.map((trade) => (
                  <tr key={trade.id} className="transition hover:bg-slate-700/20">
                    <td className="px-4 py-4 font-medium text-white">{getTradeLabel(trade)}</td>
                    <td className="px-4 py-4">
                      <div className="flex items-center gap-2">
                        <span
                          className={`inline-flex min-w-16 justify-center rounded-md px-2 py-1 text-xs font-bold ${
                            getTradeDirection(trade) === 'BUY'
                              ? 'bg-emerald-500/20 text-emerald-300'
                              : 'bg-red-500/20 text-red-300'
                          }`}
                        >
                          {getTradeDirection(trade)}
                        </span>
                        {trade.option_type ? (
                          <span className="inline-flex min-w-12 justify-center rounded-md bg-slate-700 px-2 py-1 text-xs font-bold text-slate-200">
                            {trade.option_type}
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td className="px-4 py-4 text-slate-300">{formatCurrency(trade.entry_price)}</td>
                    <td className="px-4 py-4 text-slate-300">{formatCurrency(trade.exit_price)}</td>
                    <td
                      className={`px-4 py-4 font-semibold ${
                        (trade.profit_loss ?? 0) > 0
                          ? 'text-emerald-400'
                          : (trade.profit_loss ?? 0) < 0
                            ? 'text-red-400'
                            : 'text-slate-400'
                      }`}
                    >
                      {formatCurrency(trade.profit_loss)}
                    </td>
                    <td className="px-4 py-4 text-sm text-slate-400">{formatDateTime(trade.entry_time)}</td>
                    <td className="px-4 py-4">
                      <span
                        className={`inline-flex min-w-16 justify-center rounded-md px-2 py-1 text-xs font-bold ${
                          trade.status === 'OPEN'
                            ? 'bg-amber-500/20 text-amber-300'
                            : 'bg-slate-600/30 text-slate-300'
                        }`}
                      >
                        {trade.status}
                      </span>
                    </td>
                    <td className="px-4 py-4 text-right">
                      {trade.status === 'OPEN' ? (
                        <button
                          type="button"
                          onClick={() => void handleCloseTrade(trade)}
                          disabled={closingTradeId === trade.id}
                          className="inline-flex h-9 items-center justify-center rounded-lg border border-red-500/30 bg-red-500/10 px-3 text-sm font-semibold text-red-200 transition hover:border-red-400/40 hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {closingTradeId === trade.id ? 'Closing...' : 'Close Trade'}
                        </button>
                      ) : (
                        <span className="text-sm text-slate-500">{formatDateTime(trade.exit_time)}</span>
                      )}
                    </td>
                  </tr>
                ))}

                {loading ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-slate-500">
                      Loading dashboard...
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

export default App;
