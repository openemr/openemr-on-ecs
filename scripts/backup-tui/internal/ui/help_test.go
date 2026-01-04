package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestFormatHelpItem(t *testing.T) {
	tests := []struct {
		name     string
		key      string
		desc     string
		contains []string // Strings that should be present in the output
	}{
		{"Simple key and description", "Enter", "Select backup", []string{"Enter", "Select backup"}},
		{"Key with special chars", "Esc/q", "Quit application", []string{"Esc/q", "Quit application"}},
		{"Navigation keys", "↑/↓", "Navigate list", []string{"↑/↓", "Navigate list"}},
		{"Empty key", "", "Description", []string{"Description"}},
		{"Empty description", "Key", "", []string{"Key"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := formatHelpItem(tt.key, tt.desc)
			// Check that the result contains all expected strings
			for _, expected := range tt.contains {
				if !strings.Contains(result, expected) {
					t.Errorf("formatHelpItem(%q, %q) = %q, should contain %q", tt.key, tt.desc, result, expected)
				}
			}
			// Result should not be empty (unless both inputs are empty)
			if tt.key == "" && tt.desc == "" {
				if result != "" {
					t.Errorf("formatHelpItem(%q, %q) = %q, want empty string", tt.key, tt.desc, result)
				}
			} else if result == "" {
				t.Errorf("formatHelpItem(%q, %q) = %q, should not be empty", tt.key, tt.desc, result)
			}
		})
	}
}

func TestNewHelpModel(t *testing.T) {
	model := NewHelpModel()

	// Test that model is initialized (can call View without panicking)
	view := model.View()
	if view == "" {
		t.Error("NewHelpModel().View() returned empty string")
	}
}

func TestHelpModel_Init(t *testing.T) {
	model := NewHelpModel()
	cmd := model.Init()

	// Init should return nil (no commands needed)
	if cmd != nil {
		t.Error("HelpModel.Init() should return nil")
	}
}

func TestHelpModel_View(t *testing.T) {
	model := NewHelpModel()

	// Test View returns non-empty string
	view := model.View()
	if view == "" {
		t.Error("HelpModel.View() returned empty string")
	}

	// View should contain help information
	if !strings.Contains(view, "Navigation") && !strings.Contains(view, "Controls") {
		t.Error("HelpModel.View() should contain help content")
	}
}

func TestHelpModel_Update(t *testing.T) {
	model := NewHelpModel()

	// Test Update with WindowSizeMsg
	msg := tea.WindowSizeMsg{Width: 100, Height: 50}
	newModel, cmd := model.Update(msg)

	// Should return updated model and nil command
	if cmd != nil {
		t.Error("HelpModel.Update(WindowSizeMsg) should return nil command")
	}

	// Model should be updated (width/height set)
	_ = newModel

	// Test Update with KeyMsg (should return unchanged model)
	keyMsg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'q'}}
	newModel2, cmd2 := model.Update(keyMsg)

	if cmd2 != nil {
		t.Error("HelpModel.Update(KeyMsg) should return nil command")
	}

	// Model should be unchanged for key messages
	_ = newModel2
}
