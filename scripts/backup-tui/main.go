// Package main provides the entry point for the OpenEMR Backup Manager TUI.
// This application provides an interactive terminal interface for managing
// and restoring AWS backups for OpenEMR infrastructure.
//
// The TUI is built using Bubbletea and Lipgloss, providing a modern,
// keyboard-driven interface for backup management operations.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/openemr/openemr-on-ecs/scripts/backup-tui/internal/app"
	"github.com/openemr/openemr-on-ecs/scripts/backup-tui/internal/aws"
)

func main() {
	// Parse command-line arguments
	var (
		stackName    = flag.String("stack", "", "CloudFormation stack name (auto-discovered if not provided)")
		vaultName    = flag.String("vault", "", "Backup vault name (auto-discovered if not provided)")
		region       = flag.String("region", "us-west-2", "AWS region")
		resourceType = flag.String("type", "", "Resource type to filter (RDS or EFS, empty for all)")
		showHelp     = flag.Bool("help", false, "Show help message")
	)
	flag.Parse()

	// Show help and exit if requested
	if *showHelp {
		printHelp()
		os.Exit(0)
	}

	// Create context with cancellation for graceful shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle interrupt signals (Ctrl+C, SIGTERM) for graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigChan
		cancel()
	}()

	// Auto-discover stack name if not provided
	finalStackName := *stackName
	if finalStackName == "" {
		// Create a temporary AWS client for stack discovery
		backupClient, err := aws.NewBackupClient(ctx, *region)
		if err != nil {
			errMsg := err.Error()
			fmt.Fprintf(os.Stderr, "Error: Failed to create AWS client: %v\n", err)
			if strings.Contains(errMsg, "credentials") || strings.Contains(errMsg, "NoCredentialProviders") ||
				strings.Contains(errMsg, "EC2RoleRequestError") || strings.Contains(errMsg, "SharedCredsLoad") {
				fmt.Fprintf(os.Stderr, "\nAWS credentials are required to launch the TUI.\n")
				fmt.Fprintf(os.Stderr, "Configure AWS credentials using one of:\n")
				fmt.Fprintf(os.Stderr, "  - Environment variables: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY\n")
				fmt.Fprintf(os.Stderr, "  - AWS credentials file: ~/.aws/credentials (run 'aws configure')\n")
				fmt.Fprintf(os.Stderr, "  - IAM role: if running on EC2/ECS, ensure instance/task role has permissions\n")
			} else {
				fmt.Fprintf(os.Stderr, "Please ensure AWS credentials are configured.\n")
			}
			cancel() // Cancel context before exiting
			//nolint:gocritic // exitAfterDefer: we explicitly call cancel() before os.Exit
			os.Exit(1)
		}

		discoveredStack, err := backupClient.DiscoverStackName(ctx)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error: Failed to auto-discover CloudFormation stack: %v\n", err)
			fmt.Fprintf(os.Stderr, "\nPlease specify a stack name using the -stack flag:\n")
			fmt.Fprintf(os.Stderr, "  %s -stack YourStackName\n", os.Args[0])
			cancel() // Cancel context before exiting
			//nolint:gocritic // exitAfterDefer: we explicitly call cancel() before os.Exit
			os.Exit(1)
		}
		finalStackName = discoveredStack
		fmt.Fprintf(os.Stderr, "Auto-discovered stack: %s\n", finalStackName)
	}

	// Initialize the application model with configuration
	model := app.NewModel(ctx, finalStackName, *vaultName, *region, *resourceType)

	// Create and run the Bubbletea program
	// WithAltScreen enables full-screen terminal mode for better UI experience
	// WithMouseCellMotion enables mouse support (optional but enhances UX)
	p := tea.NewProgram(model, tea.WithAltScreen(), tea.WithMouseCellMotion())
	if _, err := p.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "Error running application: %v\n", err)
		os.Exit(1)
	}
}

// printHelp displays usage information and exits.
// This provides users with information about available command-line options,
// examples, and environment variables that can be used to configure the application.
func printHelp() {
	fmt.Print(`OpenEMR Backup Manager - Interactive TUI for managing AWS backups

Usage:
  backup-tui [options]

Options:
  -stack string     CloudFormation stack name (auto-discovered if not provided)
  -vault string     Backup vault name (auto-discovered if not provided)
  -region string    AWS region (default: "us-west-2")
  -type string      Resource type to filter (RDS or EFS, empty for all)
  -help             Show this help message

Examples:
  # Launch with auto-discovery (recommended)
  backup-tui

  # Specify stack explicitly
  backup-tui -stack MyStack -region us-east-1

  # Filter by resource type
  backup-tui -type RDS

Environment Variables (Required):
  AWS_ACCESS_KEY_ID          AWS access key (REQUIRED)
  AWS_SECRET_ACCESS_KEY      AWS secret key (REQUIRED)
  AWS_SESSION_TOKEN          AWS session token (for temporary credentials)
  AWS_DEFAULT_REGION         AWS region (overridden by -region flag)

Note: AWS credentials are REQUIRED to use this application. Configure them using:
  - Environment variables: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY
  - AWS credentials file: ~/.aws/credentials (run 'aws configure')
  - IAM role: if running on EC2/ECS, ensure instance/task role has permissions

Controls:
  ↑/↓            Navigate backup list
  Enter          Select backup / Confirm action
  Esc/q          Quit application
  r              Refresh backup list
  /              Search/filter backups
  ?              Show help

Features:
  • Browse backups interactively
  • View backup details (size, creation date, status)
  • Initiate restore operations
  • Monitor restore progress
  • Filter by resource type (RDS/EFS)
`)
}
