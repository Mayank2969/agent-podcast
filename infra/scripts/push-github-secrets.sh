#!/bin/bash
# Pushes local .env and AWS credentials to GitHub Actions Secrets automatically.

# 1. Check for GitHub CLI
if ! command -v gh &> /dev/null; then
    echo "❌ GitHub CLI (gh) is not installed. Please install it first: brew install gh"
    exit 1
fi

# 2. Check login status
if ! gh auth status &> /dev/null; then
    echo "🔒 Please login to GitHub CLI first by running: gh auth login"
    exit 1
fi

# 3. Read and push from .env
if [ -f "../../.env" ]; then
    ENV_FILE="../../.env"
elif [ -f ".env" ]; then
    ENV_FILE=".env"
else
    echo "❌ Could not find .env file. Run this from the project root."
    exit 1
fi

echo "🚀 Pushing application secrets from $ENV_FILE to GitHub..."

while IFS='=' read -r key value; do
    # Ignore comments and empty lines
    if [[ $key == \#* ]] || [[ -z $key ]]; then
        continue
    fi
    
    # Trim any whitespace from the key
    clean_key=$(echo "$key" | xargs)
    
    # Strip any potential carriage returns or quotes from value
    clean_value=$(echo "$value" | tr -d '\r' | sed -e 's/^"//' -e 's/"$//')
    
    # Only push specific necessary keys to avoid dumping everything
    if [[ "$clean_key" =~ ^(POSTGRES_PASSWORD|ADMIN_API_KEY|ANTHROPIC_API_KEY|GOOGLE_API_KEY|DEEPGRAM_TTS_API_KEY|CARTESIA_API_KEY)$ ]]; then
        echo "   Setting $clean_key..."
        echo "$clean_value" | gh secret set "$clean_key"
    fi
done < "$ENV_FILE"

# 4. Grab AWS Credentials from the local AWS CLI
echo "☁️  Checking local AWS CLI for credentials..."
if aws configure get aws_access_key_id &> /dev/null; then
    aws configure get aws_access_key_id | gh secret set AWS_ACCESS_KEY_ID
    aws configure get aws_secret_access_key | gh secret set AWS_SECRET_ACCESS_KEY
    echo "   ✅ AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY set from your local AWS profile."
else
    echo "   ⚠️  AWS CLI not configured locally. You will need to add AWS keys manually or run 'aws configure'."
fi

echo "🎉 Done! All secrets are securely stored in your GitHub repository."
