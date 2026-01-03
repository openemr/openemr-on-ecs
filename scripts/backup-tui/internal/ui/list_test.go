package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

func TestNewListModel(t *testing.T) {
	model := NewListModel()

	// Test that model is initialized correctly (check SelectedIndex)
	if model.SelectedIndex() != 0 {
		t.Errorf("NewListModel() SelectedIndex() = %d, want 0", model.SelectedIndex())
	}

	// Test that SetItems works
	items := []string{"item1", "item2", "item3"}
	model.SetItems(items)

	// Verify SelectedIndex works
	if model.SelectedIndex() != 0 {
		t.Errorf("SelectedIndex() = %d, want 0", model.SelectedIndex())
	}

	// Test with empty items
	emptyModel := NewListModel()
	emptyModel.SetItems([]string{})
	if emptyModel.SelectedIndex() != 0 {
		t.Errorf("SelectedIndex() with empty items = %d, want 0", emptyModel.SelectedIndex())
	}
}

func TestListModel_SetItems(t *testing.T) {
	model := NewListModel()
	items := []string{"item1", "item2", "item3"}

	model.SetItems(items)

	// Verify SelectedIndex reflects the current state
	if model.SelectedIndex() < 0 || model.SelectedIndex() >= len(items) {
		t.Errorf("SelectedIndex() = %d, should be in range [0, %d)", model.SelectedIndex(), len(items))
	}
}

func TestListModel_SelectedIndex(t *testing.T) {
	model := NewListModel()
	items := []string{"item1", "item2", "item3"}
	model.SetItems(items)

	// SelectedIndex should be 0 initially
	if model.SelectedIndex() != 0 {
		t.Errorf("SelectedIndex() = %d, want 0", model.SelectedIndex())
	}
}

func TestListModel_Init(t *testing.T) {
	model := NewListModel()
	cmd := model.Init()

	// Init should return nil (no commands needed)
	if cmd != nil {
		t.Error("ListModel.Init() should return nil")
	}
}

func TestListModel_View(t *testing.T) {
	model := NewListModel()

	// Test View with empty items
	view1 := model.View()
	if view1 == "" {
		t.Error("ListModel.View() returned empty string with empty items")
	}

	// Test View with items
	items := []string{"item1", "item2", "item3"}
	model.SetItems(items)
	view2 := model.View()

	if view2 == "" {
		t.Error("ListModel.View() returned empty string with items")
	}

	// View should be different when items are set
	if view1 == view2 {
		t.Error("ListModel.View() should be different when items are set")
	}
}

func TestListModel_Update(t *testing.T) {
	model := NewListModel()
	items := []string{"item1", "item2", "item3", "item4"}
	model.SetItems(items)

	// Test Update with WindowSizeMsg
	msg := tea.WindowSizeMsg{Width: 100, Height: 50}
	newModel, cmd := model.Update(msg)

	// Should return updated model and nil command
	if cmd != nil {
		t.Error("ListModel.Update(WindowSizeMsg) should return nil command")
	}

	// Model should be updated (width/height set)
	_ = newModel

	// Test Update with up arrow key
	upKey := tea.KeyMsg{Type: tea.KeyUp}
	modelAfterUp, cmdUp := model.Update(upKey)
	if cmdUp != nil {
		t.Error("ListModel.Update(KeyUp) should return nil command")
	}
	_ = modelAfterUp

	// Test Update with down arrow key
	downKey := tea.KeyMsg{Type: tea.KeyDown}
	modelAfterDown, cmdDown := model.Update(downKey)
	if cmdDown != nil {
		t.Error("ListModel.Update(KeyDown) should return nil command")
	}
	_ = modelAfterDown

	// Test Update with 'k' key (vim-style up)
	kKey := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'k'}}
	modelAfterK, cmdK := model.Update(kKey)
	if cmdK != nil {
		t.Error("ListModel.Update('k') should return nil command")
	}
	_ = modelAfterK

	// Test Update with 'j' key (vim-style down)
	jKey := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'j'}}
	modelAfterJ, cmdJ := model.Update(jKey)
	if cmdJ != nil {
		t.Error("ListModel.Update('j') should return nil command")
	}
	_ = modelAfterJ
}

func TestListModel_SetItems_CursorAdjustment(t *testing.T) {
	model := NewListModel()

	// Set items and move cursor to last item
	items1 := []string{"item1", "item2", "item3"}
	model.SetItems(items1)

	// Simulate cursor at last position (by calling Update with down key multiple times)
	for i := 0; i < len(items1); i++ {
		downKey := tea.KeyMsg{Type: tea.KeyDown}
		model, _ = model.Update(downKey)
	}

	// Now set fewer items - cursor should be adjusted
	items2 := []string{"item1", "item2"}
	model.SetItems(items2)

	// Cursor should be within bounds
	if model.SelectedIndex() >= len(items2) {
		t.Errorf("SelectedIndex() = %d, should be < %d after SetItems", model.SelectedIndex(), len(items2))
	}
}

func TestListModel_View_EmptyList(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{})

	view := model.View()
	if view == "" {
		t.Error("ListModel.View() returned empty string for empty list")
	}

	// Should show "No backups found" or similar
	if !strings.Contains(view, "No") && !strings.Contains(view, "backup") {
		t.Error("ListModel.View() for empty list should indicate no items")
	}
}
