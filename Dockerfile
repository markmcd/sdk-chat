FROM python:3.11-slim

# Install git and uv
RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Copy the rest of the application
COPY . .

# Ensure run.sh is executable
RUN chmod +x run.sh

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

# Create data directory
RUN mkdir -p /app/data

# Default command
ENTRYPOINT ["/app/run.sh"]
