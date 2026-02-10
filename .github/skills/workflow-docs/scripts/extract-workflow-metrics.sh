#!/bin/bash
set -euo pipefail

# Extract workflow timing metrics from GitHub Actions API
# Discovers all workflows in .github/workflows and generates performance metrics
# Example workflows: ci.yml, cdk-config-matrix.yml, manual-release.yml
# Usage: .github/skills/workflow-docs/scripts/extract-workflow-metrics.sh [--json|--markdown]

OUTPUT_FORMAT="${1:---markdown}"
OUTPUT_DIR="$(dirname "$0")/../docs"
CACHE_FILE="/tmp/workflow-metrics-cache.json"
CACHE_MAX_AGE=3600  # 1 hour

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Auto-detect repository from git remote
# Priority: 1) GITHUB_REPOSITORY env var (set in GitHub Actions)
#          2) Extract from git remote origin URL (local execution)
# Works with both SSH (git@github.com:owner/repo.git) and HTTPS (https://github.com/owner/repo.git)
if [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
    REPO="$GITHUB_REPOSITORY"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
    
    if [[ -d "$REPO_ROOT/.git" ]]; then
        # Extract owner/repo from git remote URL
        REMOTE_URL=$(cd "$REPO_ROOT" && git remote get-url origin 2>/dev/null || echo "")
        if [[ -n "$REMOTE_URL" ]]; then
            # Handle both HTTPS and SSH URLs
            # Examples: git@github.com:owner/repo.git or https://github.com/owner/repo.git
            REPO=$(echo "$REMOTE_URL" | sed -E 's#.*(github\.com[:/])##' | sed 's#\.git$##')
        fi
    fi
    
    if [[ -z "${REPO:-}" ]]; then
        echo -e "${RED}Error: Could not detect repository. Set GITHUB_REPOSITORY env var or run from a git repository.${NC}" >&2
        exit 1
    fi
fi

echo -e "${GREEN}Extracting workflow metrics for ${REPO}...${NC}\n"

# Check if cache is valid
if [[ -f "$CACHE_FILE" ]]; then
    CACHE_AGE=$(($(date +%s) - $(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null)))
    if [[ $CACHE_AGE -lt $CACHE_MAX_AGE ]]; then
        echo -e "${YELLOW}Using cached data (${CACHE_AGE}s old)...${NC}"
        cat "$CACHE_FILE"
        exit 0
    fi
fi

# Dynamically discover all workflow files from .github/workflows directory
# This includes workflows like ci.yml, cdk-config-matrix.yml, manual-release.yml, etc.
echo -e "${YELLOW}Discovering workflow files...${NC}" >&2

# Set repo root if not already set (when GITHUB_REPOSITORY was available)
if [[ -z "${REPO_ROOT:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
fi

WORKFLOWS_DIR="$REPO_ROOT/.github/workflows"

if [[ ! -d "$WORKFLOWS_DIR" ]]; then
    echo -e "${RED}Error: Workflows directory not found at $WORKFLOWS_DIR${NC}" >&2
    exit 1
fi

# Get all .yml files in workflows directory, excluding docs subdirectory
WORKFLOWS=()
while IFS= read -r workflow_file; do
    WORKFLOWS+=("$workflow_file")
done < <(find "$WORKFLOWS_DIR" -maxdepth 1 -type f -name "*.yml" -exec basename {} \; | sort)

if [[ ${#WORKFLOWS[@]} -eq 0 ]]; then
    echo -e "${RED}Error: No workflow files found in $WORKFLOWS_DIR${NC}" >&2
    exit 1
fi

echo -e "${GREEN}Found ${#WORKFLOWS[@]} workflow files${NC}\n" >&2

# Function to get workflow runs and calculate metrics
get_workflow_metrics() {
    local workflow_file=$1
    local workflow_name=$(echo "$workflow_file" | sed 's/.yml$//')
    
    echo -e "Processing ${YELLOW}${workflow_file}${NC}..." >&2
    
    # Get last 10 successful runs for the workflow
    local runs=$(gh api \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "/repos/${REPO}/actions/workflows/${workflow_file}/runs?status=success&per_page=10" \
        --jq '.workflow_runs[] | {id: .id, conclusion: .conclusion, created_at: .created_at, updated_at: .updated_at, run_started_at: .run_started_at}' 2>/dev/null || echo "[]")
    
    if [[ -z "$runs" || "$runs" == "[]" ]]; then
        echo -e "  ${RED}No successful runs found${NC}" >&2
        echo "{\"workflow\": \"$workflow_file\", \"name\": \"$workflow_name\", \"runs\": 0, \"avg_duration\": 0, \"min_duration\": 0, \"max_duration\": 0, \"success_rate\": 0, \"last_run\": \"Never\"}"
        return
    fi
    
    # Calculate durations and statistics
    local durations=()
    local total_runs=0
    local last_run=""
    
    while IFS= read -r run; do
        if [[ -n "$run" ]]; then
            local created=$(echo "$run" | jq -r '.created_at')
            local updated=$(echo "$run" | jq -r '.updated_at')
            
            # Calculate duration in seconds
            local created_ts=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$created" "+%s" 2>/dev/null || date -d "$created" "+%s" 2>/dev/null)
            local updated_ts=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$updated" "+%s" 2>/dev/null || date -d "$updated" "+%s" 2>/dev/null)
            local duration=$((updated_ts - created_ts))
            
            durations+=($duration)
            ((total_runs++))
            
            if [[ -z "$last_run" ]]; then
                last_run="$created"
            fi
        fi
    done <<< "$runs"
    
    # Calculate statistics
    if [[ ${#durations[@]} -eq 0 ]]; then
        echo "{\"workflow\": \"$workflow_file\", \"name\": \"$workflow_name\", \"runs\": 0, \"avg_duration\": 0, \"min_duration\": 0, \"max_duration\": 0, \"success_rate\": 0, \"last_run\": \"Never\"}"
        return
    fi
    
    local sum=0
    local min=${durations[0]}
    local max=${durations[0]}
    
    for duration in "${durations[@]}"; do
        sum=$((sum + duration))
        if [[ $duration -lt $min ]]; then min=$duration; fi
        if [[ $duration -gt $max ]]; then max=$duration; fi
    done
    
    local avg=$((sum / ${#durations[@]}))
    
    # Get total runs (successful + failed) for success rate
    local total_all_runs=$(gh api \
        -H "Accept: application/vnd.github+json" \
        "/repos/${REPO}/actions/workflows/${workflow_file}/runs?per_page=100" \
        --jq '.total_count' 2>/dev/null || echo "0")
    
    local success_rate=100
    if [[ $total_all_runs -gt 0 ]]; then
        local successful_runs=$(gh api \
            -H "Accept: application/vnd.github+json" \
            "/repos/${REPO}/actions/workflows/${workflow_file}/runs?status=success&per_page=100" \
            --jq '.total_count' 2>/dev/null || echo "0")
        success_rate=$((successful_runs * 100 / total_all_runs))
    fi
    
    # Format last run date
    local last_run_formatted=$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$last_run" "+%Y-%m-%d" 2>/dev/null || date -d "$last_run" "+%Y-%m-%d" 2>/dev/null || echo "Unknown")
    
    echo -e "  ${GREEN}✓${NC} $total_runs runs, avg: ${avg}s, success: ${success_rate}%" >&2
    
    echo "{\"workflow\": \"$workflow_file\", \"name\": \"$workflow_name\", \"runs\": $total_runs, \"avg_duration\": $avg, \"min_duration\": $min, \"max_duration\": $max, \"success_rate\": $success_rate, \"last_run\": \"$last_run_formatted\"}"
}

# Collect metrics for all workflows
echo "{" > "$CACHE_FILE"
echo "  \"generated_at\": \"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\"," >> "$CACHE_FILE"
echo "  \"workflows\": [" >> "$CACHE_FILE"

first=true
for workflow in "${WORKFLOWS[@]}"; do
    if [[ "$first" == "false" ]]; then
        echo "," >> "$CACHE_FILE"
    fi
    first=false
    
    metrics=$(get_workflow_metrics "$workflow")
    echo "    $metrics" >> "$CACHE_FILE"
done

echo "" >> "$CACHE_FILE"
echo "  ]" >> "$CACHE_FILE"
echo "}" >> "$CACHE_FILE"

echo -e "\n${GREEN}Metrics extraction complete!${NC}\n"

# Output based on format
if [[ "$OUTPUT_FORMAT" == "--json" ]]; then
    cat "$CACHE_FILE"
elif [[ "$OUTPUT_FORMAT" == "--markdown" ]]; then
    echo "# Workflow Timing Benchmarks"
    echo ""
    echo "**Generated:** $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
    echo ""
    echo "| Workflow | Avg Duration | Min | Max | Success Rate | Runs | Last Run |"
    echo "|----------|--------------|-----|-----|--------------|------|----------|"
    
    jq -r '.workflows[] | [
        .workflow,
        (if .avg_duration > 0 then (.avg_duration | tonumber | (. / 60 | floor | tostring) + "m " + (. % 60 | tostring) + "s") else "N/A" end),
        (if .min_duration > 0 then (.min_duration | tonumber | (. / 60 | floor | tostring) + "m " + (. % 60 | tostring) + "s") else "N/A" end),
        (if .max_duration > 0 then (.max_duration | tonumber | (. / 60 | floor | tostring) + "m " + (. % 60 | tostring) + "s") else "N/A" end),
        (.success_rate | tostring + "%"),
        (.runs | tostring),
        .last_run
    ] | "| " + (.[0]) + " | " + (.[1]) + " | " + (.[2]) + " | " + (.[3]) + " | " + (.[4]) + " | " + (.[5]) + " | " + (.[6]) + " |"' "$CACHE_FILE"
    
    echo ""
    echo "**Cache file:** \`$CACHE_FILE\` (valid for $((CACHE_MAX_AGE / 60)) minutes)"
else
    cat "$CACHE_FILE"
fi
