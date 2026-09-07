import base64
import json
import multiprocessing
import os
from datetime import datetime, timedelta
from queue import Empty

import pandas as pd
import pytz
import requests
from dotenv import load_dotenv


def _download_yfinance(
    yf_symbol: str,
    period: str,
    interval: str,
    output: multiprocessing.Queue,
) -> None:
    """Run yfinance outside the daemon so a stuck request can be terminated."""
    try:
        import yfinance as yf

        df = yf.download(
            tickers=yf_symbol,
            period=period,
            interval=interval,
            progress=False,
            timeout=30,
        )
        output.put(("ok", df))
    except Exception as exc:
        output.put(("error", str(exc)))


class MarketDataFetcher:
    DHAN_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
    DHAN_INDEXES = {
        "NIFTY": "13",
        "BANKNIFTY": "25",
    }
    DHAN_INTERVALS = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "1h": "60",
    }

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

        # This class is also created by the API process, where app_daemon's
        # load_dotenv() call is not executed.
        load_dotenv()
        self.dhan_client_id = os.getenv("DHAN_CLIENT_ID")
        self.dhan_access_token = os.getenv("DHAN_ACCESS_TOKEN")
        self._dhan_auth_failed = self._jwt_is_expired(self.dhan_access_token)
        if self._dhan_auth_failed:
            print(
                "Dhan access token has expired; "
                "using fallback market data for this process."
            )

    def fetch_live_data(self, symbol: str, exchange: str, timeframe: str) -> pd.DataFrame:
        """Fetch recent candles, preferring Dhan and using bounded fallbacks."""
        symbol = symbol.upper()
        df = pd.DataFrame()

        if self._can_use_dhan(symbol, timeframe):
            df = self._fetch_dhan(symbol, timeframe)

        # Yahoo is a bounded emergency fallback. The unofficial TradingView
        # websocket was removed because it caused repeated blocking timeouts.
        if df.empty:
            df = self._fetch_yfinance_fallback(symbol, timeframe)
        if df.empty:
            print(f"No market data available for {symbol} {timeframe}.")
            return pd.DataFrame()

        try:
            df = self._normalise_candles(df)
            if df.empty:
                return df

            # Strategies only need recent candles. Keeping this bounded also
            # reduces parquet writes and indicator calculation time.
            df = df.tail(500).reset_index(drop=True)
            filepath = os.path.join(self.data_dir, f"{symbol}_{timeframe}.parquet")
            df.to_parquet(filepath, index=False)
            return df
        except Exception as exc:
            print(f"Error processing market data for {symbol}: {exc}")
            return pd.DataFrame()

    def _can_use_dhan(self, symbol: str, timeframe: str) -> bool:
        return bool(
            not self._dhan_auth_failed
            and self.dhan_client_id
            and self.dhan_access_token
            and symbol in self.DHAN_INDEXES
            and timeframe in self.DHAN_INTERVALS
        )

    @staticmethod
    def _jwt_is_expired(token: str | None) -> bool:
        """Read JWT expiry locally; the API remains responsible for validation."""
        if not token:
            return False
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return float(claims["exp"]) <= datetime.now().timestamp()
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def _fetch_dhan(self, symbol: str, timeframe: str) -> pd.DataFrame:
        now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
        payload = {
            "securityId": self.DHAN_INDEXES[symbol],
            "exchangeSegment": "IDX_I",
            "instrument": "INDEX",
            "interval": self.DHAN_INTERVALS[timeframe],
            "oi": False,
            "fromDate": (now_ist - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
        }
        headers = {
            "access-token": self.dhan_access_token,
            "client-id": self.dhan_client_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                self.DHAN_INTRADAY_URL,
                headers=headers,
                json=payload,
                timeout=(3.05, 10),
            )
            if response.status_code in (401, 403):
                self._dhan_auth_failed = True
                print(
                    "Dhan access token is invalid or expired; "
                    "using fallback market data for this process."
                )
                return pd.DataFrame()
            response.raise_for_status()
            data = response.json()
            if data.get("errorCode"):
                print(
                    f"Dhan data rejected for {symbol}: "
                    f"{data.get('errorCode')} {data.get('errorMessage', '')}".strip()
                )
                return pd.DataFrame()

            required = ("timestamp", "open", "high", "low", "close", "volume")
            if not all(data.get(key) for key in required):
                return pd.DataFrame()
            return pd.DataFrame({key: data[key] for key in required})
        except requests.RequestException as exc:
            print(f"Dhan data unavailable for {symbol}: {exc}")
            return pd.DataFrame()
        except (TypeError, ValueError) as exc:
            print(f"Invalid Dhan response for {symbol}: {exc}")
            return pd.DataFrame()

    def _fetch_yfinance_fallback(self, symbol: str, timeframe: str) -> pd.DataFrame:
        yf_symbol = {
            "NIFTY": "^NSEI",
            "BANKNIFTY": "^NSEBANK",
        }.get(symbol, symbol if symbol.endswith(".NS") else f"{symbol}.NS")
        yf_interval = {
            "1m": "1m",
            "3m": "5m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "1d": "1d",
        }.get(timeframe, "5m")

        period = "7d" if yf_interval == "1m" else "1mo"
        context = multiprocessing.get_context("spawn")
        output = context.Queue(maxsize=1)
        process = context.Process(
            target=_download_yfinance,
            args=(yf_symbol, period, yf_interval, output),
            daemon=True,
        )

        try:
            process.start()
            status, result = output.get(timeout=45)
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if status == "error":
                print(f"Yahoo Finance fallback failed for {symbol}: {result}")
                return pd.DataFrame()

            df = result
            if df is None or df.empty:
                return pd.DataFrame()

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [column[0].lower() for column in df.columns]
            else:
                df.columns = [column.lower() for column in df.columns]

            df = df.reset_index()
            datetime_column = "Datetime" if "Datetime" in df.columns else "Date"
            if datetime_column in df.columns:
                df = df.rename(columns={datetime_column: "datetime"})
            return df
        except Empty:
            print(f"Yahoo Finance fallback timed out for {symbol} after 45 seconds.")
            return pd.DataFrame()
        except Exception as exc:
            print(f"Yahoo Finance fallback failed for {symbol}: {exc}")
            return pd.DataFrame()
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            output.close()

    @staticmethod
    def _normalise_candles(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "datetime" not in df.columns:
            if "timestamp" in df.columns:
                df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
            else:
                df = df.reset_index()
                if "datetime" not in df.columns:
                    first_column = df.columns[0]
                    df = df.rename(columns={first_column: "datetime"})

        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime"])
        ist = pytz.timezone("Asia/Kolkata")
        if df["datetime"].dt.tz is None:
            df["datetime"] = df["datetime"].dt.tz_localize(ist)
        else:
            df["datetime"] = df["datetime"].dt.tz_convert(ist)

        df["timestamp"] = df["datetime"].astype("int64") // 10**9
        required = ["open", "high", "low", "close", "volume"]
        if not all(column in df.columns for column in required):
            return pd.DataFrame()
        return df.sort_values(by="datetime").drop_duplicates("datetime", keep="last")
