# Use a slim, stable Python base image
FROM python:3.11-slim

# Install system dependencies required for data compiling and network configurations
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside the container
WORKDIR /app

# Copy dependency mappings first to optimize Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire app repository structure (including app.py and the pages directory)
COPY . .

# Expose Streamlit's default networking port
EXPOSE 8501

# Configure Streamlit to run headlessly, listen on all interfaces, and suppress browser pops
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health

ENTRYPOINT ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
