FROM python:3.11-slim

# Security: Create non-root user for production
RUN groupadd -r dischargeiq && useradd -r -g dischargeiq -d /app -s /sbin/nologin dischargeiq

WORKDIR /app

# Install dependencies first (Docker cache optimization)
COPY python/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY python/ .

# Switch to non-root user
RUN chown -R dischargeiq:dischargeiq /app
USER dischargeiq

EXPOSE 8000

# Health check — Docker/Railway can verify the server is responsive
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Production: 2 workers for concurrent FHIR requests, graceful shutdown
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--timeout-graceful-shutdown", "10"]
