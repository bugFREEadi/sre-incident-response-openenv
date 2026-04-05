# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Install system deps needed by psycopg binary wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifests first so Docker can cache the pip layer
COPY pyproject.toml README.md openenv.yaml ./

# Install the package (resolves its dependencies)
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Copy application source
COPY actions.py models.py observation.py reward.py verifier.py world.py ./
COPY scenarios ./scenarios
COPY server ./server
COPY sre_incident_env ./sre_incident_env
COPY inference.py ./

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD python - <<'EOF'
import urllib.request, sys
try:
    urllib.request.urlopen("http://localhost:8000/health", timeout=4)
    sys.exit(0)
except Exception:
    sys.exit(1)
EOF

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
