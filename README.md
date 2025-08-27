# Stock Line Bot API

This project provides a stock analysis API that utilizes the `yfinance` library to fetch stock data and calculate various technical indicators. The API allows users to request stock analysis for specific tickers.

## Project Structure

```
stockLinebot-api
├── api
│   └── index.py        # Main API logic
├── requirements.txt     # Project dependencies
├── vercel.json          # Vercel deployment configuration
└── README.md            # Project documentation
```

## Setup Instructions

1. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd stockLinebot-api
   ```

2. **Install dependencies:**
   Make sure you have Python installed. Then, create a virtual environment and install the required packages:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   pip install -r requirements.txt
   ```

## Usage

To run the API locally, navigate to the `api` directory and execute the following command:

```bash
python index.py
```

The API will be available at `http://localhost:5000`.

## API Endpoints

- **GET /analyze**
  - Query parameters:
    - `ticker`: The stock ticker symbol (e.g., AAPL, AMZN).
  - Response: Returns stock analysis including indicators and recommendations.

## Deployment

This project is configured to be deployed on Vercel. To deploy, simply push your changes to the main branch of your repository, and Vercel will automatically build and deploy the project.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.
