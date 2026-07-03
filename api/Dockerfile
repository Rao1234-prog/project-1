FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY greenledger ./greenledger

EXPOSE 8000
CMD ["uvicorn", "greenledger.main:app", "--host", "0.0.0.0", "--port", "8000"]
