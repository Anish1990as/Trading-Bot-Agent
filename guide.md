python app_daemon.py    backend

py -3.13 app_daemon.py

frontend   npm run dev

dhan scanner   powershell -ExecutionPolicy Bypass -File .\start-dev.ps1

Live Dhan execution
-------------------
The default mode is paper trading. Copy .env.example to .env and keep these
values until the integration has been tested:

	DHAN_CLIENT_ID=DUMMY
	DHAN_ACCESS_TOKEN=DUMMY
	LIVE_TRADING_ENABLED=false
	PAPER_TRADING=true

The daemon will not send a broker order with dummy credentials or while live
trading is disabled. To deliberately enable live execution, set the real Dhan
Client ID and access token, set LIVE_TRADING_ENABLED=true, and set the
PAPER_TRADING=false. The daemon resolves the exact
NFO option security ID from Dhan's scrip master before placing an order.
In live mode, entries use a Dhan Super Order with broker-side target and stop
loss. The daemon also compares every local open trade with Dhan positions before
allowing a new entry; a mismatch halts new entries. Order placement requires a
whitelisted static IP, an active F&O segment, and a valid token.

NIFTY and SENSEX can be controlled independently with
TRADE_NIFTY_ENABLED=true and TRADE_SENSEX_ENABLED=true. Set either value to
false and restart the daemon to skip that symbol while leaving the other on.

Before using real money, verify one minimum-size order in Dhan, its order
status, the stop-loss/target exit, reversal exit, and the 15:10 IST square-off.
Keep the process running during market hours and retain a manual Dhan kill
switch. This software does not guarantee profit and trading derivatives can
lose the full amount at risk.
