# Dockerfile for Google Cloud Run deployment
# Financial WhatsApp Agent with Gemini AI

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Set working directory
WORKDIR /app

# Install system dependencies needed for psycopg2 and other packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY langchain_agent.py .

# Copy SQL scripts if needed
COPY sql/ ./sql/

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose port (Cloud Run uses PORT env variable)
EXPOSE 8080

# Health check endpoint (optional but recommended)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/')" || exit 1

# Run the application
# Cloud Run sets PORT env variable, FastAPI/uvicorn will use it
CMD ["sh", "-c", "uvicorn langchain_agent:app --host 0.0.0.0 --port ${PORT:-8080}"]
