// Package app provides the main application model and business logic for the backup TUI.
// This file implements the Bubbletea Model interface, managing application state,
// user interactions, AWS operations, and UI rendering coordination.
package app

import (
	"context"
	"fmt"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"charm.land/lipgloss/v2/compat"
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
	state       state          // Current application state (loading, list, detail, confirm, help, error, restoring)
	listModel   ui.ListModel   // List view component for displaying backups
	detailModel ui.DetailModel // Detail view component for backup information
	helpModel   ui.HelpModel   // Help screen component
	statusMsg   string         // Status message displayed in status bar
	err         error          // Error state (nil when no error)

	// Spinner state for loading animation
	spinnerFrame int

	// AWS clients: Service clients for AWS operations
	backupClient *aws.BackupClient // AWS Backup service client and related services

	// Data: Application data and selections
	backups         []aws.RecoveryPoint // Cached list of recovery points
	allBackups      []aws.RecoveryPoint // Unfiltered list (before in-app filter)
	selectedIdx     int                 // Index of currently selected backup in backups slice
	vaultDiscovered bool                // Whether vault discovery has completed

	// In-app filter state
	activeFilter filterMode // Current in-app resource type filter

	// Restore monitoring state
	restoreJobID  string    // Active restore job ID being monitored
	restoreStart  time.Time // When the restore was initiated
	restoreStatus *aws.RestoreJobStatus

	// Restore metadata preview
	restoreMetadata *aws.RestoreMetadata
}

// state represents the current application view/state.
// The application transitions between these states based on user actions and data loading.
type state int

const (
	stateLoading   state = iota // Initial state: discovering vault and loading backups
	stateList                   // Main state: displaying list of backups
	stateDetail                 // Detail state: showing details of selected backup
	stateConfirm                // Confirm state: confirming restore operation
	stateHelp                   // Help state: displaying help screen
	stateError                  // Error state: displaying error message
	stateRestoring              // Restore monitoring: polling restore job status
)

// filterMode represents the in-app resource type filter cycle.
type filterMode int

const (
	filterAll filterMode = iota
	filterRDS
	filterEFS
)

func (f filterMode) String() string {
	switch f {
	case filterRDS:
		return "RDS"
	case filterEFS:
		return "EFS"
	default:
		return "All"
	}
}

func (f filterMode) next() filterMode {
	switch f {
	case filterAll:
		return filterRDS
	case filterRDS:
		return filterEFS
	default:
		return filterAll
	}
}

var spinnerFrames = []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}

type spinnerTickMsg time.Time

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
	cmds := []tea.Cmd{m.tickSpinner()}
	if m.vaultName == "" {
		cmds = append(cmds, m.discoverVault())
	} else {
		cmds = append(cmds, m.loadBackups())
	}
	return tea.Batch(cmds...)
}

