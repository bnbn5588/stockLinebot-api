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
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write('Hello, world!'.encode('utf-8'))
        return


# ----- FUNCTION: Calculate Indicators -----
def calculate_indicators(data, ticker):
    close_col  = f"Close_{ticker}"  if f"Close_{ticker}"  in data.columns else "Close"
    high_col   = f"High_{ticker}"   if f"High_{ticker}"   in data.columns else "High"
    low_col    = f"Low_{ticker}"    if f"Low_{ticker}"    in data.columns else "Low"
    volume_col = f"Volume_{ticker}" if f"Volume_{ticker}" in data.columns else "Volume"

    # --- Moving Averages ---
    data['SMA20'] = data[close_col].rolling(20).mean()
    data['SMA50'] = data[close_col].rolling(50).mean()
    data['EMA20'] = data[close_col].ewm(span=20, adjust=False).mean()
    data['EMA50'] = data[close_col].ewm(span=50, adjust=False).mean()

    # --- RSI ---
    delta    = data[close_col].diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs       = avg_gain / avg_loss
    data['RSI'] = 100 - (100 / (1 + rs))

    # --- MACD ---
    ema12 = data[close_col].ewm(span=12, adjust=False).mean()
    ema26 = data[close_col].ewm(span=26, adjust=False).mean()
    data['MACD']        = ema12 - ema26
    data['MACD_Signal'] = data['MACD'].ewm(span=9, adjust=False).mean()

    # --- ATR14 ---
    high_low   = data[high_col] - data[low_col]
    high_close = (data[high_col] - data[close_col].shift()).abs()
    low_close  = (data[low_col]  - data[close_col].shift()).abs()
    tr         = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    data['ATR14'] = tr.rolling(14).mean()

    # --- Volume Moving Average ---
    data['VMA20'] = data[volume_col].rolling(20).mean()

    # --- Bollinger Bands ---
    std20              = data[close_col].rolling(20).std()
    data['BB_Middle']  = data['SMA20']
    data['BB_Upper']   = data['SMA20'] + (2 * std20)
    data['BB_Lower']   = data['SMA20'] - (2 * std20)

    # --- Stochastic Oscillator (%K / %D) ---
    lowest_low   = data[low_col].rolling(14).min()
    highest_high = data[high_col].rolling(14).max()
    data['Stoch_K'] = (data[close_col] - lowest_low) / (highest_high - lowest_low) * 100
    data['Stoch_D'] = data['Stoch_K'].rolling(3).mean()

    # --- OBV (On Balance Volume) ---
    obv = [0]
    for i in range(1, len(data)):
        if data[close_col].iloc[i] > data[close_col].iloc[i - 1]:
            obv.append(obv[-1] + data[volume_col].iloc[i])
        elif data[close_col].iloc[i] < data[close_col].iloc[i - 1]:
            obv.append(obv[-1] - data[volume_col].iloc[i])
        else:
            obv.append(obv[-1])
    data['OBV'] = obv
    data['OBV_EMA20'] = data['OBV'].ewm(span=20, adjust=False).mean()

    # --- ADX (Average Directional Index) ---
    up_move   = data[high_col] - data[high_col].shift()
    down_move = data[low_col].shift() - data[low_col]
    plus_dm   = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm  = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr14_s   = tr.ewm(span=14, adjust=False).mean()
    plus_di   = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr14_s)
    minus_di  = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr14_s)
    dx        = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    data['ADX']      = dx.ewm(span=14, adjust=False).mean()
    data['Plus_DI']  = plus_di
    data['Minus_DI'] = minus_di

    return data


