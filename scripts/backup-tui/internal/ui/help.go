// Package ui provides user interface components for the backup TUI.
// This file implements the help screen component, which displays keyboard
// shortcuts, usage tips, and general application guidance.
package ui

import (
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"charm.land/lipgloss/v2/compat"
)

// HelpModel manages the state and rendering of the help screen.
// The help screen provides users with information about keyboard shortcuts,
// navigation controls, and usage tips.
type HelpModel struct {
	width  int // Available width for rendering
	height int // Available height for rendering
}

// Styling constants for the help screen component.
// Color numbers are ANSI 256 (Xterm) color codes.
// Reference: https://www.ditig.com/256-colors-cheat-sheet
var (
	// helpStyle styles the main help container
	helpStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(compat.AdaptiveColor{
			Light: lipgloss.Color("62"), // Purple/blue border
			Dark:  lipgloss.Color("63"),
		}).
		Padding(1, 2)

	// titleStyle styles the help screen title
	titleStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(compat.AdaptiveColor{
			Light: lipgloss.Color("62"),
			Dark:  lipgloss.Color("63"),
		}).
		MarginBottom(1)

	// sectionStyle styles section headers (e.g., "Navigation:", "Actions:")
	sectionStyle = lipgloss.NewStyle().
			MarginTop(1).
			MarginBottom(1).
			Foreground(compat.AdaptiveColor{
			Light: lipgloss.Color("240"),
			Dark:  lipgloss.Color("248"),
		}).
		Bold(true)

	// keyStyle styles keyboard shortcut keys (e.g., "Enter", "↑/↓")
	keyStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("229")). // Light yellow text
			Background(compat.AdaptiveColor{
			Light: lipgloss.Color("62"), // Purple/blue background
			Dark:  lipgloss.Color("63"),
		}).
		Padding(0, 1).
		Bold(true)

	// descStyle styles the description text next to keyboard shortcuts
	descStyle = lipgloss.NewStyle().
			Foreground(compat.AdaptiveColor{
			Light: lipgloss.Color("240"),
			Dark:  lipgloss.Color("252"),
		}).
		MarginLeft(2)
)

// NewHelpModel creates a new HelpModel.
func NewHelpModel() HelpModel {
	return HelpModel{}
}

// Init initializes the help model (required by Bubbletea Model interface).
// Currently returns no commands, as the help model doesn't need async initialization.
func (m HelpModel) Init() tea.Cmd {
	return nil
}

// Update handles messages and updates the help model state.
// Currently only handles window resize events to adjust rendering dimensions.
//
// Parameters:
//   - msg: Bubbletea message (tea.WindowSizeMsg for resize)
//
// Returns:
//   - HelpModel: Updated model state
//   - tea.Cmd: Command to execute (nil for this component)
func (m HelpModel) Update(msg tea.Msg) (HelpModel, tea.Cmd) {
	if sizeMsg, ok := msg.(tea.WindowSizeMsg); ok {
		// Store window dimensions for proper rendering
		m.width = sizeMsg.Width
		m.height = sizeMsg.Height
	}
	return m, nil
}

// View renders the help screen as a string.
// Displays organized sections of keyboard shortcuts, actions, general controls,
// and usage tips in a readable, formatted layout.
//
// Returns:
//   - string: Rendered help screen
func (m HelpModel) View() string {
	title := titleStyle.Render("Help - OpenEMR Backup Manager")

	// Organize help content into logical sections
	sections := []string{
		title,
		"",
		sectionStyle.Render("Navigation:"),
		formatHelpItem("↑/↓, k/j", "Navigate backup list"),
		formatHelpItem("PgUp/PgDn", "Scroll one page up/down"),
		formatHelpItem("Home/g", "Jump to first backup"),
		formatHelpItem("End/G", "Jump to last backup"),
		formatHelpItem("Enter", "Select backup / Confirm action"),
		formatHelpItem("b, ←, Esc", "Go back"),
		"",
		sectionStyle.Render("Actions:"),
		formatHelpItem("f", "Cycle filter: All → RDS → EFS"),
		formatHelpItem("r", "Refresh backup list"),
		formatHelpItem("Enter", "Restore backup (from detail view)"),
		formatHelpItem("y / n", "Confirm or cancel restore"),
		"",
		sectionStyle.Render("General:"),
		formatHelpItem("?", "Show/hide this help"),
		formatHelpItem("q", "Quit application"),
		"",
		sectionStyle.Render("Tips:"),
		descStyle.Render("• Backups are color-coded by age: green (<24h), yellow (1-7d), red (>7d)"),
		descStyle.Render("• Press f to cycle through resource type filters without restarting"),
		descStyle.Render("• Restore progress is monitored live after confirmation"),
		descStyle.Render("• You can press Esc during restore monitoring to return to the list"),
		descStyle.Render("• Use -type flag to pre-filter by RDS or EFS at launch"),
	}

	content := lipgloss.JoinVertical(lipgloss.Left, sections...)
	return helpStyle.Render(content)
}

// formatHelpItem formats a keyboard shortcut and its description into a single line.
// The key is styled with keyStyle (colored background) and the description
// is styled with descStyle.
//
// Parameters:
//   - key: Keyboard shortcut or key combination (e.g., "Enter", "↑/↓")
//   - desc: Description of what the key does
//
// Returns:
//   - string: Formatted help item line with newline
//
// Example:
//
//	formatHelpItem("Enter", "Select backup")
//	// Returns: "[Enter] Select backup\n" (with styling)
func formatHelpItem(key, desc string) string {
	return lipgloss.JoinHorizontal(lipgloss.Left,
		keyStyle.Render(key),
		descStyle.Render(desc),
	) + "\n"
}
