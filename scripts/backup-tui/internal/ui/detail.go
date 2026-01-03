// Package ui provides user interface components for the backup TUI.
// This file implements the detail view component, which displays comprehensive
// information about a selected backup recovery point and provides actions
// (such as initiating a restore).
package ui

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/openemr/openemr-on-ecs/scripts/backup-tui/internal/aws"
)

// DetailModel manages the state and rendering of the backup detail view.
// It displays information about a selected recovery point and allows the user
// to initiate restore operations.
type DetailModel struct {
	recoveryPoint *aws.RecoveryPoint // Currently displayed recovery point (nil if none selected)
	width         int                // Available width for rendering
	height        int                // Available height for rendering
}

// Styling constants for the detail view component.
var (
	// detailStyle styles the main detail container with border and padding
	detailStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.AdaptiveColor{
			Light: "62", // Purple/blue border
			Dark:  "63", // Slightly brighter for dark terminals
		}).
		Padding(1, 2).
		MarginTop(1)

	// labelStyle styles field labels (e.g., "Resource Type:", "Status:")
	labelStyle = lipgloss.NewStyle().
			Foreground(lipgloss.AdaptiveColor{
			Light: "240", // Dark gray for light terminals
			Dark:  "248", // Light gray for dark terminals
		}).
		Bold(true).
		Width(20) // Fixed width for alignment

	// valueStyle styles field values
	valueStyle = lipgloss.NewStyle().
			Foreground(lipgloss.AdaptiveColor{
			Light: "232", // Very dark for light terminals
			Dark:  "252", // Very light for dark terminals
		})

	// buttonStyle styles the action button (e.g., "Press ENTER to initiate restore")
	buttonStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("229")). // Light yellow text
			Background(lipgloss.AdaptiveColor{
			Light: "62", // Purple/blue background
			Dark:  "63",
		}).
		Padding(0, 2).
		MarginTop(1).
		Bold(true)

	// infoBoxStyle styles the help/info box at the bottom
	infoBoxStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "238",
		}).
		Padding(1).
		MarginTop(1).
		Foreground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "248",
		})
)

// NewDetailModel creates a new DetailModel with no recovery point selected.
func NewDetailModel() DetailModel {
	return DetailModel{}
}

// Init initializes the detail model (required by Bubbletea Model interface).
// Currently returns no commands, as the detail model doesn't need async initialization.
func (m DetailModel) Init() tea.Cmd {
	return nil
}

// Update handles messages and updates the detail model state.
// Currently only handles window resize events to adjust rendering dimensions.
//
// Parameters:
//   - msg: Bubbletea message (tea.WindowSizeMsg for resize)
//
// Returns:
//   - DetailModel: Updated model state
//   - tea.Cmd: Command to execute (nil for this component)
func (m DetailModel) Update(msg tea.Msg) (DetailModel, tea.Cmd) {
	if sizeMsg, ok := msg.(tea.WindowSizeMsg); ok {
		// Store window dimensions for proper rendering
		m.width = sizeMsg.Width
		m.height = sizeMsg.Height
	}
	return m, nil
}

// View renders the detail component as a string.
// Displays comprehensive information about the selected recovery point,
// including resource type, ID, status, creation date, size, and ARN.
// Also shows action buttons and keyboard shortcuts.
//
// Returns:
//   - string: Rendered detail view
func (m DetailModel) View() string {
	if m.recoveryPoint == nil {
		return "No backup selected"
	}

	rp := m.recoveryPoint

	var sections []string

	// Basic Information Section
	// Display key recovery point attributes in a formatted layout
	basicInfo := lipgloss.JoinVertical(lipgloss.Left,
		lipgloss.JoinHorizontal(lipgloss.Left, labelStyle.Render("Resource Type:"), valueStyle.Render(rp.ResourceType)),
		lipgloss.JoinHorizontal(lipgloss.Left, labelStyle.Render("Resource ID:"), valueStyle.Render(rp.ResourceID)),
		lipgloss.JoinHorizontal(lipgloss.Left, labelStyle.Render("Status:"), valueStyle.Render(rp.Status)),
		lipgloss.JoinHorizontal(lipgloss.Left, labelStyle.Render("Created:"), valueStyle.Render(rp.CreationDate.Format("2006-01-02 15:04:05 MST"))),
		lipgloss.JoinHorizontal(lipgloss.Left, labelStyle.Render("Size:"), valueStyle.Render(formatBytes(rp.BackupSizeInBytes))),
	)

	// Recovery Point ARN Section
	// ARNs can be very long, so we truncate for display while keeping it readable
	arnLabel := labelStyle.Render("Recovery Point ARN:")
	arnValue := valueStyle.Render(truncateString(rp.RecoveryPointARN, 60))
	arnRow := lipgloss.JoinHorizontal(lipgloss.Left, arnLabel, arnValue)

	sections = append(sections, basicInfo, "", arnRow)

	// Action Button
	// Prominent button to initiate restore operation
	actionButton := buttonStyle.Render("Press ENTER to initiate restore")

	sections = append(sections, "", actionButton)

	// Instructions/Help Section
	// Quick reference for keyboard shortcuts
	instructions := infoBoxStyle.Render(
		"Controls:\n" +
			"  ENTER - Initiate restore\n" +
			"  b/←   - Go back to list\n" +
			"  q     - Quit",
	)

	sections = append(sections, instructions)

	content := lipgloss.JoinVertical(lipgloss.Left, sections...)
	return detailStyle.Render(content)
}

// SetRecoveryPoint sets the recovery point to display in the detail view.
// This is called when the user selects a backup from the list view.
//
// Parameters:
//   - rp: Pointer to the recovery point to display (nil to clear the view)
func (m *DetailModel) SetRecoveryPoint(rp *aws.RecoveryPoint) {
	m.recoveryPoint = rp
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
	// Format with one decimal place and appropriate unit (K, M, G, T, P, E)
	return fmt.Sprintf("%.1f %cB", float64(bytes)/float64(div), "KMGTPE"[exp])
}

// truncateString truncates a string to the specified maximum length,
// adding "..." if the string was truncated.
//
// Parameters:
//   - s: String to truncate
//   - maxLen: Maximum length (including "..." if truncated)
//
// Returns:
//   - string: Truncated string (original if already shorter than maxLen)
//
// Example:
//
//	truncateString("very long string", 10) // Returns: "very lo..."
func truncateString(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-3] + "..."
}
