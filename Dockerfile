# syntax=docker/dockerfile:1.3
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.6.4 /uv /uvx /bin/

# Install system dependencies including Node.js and Chromium for Nova Act
RUN apt-get update && apt-get install -y \
    htop \
    vim \
    curl \
    tar \
    unzip \
    python3-dev \
    build-essential \
    gcc \
    cmake \
    netcat-openbsd \
    ca-certificates \
    gnupg \
    chromium \
    chromium-driver \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install tctl (Temporal CLI)
RUN curl -L https://github.com/temporalio/tctl/releases/download/v1.18.1/tctl_1.18.1_linux_arm64.tar.gz -o /tmp/tctl.tar.gz && \
    tar -xzf /tmp/tctl.tar.gz -C /usr/local/bin && \
    chmod +x /usr/local/bin/tctl && \
    rm /tmp/tctl.tar.gz

RUN uv pip install --system --upgrade pip setuptools wheel

ENV UV_HTTP_TIMEOUT=1000

# Copy pyproject.toml and README.md to install dependencies
COPY pyproject.toml /app/novasell/pyproject.toml
COPY README.md /app/novasell/README.md

WORKDIR /app/novasell

# Copy the project code
COPY project /app/novasell/project


# Install dependencies
RUN uv pip install --system --no-cache \
    temporalio \
    openai \
    python-dotenv \
    termcolor \
    httpx \
    uvicorn \
    agentex-sdk \
    Pillow \
    pydantic \
    pyotp \
    requests \
    nova-act

# Install Playwright Chrome browser (required by Nova Act SDK)
RUN python -m playwright install chrome && \
    python -m playwright install-deps chrome

WORKDIR /app/novasell

ENV PYTHONPATH=/app
ENV AGENT_NAME=novasell-agent

# Run the ACP server
CMD ["uvicorn", "project.acp:acp", "--host", "0.0.0.0", "--port", "8000"]