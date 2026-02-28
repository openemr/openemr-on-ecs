package ui

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
)

func TestFormatHelpItem(t *testing.T) {
	tests := []struct {
		name     string
		key      string
		desc     string
		contains []string
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
			for _, expected := range tt.contains {
				if !strings.Contains(result, expected) {
					t.Errorf("formatHelpItem(%q, %q) = %q, should contain %q", tt.key, tt.desc, result, expected)
				}
			}
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

	view := model.View()
	if view == "" {
		t.Error("NewHelpModel().View() returned empty string")
	}
}

func TestHelpModel_Init(t *testing.T) {
	model := NewHelpModel()
	cmd := model.Init()

	if cmd != nil {
		t.Error("HelpModel.Init() should return nil")
	}
}

func TestHelpModel_View(t *testing.T) {
	model := NewHelpModel()

	view := model.View()
	if view == "" {
		t.Error("HelpModel.View() returned empty string")
	}

	if !strings.Contains(view, "Navigation") && !strings.Contains(view, "Controls") {
		t.Error("HelpModel.View() should contain help content")
	}
}

func TestHelpModel_Update(t *testing.T) {
	model := NewHelpModel()

	msg := tea.WindowSizeMsg{Width: 100, Height: 50}
	newModel, cmd := model.Update(msg)

	if cmd != nil {
		t.Error("HelpModel.Update(WindowSizeMsg) should return nil command")
	}
	_ = newModel

	keyMsg := tea.KeyPressMsg{Code: 'q', Text: "q"}
	newModel2, cmd2 := model.Update(keyMsg)

	if cmd2 != nil {
		t.Error("HelpModel.Update(KeyPressMsg) should return nil command")
	}
	_ = newModel2
}

func TestHelpModel_ViewContainsSections(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	sections := []string{"Navigation", "Actions", "General", "Tips"}
	for _, section := range sections {
		if !strings.Contains(view, section) {
			t.Errorf("HelpModel.View() should contain section %q", section)
		}
	}
}

func TestHelpModel_ViewContainsKeyBindings(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	keys := []string{"PgUp", "PgDn", "Home", "End", "Enter"}
	for _, key := range keys {
		if !strings.Contains(view, key) {
			t.Errorf("HelpModel.View() should mention key %q", key)
		}
	}
}

func TestHelpModel_ViewContainsFilterKey(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	if !strings.Contains(view, "filter") && !strings.Contains(view, "Filter") {
		t.Error("HelpModel.View() should document the filter key")
	}
}

func TestHelpModel_ViewContainsFreshnessInfo(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	if !strings.Contains(view, "color") && !strings.Contains(view, "Color") {
		t.Error("HelpModel.View() should mention color-coding")
	}
}

func TestHelpModel_ViewContainsRestoreMonitoring(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	if !strings.Contains(view, "monitor") && !strings.Contains(view, "progress") {
		t.Error("HelpModel.View() should mention restore monitoring/progress")
	}
}

// --- Unit Tests: Vim keybindings in help ---

func TestHelpModel_ViewContainsVimKeys(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	vimKeys := []string{"j", "k"}
	for _, key := range vimKeys {
		if !strings.Contains(view, key) {
			t.Errorf("HelpModel.View() should mention vim key %q", key)
		}
	}
}

// --- Unit Tests: Help mentions specific tips ---

func TestHelpModel_ViewContainsEscTip(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	if !strings.Contains(view, "Esc") {
		t.Error("help view should mention Esc key")
	}
}

func TestHelpModel_ViewContainsTypeFlag(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	if !strings.Contains(view, "-type") {
		t.Error("help view should mention -type flag")
	}
}

func TestHelpModel_ViewContainsRefreshKey(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	if !strings.Contains(view, "Refresh") || !strings.Contains(view, "r") {
		t.Error("help view should mention r for Refresh")
	}
}

// --- Unit Tests: Help model handles window resize ---

func TestHelpModel_UpdateWindowSize(t *testing.T) {
	model := NewHelpModel()

	updated, cmd := model.Update(tea.WindowSizeMsg{Width: 120, Height: 40})
	if cmd != nil {
		t.Error("window size msg should not produce a command")
	}

	view := updated.View()
	if view == "" {
		t.Error("view after resize should not be empty")
	}
}

// --- Unit Tests: Help model ignores unknown messages ---

func TestHelpModel_IgnoresUnknownMsg(t *testing.T) {
	model := NewHelpModel()

	type customMsg struct{}
	updated, cmd := model.Update(customMsg{})
	if cmd != nil {
		t.Error("unknown msg should not produce a command")
	}

	view := updated.View()
	if view == "" {
		t.Error("view should still work after unknown msg")
	}
}

// --- Unit Tests: Help view has consistent structure ---

func TestHelpModel_ViewNonEmpty(t *testing.T) {
	model := HelpModel{}
	view := model.View()

	if view == "" {
		t.Error("zero-value HelpModel view should not be empty")
	}
}

func TestHelpModel_ViewContainsQuitKey(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	if !strings.Contains(view, "q") && !strings.Contains(view, "Quit") {
		t.Error("help view should mention quit")
	}
}

func TestHelpModel_ViewContainsHelpKey(t *testing.T) {
	model := NewHelpModel()
	view := model.View()

	if !strings.Contains(view, "?") {
		t.Error("help view should mention ? key")
	}
}

// --- Unit Tests: formatHelpItem edge cases ---

func TestFormatHelpItem_BothEmpty(t *testing.T) {
	result := formatHelpItem("", "")
	// Even with empty key and desc, the function renders styled (ANSI) output
	if result == "" {
		t.Error("formatHelpItem always produces styled output, should not be literally empty")
	}
}

func TestFormatHelpItem_LongDescription(t *testing.T) {
	result := formatHelpItem("x", "This is a very long description that describes what the key does in detail")
	if result == "" {
		t.Error("should handle long descriptions")
	}
	if !strings.Contains(result, "very long description") {
		t.Error("should contain the full description")
	}
}
