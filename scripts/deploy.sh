#!/bin/bash

# WhatIsMyTip Deployment Script
# This script helps deploy to Digital Ocean

set -e

echo "🚀 WhatIsMyTip Deployment Script"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if doctl is installed
if ! command -v doctl &> /dev/null; then
    echo -e "${YELLOW}doctl is not installed. Please install it first:${NC}"
    echo "https://docs.digitalocean.com/reference/doctl/how-to/install/"
    exit 1
fi

# Check authentication
echo "Checking Digital Ocean authentication..."
if ! doctl account get &> /dev/null; then
    echo -e "${YELLOW}Not authenticated with Digital Ocean. Please run:${NC}"
    echo "doctl auth init"
    exit 1
fi

echo -e "${GREEN}✓ Authenticated with Digital Ocean${NC}"
echo ""

# Deploy backend
echo "Deploying backend to App Platform..."
doctl apps create --spec .do/app.yaml || doctl apps update --spec .do/app.yaml

# Deploy frontend
echo ""
echo "Deploying frontend to App Platform..."
doctl apps create --spec .do/frontend.yaml || doctl apps update --spec .do/frontend.yaml

echo ""
echo -e "${GREEN}✓ Deployment complete!${NC}"
echo ""
echo "Backend API: https://api.whatismytip.com"
echo "Frontend: https://whatismytip.com"
