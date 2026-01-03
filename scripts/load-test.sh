#!/bin/bash
# Load testing script for OpenEMR deployment
# Tests the deployed application's ability to handle concurrent requests

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
DURATION=${DURATION:-60}           # Test duration in seconds
CONCURRENT_USERS=${CONCURRENT_USERS:-50}  # Number of concurrent users
REQUESTS_PER_SECOND=${REQUESTS_PER_SECOND:-100}  # Target requests per second
WARMUP_TIME=${WARMUP_TIME:-10}     # Warmup period in seconds

log() {
    echo -e "${BLUE}[$(date +%H:%M:%S)]${NC} $1"
}

success() {
    echo -e "${GREEN}✓${NC} $1"
}

error() {
    echo -e "${RED}✗${NC} $1"
}

warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check for required tools
check_dependencies() {
    log "Checking dependencies..."
    
    if ! command -v python3 &> /dev/null; then
        error "Python 3 is required but not installed"
        exit 1
    fi
    
    # Check if requests library is available
    if ! python3 -c "import requests" 2>/dev/null; then
        warning "requests library not found, installing..."
        pip3 install requests --quiet || pip install requests --quiet
    fi
    
    success "Dependencies check passed"
}

# Get application URL from CloudFormation outputs
get_app_url() {
    local stack_name=${1:-"OpenemrEcsStack"}
    local region=${AWS_REGION:-$(aws configure get region || echo "us-east-1")}
    
    log "Getting application URL from stack: $stack_name"
    
    local url
    url=$(aws cloudformation describe-stacks \
        --stack-name "$stack_name" \
        --region "$region" \
        --query "Stacks[0].Outputs[?OutputKey==\`ApplicationURL\`].OutputValue" \
        --output text 2>/dev/null || echo "")
    
    if [ -z "$url" ] || [ "$url" == "None" ]; then
        # Try LoadBalancerDNS as fallback
        url=$(aws cloudformation describe-stacks \
            --stack-name "$stack_name" \
            --region "$region" \
            --query "Stacks[0].Outputs[?OutputKey==\`LoadBalancerDNS\`].OutputValue" \
            --output text 2>/dev/null || echo "")
        
        if [ -n "$url" ] && [ "$url" != "None" ]; then
            url="https://$url"
        fi
    fi
    
    if [ -z "$url" ] || [ "$url" == "None" ]; then
        error "Could not find application URL in stack outputs"
        error "Make sure the stack is deployed and has ApplicationURL or LoadBalancerDNS output"
        return 1
    fi
    
    echo "$url"
}

# Wait for application to be ready
wait_for_app() {
    local url=$1
    local max_attempts=30
    local attempt=0
    
    log "Waiting for application to be ready at $url"
    
    while [ $attempt -lt $max_attempts ]; do
        if curl -s -f -k --max-time 5 "$url" > /dev/null 2>&1; then
            success "Application is ready"
            return 0
        fi
        
        attempt=$((attempt + 1))
        echo -n "."
        sleep 2
    done
    
    echo ""
    error "Application did not become ready after $((max_attempts * 2)) seconds"
    return 1
}