# ----- FUNCTION: Generate recommendation -----
def generate_recommendation(latest, ticker):
    score_buy  = 0.0
    score_sell = 0.0

    close_col = f"Close_{ticker}" if f"Close_{ticker}" in latest.index else "Close"

    # --- EMA crossover (weight: 1.0) ---
    if latest['EMA20'] > latest['EMA50']:
        score_buy += 1.0
    elif latest['EMA20'] < latest['EMA50']:
        score_sell += 1.0

    # --- MACD crossover (weight: 2.0) ---
    if latest['MACD'] > latest['MACD_Signal']:
        score_buy += 2.0
    elif latest['MACD'] < latest['MACD_Signal']:
        score_sell += 2.0

    # --- RSI (weight: 1.5) ---
    if latest['RSI'] < 30:
        score_buy += 1.5
    elif latest['RSI'] > 70:
        score_sell += 1.5

    # --- Bollinger Bands (weight: 2.0) ---
    close_val = latest[close_col]
    if close_val < latest['BB_Lower']:
        score_buy += 2.0
    elif close_val > latest['BB_Upper']:
        score_sell += 2.0

    # --- Stochastic Oscillator (weight: 1.5) ---
    if latest['Stoch_K'] < 20:
        score_buy += 1.5
    elif latest['Stoch_K'] > 80:
        score_sell += 1.5

    # --- OBV trend (weight: 1.0) ---
    if latest['OBV'] > latest['OBV_EMA20']:
        score_buy += 1.0
    elif latest['OBV'] < latest['OBV_EMA20']:
        score_sell += 1.0

    # --- SMA crossover (weight: 0.5) ---
    if latest['SMA20'] > latest['SMA50']:
        score_buy += 0.5
    elif latest['SMA20'] < latest['SMA50']:
        score_sell += 0.5

    total_possible = 1.0 + 2.0 + 1.5 + 2.0 + 1.5 + 1.0 + 0.5  # = 9.5
    winning_score  = max(score_buy, score_sell)
    confidence     = round((winning_score / total_possible) * 100, 1)

    # ADX-based trend strength
    adx_val = latest['ADX']
    if adx_val >= 40:
        trend_label = "Strong"
    elif adx_val >= 20:
        trend_label = "Moderate"
    else:
        trend_label = "Weak"
        # Reduce confidence in sideways market
        confidence = round(confidence * 0.75, 1)

    # Determine direction and strength label
    if score_buy > score_sell:
        direction = "BUY"
    elif score_sell > score_buy:
        direction = "SELL"
    else:
        direction = "HOLD"
        confidence = 0.0

    if confidence >= 80:
        strength = "Strong"
    elif confidence >= 60:
        strength = "Moderate"
    elif confidence >= 40:
        strength = "Weak"
    else:
        direction = "HOLD"
        strength  = "Insufficient"

    return direction, strength, confidence, trend_label, round(float(adx_val), 2)


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
        period = data.get('period', '90d')  # default bumped to 90d for SMA50

        if not ticker:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Ticker is required"})
            }

        stock_data = yf.download(ticker, period=period, auto_adjust=True)

        if isinstance(stock_data.columns, pd.MultiIndex):
            stock_data.columns = ['_'.join(filter(None, col)).strip() for col in stock_data.columns.values]

        stock_data = calculate_indicators(stock_data, ticker)

        required_cols = ['SMA20', 'SMA50', 'EMA20', 'EMA50', 'RSI', 'MACD',
                         'MACD_Signal', 'ATR14', 'BB_Upper', 'BB_Lower',
                         'Stoch_K', 'Stoch_D', 'OBV', 'OBV_EMA20', 'ADX']
        latest_data = stock_data.dropna(subset=required_cols)
        if latest_data.empty:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Not enough data to calculate indicators. Try a longer period."})
            }

        latest = latest_data.iloc[-1]

        close_col = f"Close_{ticker}" if f"Close_{ticker}" in latest.index else "Close"
        close_val = float(latest[close_col])

        recommendation, strength, confidence, trend_label, adx_val = generate_recommendation(latest, ticker)

        details = {
            "SMA_Signal":      "BUY" if latest['SMA20'] > latest['SMA50'] else "SELL",
            "EMA_Signal":      "BUY" if latest['EMA20'] > latest['EMA50'] else "SELL",
            "RSI": {
                "value":  round(float(latest['RSI']), 2),
                "signal": "Overbought (SELL)" if latest['RSI'] > 70 else "Oversold (BUY)" if latest['RSI'] < 30 else "Neutral"
            },
            "MACD": {
                "value":  round(float(latest['MACD']), 4),
                "signal": "Bullish" if latest['MACD'] > latest['MACD_Signal'] else "Bearish"
            },
            "ATR14": {
                "value":       round(float(latest['ATR14']), 2),
                "description": "Volatility"
            },
            "BollingerBands": {
                "upper":    round(float(latest['BB_Upper']), 2),
                "middle":   round(float(latest['BB_Middle']), 2),
                "lower":    round(float(latest['BB_Lower']), 2),
                "close":    round(close_val, 2),
                "signal":   "BUY" if close_val < latest['BB_Lower'] else "SELL" if close_val > latest['BB_Upper'] else "Neutral"
            },
            "Stochastic": {
                "K":      round(float(latest['Stoch_K']), 2),
                "D":      round(float(latest['Stoch_D']), 2),
                "signal": "Oversold (BUY)" if latest['Stoch_K'] < 20 else "Overbought (SELL)" if latest['Stoch_K'] > 80 else "Neutral"
            },
            "OBV": {
                "value":  round(float(latest['OBV']), 0),
                "signal": "Bullish" if latest['OBV'] > latest['OBV_EMA20'] else "Bearish"
            }
        }

        response = {
            "ticker":          ticker,
            "recommendation":  recommendation,
            "strength":        strength,
            "confidence":      confidence,
            "trend_strength":  f"{trend_label} (ADX: {adx_val})",
            "details":         details,
            "latest_data":     latest.to_dict()
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
# ----- Local Dev Runner -----
if __name__ == "__main__":
    from http.server import HTTPServer
    port = int(os.getenv("PORT", 5000))
    server = HTTPServer(('localhost', port), handler)
    print(f"Running on http://localhost:{port}")
    server.serve_forever()
# ----- END OF FILE -----
