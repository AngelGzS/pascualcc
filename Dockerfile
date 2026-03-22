FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt aiohttp

# Copy app
COPY . .

# Create state directory
RUN mkdir -p data/paper logs

# Expose web dashboard
EXPOSE 8082

# Health check
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/status')" || exit 1

# Default: run paper trading with web dashboard
CMD ["python", "run_paper.py"]