func (m *Model) tickSpinner() tea.Cmd {
	return tea.Tick(80*time.Millisecond, func(t time.Time) tea.Msg {
		return spinnerTickMsg(t)
	})
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
	case spinnerTickMsg:
		if m.state == stateLoading || m.state == stateRestoring {
			m.spinnerFrame = (m.spinnerFrame + 1) % len(spinnerFrames)
			cmds = append(cmds, m.tickSpinner())
		}

	case tea.KeyPressMsg:
		switch msg.String() {
		case "q", "ctrl+c":
			if m.state == stateHelp {
				m.state = stateList
				return m, nil
			}
			if m.state == stateConfirm {
				m.state = stateDetail
				return m, nil
			}
			if m.state == stateRestoring {
				m.state = stateList
				return m, nil
			}
			return m, tea.Quit
		case "esc":
			if m.state == stateHelp {
				m.state = stateList
				return m, nil
			}
			if m.state == stateConfirm {
				m.state = stateDetail
				return m, nil
			}
			if m.state == stateRestoring {
				m.state = stateList
				return m, nil
			}
			if m.state == stateDetail {
				m.state = stateList
				return m, nil
			}
			return m, tea.Quit
		case "?":
			if m.state == stateList || m.state == stateDetail {
				m.state = stateHelp
				return m, nil
			}
		case "r":
			if m.state == stateList {
				m.state = stateLoading
				cmds = append(cmds, m.loadBackups(), m.tickSpinner())
			}
		case "f":
			if m.state == stateList {
				m.cycleFilter()
			}
		}

		switch m.state {
		case stateList:
			if msg.String() == "enter" {
				if len(m.backups) > 0 && m.listModel.SelectedIndex() < len(m.backups) {
					m.selectedIdx = m.listModel.SelectedIndex()
					m.detailModel.SetRecoveryPoint(&m.backups[m.selectedIdx])
					m.state = stateDetail
					m.restoreMetadata = nil
				}
			}
			m.listModel, cmd = m.listModel.Update(msg)
			cmds = append(cmds, cmd)
			m.selectedIdx = m.listModel.SelectedIndex()

		case stateDetail:
			switch msg.String() {
			case "backspace", "b", "left":
				m.state = stateList
				m.restoreMetadata = nil
			case "enter":
				m.state = stateConfirm
				if m.selectedIdx < len(m.backups) {
					cmds = append(cmds, m.fetchRestoreMetadata())
				}
			}
			m.detailModel, cmd = m.detailModel.Update(msg)
			cmds = append(cmds, cmd)

		case stateConfirm:
			switch msg.String() {
			case "y", "Y":
				m.restoreStart = time.Now()
				m.statusMsg = "Restoring..."
				cmds = append(cmds, m.initiateRestore())
			case "n", "N", "backspace":
				m.state = stateDetail
				m.restoreMetadata = nil
			}

		case stateHelp:
			m.helpModel, cmd = m.helpModel.Update(msg)
			cmds = append(cmds, cmd)
		}

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
		if msg.err != nil {
			m.err = msg.err
			m.state = stateError
		} else {
			m.allBackups = msg.backups
			m.applyFilter()
			m.state = stateList
			m.listModel.SetItems(m.formatBackupsForList())
			m.statusMsg = ""
		}

	case restoreInitiatedMsg:
		if msg.err != nil {
			m.err = msg.err
			m.state = stateError
		} else {
			m.restoreJobID = msg.jobID
			m.state = stateRestoring
			m.statusMsg = fmt.Sprintf("Restore job started: %s", msg.jobID)
			cmds = append(cmds, m.pollRestoreStatus(), m.tickSpinner())
		}

	case restoreStatusMsg:
		if msg.err != nil {
			m.statusMsg = fmt.Sprintf("Error checking restore: %v", msg.err)
		} else {
			m.restoreStatus = msg.status
			if msg.status.IsTerminal {
				m.statusMsg = fmt.Sprintf("Restore %s: %s", msg.status.Status, msg.status.StatusMessage)
			} else if m.state == stateRestoring {
				cmds = append(cmds, m.pollRestoreStatus())
			}
		}

	case restoreMetadataMsg:
		if msg.err == nil {
			m.restoreMetadata = msg.metadata
		}

	case error:
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
func (m *Model) View() tea.View {
	var content string

	switch m.state {
	case stateError:
		content = m.renderError()
	case stateLoading:
		content = m.renderLoading()
	default:
		var view string
		switch m.state {
		case stateList:
			view = m.renderList()
		case stateDetail:
			view = m.renderDetail()
		case stateConfirm:
			view = m.renderConfirm()
		case stateHelp:
			view = m.renderHelp()
		case stateRestoring:
			view = m.renderRestoring()
		default:
			view = "Unknown state"
		}

		statusBar := m.renderStatusBar()
		keyHints := m.renderKeyHints()
		content = lipgloss.JoinVertical(lipgloss.Left, view, statusBar, keyHints)
	}

	v := tea.NewView(content)
	v.AltScreen = true
	v.MouseMode = tea.MouseModeCellMotion
	return v
}

// renderLoading renders the loading state view.
// Displayed when the application is discovering the vault or loading backups.
//
// Returns:
//   - string: Loading message with styled border
func (m *Model) renderLoading() string {
	spinner := spinnerFrames[m.spinnerFrame]
	label := "Loading backups..."
	if !m.vaultDiscovered && m.vaultName == "" {
		label = "Discovering backup vault..."
	}
	return lipgloss.NewStyle().
		Padding(1, 2).
		Border(lipgloss.RoundedBorder()).
		BorderForeground(compat.AdaptiveColor{
			Light: lipgloss.Color("62"),
			Dark:  lipgloss.Color("63"),
		}).
		Foreground(compat.AdaptiveColor{
			Light: lipgloss.Color("240"),
			Dark:  lipgloss.Color("252"),
		}).
		Render(fmt.Sprintf("%s %s", spinner, label))
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
		Foreground(compat.AdaptiveColor{
			Light: lipgloss.Color("62"),
			Dark:  lipgloss.Color("63"),
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
		Foreground(compat.AdaptiveColor{
			Light: lipgloss.Color("240"),
			Dark:  lipgloss.Color("248"),
		}).
		MarginBottom(1)

	infoSection := lipgloss.JoinHorizontal(
		lipgloss.Left,
		infoStyle.Render(vaultInfo),
		"  ",
		infoStyle.Render(regionInfo),
	)

	// Show active filter (CLI flag or in-app toggle)
	var filterLabel string
	if m.resourceType != "" {
		filterLabel = m.resourceType
	}
	if m.activeFilter != filterAll {
		filterLabel = m.activeFilter.String()
	}
	if filterLabel != "" {
		filterStyle := lipgloss.NewStyle().
			Foreground(lipgloss.Color("229")).
			Background(compat.AdaptiveColor{Light: lipgloss.Color("62"), Dark: lipgloss.Color("63")}).
			Padding(0, 1).
			Bold(true)
		filter := filterStyle.Render(fmt.Sprintf("Filter: %s", filterLabel))
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
		status = m.statusMsg
		statusStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("114"))
	case len(m.backups) > 0:
		if m.activeFilter != filterAll && len(m.allBackups) != len(m.backups) {
			status = fmt.Sprintf("✓ %d of %d backup(s) shown (%s)", len(m.backups), len(m.allBackups), m.activeFilter)
		} else {
			status = fmt.Sprintf("✓ %d backup(s) found", len(m.backups))
		}
		statusStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("114"))
	default:
		if m.vaultDiscovered && m.vaultName != "" {
			status = fmt.Sprintf("○ No backups found in vault: %s", m.vaultName)
		} else {
			status = "○ No backups found"
		}
		statusStyle = lipgloss.NewStyle().Foreground(compat.AdaptiveColor{
			Light: lipgloss.Color("240"),
			Dark:  lipgloss.Color("248"),
		})
	}

	return statusStyle.
		Padding(0, 1).
		Border(lipgloss.RoundedBorder()).
		BorderTop(true).
		BorderForeground(compat.AdaptiveColor{
			Light: lipgloss.Color("240"),
			Dark:  lipgloss.Color("238"),
		}).
		Render(status)
}

func (m *Model) renderConfirm() string {
	header := m.renderHeader()

	if m.selectedIdx >= len(m.backups) {
		return lipgloss.JoinVertical(lipgloss.Left, header, "No backup selected")
	}

	rp := m.backups[m.selectedIdx]

	warningStyle := lipgloss.NewStyle().
		Foreground(lipgloss.Color("214")).
		Bold(true)

	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("214")).
		Padding(1, 2).
		MarginTop(1)

	infoStyle := lipgloss.NewStyle().
		Foreground(compat.AdaptiveColor{Light: lipgloss.Color("240"), Dark: lipgloss.Color("252")})

	promptStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(compat.AdaptiveColor{Light: lipgloss.Color("232"), Dark: lipgloss.Color("255")}).
		MarginTop(1)

	yStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.Color("114")).
		Background(compat.AdaptiveColor{Light: lipgloss.Color("62"), Dark: lipgloss.Color("63")}).
		Padding(0, 1)

	nStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.Color("196")).
		Background(compat.AdaptiveColor{Light: lipgloss.Color("240"), Dark: lipgloss.Color("238")}).
		Padding(0, 1)

	sections := []string{
		warningStyle.Render("⚠  Confirm Restore Operation"),
		"",
		infoStyle.Render(fmt.Sprintf("Resource:  %s (%s)", rp.ResourceID, rp.ResourceType)),
		infoStyle.Render(fmt.Sprintf("Created:   %s (%s)", rp.CreationDate.Format("2006-01-02 15:04:05 MST"), relativeTime(rp.CreationDate))),
		infoStyle.Render(fmt.Sprintf("Size:      %s", formatBytes(rp.BackupSizeInBytes))),
	}

	if m.restoreMetadata != nil {
		meta := m.restoreMetadata
		metaStyle := lipgloss.NewStyle().
			Foreground(compat.AdaptiveColor{Light: lipgloss.Color("240"), Dark: lipgloss.Color("248")})

		sections = append(sections, "")
		sections = append(sections, metaStyle.Render("Restore Parameters:"))
		switch meta.ResourceType {
		case "RDS":
			sections = append(sections, infoStyle.Render(fmt.Sprintf("  Cluster:    %s", meta.ClusterID)))
			sections = append(sections, infoStyle.Render(fmt.Sprintf("  Subnet:     %s", meta.SubnetGroup)))
			sections = append(sections, infoStyle.Render(fmt.Sprintf("  Security:   %s", meta.SecurityGroups)))
		case "EFS":
			sections = append(sections, infoStyle.Render(fmt.Sprintf("  File System: %s", meta.ResourceID)))
			sections = append(sections, infoStyle.Render(fmt.Sprintf("  Encrypted:   %v", meta.Encrypted)))
			sections = append(sections, infoStyle.Render("  In-place:    true"))
		}
	}

	sections = append(sections,
		"",
		promptStyle.Render("Are you sure you want to restore this backup?"),
		"",
		lipgloss.JoinHorizontal(lipgloss.Left,
			yStyle.Render("y"),
			"  Yes, restore   ",
			nStyle.Render("n"),
			"  Cancel",
		),
	)

	content := lipgloss.JoinVertical(lipgloss.Left, sections...)

	return lipgloss.JoinVertical(lipgloss.Left, header, boxStyle.Render(content))
}

