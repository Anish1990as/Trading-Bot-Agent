import base64
import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import pytz
import requests
from dotenv import load_dotenv


class MarketDataFetcher:
    DHAN_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
    DHAN_INDEXES = {
        "NIFTY": "13",
        "BANKNIFTY": "25",
        "SENSEX": "51",
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
        self._cache = {}
        self._cache_time = {}
        self._last_request_time = 0.0
        self._sync_credentials()

    def _sync_credentials(self):
        load_dotenv(override=True)
        self.dhan_client_id = os.getenv("DHAN_CLIENT_ID")
        self.dhan_access_token = os.getenv("DHAN_ACCESS_TOKEN")
        self._dhan_auth_failed = self._jwt_is_expired(self.dhan_access_token)

    def fetch_live_data(self, symbol: str, exchange: str, timeframe: str) -> pd.DataFrame:
        """Fetch recent candles from Dhan only with caching and fallback."""
        symbol = symbol.upper()
        now_ts = time.time()
        cache_key = f"{symbol}_{timeframe}"
        if cache_key in self._cache and (now_ts - self._cache_time.get(cache_key, 0)) < 3.0:
            return self._cache[cache_key].copy()

        self._sync_credentials()
        df = pd.DataFrame()

        if self._can_use_dhan(symbol, timeframe):
            df = self._fetch_dhan(symbol, timeframe)

        if df.empty:
            filepath = os.path.join(self.data_dir, f"{symbol}_{timeframe}.parquet")
            if os.path.exists(filepath):
                try:
                    cached_df = pd.read_parquet(filepath)
                    if not cached_df.empty:
                        return cached_df
                except Exception:
                    pass
            print(f"No Dhan market data available for {symbol} {timeframe}.")
            return pd.DataFrame()

        try:
            df = self._normalise_candles(df)
            if df.empty:
                return df

            # Strategies only need recent candles. Keeping this bounded also
            # reduces parquet writes and indicator calculation time.
            df = df.tail(500).reset_index(drop=True)
            self._cache[cache_key] = df
            self._cache_time[cache_key] = now_ts
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
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.2:
            time.sleep(0.2 - elapsed)

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
            self._last_request_time = time.time()
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
                    "market data is disabled."
                )
                return pd.DataFrame()
            if response.status_code == 429:
                time.sleep(1.0)
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
            if "429" not in str(exc):
                print(f"Dhan data unavailable for {symbol}: {exc}")
            return pd.DataFrame()
        except (TypeError, ValueError) as exc:
            print(f"Invalid Dhan response for {symbol}: {exc}")
            return pd.DataFrame()

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
