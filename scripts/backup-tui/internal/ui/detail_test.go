package ui

import (
	"image/color"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
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

	view := model.View()
	if view == "" {
		t.Error("NewDetailModel().View() returned empty string")
	}
}

func TestDetailModel_Init(t *testing.T) {
	model := NewDetailModel()
	cmd := model.Init()

	if cmd != nil {
		t.Error("DetailModel.Init() should return nil")
	}
}

func TestDetailModel_SetRecoveryPoint(t *testing.T) {
	model := NewDetailModel()

	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123456789012:recovery-point:rp-123",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "my-cluster",
		BackupSizeInBytes: 1024 * 1024 * 1024,
	}

	model.SetRecoveryPoint(rp)

	view := model.View()
	if view == "" {
		t.Error("DetailModel.View() returned empty string after SetRecoveryPoint")
	}
}

func TestDetailModel_View(t *testing.T) {
	model := NewDetailModel()

	view1 := model.View()
	if view1 == "" {
		t.Error("DetailModel.View() returned empty string with no recovery point")
	}

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

	if view1 == view2 {
		t.Error("DetailModel.View() should be different when recovery point is set")
	}
}

func TestDetailModel_Update(t *testing.T) {
	model := NewDetailModel()

	msg := tea.WindowSizeMsg{Width: 100, Height: 50}
	newModel, cmd := model.Update(msg)

	if cmd != nil {
		t.Error("DetailModel.Update(WindowSizeMsg) should return nil command")
	}
	_ = newModel

	keyMsg := tea.KeyPressMsg{Code: 'q', Text: "q"}
	newModel2, cmd2 := model.Update(keyMsg)

	if cmd2 != nil {
		t.Error("DetailModel.Update(KeyPressMsg) should return nil command")
	}
	_ = newModel2
}

func TestDetailModel_ViewContainsFields(t *testing.T) {
	model := NewDetailModel()

	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123456789012:recovery-point:rp-abc",
		CreationDate:      time.Date(2026, 1, 15, 10, 30, 0, 0, time.UTC),
		Status:            "COMPLETED",
		ResourceType:      "EFS",
		ResourceID:        "fs-12345678",
		BackupSizeInBytes: 2048 * 1024 * 1024,
	}
	model.SetRecoveryPoint(rp)

	view := model.View()

	checks := []string{"EFS", "fs-12345678", "COMPLETED", "2026-01-15", "2.0 GB"}
	for _, want := range checks {
		if !strings.Contains(view, want) {
			t.Errorf("DetailModel.View() should contain %q", want)
		}
	}
}

func TestDetailModel_ViewContainsRestoreButton(t *testing.T) {
	model := NewDetailModel()
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123456789012:recovery-point:rp-x",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "cluster-1",
		BackupSizeInBytes: 1024,
	}
	model.SetRecoveryPoint(rp)

	view := model.View()
	if !strings.Contains(view, "ENTER") && !strings.Contains(view, "restore") {
		t.Error("DetailModel.View() should contain restore action text")
	}
}

func TestDetailModel_ViewContainsRelativeTime(t *testing.T) {
	model := NewDetailModel()
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123456789012:recovery-point:rp-rel",
		CreationDate:      time.Now().Add(-3 * time.Hour),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "cluster-rel",
		BackupSizeInBytes: 1024,
	}
	model.SetRecoveryPoint(rp)

	view := model.View()
	if !strings.Contains(view, "3h ago") {
		t.Error("DetailModel.View() should contain relative time string")
	}
}

func TestDefaultRelativeTime(t *testing.T) {
	now := time.Now()
	tests := []struct {
		name     string
		t        time.Time
		contains string
	}{
		{"just now", now.Add(-5 * time.Second), "just now"},
		{"minutes", now.Add(-10 * time.Minute), "10m ago"},
		{"hours", now.Add(-5 * time.Hour), "5h ago"},
		{"days", now.Add(-3 * 24 * time.Hour), "3d ago"},
		{"months", now.Add(-45 * 24 * time.Hour), "mo ago"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := defaultRelativeTime(tt.t)
			if !strings.Contains(result, tt.contains) {
				t.Errorf("defaultRelativeTime() = %q, want to contain %q", result, tt.contains)
			}
		})
	}
}

