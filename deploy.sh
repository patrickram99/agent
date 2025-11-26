#!/bin/bash
# =============================================================================
# deploy.sh - Deploy WhatsApp Finance Agent to Google Cloud Run
# =============================================================================
# Usage: ./deploy.sh [PROJECT_ID] [REGION]
# 
# Prerequisites:
#   1. Install Google Cloud SDK: https://cloud.google.com/sdk/docs/install
#   2. Authenticate: gcloud auth login
#   3. Enable required APIs (script will do this)
#   4. Set up secrets in Secret Manager (script will guide you)
# =============================================================================

set -e  # Exit on error

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration (can be overridden by environment or arguments)
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${2:-us-central1}"
SERVICE_NAME="whatsapp-finance-agent"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  WhatsApp Finance Agent Deployment    ${NC}"
echo -e "${BLUE}========================================${NC}"

# Check if PROJECT_ID is set
if [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}Error: PROJECT_ID is required${NC}"
    echo "Usage: ./deploy.sh <PROJECT_ID> [REGION]"
    echo "Or set GOOGLE_CLOUD_PROJECT environment variable"
    exit 1
fi

echo -e "${GREEN}Project:${NC} $PROJECT_ID"
echo -e "${GREEN}Region:${NC} $REGION"
echo -e "${GREEN}Service:${NC} $SERVICE_NAME"
echo ""

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}Error: gcloud CLI is not installed${NC}"
    echo "Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Set the project
echo -e "${YELLOW}Setting Google Cloud project...${NC}"
gcloud config set project "$PROJECT_ID"

# Enable required APIs
echo -e "${YELLOW}Enabling required APIs...${NC}"
gcloud services enable \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    containerregistry.googleapis.com \
    secretmanager.googleapis.com \
    --quiet

# Check if secrets exist, if not prompt user to create them
echo -e "${YELLOW}Checking secrets in Secret Manager...${NC}"

check_secret() {
    local secret_name=$1
    if ! gcloud secrets describe "$secret_name" &> /dev/null; then
        echo -e "${RED}Secret '$secret_name' not found.${NC}"
        return 1
    fi
    echo -e "${GREEN}✓ Secret '$secret_name' exists${NC}"
    return 0
}

create_secret_if_missing() {
    local secret_name=$1
    local description=$2
    
    if ! check_secret "$secret_name"; then
        echo -e "${YELLOW}Creating secret '$secret_name'...${NC}"
        echo -e "${BLUE}$description${NC}"
        read -sp "Enter value for $secret_name: " secret_value
        echo ""
        
        echo -n "$secret_value" | gcloud secrets create "$secret_name" \
            --data-file=- \
            --replication-policy="automatic"
        
        echo -e "${GREEN}✓ Secret '$secret_name' created${NC}"
    fi
}

# Required secrets
echo ""
echo -e "${BLUE}Setting up required secrets...${NC}"
create_secret_if_missing "DATABASE_URL" "PostgreSQL connection string (e.g., postgresql://user:pass@host:5432/dbname)"
create_secret_if_missing "GEMINI_API_KEY" "Google Gemini API key from https://makersuite.google.com/app/apikey"
create_secret_if_missing "EVOLUTION_API_KEY" "Evolution API key for WhatsApp integration"

# Grant Cloud Run access to secrets
echo -e "${YELLOW}Granting Cloud Run service account access to secrets...${NC}"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for secret in DATABASE_URL GEMINI_API_KEY EVOLUTION_API_KEY; do
    gcloud secrets add-iam-policy-binding "$secret" \
        --member="serviceAccount:${SERVICE_ACCOUNT}" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet 2>/dev/null || true
done

# Build the Docker image
echo ""
echo -e "${YELLOW}Building Docker image...${NC}"
gcloud builds submit --tag "$IMAGE_NAME:latest" .

# Deploy to Cloud Run
echo ""
echo -e "${YELLOW}Deploying to Cloud Run...${NC}"
gcloud run deploy "$SERVICE_NAME" \
    --image "$IMAGE_NAME:latest" \
    --region "$REGION" \
    --platform managed \
    --allow-unauthenticated \
    --memory 512Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 10 \
    --timeout 300s \
    --set-env-vars "USE_GEMINI=true,GEMINI_MODEL=gemini-2.5-flash,TIMEZONE=America/Lima,OTP_TTL_MINUTES=5,EVOLUTION_BASE_URL=http://34.121.145.34:8080,INSTANCE_ID=ConstruccionSOftware" \
    --set-secrets "DATABASE_URL=DATABASE_URL:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,EVOLUTION_API_KEY=EVOLUTION_API_KEY:latest"

# Get the service URL
echo ""
echo -e "${YELLOW}Getting service URL...${NC}"
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)')

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Deployment Complete! 🎉              ${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Service URL:${NC} $SERVICE_URL"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "1. Configure your Evolution API webhook URL to:"
echo -e "   ${GREEN}${SERVICE_URL}/webhook${NC}"
echo ""
echo "2. Test the deployment:"
echo "   curl ${SERVICE_URL}/"
echo ""
echo "3. View logs:"
echo "   gcloud run logs read --service=$SERVICE_NAME --region=$REGION"
echo ""
echo "4. To update environment variables, use:"
echo "   gcloud run services update $SERVICE_NAME --region=$REGION --set-env-vars KEY=VALUE"
