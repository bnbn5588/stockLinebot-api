# Stock Line Bot API

A stock technical analysis API built on Vercel serverless functions. It fetches real-time stock data from Yahoo Finance, calculates a set of technical indicators, and returns a weighted BUY / SELL / HOLD recommendation with a confidence score.

---

## Project Structure

```
stockLinebot-api/
├── api/
│   └── stock_analysis.py   # All API logic (indicators, scoring, HTTP handler)
├── requirements.txt         # Python dependencies
├── vercel.json              # Vercel serverless routing config
└── README.md
```

---

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

Create `api/.env`:

```
API_KEY=your_secret_key_here
```

> For Vercel production, set `API_KEY` via `vercel env add API_KEY` instead of committing the file.

---

## Running Locally

```bash
cd api
python stock_analysis.py
# Listening on http://localhost:5000
```

---

## API Reference

### Authentication

Every request requires an `x-api-key` header:

```
x-api-key: your_secret_key_here
```

Returns `403 Forbidden` if missing or incorrect.

---

### POST `/api/stock_analysis`

Analyse a stock and return a recommendation.

**Request body (JSON):**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `ticker` | string | Yes | — | Stock ticker symbol (e.g. `AAPL`, `PTT.BK`) |
| `period` | string | No | `90d` | Data window passed to yfinance (e.g. `30d`, `6mo`, `1y`) |

**Example request:**

```bash
curl -X POST http://localhost:5000 \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_API_KEY" \
  -d '{"ticker": "AAPL", "period": "90d"}'
```

**Example response:**

```json
{
  "ticker": "AAPL",
  "recommendation": "BUY",
  "strength": "Moderate",
  "confidence": 63.2,
  "trend_strength": "Strong (ADX: 42.1)",
  "details": {
    "SMA_Signal": "BUY",
    "EMA_Signal": "BUY",
    "RSI": {
      "value": 28.4,
      "signal": "Oversold (BUY)"
    },
    "MACD": {
      "value": 0.45,
      "signal": "Bullish"
    },
    "ATR14": {
      "value": 2.3,
      "description": "Volatility"
    },
    "BollingerBands": {
      "upper": 195.40,
      "middle": 188.10,
      "lower": 180.80,
      "close": 179.50,
      "signal": "BUY"
    },
    "Stochastic": {
      "K": 18.2,
      "D": 22.1,
      "signal": "Oversold (BUY)"
    },
    "OBV": {
      "value": 123456789,
      "signal": "Bullish"
    }
  },
  "latest_data": { "...": "full row of calculated values" }
}
```

**Response fields:**

| Field | Description |
|-------|-------------|
| `recommendation` | `BUY`, `SELL`, or `HOLD` |
| `strength` | `Strong`, `Moderate`, `Weak`, or `Insufficient` |
| `confidence` | Percentage score (0–100) of how strongly signals agree |
| `trend_strength` | ADX-based label: `Strong`, `Moderate`, or `Weak`, plus the raw ADX value |

**Error responses:**

| Code | Reason |
|------|--------|
| 400 | Missing `ticker`, no body, or insufficient historical data |
| 403 | Invalid or missing API key |
| 500 | Unexpected error during processing |

---

### GET `/api/stock_analysis`

Health check. Returns `Hello, world!` if the API key is valid.

---

## Analysis Logic

This section explains every indicator used and how they combine into a final recommendation. Reading this gives you a solid foundation in technical analysis.

---

### Indicators Calculated

#### 1. Simple Moving Average (SMA)

```
SMA20 = average closing price over the last 20 days
SMA50 = average closing price over the last 50 days
```

- A **lagging** indicator — it smooths out price noise but reacts slowly.
- When SMA20 crosses **above** SMA50 → uptrend forming (bullish).
- When SMA20 crosses **below** SMA50 → downtrend forming (bearish).
- Requires at least 50 days of data, which is why the default period is `90d`.

---

#### 2. Exponential Moving Average (EMA)

```
EMA = previous_EMA + multiplier × (close - previous_EMA)
multiplier = 2 / (span + 1)
```

- Like SMA but gives **more weight to recent prices**, so it reacts faster to price changes.
- EMA20 vs EMA50 crossover is used as the primary trend signal in this API (weighted higher than SMA).

---

#### 3. RSI — Relative Strength Index

```
RS       = average gain over 14 days / average loss over 14 days
RSI      = 100 - (100 / (1 + RS))
```

- Oscillates between 0 and 100.
- **RSI < 30** → stock is *oversold* — price may have fallen too far → BUY signal.
- **RSI > 70** → stock is *overbought* — price may have risen too far → SELL signal.
- **30–70** → neutral zone, no signal.
- Best used in combination with trend indicators (a stock can stay oversold in a strong downtrend).

---

#### 4. MACD — Moving Average Convergence Divergence

```
MACD         = EMA(12) - EMA(26)
MACD Signal  = EMA(9) of MACD
```