func TestDefaultFreshnessColor(t *testing.T) {
	now := time.Now()
	fresh := defaultFreshnessColor(now.Add(-1 * time.Hour))
	if fresh == nil {
		t.Error("fresh color should not be nil")
	}

	recent := defaultFreshnessColor(now.Add(-3 * 24 * time.Hour))
	if recent == nil {
		t.Error("recent color should not be nil")
	}

	stale := defaultFreshnessColor(now.Add(-10 * 24 * time.Hour))
	if stale == nil {
		t.Error("stale color should not be nil")
	}
}

// --- Unit Tests: View with nil recovery point ---

func TestDetailModel_ViewNilRecoveryPoint(t *testing.T) {
	model := DetailModel{}
	view := model.View()
	if !strings.Contains(view, "No backup selected") {
		t.Errorf("nil recovery point view should say 'No backup selected', got: %s", view)
	}
}

// --- Unit Tests: ARN truncation ---

func TestDetailModel_ViewTruncatesLongARN(t *testing.T) {
	model := NewDetailModel()
	longARN := "arn:aws:backup:us-west-2:123456789012:recovery-point:very-long-recovery-point-identifier-that-exceeds-sixty-characters"
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  longARN,
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "cluster",
		BackupSizeInBytes: 1024,
	}
	model.SetRecoveryPoint(rp)

	view := model.View()
	// The full ARN should not appear (it would be >60 chars), but truncated version should
	if strings.Contains(view, longARN) {
		t.Error("view should truncate long ARN")
	}
	if !strings.Contains(view, "...") {
		t.Error("view should show ... for truncated ARN")
	}
}

// --- Unit Tests: View contains controls section ---

func TestDetailModel_ViewContainsControls(t *testing.T) {
	model := NewDetailModel()
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123:recovery-point:rp-x",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "my-cluster",
		BackupSizeInBytes: 1024,
	}
	model.SetRecoveryPoint(rp)

	view := model.View()
	if !strings.Contains(view, "Controls") {
		t.Error("detail view should contain Controls section")
	}
	if !strings.Contains(view, "ENTER") {
		t.Error("detail view should mention ENTER for restore")
	}
	if !strings.Contains(view, "b/←") {
		t.Error("detail view should mention b/← for going back")
	}
}

// --- Unit Tests: Custom DetailRelativeTime override ---

func TestDetailModel_CustomRelativeTimeHook(t *testing.T) {
	originalFn := DetailRelativeTime
	defer func() { DetailRelativeTime = originalFn }()

	DetailRelativeTime = func(_ time.Time) string {
		return "custom-time"
	}

	model := NewDetailModel()
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123:rp:test",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "EFS",
		ResourceID:        "fs-hook",
		BackupSizeInBytes: 512,
	}
	model.SetRecoveryPoint(rp)

	view := model.View()
	if !strings.Contains(view, "custom-time") {
		t.Error("view should use custom DetailRelativeTime function")
	}
}

// --- Unit Tests: Custom DetailFreshnessColor override ---

func TestDetailModel_CustomFreshnessColorHook(t *testing.T) {
	originalFn := DetailFreshnessColor
	defer func() { DetailFreshnessColor = originalFn }()

	DetailFreshnessColor = func(_ time.Time) color.Color {
		return lipgloss.Color("99")
	}

	model := NewDetailModel()
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123:rp:colortest",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "cluster-color",
		BackupSizeInBytes: 2048,
	}
	model.SetRecoveryPoint(rp)

	view := model.View()
	if view == "" {
		t.Error("view with custom color hook should not be empty")
	}
}

// --- Unit Tests: formatBytes edge cases in UI package ---

func TestFormatBytes_OneByte(t *testing.T) {
	result := formatBytes(1)
	if result != "1 B" {
		t.Errorf("formatBytes(1) = %q, want '1 B'", result)
	}
}

func TestFormatBytes_Exactly1023(t *testing.T) {
	result := formatBytes(1023)
	if result != "1023 B" {
		t.Errorf("formatBytes(1023) = %q, want '1023 B'", result)
	}
}

func TestFormatBytes_JustOverKB(t *testing.T) {
	result := formatBytes(1025)
	if !strings.Contains(result, "KB") {
		t.Errorf("formatBytes(1025) = %q, want KB", result)
	}
}

// --- Unit Tests: truncateString edge cases ---

func TestTruncateString_MaxLen3(t *testing.T) {
	result := truncateString("abcdef", 3)
	if result != "..." {
		t.Errorf("truncateString('abcdef', 3) = %q, want '...'", result)
	}
}

func TestTruncateString_ExactlyMaxLen(t *testing.T) {
	result := truncateString("abc", 3)
	if result != "abc" {
		t.Errorf("truncateString('abc', 3) = %q, want 'abc'", result)
	}
}

