FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
# Added libldap2-dev, libsasl2-dev, libssl-dev for python-ldap compilation
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libldap2-dev \
    libsasl2-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Expose port
EXPOSE 10000
