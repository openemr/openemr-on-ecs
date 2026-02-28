package ui

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
)

func TestNewListModel(t *testing.T) {
	model := NewListModel()

	if model.SelectedIndex() != 0 {
		t.Errorf("NewListModel() SelectedIndex() = %d, want 0", model.SelectedIndex())
	}

	items := []string{"item1", "item2", "item3"}
	model.SetItems(items)

	if model.SelectedIndex() != 0 {
		t.Errorf("SelectedIndex() = %d, want 0", model.SelectedIndex())
	}

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

	if model.SelectedIndex() < 0 || model.SelectedIndex() >= len(items) {
		t.Errorf("SelectedIndex() = %d, should be in range [0, %d)", model.SelectedIndex(), len(items))
	}
}

func TestListModel_SelectedIndex(t *testing.T) {
	model := NewListModel()
	items := []string{"item1", "item2", "item3"}
	model.SetItems(items)

	if model.SelectedIndex() != 0 {
		t.Errorf("SelectedIndex() = %d, want 0", model.SelectedIndex())
	}
}

func TestListModel_Init(t *testing.T) {
	model := NewListModel()
	cmd := model.Init()

	if cmd != nil {
		t.Error("ListModel.Init() should return nil")
	}
}

func TestListModel_View(t *testing.T) {
	model := NewListModel()

	view1 := model.View()
	if view1 == "" {
		t.Error("ListModel.View() returned empty string with empty items")
	}

	items := []string{"item1", "item2", "item3"}
	model.SetItems(items)
	view2 := model.View()

	if view2 == "" {
		t.Error("ListModel.View() returned empty string with items")
	}

	if view1 == view2 {
		t.Error("ListModel.View() should be different when items are set")
	}
}

func TestListModel_Update(t *testing.T) {
	model := NewListModel()
	items := []string{"item1", "item2", "item3", "item4"}
	model.SetItems(items)

	sizeMsg := tea.WindowSizeMsg{Width: 100, Height: 50}
	newModel, cmd := model.Update(sizeMsg)
	if cmd != nil {
		t.Error("ListModel.Update(WindowSizeMsg) should return nil command")
	}
	_ = newModel

	upKey := tea.KeyPressMsg{Code: tea.KeyUp}
	modelAfterUp, cmdUp := model.Update(upKey)
	if cmdUp != nil {
		t.Error("ListModel.Update(KeyUp) should return nil command")
	}
	_ = modelAfterUp

	downKey := tea.KeyPressMsg{Code: tea.KeyDown}
	modelAfterDown, cmdDown := model.Update(downKey)
	if cmdDown != nil {
		t.Error("ListModel.Update(KeyDown) should return nil command")
	}
	_ = modelAfterDown

	kKey := tea.KeyPressMsg{Code: 'k', Text: "k"}
	modelAfterK, cmdK := model.Update(kKey)
	if cmdK != nil {
		t.Error("ListModel.Update('k') should return nil command")
	}
	_ = modelAfterK

	jKey := tea.KeyPressMsg{Code: 'j', Text: "j"}
	modelAfterJ, cmdJ := model.Update(jKey)
	if cmdJ != nil {
		t.Error("ListModel.Update('j') should return nil command")
	}
	_ = modelAfterJ
}

func TestListModel_SetItems_CursorAdjustment(t *testing.T) {
	model := NewListModel()

	items1 := []string{"item1", "item2", "item3"}
	model.SetItems(items1)

	for range len(items1) {
		downKey := tea.KeyPressMsg{Code: tea.KeyDown}
		model, _ = model.Update(downKey)
	}

	items2 := []string{"item1", "item2"}
	model.SetItems(items2)

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

	if !strings.Contains(view, "No") && !strings.Contains(view, "backup") {
		t.Error("ListModel.View() for empty list should indicate no items")
	}
}

func TestListModel_Navigation_UpDown(t *testing.T) {
	model := NewListModel()
	items := []string{"a", "b", "c", "d"}
	model.SetItems(items)

	if model.SelectedIndex() != 0 {
		t.Fatal("expected cursor at 0")
	}

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	if model.SelectedIndex() != 1 {
		t.Errorf("after down: got %d, want 1", model.SelectedIndex())
	}

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	if model.SelectedIndex() != 3 {
		t.Errorf("after 3 downs: got %d, want 3", model.SelectedIndex())
	}

	// Should not go past end
	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	if model.SelectedIndex() != 3 {
		t.Errorf("past end: got %d, want 3", model.SelectedIndex())
	}

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyUp})
	if model.SelectedIndex() != 2 {
		t.Errorf("after up: got %d, want 2", model.SelectedIndex())
	}
}

