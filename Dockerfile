FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .

# Directories for persistence and screenshots
RUN mkdir -p /app/data /app/screenshots

CMD ["python", "main.py", "--send"]
