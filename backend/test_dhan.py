import os
import pandas as pd
from dotenv import load_dotenv
from dhanhq import dhanhq, DhanContext

load_dotenv()

client_id = os.getenv("DHAN_CLIENT_ID")
access_token = os.getenv("DHAN_ACCESS_TOKEN")

context = DhanContext(client_id, access_token)
dhan = dhanhq(context)

# 1. Fetch Scrip Master (once)
print("Downloading scrip master...")
df = pd.read_csv("https://images.dhan.co/api-data/api-scrip-master.csv", low_memory=False)

# 2. Find NIFTY Option Security ID
nfo = df[df['SEM_EXM_EXCH_ID'] == 'NFO']
nifty_opt = nfo[(nfo['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & (nfo['SEM_TRADING_SYMBOL'].str.startswith('NIFTY'))]

nifty_opt['SEM_EXPIRY_DATE'] = pd.to_datetime(nifty_opt['SEM_EXPIRY_DATE'])
nearest_expiry = nifty_opt['SEM_EXPIRY_DATE'].min()

target = nifty_opt[(nifty_opt['SEM_EXPIRY_DATE'] == nearest_expiry) & 
                   (nifty_opt['SEM_STRIKE_PRICE'] == 22000) & 
                   (nifty_opt['SEM_OPTION_TYPE'] == 'CE')]

if not target.empty:
    sec_id = str(target.iloc[0]['SEM_SMST_SECURITY_ID'])
    print("Found Security ID:", sec_id, target.iloc[0]['SEM_TRADING_SYMBOL'])
    
    # In DhanHQ v2, we have OptionChain endpoint? Let's try getting LTP.
    # Actually market feed or quote? 
    # Let's try get_option_chain or something, or we can use get_fund_limits to just see if auth works
    print(dhan.get_fund_limits())
else:
    print("Not found")
