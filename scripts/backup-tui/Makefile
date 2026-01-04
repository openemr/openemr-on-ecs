# Makefile for OpenEMR Backup Manager TUI

# Binary name
BINARY_NAME=backup-tui

# Build directory
BUILD_DIR=bin

# Go parameters
GOCMD=go
GOBUILD=$(GOCMD) build
GOCLEAN=$(GOCMD) clean
GOTEST=$(GOCMD) test
GOGET=$(GOCMD) get
GOMOD=$(GOCMD) mod

# Build flags
LDFLAGS=-ldflags "-s -w"
BUILD_FLAGS=-trimpath $(LDFLAGS)

# Default target
.DEFAULT_GOAL := build

# Build for current platform
.PHONY: build
build:
	@echo "Building $(BINARY_NAME)..."
	@mkdir -p $(BUILD_DIR)
	$(GOBUILD) $(BUILD_FLAGS) -o $(BUILD_DIR)/$(BINARY_NAME) .
	@echo "Build complete: $(BUILD_DIR)/$(BINARY_NAME)"

# Build for multiple platforms
.PHONY: build-all
build-all: build-linux build-darwin build-windows
	@echo "Builds complete for all platforms"

# Build for Linux (amd64)
.PHONY: build-linux
build-linux:
	@echo "Building for Linux (amd64)..."
	@mkdir -p $(BUILD_DIR)
	GOOS=linux GOARCH=amd64 $(GOBUILD) $(BUILD_FLAGS) -o $(BUILD_DIR)/$(BINARY_NAME)-linux-amd64 .
	@echo "Linux build complete: $(BUILD_DIR)/$(BINARY_NAME)-linux-amd64"

# Build for macOS (amd64)
.PHONY: build-darwin-amd64
build-darwin-amd64:
	@echo "Building for macOS (amd64)..."
	@mkdir -p $(BUILD_DIR)
	GOOS=darwin GOARCH=amd64 $(GOBUILD) $(BUILD_FLAGS) -o $(BUILD_DIR)/$(BINARY_NAME)-darwin-amd64 .
	@echo "macOS (amd64) build complete: $(BUILD_DIR)/$(BINARY_NAME)-darwin-amd64"

# Build for macOS (arm64)
.PHONY: build-darwin-arm64
build-darwin-arm64:
	@echo "Building for macOS (arm64)..."
	@mkdir -p $(BUILD_DIR)
	GOOS=darwin GOARCH=arm64 $(GOBUILD) $(BUILD_FLAGS) -o $(BUILD_DIR)/$(BINARY_NAME)-darwin-arm64 .
	@echo "macOS (arm64) build complete: $(BUILD_DIR)/$(BINARY_NAME)-darwin-arm64"

# Build for macOS (both architectures)
.PHONY: build-darwin
build-darwin: build-darwin-amd64 build-darwin-arm64

# Build for Windows (amd64)
.PHONY: build-windows
build-windows:
	@echo "Building for Windows (amd64)..."
	@mkdir -p $(BUILD_DIR)
	GOOS=windows GOARCH=amd64 $(GOBUILD) $(BUILD_FLAGS) -o $(BUILD_DIR)/$(BINARY_NAME)-windows-amd64.exe .
	@echo "Windows build complete: $(BUILD_DIR)/$(BINARY_NAME)-windows-amd64.exe"

# Clean build artifacts
.PHONY: clean
clean:
	@echo "Cleaning..."
	$(GOCLEAN)
	rm -rf $(BUILD_DIR)
	@echo "Clean complete"

# Run tests
.PHONY: test
test:
	@echo "Running tests..."
	$(GOTEST) -v ./...

# Download dependencies
.PHONY: deps
deps:
	@echo "Downloading dependencies..."
	$(GOMOD) download
	$(GOMOD) tidy
	@echo "Dependencies updated"

# Format code
.PHONY: fmt
fmt:
	@echo "Formatting code..."
	$(GOCMD) fmt ./...
	@echo "Formatting complete"

# Run linter (requires golangci-lint)
.PHONY: lint
lint:
	@echo "Linting..."
	@if command -v golangci-lint >/dev/null 2>&1; then \
		golangci-lint run; \
	else \
		echo "golangci-lint not installed. Install with: go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest"; \
	fi

# Install the binary to $GOPATH/bin or $GOBIN
.PHONY: install
install: build
	@echo "Installing $(BINARY_NAME)..."
	$(GOCMD) install $(BUILD_FLAGS) .
	@echo "Installation complete"

# Show help
.PHONY: help
help:
	@echo "OpenEMR Backup Manager TUI - Makefile"
	@echo ""
	@echo "Available targets:"
	@echo "  build              - Build for current platform (default)"
	@echo "  build-all          - Build for Linux, macOS, and Windows"
	@echo "  build-linux        - Build for Linux (amd64)"
	@echo "  build-darwin       - Build for macOS (amd64 and arm64)"
	@echo "  build-darwin-amd64 - Build for macOS (amd64)"
	@echo "  build-darwin-arm64 - Build for macOS (arm64)"
	@echo "  build-windows      - Build for Windows (amd64)"
	@echo "  clean              - Remove build artifacts"
	@echo "  test               - Run tests"
	@echo "  deps               - Download and tidy dependencies"
	@echo "  fmt                - Format code"
	@echo "  lint               - Run linter (requires golangci-lint)"
	@echo "  install            - Install binary to $GOPATH/bin"
	@echo "  help               - Show this help message"
	@echo ""
	@echo "Examples:"
	@echo "  make build         # Build for current platform"
	@echo "  make build-all     # Build for all platforms"
	@echo "  make clean         # Clean build artifacts"