func (m *Model) renderKeyHints() string {
	hintStyle := lipgloss.NewStyle().
		Foreground(compat.AdaptiveColor{Light: lipgloss.Color("245"), Dark: lipgloss.Color("242")})

	keyStyle := lipgloss.NewStyle().
		Foreground(compat.AdaptiveColor{Light: lipgloss.Color("62"), Dark: lipgloss.Color("63")}).
		Bold(true)

	var hints string
	switch m.state {
	case stateList:
		hints = fmt.Sprintf(
			"%s navigate  %s select  %s filter  %s refresh  %s help  %s quit",
			keyStyle.Render("↑↓"),
			keyStyle.Render("enter"),
			keyStyle.Render("f"),
			keyStyle.Render("r"),
			keyStyle.Render("?"),
			keyStyle.Render("q"),
		)
	case stateDetail:
		hints = fmt.Sprintf(
			"%s restore  %s back  %s help  %s quit",
			keyStyle.Render("enter"),
			keyStyle.Render("b/←"),
			keyStyle.Render("?"),
			keyStyle.Render("q"),
		)
	case stateConfirm:
		hints = fmt.Sprintf(
			"%s confirm  %s cancel",
			keyStyle.Render("y"),
			keyStyle.Render("n/esc"),
		)
	case stateHelp:
		hints = fmt.Sprintf(
			"%s close help  %s quit",
			keyStyle.Render("esc/?"),
			keyStyle.Render("q"),
		)
	case stateRestoring:
		hints = fmt.Sprintf(
			"%s back to list (restore continues)",
			keyStyle.Render("esc/q"),
		)
	default:
		return ""
	}

	return hintStyle.Render(" " + hints)
}

