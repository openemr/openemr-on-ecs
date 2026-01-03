package ui

import (
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/openemr/openemr-on-ecs/scripts/backup-tui/internal/aws"
)

func TestFormatBytes(t *testing.T) {
	tests := []struct {
		name     string
		input    int64
		expected string
	}{
		{"Zero bytes", 0, "0 B"},
		{"Less than 1 KB", 512, "512 B"},
		{"Exactly 1 KB", 1024, "1.0 KB"},
		{"1.5 KB", 1536, "1.5 KB"},
		{"Less than 1 MB", 512 * 1024, "512.0 KB"},
		{"Exactly 1 MB", 1024 * 1024, "1.0 MB"},
		{"1.5 MB", 1536 * 1024, "1.5 MB"},
		{"Less than 1 GB", 512 * 1024 * 1024, "512.0 MB"},
		{"Exactly 1 GB", 1024 * 1024 * 1024, "1.0 GB"},
		{"1.5 GB", 1536 * 1024 * 1024, "1.5 GB"},
		{"Large value", 5 * 1024 * 1024 * 1024, "5.0 GB"},
		{"Very large value", 2500 * 1024 * 1024 * 1024, "2.4 TB"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := formatBytes(tt.input)
			if result != tt.expected {
				t.Errorf("formatBytes(%d) = %q, want %q", tt.input, result, tt.expected)
			}
		})
	}
}

func TestTruncateString(t *testing.T) {
	tests := []struct {
		name     string
		input    string
		maxLen   int
		expected string
	}{
		{"Empty string", "", 10, ""},
		{"String shorter than max", "short", 10, "short"},
		{"String exactly max length", "exactlyten", 10, "exactlyten"},
		{"String longer than max", "very long string", 10, "very lo..."},
		{"String much longer than max", "this is a very long string that should be truncated", 20, "this is a very lo..."},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := truncateString(tt.input, tt.maxLen)
			if result != tt.expected {
				t.Errorf("truncateString(%q, %d) = %q, want %q", tt.input, tt.maxLen, result, tt.expected)
			}
		})
	}
}

func TestNewDetailModel(t *testing.T) {
	model := NewDetailModel()

	// Test that model is initialized (can call View without panicking)
	view := model.View()
	if view == "" {
		t.Error("NewDetailModel().View() returned empty string")
	}
}

func TestDetailModel_Init(t *testing.T) {
	model := NewDetailModel()
	cmd := model.Init()

	// Init should return nil (no commands needed)
	if cmd != nil {
		t.Error("DetailModel.Init() should return nil")
	}
}

func TestDetailModel_SetRecoveryPoint(t *testing.T) {
	model := NewDetailModel()

	// Test setting a recovery point
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123456789012:recovery-point:rp-123",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "my-cluster",
		BackupSizeInBytes: 1024 * 1024 * 1024,
	}

	model.SetRecoveryPoint(rp)

	// Verify View renders something (not empty)
	view := model.View()
	if view == "" {
		t.Error("DetailModel.View() returned empty string after SetRecoveryPoint")
	}
}

func TestDetailModel_View(t *testing.T) {
	model := NewDetailModel()

	// Test View with no recovery point (should show empty/default state)
	view1 := model.View()
	if view1 == "" {
		t.Error("DetailModel.View() returned empty string with no recovery point")
	}

	// Test View with a recovery point
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123456789012:recovery-point:rp-123",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "my-cluster",
		BackupSizeInBytes: 1024 * 1024 * 1024,
	}
	model.SetRecoveryPoint(rp)

	view2 := model.View()
	if view2 == "" {
		t.Error("DetailModel.View() returned empty string with recovery point")
	}

	// View should be different when recovery point is set
	if view1 == view2 {
		t.Error("DetailModel.View() should be different when recovery point is set")
	}
}

func TestDetailModel_Update(t *testing.T) {
	model := NewDetailModel()

	// Test Update with WindowSizeMsg
	msg := tea.WindowSizeMsg{Width: 100, Height: 50}
	newModel, cmd := model.Update(msg)

	// Should return updated model and nil command
	if cmd != nil {
		t.Error("DetailModel.Update(WindowSizeMsg) should return nil command")
	}

	// Model should be updated (width/height set)
	_ = newModel

	// Test Update with KeyMsg (should return unchanged model)
	keyMsg := tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'q'}}
	newModel2, cmd2 := model.Update(keyMsg)

	if cmd2 != nil {
		t.Error("DetailModel.Update(KeyMsg) should return nil command")
	}

	// Model should be unchanged for key messages
	_ = newModel2
}
