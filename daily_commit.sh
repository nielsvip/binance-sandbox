#!/bin/bash

# Navigate to the repository directory
cd "$(dirname "$0")"

# Add all changes
git add .

# Commit with a timestamp
git commit -m "Automated daily commit $(date -u '+%Y-%m-%d %H:%M:%S') UTC"

# Push to the remote repository
git push origin master