func TestListModel_Navigation_HomeEnd(t *testing.T) {
	model := NewListModel()
	items := []string{"a", "b", "c", "d", "e"}
	model.SetItems(items)

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})

	// End key (G)
	model, _ = model.Update(tea.KeyPressMsg{Code: 'G', Text: "G"})
	if model.SelectedIndex() != 4 {
		t.Errorf("after G: got %d, want 4", model.SelectedIndex())
	}

	// Home key (g)
	model, _ = model.Update(tea.KeyPressMsg{Code: 'g', Text: "g"})
	if model.SelectedIndex() != 0 {
		t.Errorf("after g: got %d, want 0", model.SelectedIndex())
	}
}

func TestListModel_Navigation_PageUpDown(t *testing.T) {
	model := NewListModel()
	items := make([]string, 50)
	for i := range items {
		items[i] = "item"
	}
	model.SetItems(items)

	// Set a page size by sending a window size
	model, _ = model.Update(tea.WindowSizeMsg{Width: 80, Height: 20})

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyPgDown})
	if model.SelectedIndex() == 0 {
		t.Error("pgdown should move cursor forward")
	}

	prevIdx := model.SelectedIndex()
	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyPgUp})
	if model.SelectedIndex() >= prevIdx {
		t.Error("pgup should move cursor backward")
	}
}

func TestListModel_ViewportScrollIndicators(t *testing.T) {
	model := NewListModel()
	items := make([]string, 50)
	for i := range items {
		items[i] = "item"
	}
	model.SetItems(items)
	model, _ = model.Update(tea.WindowSizeMsg{Width: 80, Height: 20})

	// Move to middle
	for range 25 {
		model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	}

	view := model.View()
	if !strings.Contains(view, "more above") {
		t.Error("expected scroll-up indicator when scrolled down")
	}
	if !strings.Contains(view, "more below") {
		t.Error("expected scroll-down indicator when not at end")
	}
}

// --- Vim key navigation ---

func TestListModel_VimKeys_JK(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{"a", "b", "c"})

	// j moves down
	model, _ = model.Update(tea.KeyPressMsg{Code: 'j', Text: "j"})
	if model.SelectedIndex() != 1 {
		t.Errorf("j should move cursor to 1, got %d", model.SelectedIndex())
	}

	model, _ = model.Update(tea.KeyPressMsg{Code: 'j', Text: "j"})
	if model.SelectedIndex() != 2 {
		t.Errorf("second j should move cursor to 2, got %d", model.SelectedIndex())
	}

	// k moves up
	model, _ = model.Update(tea.KeyPressMsg{Code: 'k', Text: "k"})
	if model.SelectedIndex() != 1 {
		t.Errorf("k should move cursor to 1, got %d", model.SelectedIndex())
	}
}

// --- Boundary: up at 0, down at end ---

func TestListModel_UpAtZero(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{"a", "b"})

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyUp})
	if model.SelectedIndex() != 0 {
		t.Errorf("up at 0 should stay 0, got %d", model.SelectedIndex())
	}

	model, _ = model.Update(tea.KeyPressMsg{Code: 'k', Text: "k"})
	if model.SelectedIndex() != 0 {
		t.Errorf("k at 0 should stay 0, got %d", model.SelectedIndex())
	}
}

func TestListModel_DownAtEnd(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{"a", "b"})

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	if model.SelectedIndex() != 1 {
		t.Errorf("down past end should stay at last, got %d", model.SelectedIndex())
	}
}

// --- View shows selected indicator ---

func TestListModel_ViewShowsSelectedIndicator(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{"first item", "second item"})

	view := model.View()
	if !strings.Contains(view, "▶") {
		t.Error("list view should contain ▶ for selected item")
	}
}

// --- View shows position indicator ---

func TestListModel_ViewShowsPosition(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{"a", "b", "c"})

	view := model.View()
	if !strings.Contains(view, "1/3") {
		t.Errorf("list view should show position '1/3', got: %s", view)
	}

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	view = model.View()
	if !strings.Contains(view, "2/3") {
		t.Errorf("list view should show position '2/3' after down, got: %s", view)
	}
}

// --- View shows header with column names ---

func TestListModel_ViewShowsHeader(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{"item"})

	view := model.View()
	if !strings.Contains(view, "Type") || !strings.Contains(view, "Resource") {
		t.Error("list view should show column header")
	}
}

// --- PageUp at start stays at 0 ---

