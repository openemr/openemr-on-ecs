// Package ui provides user interface components for the backup TUI.
// This file implements the list view component, which displays a scrollable
// list of backup recovery points with keyboard navigation support.
package ui

import (
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// ListModel manages the state and rendering of the backup list view.
// It handles cursor navigation, item selection, and visual styling for
// the list of recovery points displayed to the user.
type ListModel struct {
	items  []string // Formatted backup items to display
	cursor int      // Currently selected item index (0-based)
	height int      // Available height for rendering (from window size)
	width  int      // Available width for rendering (from window size)
}

// Styling constants for the list view component.
// These styles use adaptive colors that work well in both light and dark terminals.
var (
	// listItemStyle styles unselected list items
	listItemStyle = lipgloss.NewStyle().
			PaddingLeft(2).
			Foreground(lipgloss.AdaptiveColor{
			Light: "240", // Dark gray for light terminals
			Dark:  "252", // Light gray for dark terminals
		}).
		MarginRight(1)

	// selectedItemStyle styles the currently selected/highlighted item
	selectedItemStyle = lipgloss.NewStyle().
				PaddingLeft(1).
				PaddingRight(1).
				Foreground(lipgloss.Color("229")). // Light yellow text
				Background(lipgloss.AdaptiveColor{
			Light: "62", // Purple/blue background
			Dark:  "63", // Slightly brighter for dark terminals
		}).
		Bold(true).
		MarginRight(1)

	// listHeaderStyle styles the column header row
	listHeaderStyle = lipgloss.NewStyle().
			BorderStyle(lipgloss.RoundedBorder()).
			BorderBottom(true).
			BorderForeground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "238",
		}).
		PaddingBottom(1).
		MarginBottom(1).
		Foreground(lipgloss.AdaptiveColor{
			Light: "240",
			Dark:  "248",
		}).
		Bold(true)
)

// NewListModel creates a new ListModel with empty items and cursor at position 0.
// This should be called when initializing the application model.
func NewListModel() ListModel {
	return ListModel{
		items:  []string{},
		cursor: 0,
	}
}

// Init initializes the list model (required by Bubbletea Model interface).
// Currently returns no commands, as the list model doesn't need async initialization.
func (m ListModel) Init() tea.Cmd {
	return nil
}

// Update handles messages and updates the list model state.
// This method processes keyboard input for navigation (up/down arrows, vim keys)
// and window resize events to adjust rendering dimensions.
//
// Parameters:
//   - msg: Bubbletea message (tea.KeyMsg for keyboard input, tea.WindowSizeMsg for resize)
//
// Returns:
//   - ListModel: Updated model state
//   - tea.Cmd: Command to execute (nil for this component)
func (m ListModel) Update(msg tea.Msg) (ListModel, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		// Store window dimensions for proper rendering
		m.width = msg.Width
		m.height = msg.Height
	case tea.KeyMsg:
		switch msg.String() {
		case "up", "k": // Move cursor up (vim-style 'k' key supported)
			if m.cursor > 0 {
				m.cursor--
			}
		case "down", "j": // Move cursor down (vim-style 'j' key supported)
			if m.cursor < len(m.items)-1 {
				m.cursor++
			}
		}
	}
	return m, nil
}

// View renders the list component as a string.
// This displays the column header and all backup items, with the currently
// selected item highlighted using selectedItemStyle.
//
// Returns:
//   - string: Rendered list view with header and items
func (m ListModel) View() string {
	// Handle empty list case
	if len(m.items) == 0 {
		return lipgloss.NewStyle().
			Foreground(lipgloss.Color("240")).
			Padding(1).
			Render("No backups found")
	}

	// Render column header with border
	header := listHeaderStyle.Render("Type | Resource ID | Creation Date | Size")

	// Render each item, highlighting the selected one
	var items []string
	for i, item := range m.items {
		if i == m.cursor {
			// Selected item: use highlight style with arrow indicator
			items = append(items, selectedItemStyle.Render("▶ "+item))
		} else {
			// Unselected item: use normal style with spacing
			items = append(items, listItemStyle.Render("  "+item))
		}
	}

	list := lipgloss.JoinVertical(lipgloss.Left, items...)
	return lipgloss.JoinVertical(lipgloss.Left, header, list)
}

// SetItems updates the list items and adjusts the cursor position if necessary.
// This is called when backup data is loaded or refreshed.
//
// Parameters:
//   - items: New list of formatted backup items to display
//
// Note: If the cursor is beyond the new item count, it's adjusted to the last item.
// If the list is empty, cursor is set to 0.
func (m *ListModel) SetItems(items []string) {
	m.items = items
	// Ensure cursor stays within valid range
	if m.cursor >= len(m.items) {
		m.cursor = len(m.items) - 1
	}
	if m.cursor < 0 {
		m.cursor = 0
	}
}

// SelectedIndex returns the index of the currently selected item.
// This is used by the parent model to determine which backup was selected
// when the user presses Enter.
//
// Returns:
//   - int: Zero-based index of the selected item
func (m ListModel) SelectedIndex() int {
	return m.cursor
}
