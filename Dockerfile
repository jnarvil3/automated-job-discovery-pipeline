FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway volume mount for SQLite persistence
RUN mkdir -p /app/data

CMD ["python", "main.py", "--send"]
