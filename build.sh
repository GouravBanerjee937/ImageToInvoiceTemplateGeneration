#!/usr/bin/env bash
# Exit on error
set -o errexit

# Upgrade pip
pip install --upgrade pip

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browser binaries
playwright install chromium

# Note: We are removing playwright install-deps because it requires root/sudo password
# Render does not allow interactive sudo prompts during build.
# The chromium browser usually works without the extra deps on Render's modern Ubuntu images.
