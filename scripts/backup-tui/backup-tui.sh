#!/bin/sh
# POSIX-compliant launcher script for OpenEMR Backup Manager TUI
# This script locates and executes the backup-tui binary

# Script directory (POSIX-compliant way to get script directory)
SCRIPT_DIR=$(dirname "$0")
SCRIPT_DIR=$(cd "$SCRIPT_DIR" && pwd)

# Binary name
BINARY_NAME="backup-tui"

# Default binary location (in the same directory as this script)
DEFAULT_BINARY="$SCRIPT_DIR/$BINARY_NAME"

# Binary in bin subdirectory
BIN_DIR_BINARY="$SCRIPT_DIR/bin/$BINARY_NAME"

# Alternative locations to check
ALTERNATIVE_PATHS="/usr/local/bin/$BINARY_NAME /opt/openemr/bin/$BINARY_NAME"

# Find the binary
find_binary() {
    # Check default location first (same directory as script)
    if [ -x "$DEFAULT_BINARY" ]; then
        echo "$DEFAULT_BINARY"
        return 0
    fi

    # Check bin subdirectory
    if [ -x "$BIN_DIR_BINARY" ]; then
        echo "$BIN_DIR_BINARY"
        return 0
    fi

    # Check alternative paths
    for path in $ALTERNATIVE_PATHS; do
        if [ -x "$path" ]; then
            echo "$path"
            return 0
        fi
    done

    # Check if binary is in PATH
    if command -v "$BINARY_NAME" >/dev/null 2>&1; then
        echo "$BINARY_NAME"
        return 0
    fi

    return 1
}

# Error handling
error_exit() {
    echo "Error: $1" >&2
    exit 1
}

# Find the binary
BINARY=$(find_binary)

if [ -z "$BINARY" ] || [ ! -x "$BINARY" ]; then
    error_exit "Cannot find $BINARY_NAME binary.

Please ensure the binary is:
  1. In the same directory as this script: $DEFAULT_BINARY
  2. In the bin subdirectory: $BIN_DIR_BINARY
  3. In one of these locations: $ALTERNATIVE_PATHS
  4. In your PATH

To build the binary:
  cd $SCRIPT_DIR
  make build
  # or
  go build -o $BINARY_NAME .
  # or (to build in bin directory)
  go build -o bin/$BINARY_NAME .
"
fi

# Execute the binary with all passed arguments
# Capture exit code to provide helpful error messages if the TUI fails to start
if ! "$BINARY" "$@"; then
	EXIT_CODE=$?
	echo "Error: The backup TUI exited with code $EXIT_CODE." >&2
	echo "" >&2
        echo "Common issues:" >&2
        echo "  - AWS credentials required: Configure using one of:" >&2
        echo "      * Environment variables: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY" >&2
        echo "      * AWS credentials file: ~/.aws/credentials" >&2
        echo "      * AWS CLI: run 'aws configure' to set up credentials" >&2
        echo "      * IAM role: if running on EC2/ECS, ensure instance/task role has permissions" >&2
        echo "  - No CloudFormation stack found (specify with -stack flag if auto-discovery fails)" >&2
        echo "  - Network connectivity issues" >&2
        echo "  - Invalid AWS region specified" >&2
	exit $EXIT_CODE
fi

