// Package ui provides user interface components for the backup TUI.
// This file implements the list view component, which displays a scrollable
// list of backup recovery points with keyboard navigation support.
package ui

import (
	"fmt"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"charm.land/lipgloss/v2/compat"
)

// ListModel manages the state and rendering of the backup list view.
// It handles cursor navigation, item selection, viewport scrolling,
// and visual styling for the list of recovery points displayed to the user.
type ListModel struct {
	items    []string // Formatted backup items to display
	cursor   int      // Currently selected item index (0-based)
	offset   int      // Scroll offset (first visible item index)
	height   int      // Available height for rendering (from window size)
	width    int      // Available width for rendering (from window size)
	pageSize int      // Number of items visible in viewport
}

// Styling constants for the list view component.
// These styles use adaptive colors that work well in both light and dark terminals.
//
// Color numbers are ANSI 256 (Xterm) color codes.
// Reference: https://www.ditig.com/256-colors-cheat-sheet
var (
	// listItemStyle styles unselected list items
	listItemStyle = lipgloss.NewStyle().
			PaddingLeft(2).
			Foreground(compat.AdaptiveColor{
			Light: lipgloss.Color("240"), // Dark gray for light terminals
			Dark:  lipgloss.Color("252"), // Light gray for dark terminals
		}).
		MarginRight(1)

	// selectedItemStyle styles the currently selected/highlighted item
	selectedItemStyle = lipgloss.NewStyle().
				PaddingLeft(1).
				PaddingRight(1).
				Foreground(lipgloss.Color("229")). // Light yellow text
				Background(compat.AdaptiveColor{
			Light: lipgloss.Color("62"), // Purple/blue background
			Dark:  lipgloss.Color("63"), // Slightly brighter for dark terminals
		}).
		Bold(true).
		MarginRight(1)

	// listHeaderStyle styles the column header row
	listHeaderStyle = lipgloss.NewStyle().
			BorderStyle(lipgloss.RoundedBorder()).
			BorderBottom(true).
			BorderForeground(compat.AdaptiveColor{
			Light: lipgloss.Color("240"),
			Dark:  lipgloss.Color("238"),
		}).
		PaddingBottom(1).
		MarginBottom(1).
		Foreground(compat.AdaptiveColor{
			Light: lipgloss.Color("240"),
			Dark:  lipgloss.Color("248"),
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
		m.width = msg.Width
		m.height = msg.Height
		m.pageSize = max(m.height-8, 5) // Reserve space for header, status bar, key hints
	case tea.KeyPressMsg:
		switch msg.String() {
		case "up", "k":
			if m.cursor > 0 {
				m.cursor--
			}
		case "down", "j":
			if m.cursor < len(m.items)-1 {
				m.cursor++
			}
		case "pgup":
			m.cursor -= m.visibleItems()
			if m.cursor < 0 {
				m.cursor = 0
			}
		case "pgdown":
			m.cursor += m.visibleItems()
			if m.cursor >= len(m.items) {
				m.cursor = len(m.items) - 1
			}
			if m.cursor < 0 {
				m.cursor = 0
			}
		case "home", "g":
			m.cursor = 0
		case "end", "G":
			if len(m.items) > 0 {
				m.cursor = len(m.items) - 1
			}
		}
	}
	m.adjustOffset()
	return m, nil
}

func (m ListModel) visibleItems() int {
	if m.pageSize > 0 {
		return m.pageSize
	}
	return 20
}

func (m *ListModel) adjustOffset() {
	visible := m.visibleItems()
	if m.cursor < m.offset {
		m.offset = m.cursor
	}
	if m.cursor >= m.offset+visible {
		m.offset = m.cursor - visible + 1
	}
	if m.offset < 0 {
		m.offset = 0
	}
}

// View renders the list component as a string.
// This displays the column header and all backup items, with the currently
// selected item highlighted using selectedItemStyle.
//
// Returns:
//   - string: Rendered list view with header and items
func (m ListModel) View() string {
	if len(m.items) == 0 {
		return lipgloss.NewStyle().
			Foreground(lipgloss.Color("240")).
			Padding(1).
			Render("No backups found")
	}

	header := listHeaderStyle.Render("Type | Resource ID | Creation Date | Size")

	visible := m.visibleItems()
	end := m.offset + visible
	if end > len(m.items) {
		end = len(m.items)
	}

	var items []string

	if m.offset > 0 {
		scrollUpStyle := lipgloss.NewStyle().
			Foreground(compat.AdaptiveColor{Light: lipgloss.Color("245"), Dark: lipgloss.Color("242")}).
			PaddingLeft(2)
		items = append(items, scrollUpStyle.Render(fmt.Sprintf("  ↑ %d more above", m.offset)))
	}

	for i := m.offset; i < end; i++ {
		if i == m.cursor {
			items = append(items, selectedItemStyle.Render("▶ "+m.items[i]))
		} else {
			items = append(items, listItemStyle.Render("  "+m.items[i]))
		}
	}

	remaining := len(m.items) - end
	if remaining > 0 {
		scrollDownStyle := lipgloss.NewStyle().
			Foreground(compat.AdaptiveColor{Light: lipgloss.Color("245"), Dark: lipgloss.Color("242")}).
			PaddingLeft(2)
		items = append(items, scrollDownStyle.Render(fmt.Sprintf("  ↓ %d more below", remaining)))
	}

	posStyle := lipgloss.NewStyle().
		Foreground(compat.AdaptiveColor{Light: lipgloss.Color("245"), Dark: lipgloss.Color("242")}).
		PaddingLeft(2)
	items = append(items, posStyle.Render(fmt.Sprintf("  %d/%d", m.cursor+1, len(m.items))))

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
