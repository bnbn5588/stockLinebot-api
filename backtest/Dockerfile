FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
COPY backtest/requirements.txt ./backtest-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r backtest-requirements.txt
CMD ["python", "-m", "backtest.run"]
