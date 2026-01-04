// Package app provides the main application model and business logic for the backup TUI.
// This file implements the Bubbletea Model interface, managing application state,
// user interactions, AWS operations, and UI rendering coordination.
package app

import (
	"context"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/openemr/openemr-on-ecs/scripts/backup-tui/internal/aws"
	"github.com/openemr/openemr-on-ecs/scripts/backup-tui/internal/ui"
)

// Model represents the main application state and implements the Bubbletea Model interface.
// It manages the application lifecycle, coordinates between UI components and AWS services,
// handles user input, and orchestrates backup operations.
//
// The model follows the Bubbletea architecture:
// - State is immutable; Update() returns a new model state
// - Commands are used for async operations (AWS API calls)
// - Messages are used to communicate results back to the model
type Model struct {
	// Configuration: User-provided or discovered configuration
	ctx          context.Context // Context for cancellation and timeout control
	stackName    string          // CloudFormation stack name (e.g., "OpenemrEcsStack")
	vaultName    string          // Backup vault name (auto-discovered if not provided)
	region       string          // AWS region (e.g., "us-west-2")
	resourceType string          // Optional filter: "RDS", "EFS", or "" for all

	// UI state: Current view and component state
	state       state          // Current application state (loading, list, detail, help, error)
	listModel   ui.ListModel   // List view component for displaying backups
	detailModel ui.DetailModel // Detail view component for backup information
	helpModel   ui.HelpModel   // Help screen component
	statusMsg   string         // Status message displayed in status bar
	err         error          // Error state (nil when no error)

	// AWS clients: Service clients for AWS operations
	backupClient *aws.BackupClient // AWS Backup service client and related services

	// Data: Application data and selections
	backups         []aws.RecoveryPoint // Cached list of recovery points
	selectedIdx     int                 // Index of currently selected backup in backups slice
	vaultDiscovered bool                // Whether vault discovery has completed
}

// state represents the current application view/state.
// The application transitions between these states based on user actions and data loading.
type state int

const (
	stateLoading state = iota // Initial state: discovering vault and loading backups
	stateList                 // Main state: displaying list of backups
	stateDetail               // Detail state: showing details of selected backup
	stateHelp                 // Help state: displaying help screen
	stateError                // Error state: displaying error message
)

// NewModel creates and initializes a new application Model.
// This function sets up the initial state, initializes AWS clients, and prepares
// UI components for use.
//
// Parameters:
//   - ctx: Context for cancellation and timeout control (used for AWS API calls)
//   - stackName: CloudFormation stack name for vault discovery
//   - vaultName: Backup vault name (empty string triggers auto-discovery)
//   - region: AWS region for API calls
//   - resourceType: Optional resource type filter ("RDS", "EFS", or "")
//
// Returns:
//   - *Model: Initialized model (may be in error state if AWS client creation fails)
//
// Note: If AWS client initialization fails, the model is placed in stateError
// with the error stored in m.err. The model can still be used (to display the error).
func NewModel(ctx context.Context, stackName, vaultName, region, resourceType string) *Model {
	m := &Model{
		ctx:          ctx,
		stackName:    stackName,
		vaultName:    vaultName,
		region:       region,
		resourceType: resourceType,
		state:        stateLoading, // Start in loading state
		selectedIdx:  0,
	}

	// Initialize AWS clients (required for all operations)
	var err error
	m.backupClient, err = aws.NewBackupClient(ctx, region)
	if err != nil {
		m.err = fmt.Errorf("failed to create backup client: %w", err)
		m.state = stateError // Set error state immediately
		return m
	}

	// Initialize UI components (these are stateless and don't need async setup)
	m.listModel = ui.NewListModel()
	m.detailModel = ui.DetailModel{}
	m.helpModel = ui.HelpModel{}

	return m
}

// Init initializes the model and returns initial commands to execute.
// This is called by Bubbletea when the program starts, and should return
// commands that perform async initialization (e.g., AWS API calls).
//
// Returns:
//   - tea.Cmd: Batch command that executes vault discovery and backup loading in parallel
//
// Note: These commands run concurrently. The model will receive messages when
// they complete, triggering state transitions.
func (m *Model) Init() tea.Cmd {
	// Only start vault discovery if vault name not provided
	// Backup loading will be triggered after vault discovery completes
	if m.vaultName == "" {
		return m.discoverVault() // Discover backup vault first
	}
	// If vault name already provided, load backups immediately
	return m.loadBackups()
}

