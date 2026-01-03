// Package ui provides user interface components for the backup TUI.
// This file implements the help screen component, which displays keyboard
// shortcuts, usage tips, and general application guidance.
package ui

import (
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// HelpModel manages the state and rendering of the help screen.
// The help screen provides users with information about keyboard shortcuts,
// navigation controls, and usage tips.
type HelpModel struct {
	width  int // Available width for rendering
	height int // Available height for rendering
}

// Styling constants for the help screen component.
var (
	// helpStyle styles the main help container
	helpStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.AdaptiveColor{
			Light: "62", // Purple/blue border
			Dark:  "63",
		}).
		Padding(1, 2)

	// titleStyle styles the help screen title
	titleStyle = lipgloss.NewStyle().
			Bold(true).
			Foreground(lipgloss.AdaptiveColor{
			Light: "62",
			Dark:  "63",
		}).
		MarginBottom(1)

	// sectionStyle styles section headers (e.g., "Navigation:", "Actions:")
	sectionStyle = lipgloss.NewStyle().
			MarginTop(1).
			MarginBottom(1).
			Foreground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "248",
		}).
		Bold(true)

	// keyStyle styles keyboard shortcut keys (e.g., "Enter", "↑/↓")
	keyStyle = lipgloss.NewStyle().
			Foreground(lipgloss.Color("229")). // Light yellow text
			Background(lipgloss.AdaptiveColor{
			Light: "62", // Purple/blue background
			Dark:  "63",
		}).
		Padding(0, 1).
		Bold(true)

	// descStyle styles the description text next to keyboard shortcuts
	descStyle = lipgloss.NewStyle().
			Foreground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "252",
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
		formatHelpItem("Enter", "Select backup / Confirm action"),
		formatHelpItem("b, ←", "Go back"),
		"",
		sectionStyle.Render("Actions:"),
		formatHelpItem("r", "Refresh backup list"),
		formatHelpItem("/", "Search/filter (coming soon)"),
		"",
		sectionStyle.Render("General:"),
		formatHelpItem("?", "Show/hide this help"),
		formatHelpItem("q, Esc", "Quit application"),
		"",
		sectionStyle.Render("Tips:"),
		descStyle.Render("• Backups are listed with creation date and size"),
		descStyle.Render("• Select a backup and press Enter to view details"),
		descStyle.Render("• Initiate restore from the detail view"),
		descStyle.Render("• Use -type flag to filter by RDS or EFS"),
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