func (m *Model) formatBackupsForList() []string {
	items := make([]string, len(m.backups))
	for i, backup := range m.backups {
		date := backup.CreationDate.Format("2006-01-02 15:04:05")
		relative := relativeTime(backup.CreationDate)
		size := formatBytes(backup.BackupSizeInBytes)
		dot := freshnessIndicator(backup.CreationDate)
		items[i] = fmt.Sprintf("%s %s | %s | %s (%s) | %s", dot, backup.ResourceType, backup.ResourceID, date, relative, size)
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

// restoreStatusMsg is sent when a restore job status poll completes.
type restoreStatusMsg struct {
	status *aws.RestoreJobStatus
	err    error
}

// restoreMetadataMsg is sent when restore metadata lookup completes.
type restoreMetadataMsg struct {
	metadata *aws.RestoreMetadata
	err      error
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
func (m *Model) initiateRestore() tea.Cmd {
	return func() tea.Msg {
		if m.selectedIdx >= len(m.backups) {
			return restoreInitiatedMsg{err: fmt.Errorf("invalid backup selection")}
		}

		backup := m.backups[m.selectedIdx]
		jobID, err := m.backupClient.StartRestoreJob(m.ctx, backup, m.stackName, m.vaultName)
		if err != nil {
			return restoreInitiatedMsg{err: err}
		}

		return restoreInitiatedMsg{jobID: jobID}
	}
}

// pollRestoreStatus returns a command that waits 5 seconds then checks restore job status.
func (m *Model) pollRestoreStatus() tea.Cmd {
	jobID := m.restoreJobID
	return tea.Tick(5*time.Second, func(_ time.Time) tea.Msg {
		status, err := m.backupClient.GetRestoreJobStatus(m.ctx, jobID)
		return restoreStatusMsg{status: status, err: err}
	})
}

// fetchRestoreMetadata returns a command that fetches restore parameters for preview.
func (m *Model) fetchRestoreMetadata() tea.Cmd {
	if m.selectedIdx >= len(m.backups) {
		return nil
	}
	rp := m.backups[m.selectedIdx]
	stackName := m.stackName
	return func() tea.Msg {
		meta, err := m.backupClient.GetRestoreMetadata(m.ctx, rp, stackName)
		return restoreMetadataMsg{metadata: meta, err: err}
	}
}

// renderRestoring renders the restore monitoring view with live status.
func (m *Model) renderRestoring() string {
	header := m.renderHeader()

	spinner := spinnerFrames[m.spinnerFrame]

	boxStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(compat.AdaptiveColor{Light: lipgloss.Color("62"), Dark: lipgloss.Color("63")}).
		Padding(1, 2).
		MarginTop(1)

	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(compat.AdaptiveColor{Light: lipgloss.Color("62"), Dark: lipgloss.Color("63")})

	infoStyle := lipgloss.NewStyle().
		Foreground(compat.AdaptiveColor{Light: lipgloss.Color("240"), Dark: lipgloss.Color("252")})

	sections := []string{
		titleStyle.Render(fmt.Sprintf("%s  Restore In Progress", spinner)),
		"",
		infoStyle.Render(fmt.Sprintf("Job ID:  %s", m.restoreJobID)),
	}

	elapsed := time.Since(m.restoreStart).Truncate(time.Second)
	sections = append(sections, infoStyle.Render(fmt.Sprintf("Elapsed: %s", elapsed)))

	if m.restoreStatus != nil {
		rs := m.restoreStatus
		statusColor := lipgloss.Color("114") // green
		switch rs.Status {
		case "FAILED", "ABORTED":
			statusColor = lipgloss.Color("196") // red
		case "PENDING", "RUNNING":
			statusColor = lipgloss.Color("214") // yellow/orange
		}
		statusStyle := lipgloss.NewStyle().Foreground(statusColor).Bold(true)

		sections = append(sections, "")
		sections = append(sections, lipgloss.JoinHorizontal(lipgloss.Left,
			infoStyle.Render("Status:  "),
			statusStyle.Render(rs.Status),
		))
		if rs.PercentDone != "" {
			sections = append(sections, infoStyle.Render(fmt.Sprintf("Progress: %s%%", rs.PercentDone)))
		}
		if rs.StatusMessage != "" {
			sections = append(sections, infoStyle.Render(fmt.Sprintf("Message: %s", rs.StatusMessage)))
		}
		if rs.IsTerminal && !rs.CompletedAt.IsZero() {
			duration := rs.CompletedAt.Sub(rs.CreatedAt).Truncate(time.Second)
			sections = append(sections, infoStyle.Render(fmt.Sprintf("Duration: %s", duration)))
		}
	}

	content := lipgloss.JoinVertical(lipgloss.Left, sections...)
	return lipgloss.JoinVertical(lipgloss.Left, header, boxStyle.Render(content))
}

// cycleFilter advances the in-app filter and re-filters the backup list.
func (m *Model) cycleFilter() {
	m.activeFilter = m.activeFilter.next()
	m.applyFilter()
	m.listModel.SetItems(m.formatBackupsForList())
}

// applyFilter filters allBackups based on the active filter mode.
func (m *Model) applyFilter() {
	if m.activeFilter == filterAll {
		m.backups = m.allBackups
		return
	}
	filterStr := m.activeFilter.String()
	filtered := make([]aws.RecoveryPoint, 0, len(m.allBackups))
	for _, bp := range m.allBackups {
		if bp.ResourceType == filterStr {
			filtered = append(filtered, bp)
		}
	}
	m.backups = filtered
}

// relativeTime returns a human-readable relative time string (e.g., "2h ago", "3d ago").
func relativeTime(t time.Time) string {
	d := time.Since(t)
	switch {
	case d < time.Minute:
		return "just now"
	case d < time.Hour:
		mins := int(d.Minutes())
		return fmt.Sprintf("%dm ago", mins)
	case d < 24*time.Hour:
		hours := int(d.Hours())
		return fmt.Sprintf("%dh ago", hours)
	case d < 30*24*time.Hour:
		days := int(d.Hours() / 24)
		return fmt.Sprintf("%dd ago", days)
	default:
		months := int(d.Hours() / 24 / 30)
		if months < 1 {
			months = 1
		}
		return fmt.Sprintf("%dmo ago", months)
	}
}

// freshnessIndicator returns a colored dot based on backup age.
// Color numbers are ANSI 256 (Xterm) codes: 114=PaleGreen3, 214=Orange1, 196=Red1.
// Full palette reference: https://www.ditig.com/256-colors-cheat-sheet
func freshnessIndicator(t time.Time) string {
	age := time.Since(t)
	switch {
	case age < 24*time.Hour:
		return lipgloss.NewStyle().Foreground(lipgloss.Color("114")).Render("●") // green
	case age < 7*24*time.Hour:
		return lipgloss.NewStyle().Foreground(lipgloss.Color("214")).Render("●") // yellow
	default:
		return lipgloss.NewStyle().Foreground(lipgloss.Color("196")).Render("●") // red
	}
}

// RelativeTime is an exported wrapper for use by UI components.
func RelativeTime(t time.Time) string {
	return relativeTime(t)
}
