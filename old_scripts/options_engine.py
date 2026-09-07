import time
import math
from datetime import datetime, time as dt_time, timedelta
import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq

from dhanhq import DhanContext, dhanhq
from jugaad_data.nse import NSELive
from config import (
    DHAN_ACCESS_TOKEN,
    DHAN_CLIENT_ID,
    DHAN_UNDERLYING_SECURITY_IDS,
    DHAN_UNDERLYING_SEGMENTS,
    EXPIRY_DATES,
    EXPIRY_WEEKDAYS,
)

# Global win rate tracker
TRADE_STATS = {
    'total_trades': 0,
    'winning_trades': 0,
    'losing_trades': 0,
}

class BlackScholesCalculator:
    """Black-Scholes pricing model for Greeks calculations"""
    
    @staticmethod
    def calculate_greeks(S, K, T, r, sigma, option_type='CE'):
        """
        Calculate Greeks using Black-Scholes model
        S: Spot price
        K: Strike price
        T: Time to expiry (in years)
        r: Risk-free rate
        sigma: Volatility (IV)
        """
        if T <= 0 or sigma <= 0:
            return {'delta': 0, 'gamma': 0, 'vega': 0, 'theta': 0}
        
        try:
            d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)
            
            if option_type == 'CE':
                delta = norm.cdf(d1)
                theta = (-S * norm.pdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365
            else:  # PE
                delta = norm.cdf(d1) - 1
                theta = (-S * norm.pdf(d1) * sigma / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365
            
            gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
            vega = S * norm.pdf(d1) * math.sqrt(T) / 100
            
            return {
                'delta': round(delta, 3),
                'gamma': round(gamma, 4),
                'vega': round(vega, 3),
                'theta': round(theta, 3)
            }
        except Exception:
            return {'delta': 0, 'gamma': 0, 'vega': 0, 'theta': 0}
    
    @staticmethod
    def calculate_iv(S, K, T, r, market_price, option_type='CE'):
        """Calculate Implied Volatility using Newton-Raphson method"""
        def bs_price(sigma):
            if T <= 0 or sigma <= 0:
                return 0
            d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)
            
            if option_type == 'CE':
                return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
            else:
                return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        
        try:
            sigma = brentq(lambda x: bs_price(x) - market_price, 0.01, 5.0)
            return max(0, round(sigma * 100, 2))
        except Exception:
            return None

class OptionsEngine:
    def __init__(self):
        self.nse = NSELive()
        self.dhan = None
        if DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN:
            try:
                self.dhan = dhanhq(DhanContext(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN))
            except Exception as e:
                print(f"Error initializing Dhan client: {e}")
        # Simple cache to avoid getting blocked by NSE (Cache for 60 seconds)
        self.cache = {}
        self.bs = BlackScholesCalculator()

    def _get_expiry_date(self, instrument_name):
        exact_date = EXPIRY_DATES.get(instrument_name)
        if exact_date:
            return exact_date

        now = datetime.now()
        expiry_weekday = EXPIRY_WEEKDAYS.get(instrument_name, 3)
        days_until_expiry = (expiry_weekday - now.weekday()) % 7
        if days_until_expiry == 0 and now.time() > dt_time(15, 30):
            days_until_expiry = 7

        return (now + timedelta(days=days_until_expiry)).strftime("%Y-%m-%d")

    def _get_chain_data(self, instrument_name):
        """Fetches and caches the option chain data for the instrument."""
        # Mapping index names to NSE symbols
        symbol_map = {
            "Nifty 50": "NIFTY",
            "BankNifty": "BANKNIFTY",
            "FinNifty": "FINNIFTY"
        }
        
        symbol = symbol_map.get(instrument_name)
        if not symbol:
            return None # Sensex or others not supported easily by NSELive index_option_chain
            
        current_time = time.time()
        
        # Return from cache if it's less than 60 seconds old
        if symbol in self.cache and (current_time - self.cache[symbol]['timestamp'] < 60):
            return self.cache[symbol]['data']
            
        try:
            data = self.nse.index_option_chain(symbol)
            self.cache[symbol] = {'timestamp': current_time, 'data': data}
            return data
        except Exception as e:
            print(f"Error fetching option chain for {symbol}: {e}")
            return None

    def _get_dhan_option_chain(self, instrument_name):
        if not self.dhan:
            return None

        under_security_id = DHAN_UNDERLYING_SECURITY_IDS.get(instrument_name)
        if not under_security_id:
            return None

        segment = DHAN_UNDERLYING_SEGMENTS.get(instrument_name, "BSE_FNO")
        expiry = self._get_expiry_date(instrument_name)
        cache_key = f"DHAN_CHAIN_{instrument_name}_{expiry}"
        current_time = time.time()

        if cache_key in self.cache and (current_time - self.cache[cache_key]['timestamp'] < 60):
            return self.cache[cache_key]['data']

        try:
            data = self.dhan.option_chain(int(under_security_id), segment, expiry)
            self.cache[cache_key] = {'timestamp': current_time, 'data': data}
            return data
        except Exception as e:
            print(f"Error fetching Dhan option chain for {instrument_name}: {e}")
            return None

    def _extract_dhan_chain_ltp(self, data, strike, opt_type):
        if not data:
            return None

        payload = data.get("data", data) if isinstance(data, dict) else data
        chain = payload.get("oc", payload.get("option_chain", payload)) if isinstance(payload, dict) else payload

        candidates = []
        if isinstance(chain, dict):
            candidates = chain.items()
        elif isinstance(chain, list):
            candidates = [(item.get("strikePrice") or item.get("strike_price"), item) for item in chain if isinstance(item, dict)]

        side_key = "ce" if opt_type == "CE" else "pe"
        for strike_key, item in candidates:
            try:
                if int(float(strike_key)) != int(strike):
                    continue
            except (TypeError, ValueError):
                continue

            side = None
            if isinstance(item, dict):
                side = item.get(side_key) or item.get(side_key.upper()) or item.get(opt_type)
            if not isinstance(side, dict):
                continue

            for key in ("last_price", "lastPrice", "ltp", "LTP"):
                value = side.get(key)
                if value not in (None, ""):
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return None

        return None

    def get_option_details(self, instrument_name, strike, opt_type, spot_price):
        """Extract comprehensive option details including OI, Bid-Ask Spread"""
        data = self._get_chain_data(instrument_name)
        if not data:
            return None
        
        try:
            for item in data['filtered']['data']:
                if item['strikePrice'] == strike:
                    side = item.get(opt_type.lower(), {})
                    if side:
                        return {
                            'ltp': side.get('lastPrice', 0),
                            'bid': side.get('bidPrice', side.get('lastPrice', 0)),
                            'ask': side.get('askPrice', side.get('lastPrice', 0)),
                            'oi': side.get('openInterest', 0),
                            'volume': side.get('totalTradedVolume', 0),
                            'iv': side.get('impliedVolatility', None),
                        }
        except Exception:
            pass
        
        return None

    def fetch_option_chain_context(self, instrument_name, spot_price):
        """
        Calculates PCR, Max Pain, Support and Resistance using real NSE Option Chain data.
        """
        data = self._get_chain_data(instrument_name)
        
        # Fallback to safe defaults if fetching fails or instrument is Sensex
        if not data:
            return {
                'pcr': 1.0,
                'max_pain': int(round(spot_price)),
                'resistance': "Data Unavailable",
                'support': "Data Unavailable",
                'sentiment': "Neutral"
            }
            
        try:
            filtered = data['filtered']
            ce_tot_oi = filtered['CE']['totOI']
            pe_tot_oi = filtered['PE']['totOI']
            
            pcr = round(pe_tot_oi / ce_tot_oi, 2) if ce_tot_oi > 0 else 1.0
            
            sentiment = "Neutral"
            if pcr > 1.2:
                sentiment = "Highly Bullish (Put Writers Dominating)"
            elif pcr < 0.8:
                sentiment = "Highly Bearish (Call Writers Dominating)"
                
            # Find Support (Highest Put OI) and Resistance (Highest Call OI) near ATM
            max_pe_oi = 0
            support_strike = 0
            max_ce_oi = 0
            res_strike = 0
            
            for item in filtered['data']:
                strike = item['strikePrice']
                # Only consider strikes within +/- 5% of spot to avoid deep OTM outliers
                if abs(strike - spot_price) / spot_price > 0.05:
                    continue
                    
                ce_oi = item.get('CE', {}).get('openInterest', 0)
                pe_oi = item.get('PE', {}).get('openInterest', 0)
                
                if ce_oi > max_ce_oi:
                    max_ce_oi = ce_oi
                    res_strike = strike
                    
                if pe_oi > max_pe_oi:
                    max_pe_oi = pe_oi
                    support_strike = strike
                    
            # For simplicity, approximate max pain to the strike with highest combined OI near ATM
            max_combined_oi = 0
            max_pain = 0
            for item in filtered['data']:
                strike = item['strikePrice']
                if abs(strike - spot_price) / spot_price > 0.05:
                    continue
                combined = item.get('CE', {}).get('openInterest', 0) + item.get('PE', {}).get('openInterest', 0)
                if combined > max_combined_oi:
                    max_combined_oi = combined
                    max_pain = strike

            return {
                'pcr': pcr,
                'max_pain': max_pain,
                'resistance': res_strike,
                'support': support_strike,
                'sentiment': sentiment
            }
        except Exception as e:
            return {
                'pcr': 1.0, 'max_pain': 0, 'resistance': 0, 'support': 0, 'sentiment': "Neutral"
            }

    def get_live_premium(self, instrument_name, strike, opt_type):
        """
        Fetches the exact live trading price of the option premium.
        """
        if instrument_name == "Sensex":
            data = self._get_dhan_option_chain(instrument_name)
            ltp = self._extract_dhan_chain_ltp(data, strike, opt_type)
            if ltp is None:
                print(
                    "Sensex option LTP unavailable. Set SENSEX_UNDERLYING_SECURITY_ID "
                    "from Dhan instrument master; using fallback value."
                )
                return round(strike * 0.005, 1)  # Fallback instead of None
            return ltp

        data = self._get_chain_data(instrument_name)
        
        # If fetching fails, fallback to standard BS approximation
        if not data:
            return round(strike * 0.005, 1)
            
        try:
            for item in data['filtered']['data']:
                if item['strikePrice'] == strike:
                    if opt_type == 'CE' and 'CE' in item:
                        price = item['CE'].get('lastPrice', 0)
                        if price and price > 0:
                            return price
                    elif opt_type == 'PE' and 'PE' in item:
                        price = item['PE'].get('lastPrice', 0)
                        if price and price > 0:
                            return price
        except Exception as e:
            pass
            
        # Fallback if strike not found
        return round(strike * 0.005, 1)

    def calculate_premium_trade_advanced(self, instrument_name, strike, opt_type, spot_sl_points, spot_t1_points, risk_amount, lot_size, spot_price):
        """
        Advanced Premium Trade calculation with Greeks, IV, OI, Bid-Ask Spread, Time Decay, Win Rate
        """
        opt_details = self.get_option_details(instrument_name, strike, opt_type, spot_price)
        if not opt_details:
            live_premium = self.get_live_premium(instrument_name, strike, opt_type)
            if live_premium is None:
                live_premium = max(0.5, round(strike * 0.005, 1))
            opt_details = {
                'ltp': max(0.5, live_premium),
                'bid': 0,
                'ask': 0,
                'oi': 0,
                'volume': 0,
                'iv': None,
            }
        
        ltp = opt_details.get('ltp', 0.5)
        if ltp is None or ltp <= 0:
            ltp = 0.5
        
        # Get ATM Delta approximation
        delta = 0.52
        
        # Translate Spot Points to Premium Points
        premium_sl_points = spot_sl_points * delta
        premium_t1_points = spot_t1_points * delta
        premium_t2_points = (spot_t1_points * 2) * delta
        
        # Calculate Prices
        stop_loss = max(0.1, ltp - premium_sl_points)
        target_1 = ltp + premium_t1_points
        target_2 = ltp + premium_t2_points
        
        # Position Sizing
        risk_per_lot = (ltp - stop_loss) * lot_size
        lots = max(1, int(risk_amount // max(1, risk_per_lot)))
        actual_risk = lots * risk_per_lot
        
        # Calculate Greeks
        expiry_date = self._get_expiry_date(instrument_name)
        try:
            exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
            T = (exp_dt - datetime.now()).days / 365.0
        except:
            T = 7 / 365.0  # Default to 7 days
        
        T = max(0.001, T)  # Minimum time value
        
        # Use market IV or estimate
        market_iv = opt_details.get('iv') or self._estimate_iv(strike, spot_price, opt_type)
        
        greeks = self.bs.calculate_greeks(spot_price, strike, T, 0.06, market_iv, opt_type)
        
        # Bid-Ask Spread
        bid = opt_details.get('bid', ltp * 0.99)
        ask = opt_details.get('ask', ltp * 1.01)
        spread = (ask - bid) if ask > bid else 0.05
        spread_pct = (spread / ltp * 100) if ltp > 0 else 0
        
        # Time Decay (Theta decay for 1 day)
        time_decay = abs(greeks.get('theta', 0)) * ltp / 100 if ltp > 0 else 0
        
        # Win Rate
        win_rate = (TRADE_STATS['winning_trades'] / max(1, TRADE_STATS['total_trades']) * 100) if TRADE_STATS['total_trades'] > 0 else 50
        
        return {
            'ltp': round(ltp, 2),
            'bid': round(bid, 2),
            'ask': round(ask, 2),
            'spread': round(spread, 2),
            'spread_pct': round(spread_pct, 2),
            'sl': round(stop_loss, 2),
            't1': round(target_1, 2),
            't2': round(target_2, 2),
            'lots': lots,
            'risk': round(actual_risk, 2),
            'oi': opt_details.get('oi', 0),
            'volume': opt_details.get('volume', 0),
            'iv': round(market_iv, 2),
            'delta': greeks.get('delta', 0),
            'gamma': greeks.get('gamma', 0),
            'vega': greeks.get('vega', 0),
            'theta': greeks.get('theta', 0),
            'time_decay': round(time_decay, 2),
            'win_rate': round(win_rate, 1),
            'dte': round(T * 365, 0),  # Days to expiry
        }

    def calculate_premium_trade(self, instrument_name, strike, opt_type, spot_sl_points, spot_t1_points, risk_amount, lot_size):
        """Legacy method - calls advanced version"""
        try:
            # Get spot price
            data = self._get_chain_data(instrument_name)
            spot_price = 23000  # Default fallback
            if data and 'records' in data:
                spot_price = data['records'][0].get('PE', {}).get('underlyingValue', 23000)
        except:
            spot_price = 23000
        
        result = self.calculate_premium_trade_advanced(
            instrument_name, strike, opt_type, spot_sl_points, spot_t1_points, 
            risk_amount, lot_size, spot_price
        )
        
        # Return legacy format
        return {
            'ltp': result['ltp'],
            'sl': result['sl'],
            't1': result['t1'],
            't2': result['t2'],
            'lots': result['lots'],
            'risk': result['risk'],
        }

    def _estimate_iv(self, strike, spot_price, opt_type):
        """Estimate IV based on moneyness"""
        moneyness = strike / spot_price
        
        if opt_type == 'CE':
            if moneyness < 0.95:  # ITM Call
                return 18
            elif moneyness > 1.05:  # OTM Call
                return 22
        else:  # PE
            if moneyness > 1.05:  # ITM Put
                return 18
            elif moneyness < 0.95:  # OTM Put
                return 22
        
        return 20  # ATM

    def log_trade_result(self, won):
        """Log trade result for win rate calculation"""
        TRADE_STATS['total_trades'] += 1
        if won:
            TRADE_STATS['winning_trades'] += 1
        else:
            TRADE_STATS['losing_trades'] += 1

# Global instance
options_ai = OptionsEngine()