// Update handles messages and updates the model state.
// This is the core of the Bubbletea architecture: all user input, async operations,
// and system events are delivered as messages, and Update() processes them to
// produce a new model state and optional commands.
//
// Parameters:
//   - msg: Message from Bubbletea (tea.KeyMsg for keyboard input, custom messages for async results)
//
// Returns:
//   - tea.Model: Updated model (returns self for type compatibility)
//   - tea.Cmd: Commands to execute (nil or batch of commands)
//
// Message Types Handled:
//   - tea.KeyMsg: Keyboard input (navigation, actions, quit)
//   - vaultDiscoveredMsg: Vault discovery completion
//   - backupsLoadedMsg: Backup list loading completion
//   - restoreInitiatedMsg: Restore job initiation completion
//   - error: Generic error message
func (m *Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmd tea.Cmd
	var cmds []tea.Cmd

	switch msg := msg.(type) {
	case tea.KeyMsg:
		// Handle global keyboard shortcuts (work in all states)
		switch msg.String() {
		case "q", "esc", "ctrl+c":
			// Quit: exit help screen or quit application
			if m.state == stateHelp {
				m.state = stateList // Exit help, return to list
				return m, nil
			}
			return m, tea.Quit // Quit application
		case "?":
			// Toggle help screen (only from list or detail views)
			if m.state == stateList || m.state == stateDetail {
				m.state = stateHelp
				return m, nil
			}
		case "r":
			// Refresh: reload backup list (only from list view)
			if m.state == stateList {
				cmds = append(cmds, m.loadBackups())
			}
		}

		// Handle state-specific keyboard input
		switch m.state {
		case stateList:
			// List view: navigation and selection
			if msg.String() == "enter" {
				// Select backup: transition to detail view
				if len(m.backups) > 0 && m.listModel.SelectedIndex() < len(m.backups) {
					m.selectedIdx = m.listModel.SelectedIndex()
					m.detailModel.SetRecoveryPoint(&m.backups[m.selectedIdx])
					m.state = stateDetail
				}
			}
			// Delegate navigation (up/down) to list model
			m.listModel, cmd = m.listModel.Update(msg)
			cmds = append(cmds, cmd)
			// Keep selectedIdx in sync with list model cursor
			m.selectedIdx = m.listModel.SelectedIndex()

		case stateDetail:
			// Detail view: actions and navigation
			keyStr := msg.String()
			// Handle back navigation with multiple key options
			if keyStr == "backspace" || keyStr == "b" || keyStr == "left" || msg.Type == tea.KeyLeft {
				// Go back: return to list view
				m.state = stateList
			} else if keyStr == "enter" || msg.Type == tea.KeyEnter {
				// Initiate restore: start restore job
				cmds = append(cmds, m.initiateRestore())
			}
			// Delegate to detail model (handles window resize, etc.)
			m.detailModel, cmd = m.detailModel.Update(msg)
			cmds = append(cmds, cmd)

		case stateHelp:
			// Help view: delegate to help model (handles window resize)
			m.helpModel, cmd = m.helpModel.Update(msg)
			cmds = append(cmds, cmd)
		}

	// Handle async operation results
	case vaultDiscoveredMsg:
		// Vault discovery completed
		m.vaultName = msg.vaultName
		m.vaultDiscovered = true
		if !msg.success {
			m.err = fmt.Errorf("failed to discover backup vault: %w", msg.err)
			m.state = stateError
		} else if msg.vaultName != "" {
			// If vault was discovered successfully, now load backups
			// The vault name is now set in m.vaultName, so loadBackups() will use it
			cmds = append(cmds, m.loadBackups())
		}

	case backupsLoadedMsg:
		// Backup list loading completed
		m.backups = msg.backups
		if msg.err != nil {
			m.err = msg.err
			m.state = stateError
		} else {
			m.state = stateList                            // Transition to list view
			m.listModel.SetItems(m.formatBackupsForList()) // Update list component
			// Clear any previous status messages when backups are loaded
			m.statusMsg = ""
		}

	case restoreInitiatedMsg:
		// Restore job initiation completed
		m.statusMsg = fmt.Sprintf("Restore job started: %s", msg.jobID)
		if msg.err != nil {
			m.err = msg.err
			m.state = stateError
		}
		// Note: Restore job runs asynchronously; user should monitor via AWS console

	case error:
		// Generic error message (catch-all for unexpected errors)
		m.err = msg
		m.state = stateError
	}

	// Execute all collected commands in parallel
	return m, tea.Batch(cmds...)
}

