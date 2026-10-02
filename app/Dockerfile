FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY static/ static/

# Binds port 80 directly (Fargate awsvpc mode has no host/container port
# remapping) - runs as root (image default) since that's required to bind
# a privileged port and this is a single-purpose container with no
# untrusted code running inside it.
EXPOSE 80

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]