- Measures **momentum** — how fast the trend is accelerating or decelerating.
- When MACD crosses **above** its signal line → bullish momentum → BUY.
- When MACD crosses **below** its signal line → bearish momentum → SELL.
- One of the most reliable momentum indicators, given the highest weight (2.0) in scoring.

---

#### 5. Bollinger Bands

```
Middle Band = SMA20
Upper Band  = SMA20 + (2 × 20-day standard deviation)
Lower Band  = SMA20 - (2 × 20-day standard deviation)
```

- Bands widen during volatile periods and narrow during calm ones.
- Price touching the **lower band** → oversold relative to recent volatility → BUY.
- Price touching the **upper band** → overbought relative to recent volatility → SELL.
- More precise than SMA crossover because it adapts to current market conditions.

---

#### 6. Stochastic Oscillator (%K / %D)

```
%K = (Close - Lowest Low over 14 days) / (Highest High - Lowest Low over 14 days) × 100
%D = 3-period SMA of %K  (signal line)
```

- Measures where the current price sits within the recent high–low range.
- **%K < 20** → price near the bottom of its recent range → oversold → BUY.
- **%K > 80** → price near the top of its recent range → overbought → SELL.
- Acts as a second confirmation alongside RSI — both measuring mean-reversion from extremes.

---

#### 7. ATR14 — Average True Range

```
True Range = max(High - Low,  |High - Previous Close|,  |Low - Previous Close|)
ATR14      = 14-period rolling average of True Range
```

- Measures **volatility** — how much a stock typically moves per day.
- Not used directly in the BUY/SELL scoring, but included in the response so you can assess risk.
- A high ATR means bigger price swings (higher risk/reward). A low ATR means calmer movement.

---

#### 8. OBV — On Balance Volume

```
If Close > Previous Close:  OBV = OBV_prev + Volume
If Close < Previous Close:  OBV = OBV_prev - Volume
If Close = Previous Close:  OBV = OBV_prev
```

- Tracks whether **volume is flowing into or out of a stock**.
- Rising OBV = buyers are more aggressive = bullish confirmation.
- Falling OBV = sellers dominate = bearish confirmation.
- Compared against its own 20-period EMA: OBV > OBV_EMA20 → bullish trend in volume.

---

#### 9. ADX — Average Directional Index

```
+DM = today's High - yesterday's High  (if positive, else 0)
-DM = yesterday's Low - today's Low    (if positive, else 0)
+DI = 100 × EMA(+DM) / ATR14
-DI = 100 × EMA(-DM) / ATR14
DX  = |+DI - -DI| / (+DI + -DI) × 100
ADX = EMA(14) of DX
```

- Measures **trend strength**, not direction.
- **ADX < 20** → market is ranging / sideways — indicator signals are less reliable.
- **ADX 20–40** → moderate trend — normal confidence.
- **ADX ≥ 40** → strong trend — high confidence in directional signals.
- Used as a **multiplier** in this API: weak ADX reduces the confidence score by 25%.

---

### Scoring & Recommendation System

Each signal casts a weighted vote. The total possible score is **9.5**.

| Indicator | Signal condition | Weight |
|-----------|-----------------|--------|
| MACD | MACD > Signal line | 2.0 |
| Bollinger Bands | Price outside a band | 2.0 |
| RSI | RSI < 30 or > 70 | 1.5 |
| Stochastic | %K < 20 or > 80 | 1.5 |
| EMA crossover | EMA20 vs EMA50 | 1.0 |
| OBV | OBV vs OBV_EMA20 | 1.0 |
| SMA crossover | SMA20 vs SMA50 | 0.5 |

**Why these weights?**

- **MACD and Bollinger Bands** are the strongest signals — MACD captures momentum shifts, Bollinger Bands capture price extremes relative to volatility. Both get 2.0.
- **RSI and Stochastic** are both mean-reversion oscillators. They confirm each other but are slightly less decisive on their own. Both get 1.5.
- **EMA and OBV** confirm trend direction and volume alignment. Supporting roles — 1.0 each.
- **SMA** is the most lagging indicator and largely duplicates the EMA signal. Lowest weight — 0.5.

**Confidence calculation:**

```
confidence = (winning_score / 9.5) × 100

If ADX < 20 (sideways market):
    confidence = confidence × 0.75   ← penalised for weak trend
```

**Strength labels:**

| Confidence | Strength | Recommendation |
|-----------|----------|----------------|
| ≥ 80% | Strong | BUY or SELL |
| 60–79% | Moderate | BUY or SELL |
| 40–59% | Weak | BUY or SELL |
| < 40% | Insufficient | Overridden to HOLD |

---

## Deployment

Push to your connected Git branch and Vercel deploys automatically. To deploy manually:

```bash
vercel --prod
```

Environment variables must be set in Vercel (not in `.env`):

```bash
vercel env add API_KEY
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `yfinance` | Fetches OHLCV stock data from Yahoo Finance |
| `pandas` | Data manipulation and rolling calculations |
| `python-dotenv` | Loads `API_KEY` from `.env` for local development |
