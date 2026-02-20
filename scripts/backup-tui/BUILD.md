# Building the Backup TUI

## Table of Contents

- [Quick Start](#quick-start)
  - [Using Make (Recommended)](#using-make-recommended)
  - [Using Go Directly](#using-go-directly)
  - [Using the Launcher Script](#using-the-launcher-script)
- [Requirements](#requirements)
- [Building](#building)
  - [Build Options](#build-options)
  - [Build Output](#build-output)
- [Development Notes](#development-notes)
- [Implementation Status](#implementation-status)
  - [✅ Completed Features](#-completed-features)
  - [🔄 Future Enhancements](#-future-enhancements)
- [Testing](#testing)
- [Installation](#installation)
  - [Local Installation](#local-installation)
  - [System Installation](#system-installation)

## Quick Start

### Using Make (Recommended)

```bash
cd scripts/backup-tui
make build
./bin/backup-tui
```

### Using Go Directly

```bash
cd scripts/backup-tui
go mod download
go build -o backup-tui .
./backup-tui
```

### Using the Launcher Script

```bash
cd scripts/backup-tui
make build
./backup-tui.sh -stack OpenemrEcsStack -region us-west-2
```

## Requirements

- Go 1.25 or later
- AWS credentials configured
- Make (optional, for using Makefile)

## Building

### Build Options

```bash
make build              # Build for current platform
make build-all          # Build for Linux, macOS, and Windows
make build-linux        # Build for Linux (amd64)
make build-darwin       # Build for macOS (amd64 and arm64)
make build-windows      # Build for Windows (amd64)
make clean              # Remove build artifacts
make help               # Show all available targets
```

### Build Output

Built binaries are placed in the `bin/` directory:
- `bin/backup-tui` - Current platform
- `bin/backup-tui-linux-amd64` - Linux
- `bin/backup-tui-darwin-amd64` - macOS (Intel)
- `bin/backup-tui-darwin-arm64` - macOS (Apple Silicon)
- `bin/backup-tui-windows-amd64.exe` - Windows

## Development Notes

This TUI is built with:
- **Bubbletea** - TUI framework
- **Lipgloss** - Styling
- **AWS SDK v2** - AWS service clients

The application structure:
- `main.go` - Entry point and CLI parsing
- `internal/app/model.go` - Main Bubbletea model and state management
- `internal/aws/` - AWS service clients (Backup, RDS, CloudFormation, STS)
- `internal/ui/` - UI components (list, detail, help views)

## Implementation Status

### ✅ Completed Features

1. **Full RDS Restore Metadata** - Queries CloudFormation for stack outputs and RDS API for subnet groups and security groups
2. **Backup Listing** - Lists all recovery points with filtering by resource type
3. **Interactive UI** - Full TUI with keyboard navigation
4. **Backup Vault Discovery** - Auto-discovers vault from stack name
5. **Restore Job Initiation** - Starts restore jobs with proper metadata

### 🔄 Future Enhancements

1. **Restore Progress Monitoring** - Real-time monitoring of restore job status with progress updates
2. **Search/Filter** - Interactive search within the backup list
3. **Error Recovery** - Better handling of AWS API errors with retries and exponential backoff
4. **Account ID Caching** - Cache account ID to avoid fetching on every startup
5. **Multi-selection** - Select multiple backups for batch operations
6. **Export Functionality** - Export backup list to CSV/JSON
7. **Restore Job History** - View and monitor past restore jobs

## Testing

To test the application, you'll need:
- A deployed OpenEMR stack with AWS Backup configured
- At least one backup in the backup vault

Run with default settings:
```bash
./backup-tui
```

Run with specific stack and region:
```bash
./backup-tui -stack OpenemrEcsStack -region us-west-2
```

Filter by resource type:
```bash
./backup-tui -type RDS
./backup-tui -type EFS
```

## Installation

### Local Installation

Build and install to your Go bin directory:
```bash
make install
```

Or manually:
```bash
go install .
```

### System Installation

Copy the binary to a system directory:
```bash
sudo cp bin/backup-tui /usr/local/bin/
```

Or use the launcher script which will find the binary automatically.

