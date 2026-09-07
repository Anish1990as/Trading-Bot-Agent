import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd

from backend.market_data.fetcher import MarketDataFetcher


def _jwt_with_expiry(expiry: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": expiry}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


class MarketDataFetcherTests(unittest.TestCase):
    def test_expired_jwt_is_detected_without_network_call(self):
        token = _jwt_with_expiry(1)
        self.assertTrue(MarketDataFetcher._jwt_is_expired(token))
        self.assertFalse(MarketDataFetcher._jwt_is_expired("not-a-jwt"))

    def test_dhan_unauthorised_response_disables_further_calls(self):
        with tempfile.TemporaryDirectory() as data_dir, patch.dict(
            "os.environ",
            {"DHAN_CLIENT_ID": "client", "DHAN_ACCESS_TOKEN": "not-a-jwt"},
        ):
            fetcher = MarketDataFetcher(data_dir)
            response = Mock(status_code=401)
            with patch("backend.market_data.fetcher.requests.post", return_value=response):
                result = fetcher._fetch_dhan("NIFTY", "5m")

            self.assertTrue(result.empty)
            self.assertTrue(fetcher._dhan_auth_failed)
            self.assertFalse(fetcher._can_use_dhan("NIFTY", "5m"))

    def test_yahoo_success_is_used_after_dhan_failure(self):
        with tempfile.TemporaryDirectory() as data_dir, patch.dict(
            "os.environ",
            {"DHAN_CLIENT_ID": "client", "DHAN_ACCESS_TOKEN": "not-a-jwt"},
        ):
            fetcher = MarketDataFetcher(data_dir)
            candles = pd.DataFrame(
                {
                    "datetime": [datetime(2026, 6, 29, 4, 0, tzinfo=timezone.utc)],
                    "open": [25000.0],
                    "high": [25010.0],
                    "low": [24990.0],
                    "close": [25005.0],
                    "volume": [100],
                }
            )
            fetcher._fetch_dhan = Mock(return_value=pd.DataFrame())
            fetcher._fetch_yfinance_fallback = Mock(return_value=candles)

            result = fetcher.fetch_live_data("NIFTY", "NSE", "5m")

            self.assertEqual(len(result), 1)
            fetcher._fetch_yfinance_fallback.assert_called_once_with("NIFTY", "5m")


if __name__ == "__main__":
    unittest.main()