// --- Unit Tests: SetRecoveryPoint nil ---

func TestDetailModel_SetRecoveryPointNil(t *testing.T) {
	model := NewDetailModel()
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123:rp:test",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "cluster",
		BackupSizeInBytes: 1024,
	}
	model.SetRecoveryPoint(rp)

	view1 := model.View()
	if strings.Contains(view1, "No backup selected") {
		t.Error("should have content after setting RP")
	}

	model.SetRecoveryPoint(nil)
	view2 := model.View()
	if !strings.Contains(view2, "No backup selected") {
		t.Error("should show 'No backup selected' after setting nil")
	}
}

// --- Unit Tests: Update with non-WindowSizeMsg ---

func TestDetailModel_UpdateIgnoresRandomMsg(t *testing.T) {
	model := NewDetailModel()
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:test",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "cluster",
		BackupSizeInBytes: 100,
	}
	model.SetRecoveryPoint(rp)

	type customMsg struct{}
	updated, cmd := model.Update(customMsg{})
	if cmd != nil {
		t.Error("custom msg should not produce a command")
	}
	_ = updated
}

// --- Unit Tests: defaultRelativeTime boundary precision ---

func TestDefaultRelativeTime_Boundaries(t *testing.T) {
	now := time.Now()
	tests := []struct {
		name     string
		t        time.Time
		contains string
	}{
		{"30 seconds", now.Add(-30 * time.Second), "just now"},
		{"exactly 60 seconds", now.Add(-60 * time.Second), "1m ago"},
		{"2 minutes", now.Add(-2 * time.Minute), "2m ago"},
		{"exactly 60 minutes", now.Add(-60 * time.Minute), "1h ago"},
		{"exactly 24 hours", now.Add(-24 * time.Hour), "1d ago"},
		{"exactly 30 days", now.Add(-30 * 24 * time.Hour), "1mo ago"},
		{"60 days", now.Add(-60 * 24 * time.Hour), "2mo ago"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := defaultRelativeTime(tt.t)
			if !strings.Contains(result, tt.contains) {
				t.Errorf("defaultRelativeTime() = %q, want to contain %q", result, tt.contains)
			}
		})
	}
}

// --- Unit Tests: defaultFreshnessColor boundary cases ---

func TestDefaultFreshnessColor_Boundaries(t *testing.T) {
	now := time.Now()

	// Just under 24h -> should be green (114)
	c1 := defaultFreshnessColor(now.Add(-23 * time.Hour))
	if c1 == nil {
		t.Error("just under 24h color should not be nil")
	}

	// Just over 24h -> should be yellow (214)
	c2 := defaultFreshnessColor(now.Add(-25 * time.Hour))
	if c2 == nil {
		t.Error("just over 24h color should not be nil")
	}

	// Just over 7d -> should be red (196)
	c3 := defaultFreshnessColor(now.Add(-8 * 24 * time.Hour))
	if c3 == nil {
		t.Error("over 7d color should not be nil")
	}
}

// --- Unit Tests: WindowSizeMsg stores dimensions ---

func TestDetailModel_WindowSizeStoresDimensions(t *testing.T) {
	model := NewDetailModel()

	model, _ = model.Update(tea.WindowSizeMsg{Width: 150, Height: 60})

	// We can't directly check private fields, but the model should still render fine
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:test",
		CreationDate:      time.Now(),
		Status:            "COMPLETED",
		ResourceType:      "RDS",
		ResourceID:        "cluster",
		BackupSizeInBytes: 1024,
	}
	model.SetRecoveryPoint(rp)
	view := model.View()
	if view == "" {
		t.Error("view after resize should not be empty")
	}
}

// --- Unit Tests: View contains all label fields ---

func TestDetailModel_ViewContainsAllLabels(t *testing.T) {
	model := NewDetailModel()
	rp := &aws.RecoveryPoint{
		RecoveryPointARN:  "arn:aws:backup:us-west-2:123:rp:labels",
		CreationDate:      time.Now().Add(-48 * time.Hour),
		Status:            "COMPLETED",
		ResourceType:      "EFS",
		ResourceID:        "fs-labels-test",
		BackupSizeInBytes: 5 * 1024 * 1024,
	}
	model.SetRecoveryPoint(rp)

	view := model.View()
	labels := []string{"Resource Type:", "Resource ID:", "Status:", "Created:", "Size:", "Recovery Point ARN:"}
	for _, label := range labels {
		if !strings.Contains(view, label) {
			t.Errorf("view should contain label %q", label)
		}
	}
}
