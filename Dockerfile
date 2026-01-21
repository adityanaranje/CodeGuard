FROM python:3.10-slim

WORKDIR /app

# Install system dependencies if needed (e.g. for git or build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install CPU-only PyTorch first to avoid downloading huge GPU wheels
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install other requirements
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway provides PORT env var
CMD gunicorn -b 0.0.0.0:$PORT src.main:app