// View renders the current application state as a string.
// This is called by Bubbletea to get the string representation of the UI
// for display in the terminal. The view changes based on the current state.
//
// Returns:
//   - string: Rendered UI (includes header, main content, and status bar)
func (m *Model) View() string {
	// Handle error state: show error message
	if m.state == stateError {
		return m.renderError()
	}

	// Handle loading state: show loading indicator
	if m.state == stateLoading {
		return m.renderLoading()
	}

	// Render state-specific views
	var view string
	switch m.state {
	case stateList:
		view = m.renderList() // List view with header and backup list
	case stateDetail:
		view = m.renderDetail() // Detail view with header and backup details
	case stateHelp:
		view = m.renderHelp() // Help view with header and help content
	default:
		view = "Unknown state" // Fallback (should never occur)
	}

	// Add status bar at the bottom (shows backup count, status messages, etc.)
	statusBar := m.renderStatusBar()
	return lipgloss.JoinVertical(lipgloss.Left, view, statusBar)
}

// renderLoading renders the loading state view.
// Displayed when the application is discovering the vault or loading backups.
//
// Returns:
//   - string: Loading message with styled border
func (m *Model) renderLoading() string {
	return lipgloss.NewStyle().
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.AdaptiveColor{
			Light: "62",
			Dark:  "63",
		}).
		Foreground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "252",
		}).
		Render("⏳ Loading backups...")
}

// renderError renders the error state view.
// Displayed when an error occurs (AWS API failure, invalid configuration, etc.).
//
// Returns:
//   - string: Error message with red styling and quit instructions
func (m *Model) renderError() string {
	errorStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("196")). // Red text
		Bold(true).
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("196")). // Red border
		BorderTop(true).
		BorderBottom(true).
		BorderLeft(true).
		BorderRight(true)

	errorDetails := fmt.Sprintf("✗ Error: %v", m.err)

	// Add helpful context based on error type
	hint := ""
	errStr := m.err.Error()
	switch {
	case strings.Contains(errStr, "backup vault not found"):
		hint = "\n\nTip: Ensure a backup vault exists for your stack.\n     You can also specify a vault name with the -vault flag."
	case strings.Contains(errStr, "CloudFormation stack"):
		hint = "\n\nTip: Verify your AWS credentials and region are correct.\n     You can specify a stack name with the -stack flag."
	case strings.Contains(errStr, "credentials") || strings.Contains(errStr, "authentication") ||
		strings.Contains(errStr, "NoCredentialProviders") || strings.Contains(errStr, "EC2RoleRequestError") ||
		strings.Contains(errStr, "SharedCredsLoad"):
		hint = "\n\nAWS credentials are required to use this application.\n" +
			"Configure AWS credentials using one of:\n" +
			"  - Environment variables: AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY\n" +
			"  - AWS credentials file: ~/.aws/credentials (run 'aws configure')\n" +
			"  - IAM role: if running on EC2/ECS, ensure instance/task role has permissions"
	case strings.Contains(errStr, "discover"):
		hint = "\n\nTip: Check that your CloudFormation stack exists and has a backup vault.\n     You can specify the vault name directly with the -vault flag."
	}

	msg := fmt.Sprintf("%s%s\n\nPress 'q' to quit", errorDetails, hint)
	return errorStyle.Render(msg)
}

// renderList renders the list view.
// Combines the header with the list component view.
//
// Returns:
//   - string: Rendered list view with header
func (m *Model) renderList() string {
	header := m.renderHeader()
	list := m.listModel.View()
	return lipgloss.JoinVertical(lipgloss.Left, header, list)
}

// renderDetail renders the detail view.
// Combines the header with the detail component view.
//
// Returns:
//   - string: Rendered detail view with header
func (m *Model) renderDetail() string {
	header := m.renderHeader()
	detail := m.detailModel.View()
	return lipgloss.JoinVertical(lipgloss.Left, header, detail)
}

// renderHelp renders the help view.
// Combines the header with the help component view.
//
// Returns:
//   - string: Rendered help view with header
func (m *Model) renderHelp() string {
	header := m.renderHeader()
	help := m.helpModel.View()
	return lipgloss.JoinVertical(lipgloss.Left, header, help)
}

