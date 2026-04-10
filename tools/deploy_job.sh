#!/bin/bash
set -e

PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "YOUR_PROJECT")
REGION="us-central1"
REPO_NAME="sdk-chat-repo"
IMAGE_NAME="ingest-job"
JOB_NAME="sdk-chat-ingest"

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:latest"

echo "========================================"
echo "🚀 Deploying Cloud Run Job Update"
echo "Project: $PROJECT_ID"
echo "Image:   $IMAGE_URI"
echo "Job:     $JOB_NAME"
echo "========================================"

echo -e "\n[1/2] Building and pushing image via Cloud Build..."
gcloud builds submit --tag "$IMAGE_URI" .

echo -e "\n[2/2] Updating Cloud Run Job..."
gcloud run jobs update "$JOB_NAME" \
    --region "$REGION" \
    --image "$IMAGE_URI"

echo -e "\n✅ Deployment complete!"
echo "To execute the job now, run:"
echo "  gcloud run jobs execute $JOB_NAME --region $REGION"
