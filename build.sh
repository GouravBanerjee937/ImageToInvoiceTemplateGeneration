#!/usr/bin/env bash
# Exit on error
set -o errexit

# Upgrade pip
pip install --upgrade pip

# Install Python dependencies
pip install -r requirements.txt

# Note: Render uses Ubuntu underneath. We need to install poppler for pdf2image.
apt-get update && apt-get install -y poppler-utils

# Install Playwright browser binaries and OS dependencies
playwright install chromium
playwright install-deps chromium
