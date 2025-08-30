import json
import yfinance as yf
import pandas as pd
from http.server import BaseHTTPRequestHandler
import os
from dotenv import load_dotenv

load_dotenv()  # loads .env file into environment variables

API_KEY = os.getenv("API_KEY")

# ----- HTTP Handler -----
class handler(BaseHTTPRequestHandler):
    def _check_api_key(self):
        auth_header = self.headers.get("x-api-key")

        if auth_header != API_KEY:
            self.send_response(403)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error": "Forbidden - Invalid API Key"}')
            return False
        return True

    def do_POST(self):
        if not self._check_api_key():
            return
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        response = main_app({"body": post_data.decode('utf-8')}, None)
        self.send_response(response["statusCode"])
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(response["body"].encode('utf-8'))
    def do_GET(self):
        if not self._check_api_key():
            return
        self.send_response(200)
        self.send_header('Content-type','text/plain')
        self.end_headers()
        self.wfile.write('Hello, world!'.encode('utf-8'))
        return
        
# ----- FUNCTION: คำนวณ Indicators -----
def calculate_indicators(data, ticker):
    close_col = f"Close_{ticker}" if f"Close_{ticker}" in data.columns else "Close"
    high_col  = f"High_{ticker}"  if f"High_{ticker}" in data.columns else "High"
    low_col   = f"Low_{ticker}"   if f"Low_{ticker}" in data.columns else "Low"
    volume_col= f"Volume_{ticker}" if f"Volume_{ticker}" in data.columns else "Volume"

    data['SMA20'] = data[close_col].rolling(20).mean()
    data['SMA50'] = data[close_col].rolling(50).mean()
    data['EMA20'] = data[close_col].ewm(span=20, adjust=False).mean()
    data['EMA50'] = data[close_col].ewm(span=50, adjust=False).mean()

    delta = data[close_col].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    data['RSI'] = 100 - (100 / (1 + rs))

    ema12 = data[close_col].ewm(span=12, adjust=False).mean()
    ema26 = data[close_col].ewm(span=26, adjust=False).mean()
    data['MACD'] = ema12 - ema26
    data['MACD_Signal'] = data['MACD'].ewm(span=9, adjust=False).mean()

    high_low = data[high_col] - data[low_col]
    high_close = (data[high_col] - data[close_col].shift()).abs()
    low_close = (data[low_col] - data[close_col].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    data['ATR14'] = tr.rolling(14).mean()

    data['VMA20'] = data[volume_col].rolling(20).mean()
    
    return data

# ----- FUNCTION: Generate recommendation -----
def generate_recommendation(latest, ticker):
    score_buy = 0
    score_sell = 0

    if latest['SMA20'] > latest['SMA50']: score_buy += 1
    elif latest['SMA20'] < latest['SMA50']: score_sell += 1

    if latest['EMA20'] > latest['EMA50']: score_buy += 1
    elif latest['EMA20'] < latest['EMA50']: score_sell += 1

    if latest['RSI'] < 30: score_buy += 1
    elif latest['RSI'] > 70: score_sell += 1

    if latest['MACD'] > latest['MACD_Signal']: score_buy += 1
    elif latest['MACD'] < latest['MACD_Signal']: score_sell += 1

    if latest['Volume_' + ticker] > latest['VMA20']:
        score_buy += 0.5
        score_sell += 0.5
        
    if score_buy > score_sell:
        return "BUY"
    elif score_sell > score_buy:
        return "SELL"
    else:
        return "HOLD"

# ----- API Endpoint -----
def main_app(request, context):
    try:
        body = request.get("body")
        if body:
            data = json.loads(body)
        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "No body provided"})
            }
        ticker = data.get('ticker')
        period = data.get('period', '30d')

        if not ticker:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Ticker is required"})
            }

        stock_data = yf.download(ticker, period=period, auto_adjust=True)

        if isinstance(stock_data.columns, pd.MultiIndex):
            stock_data.columns = ['_'.join(filter(None, col)).strip() for col in stock_data.columns.values]

        stock_data = calculate_indicators(stock_data, ticker)
        #print(stock_data.tail())

        # ✅ ตรวจสอบว่ามีข้อมูลก่อนใช้งาน
        latest_data = stock_data.dropna(subset=['SMA20','SMA50','EMA20','EMA50','RSI','MACD','MACD_Signal','ATR14'])
        if latest_data.empty:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Not enough data to calculate indicators"})
            }

        latest = latest_data.iloc[-1]
        sma20 = float(latest['SMA20'])
        sma50 = float(latest['SMA50'])
        ema20 = float(latest['EMA20'])
        ema50 = float(latest['EMA50'])
        rsi = float(latest['RSI'])
        macd = float(latest['MACD'])
        macd_signal = float(latest['MACD_Signal'])
        atr = float(latest['ATR14'])
        
        msg = {
            "SMA_Signal": "BUY" if sma20 > sma50 else "SELL",
            "EMA_Signal": "BUY" if ema20 > ema50 else "SELL",
            "RSI": {
                "value": round(rsi, 2),
                "signal": "Overbought (SELL)" if rsi > 70 else "Oversold (BUY)" if rsi < 30 else "Neutral"
            },
            "MACD": {
                "value": round(macd, 2),
                "signal": "Bullish" if macd > macd_signal else "Bearish"
            },
            "ATR14": {
                "value": round(atr, 2),
                "description": "Volatility"
            }
        }
        recommendation = generate_recommendation(latest, ticker)

        response = {
            "ticker": ticker,
            "recommendation": recommendation,
            "details": msg,
            "latest_data": latest.to_dict()
        }
        return {
            "statusCode": 200,
            "body": json.dumps(response)
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
# ----- END OF FILE -----