// renderHeader renders the application header.
// The header displays the application title and contextual information
// (vault name, region, resource type filter) in a clean, functional layout.
//
// Returns:
//   - string: Rendered header with title and info
func (m *Model) renderHeader() string {
	// Title section
	title := "OpenEMR Backup Manager"

	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.AdaptiveColor{
			Light: "62",
			Dark:  "63",
		}).
		MarginBottom(1)

	titleSection := titleStyle.Render(title)

	// Info section: vault name, region, optional resource type filter
	vaultInfo := fmt.Sprintf("Vault: %s", m.vaultName)
	if !m.vaultDiscovered {
		vaultInfo = "Discovering vault..."
	}
	regionInfo := fmt.Sprintf("Region: %s", m.region)

	infoStyle := lipgloss.NewStyle().
		Foreground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "248",
		}).
		MarginBottom(1)

	infoSection := lipgloss.JoinHorizontal(
		lipgloss.Left,
		infoStyle.Render(vaultInfo),
		"  ",
		infoStyle.Render(regionInfo),
	)

	// Add resource type filter if specified
	if m.resourceType != "" {
		filter := infoStyle.Render(fmt.Sprintf("Filter: %s", m.resourceType))
		infoSection = lipgloss.JoinHorizontal(lipgloss.Left, infoSection, "  ", filter)
	}

	// Combine title with info
	header := lipgloss.JoinVertical(
		lipgloss.Left,
		titleSection,
		infoSection,
	)

	return header
}

// renderStatusBar renders the status bar at the bottom of the screen.
// Displays backup count, status messages (e.g., restore job started), or
// "no backups found" message with appropriate styling and icons.
//
// Returns:
//   - string: Rendered status bar with border
func (m *Model) renderStatusBar() string {
	var status string
	var statusStyle lipgloss.Style

	switch {
	case m.statusMsg != "":
		// Show status message (e.g., "Restore job started: job-123")
		status = m.statusMsg
		statusStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("114")) // Green for success messages
	case len(m.backups) > 0:
		// Show backup count
		status = fmt.Sprintf("✓ %d backup(s) found", len(m.backups))
		statusStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("114")) // Green
	default:
		// Show "no backups" message
		// This can happen if: vault exists but is empty, all backups filtered out, or API issue
		if m.vaultDiscovered && m.vaultName != "" {
			status = fmt.Sprintf("○ No backups found in vault: %s", m.vaultName)
		} else {
			status = "○ No backups found"
		}
		statusStyle = lipgloss.NewStyle().Foreground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "248",
		})
	}

	return statusStyle.
		Padding(0, 1).
		Border(lipgloss.RoundedBorder()).
		BorderTop(true).
		BorderForeground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "238",
		}).
		Render(status)
}

// formatBackupsForList formats the backups slice into strings for display in the list.
// Each backup is formatted as: "Type | Resource ID | Creation Date | Size"
//
// Returns:
//   - []string: Formatted backup strings for the list component
func (m *Model) formatBackupsForList() []string {
	items := make([]string, len(m.backups))
	for i, backup := range m.backups {
		date := backup.CreationDate.Format("2006-01-02 15:04:05")
		size := formatBytes(backup.BackupSizeInBytes)
		items[i] = fmt.Sprintf("%s | %s | %s | %s", backup.ResourceType, backup.ResourceID, date, size)
	}
	return items
}

// formatBytes formats a byte count into a human-readable string.
// Converts bytes to KB, MB, GB, TB, etc. with one decimal place.
//
// Parameters:
//   - bytes: Size in bytes
//
// Returns:
//   - string: Formatted size (e.g., "1.5 GB", "250.3 MB")
//
// Example:
//
//	formatBytes(1610612736) // Returns: "1.5 GB"
func formatBytes(bytes int64) string {
	const unit = 1024
	if bytes < unit {
		return fmt.Sprintf("%d B", bytes)
	}
	div, exp := int64(unit), 0
	for n := bytes / unit; n >= unit; n /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(bytes)/float64(div), "KMGTPE"[exp])
}

// Messages
// These message types are used to communicate async operation results
// from commands back to the model's Update() method.

// vaultDiscoveredMsg is sent when vault discovery completes.
type vaultDiscoveredMsg struct {
	vaultName string // Discovered vault name (empty if discovery failed)
	success   bool   // Whether discovery succeeded
	err       error  // Error if discovery failed (nil if success)
}