func TestListModel_PageUpAtStart(t *testing.T) {
	model := NewListModel()
	items := make([]string, 30)
	for i := range items {
		items[i] = "item"
	}
	model.SetItems(items)
	model, _ = model.Update(tea.WindowSizeMsg{Width: 80, Height: 20})

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyPgUp})
	if model.SelectedIndex() != 0 {
		t.Errorf("pgup at start should stay 0, got %d", model.SelectedIndex())
	}
}

// --- PageDown then PageUp round trips ---

func TestListModel_PageDownThenPageUp(t *testing.T) {
	model := NewListModel()
	items := make([]string, 100)
	for i := range items {
		items[i] = "item"
	}
	model.SetItems(items)
	model, _ = model.Update(tea.WindowSizeMsg{Width: 80, Height: 20})

	// Move down one page
	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyPgDown})
	pos := model.SelectedIndex()
	if pos == 0 {
		t.Error("pgdown should have moved cursor")
	}

	// Move back up
	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyPgUp})
	if model.SelectedIndex() >= pos {
		t.Error("pgup should move cursor back up")
	}
}

// --- Home/End with empty list ---

func TestListModel_HomeEndEmpty(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{})

	model, _ = model.Update(tea.KeyPressMsg{Code: 'G', Text: "G"})
	if model.SelectedIndex() != 0 {
		t.Error("G on empty list should stay at 0")
	}

	model, _ = model.Update(tea.KeyPressMsg{Code: 'g', Text: "g"})
	if model.SelectedIndex() != 0 {
		t.Error("g on empty list should stay at 0")
	}
}

// --- SetItems with longer list then shorter ---

func TestListModel_SetItems_ShrinkList(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{"a", "b", "c", "d", "e"})

	// Navigate to end
	model, _ = model.Update(tea.KeyPressMsg{Code: 'G', Text: "G"})
	if model.SelectedIndex() != 4 {
		t.Fatalf("expected cursor at 4, got %d", model.SelectedIndex())
	}

	// Shrink to 2 items
	model.SetItems([]string{"x", "y"})
	if model.SelectedIndex() != 1 {
		t.Errorf("cursor should clamp to last item (1), got %d", model.SelectedIndex())
	}
}

// --- SetItems with empty then non-empty ---

func TestListModel_SetItems_EmptyThenPopulate(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{})

	if model.SelectedIndex() != 0 {
		t.Error("empty list cursor should be 0")
	}

	model.SetItems([]string{"a", "b", "c"})
	if model.SelectedIndex() != 0 {
		t.Error("populated list cursor should start at 0")
	}
}

// --- WindowSizeMsg updates dimensions ---

func TestListModel_WindowSizeMsg(t *testing.T) {
	model := NewListModel()
	items := make([]string, 5)
	for i := range items {
		items[i] = "item"
	}
	model.SetItems(items)

	model, _ = model.Update(tea.WindowSizeMsg{Width: 120, Height: 40})

	// After resize, view should still work
	view := model.View()
	if view == "" {
		t.Error("view after resize should not be empty")
	}
}

// --- No scroll indicators at top ---

func TestListModel_NoScrollUp_AtTop(t *testing.T) {
	model := NewListModel()
	items := make([]string, 50)
	for i := range items {
		items[i] = "item"
	}
	model.SetItems(items)
	model, _ = model.Update(tea.WindowSizeMsg{Width: 80, Height: 20})

	view := model.View()
	if strings.Contains(view, "more above") {
		t.Error("should not show scroll-up indicator at top")
	}
}

// --- No scroll indicators when all items visible ---

func TestListModel_NoScrollIndicators_SmallList(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{"a", "b", "c"})
	model, _ = model.Update(tea.WindowSizeMsg{Width: 80, Height: 50})

	view := model.View()
	if strings.Contains(view, "more above") || strings.Contains(view, "more below") {
		t.Error("should not show scroll indicators when all items are visible")
	}
}

// --- PageDown on empty list ---

func TestListModel_PageDown_EmptyList(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{})
	model, _ = model.Update(tea.WindowSizeMsg{Width: 80, Height: 20})

	model, _ = model.Update(tea.KeyPressMsg{Code: tea.KeyPgDown})
	if model.SelectedIndex() != 0 {
		t.Errorf("pgdown on empty list should stay at 0, got %d", model.SelectedIndex())
	}
}

// --- Unrelated message type is ignored ---

func TestListModel_IgnoresUnknownMsg(t *testing.T) {
	model := NewListModel()
	model.SetItems([]string{"a", "b"})

	type customMsg struct{}
	updated, cmd := model.Update(customMsg{})
	if cmd != nil {
		t.Error("unknown message should return nil cmd")
	}
	if updated.SelectedIndex() != 0 {
		t.Error("unknown message should not change cursor")
	}
}
