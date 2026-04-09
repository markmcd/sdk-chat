#!/bin/bash
set -e

# Default to a project-based bucket name if not specified
PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "YOUR_PROJECT")
BUCKET=${GCS_BUCKET:-"gs://${PROJECT_ID}-sdk-chat-state"}
BRANCH="my-config"
STATE_FILES=(".store_name" "packages.json" "failed_packages.json")

COMMAND=$1

if [ "$COMMAND" == "seed" ]; then
    echo "Seeding $BUCKET from the '$BRANCH' branch..."
    mkdir -p .tmp_state
    
    # Ensure we have the latest branches
    git fetch origin $BRANCH || true

    for file in "${STATE_FILES[@]}"; do
        # Check if file exists in the branch
        if git ls-tree -r "origin/$BRANCH" --name-only 2>/dev/null | grep -q "^$file$" || \
           git ls-tree -r "$BRANCH" --name-only 2>/dev/null | grep -q "^$file$"; then
            
            # Prefer origin branch if available, fallback to local
            REF=$([ -n "$(git ls-remote --heads origin $BRANCH)" ] && echo "origin/$BRANCH" || echo "$BRANCH")
            
            echo "Extracting $file from $REF..."
            git show "$REF:$file" > ".tmp_state/$file"
            gcloud storage cp ".tmp_state/$file" "$BUCKET/$file"
        else
            echo "File $file not found in $BRANCH branch. Skipping."
        fi
    done
    rm -rf .tmp_state
    echo -e "\nSeed complete! GCS bucket is now primed with the config branch state."

elif [ "$COMMAND" == "save" ]; then
    echo "Saving state from $BUCKET back to the '$BRANCH' branch..."
    
    # Stash any uncommitted changes to avoid conflicts
    CURRENT_BRANCH=$(git branch --show-current)
    STASHED=$(git stash push -m "Temp stash for state sync" | grep "Saved working directory" || true)

    # Check out the target branch
    git checkout $BRANCH
    git pull origin $BRANCH || true

    # Download from GCS
    for file in "${STATE_FILES[@]}"; do
        gcloud storage cp "$BUCKET/$file" "$file" || echo "Note: $file not found in GCS or failed to download."
    done

    # Commit and push
    git add "${STATE_FILES[@]}"
    if git diff --staged --quiet; then
        echo "No state changes found to commit."
    else
        git commit -m "chore: sync state from GCS after ingest run"
        git push origin $BRANCH
        echo "Changes pushed to $BRANCH!"
    fi

    # Return to original state
    git checkout $CURRENT_BRANCH
    if [ -n "$STASHED" ]; then
        git stash pop
    fi
    echo -e "\nSave complete!"

else
    echo "Usage: ./sync_state.sh [seed|save]"
    echo ""
    echo "Commands:"
    echo "  seed  - Extracts .store_name and packages.json from '$BRANCH' and uploads to GCS."
    echo "          Run this BEFORE creating/running the Cloud Run Job."
    echo "  save  - Downloads updated state from GCS, commits to '$BRANCH', and pushes to git."
    echo "          Run this periodically to snapshot the Cloud Run Job's progress."
    echo ""
    echo "Environment Variables:"
    echo "  GCS_BUCKET - Override the default GCS bucket (default: gs://<gcloud-project>-sdk-chat-state)"
fi
