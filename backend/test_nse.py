import requests

def get_nse_option_ltp(symbol, opt_type, strike):
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9"
    }
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=5)
        response = session.get(url, headers=headers, timeout=5)
        data = response.json()
        records = data.get("records", {}).get("data", [])
        for record in records:
            if record.get("strikePrice") == strike:
                if opt_type == "CE" and "CE" in record:
                    return record["CE"]["lastPrice"]
                elif opt_type == "PE" and "PE" in record:
                    return record["PE"]["lastPrice"]
    except Exception as e:
        print("Error:", e)
    return None

if __name__ == "__main__":
    print("NIFTY 22000 CE:", get_nse_option_ltp("NIFTY", "CE", 22000))