// backupsLoadedMsg is sent when backup list loading completes.
type backupsLoadedMsg struct {
	backups []aws.RecoveryPoint // Loaded recovery points (empty slice if error)
	err     error               // Error if loading failed (nil if success)
}

// restoreInitiatedMsg is sent when restore job initiation completes.
type restoreInitiatedMsg struct {
	jobID string // Restore job ID if successful (empty if error)
	err   error  // Error if initiation failed (nil if success)
}

// Commands
// These functions return Bubbletea commands that perform async operations.
// Commands run in goroutines and send messages back to the model when complete.

// discoverVault returns a command that discovers the backup vault.
// If vaultName is already set, returns immediately with success.
// Otherwise, queries AWS Backup API to find a vault matching the stack name.
//
// Returns:
//   - tea.Cmd: Command that sends vaultDiscoveredMsg when complete
func (m *Model) discoverVault() tea.Cmd {
	return func() tea.Msg {
		// If vault name already provided, no discovery needed
		if m.vaultName != "" {
			return vaultDiscoveredMsg{vaultName: m.vaultName, success: true}
		}

		// Discover vault by searching for one matching the stack name
		vaultName, err := m.backupClient.DiscoverVaultByStack(m.ctx, m.stackName)
		if err != nil {
			return vaultDiscoveredMsg{success: false, err: err}
		}

		return vaultDiscoveredMsg{vaultName: vaultName, success: true}
	}
}

// loadBackups returns a command that loads the backup list from AWS.
// Requires vaultName to be set (should be set after vault discovery completes).
// Filters backups by resourceType if specified.
//
// This function accepts an optional vaultName parameter. If provided, it uses that
// instead of checking the model state (useful when called right after vault discovery).
//
// Returns:
//   - tea.Cmd: Command that sends backupsLoadedMsg when complete
func (m *Model) loadBackups() tea.Cmd {
	// Capture the current vault name and resource type when the command is created
	// This ensures we use the correct values even if the command executes asynchronously
	vaultName := m.vaultName
	resourceType := m.resourceType
	return func() tea.Msg {
		// Use the captured vault name, or fall back to checking model state
		if vaultName == "" {
			// If vault name wasn't captured, check model state
			if !m.vaultDiscovered {
				if m.err != nil {
					return backupsLoadedMsg{err: fmt.Errorf("backup vault discovery failed: %w", m.err)}
				}
				return backupsLoadedMsg{err: fmt.Errorf("backup vault discovery in progress")}
			}
			vaultName = m.vaultName
			if vaultName == "" {
				return backupsLoadedMsg{err: fmt.Errorf("backup vault name is empty")}
			}
		}

		// Use captured resource type or fall back to model state
		if resourceType == "" {
			resourceType = m.resourceType
		}

		// Load recovery points from the vault
		// Note: Empty vault name should be caught above, but double-check for safety
		if vaultName == "" {
			return backupsLoadedMsg{err: fmt.Errorf("vault name is empty - cannot list recovery points")}
		}

		backups, err := m.backupClient.ListRecoveryPoints(m.ctx, vaultName, resourceType)
		if err != nil {
			return backupsLoadedMsg{err: fmt.Errorf("failed to list recovery points from vault %s: %w", vaultName, err)}
		}

		// Return backups (may be empty if no backups exist in the vault)
		// If backups is empty but no error, the vault exists but has no recovery points
		return backupsLoadedMsg{backups: backups}
	}
}

// initiateRestore returns a command that initiates a restore job.
// Uses the currently selected backup (m.selectedIdx) and queries CloudFormation/RDS
// to get necessary metadata for the restore operation.
//
// Returns:
//   - tea.Cmd: Command that sends restoreInitiatedMsg when complete
//
// Note: The restore job runs asynchronously in AWS. This command only initiates it.
// Users should monitor restore progress via the AWS console or AWS CLI.
func (m *Model) initiateRestore() tea.Cmd {
	return func() tea.Msg {
		// Validate selection
		if m.selectedIdx >= len(m.backups) {
			return restoreInitiatedMsg{err: fmt.Errorf("invalid backup selection")}
		}

		// Get selected backup and initiate restore
		backup := m.backups[m.selectedIdx]
		jobID, err := m.backupClient.StartRestoreJob(m.ctx, backup, m.stackName, m.vaultName)
		if err != nil {
			return restoreInitiatedMsg{err: err}
		}

		return restoreInitiatedMsg{jobID: jobID}
	}
}