# Run load test using Python
run_load_test() {
    local url=$1
    
    log "Starting load test..."
    log "Target URL: $url"
    log "Duration: ${DURATION}s"
    log "Concurrent users: $CONCURRENT_USERS"
    log "Target RPS: $REQUESTS_PER_SECOND"
    
    python3 << EOF
import requests
import time
import threading
import statistics
from datetime import datetime, timedelta
import sys

url = "$url"
duration = $DURATION
concurrent_users = $CONCURRENT_USERS
target_rps = $REQUESTS_PER_SECOND
warmup_time = $WARMUP_TIME

# Statistics tracking
response_times = []
errors = []
requests_made = 0
successful_requests = 0
failed_requests = 0
lock = threading.Lock()

def make_request():
    global requests_made, successful_requests, failed_requests
    try:
        start = time.time()
        response = requests.get(url, timeout=10, verify=False, allow_redirects=True)
        elapsed = time.time() - start
        
        with lock:
            requests_made += 1
            response_times.append(elapsed * 1000)  # Convert to milliseconds
            if 200 <= response.status_code < 400:
                successful_requests += 1
            else:
                failed_requests += 1
                errors.append(f"HTTP {response.status_code}")
    except Exception as e:
        with lock:
            requests_made += 1
            failed_requests += 1
            errors.append(str(e)[:50])

def worker():
    end_time = time.time() + duration
    while time.time() < end_time:
        make_request()
        # Rate limiting to target RPS
        time.sleep(1.0 / target_rps)

# Warmup phase
print(f"Warming up for {warmup_time} seconds...")
warmup_end = time.time() + warmup_time
while time.time() < warmup_end:
    make_request()
    time.sleep(0.1)

# Clear warmup stats
with lock:
    requests_made = 0
    successful_requests = 0
    failed_requests = 0
    response_times = []
    errors = []

# Start workers
print(f"Starting {concurrent_users} concurrent workers...")
workers = []
start_time = time.time()

for _ in range(concurrent_users):
    t = threading.Thread(target=worker)
    t.daemon = True
    t.start()
    workers.append(t)

# Wait for all workers
for t in workers:
    t.join()

end_time = time.time()
actual_duration = end_time - start_time

# Calculate statistics
with lock:
    total_requests = requests_made
    total_successful = successful_requests
    total_failed = failed_requests
    if response_times:
        avg_response_time = statistics.mean(response_times)
        median_response_time = statistics.median(response_times)
        p95_response_time = statistics.quantiles(response_times, n=20)[18] if len(response_times) > 20 else max(response_times)
        p99_response_time = statistics.quantiles(response_times, n=100)[98] if len(response_times) > 100 else max(response_times)
        min_response_time = min(response_times)
        max_response_time = max(response_times)
    else:
        avg_response_time = median_response_time = p95_response_time = p99_response_time = min_response_time = max_response_time = 0
    
    actual_rps = total_requests / actual_duration if actual_duration > 0 else 0
    success_rate = (total_successful / total_requests * 100) if total_requests > 0 else 0

# Print results
print("\n" + "="*60)
print("LOAD TEST RESULTS")
print("="*60)
print(f"Test Duration:        {actual_duration:.2f}s")
print(f"Total Requests:       {total_requests}")
print(f"Successful Requests:  {total_successful}")
print(f"Failed Requests:      {total_failed}")
print(f"Success Rate:         {success_rate:.2f}%")
print(f"Actual RPS:           {actual_rps:.2f}")
print(f"Target RPS:           {target_rps}")
print("\nResponse Times (ms):")
print(f"  Average:            {avg_response_time:.2f}")
print(f"  Median:             {median_response_time:.2f}")
print(f"  P95:                {p95_response_time:.2f}")
print(f"  P99:                {p99_response_time:.2f}")
print(f"  Min:                {min_response_time:.2f}")
print(f"  Max:                {max_response_time:.2f}")

if errors:
    print(f"\nError Summary (first 10):")
    error_counts = {}
    for e in errors[:10]:
        error_counts[e] = error_counts.get(e, 0) + 1
    for error, count in list(error_counts.items())[:5]:
        print(f"  {error}: {count}")

print("="*60)

# Determine if test passed
if success_rate >= 95.0 and actual_rps >= target_rps * 0.8:
    print("✓ Load test PASSED")
    sys.exit(0)
else:
    print("✗ Load test FAILED")
    if success_rate < 95.0:
        print(f"  - Success rate {success_rate:.2f}% is below 95%")
    if actual_rps < target_rps * 0.8:
        print(f"  - Actual RPS {actual_rps:.2f} is below 80% of target {target_rps}")
    sys.exit(1)
EOF
}

# Main execution
main() {
    echo "========================================="
    echo "OpenEMR Load Testing Script"
    echo "========================================="
    echo ""
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        error "AWS credentials not configured or invalid"
        exit 1
    fi
    
    check_dependencies
    
    # Get application URL
    if ! APP_URL=$(get_app_url "$@"); then
        exit 1
    fi
    
    success "Application URL: $APP_URL"
    
    # Wait for app to be ready
    if ! wait_for_app "$APP_URL"; then
        exit 1
    fi
    
    # Run load test
    if run_load_test "$APP_URL"; then
        success "Load test completed successfully"
        exit 0
    else
        error "Load test failed"
        exit 1
    fi
}

# Run main function
main "$@"

