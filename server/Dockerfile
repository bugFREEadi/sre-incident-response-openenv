FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md openenv.yaml ./
COPY actions.py models.py observation.py reward.py verifier.py world.py ./
COPY scenarios ./scenarios
COPY server ./server
COPY sre_incident_env ./sre_incident_env

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
