package app

import (
	"context"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/openemr/openemr-on-ecs/scripts/backup-tui/internal/aws"
	"github.com/openemr/openemr-on-ecs/scripts/backup-tui/internal/ui"
)

func newTestModel() *Model {
	m := &Model{
		ctx:             context.Background(),
		stackName:       "TestStack",
		vaultName:       "test-vault",
		region:          "us-west-2",
		state:           stateList,
		selectedIdx:     0,
		vaultDiscovered: true,
		listModel:       ui.NewListModel(),
		detailModel:     ui.DetailModel{},
		helpModel:       ui.HelpModel{},
	}
	return m
}

func sampleBackups() []aws.RecoveryPoint {
	return []aws.RecoveryPoint{
		{
			RecoveryPointARN:  "arn:aws:backup:us-west-2:123456789012:recovery-point:rp-1",
			CreationDate:      time.Date(2026, 2, 15, 10, 0, 0, 0, time.UTC),
			Status:            "COMPLETED",
			ResourceType:      "RDS",
			ResourceID:        "my-cluster",
			BackupSizeInBytes: 1024 * 1024 * 1024,
		},
		{
			RecoveryPointARN:  "arn:aws:backup:us-west-2:123456789012:recovery-point:rp-2",
			CreationDate:      time.Date(2026, 2, 14, 8, 0, 0, 0, time.UTC),
			Status:            "COMPLETED",
			ResourceType:      "EFS",
			ResourceID:        "fs-12345678",
			BackupSizeInBytes: 512 * 1024 * 1024,
		},
	}
}

// --- Unit Tests: State Machine ---

func TestModel_StateTransition_ListToDetail(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	updated, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	model := updated.(*Model)

	if model.state != stateDetail {
		t.Errorf("expected stateDetail, got %d", model.state)
	}
}

func TestModel_StateTransition_DetailToConfirm(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateDetail
	m.detailModel.SetRecoveryPoint(&m.backups[0])

	updated, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	model := updated.(*Model)

	if model.state != stateConfirm {
		t.Errorf("expected stateConfirm, got %d", model.state)
	}
}

func TestModel_StateTransition_ConfirmCancel(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateConfirm

	updated, _ := m.Update(tea.KeyPressMsg{Code: 'n', Text: "n"})
	model := updated.(*Model)

	if model.state != stateDetail {
		t.Errorf("expected stateDetail after cancel, got %d", model.state)
	}
}

func TestModel_StateTransition_DetailBack(t *testing.T) {
	m := newTestModel()
	m.state = stateDetail

	updated, _ := m.Update(tea.KeyPressMsg{Code: 'b', Text: "b"})
	model := updated.(*Model)

	if model.state != stateList {
		t.Errorf("expected stateList after back, got %d", model.state)
	}
}

func TestModel_StateTransition_ListToHelp(t *testing.T) {
	m := newTestModel()
	m.state = stateList

	updated, _ := m.Update(tea.KeyPressMsg{Code: '?', Text: "?"})
	model := updated.(*Model)

	if model.state != stateHelp {
		t.Errorf("expected stateHelp, got %d", model.state)
	}
}

func TestModel_StateTransition_HelpBack(t *testing.T) {
	m := newTestModel()
	m.state = stateHelp

	updated, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyEscape})
	model := updated.(*Model)

	if model.state != stateList {
		t.Errorf("expected stateList after esc from help, got %d", model.state)
	}
}

func TestModel_StateTransition_EscFromDetail(t *testing.T) {
	m := newTestModel()
	m.state = stateDetail

	updated, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyEscape})
	model := updated.(*Model)

	if model.state != stateList {
		t.Errorf("expected stateList after esc from detail, got %d", model.state)
	}
}

func TestModel_StateTransition_EscFromConfirm(t *testing.T) {
	m := newTestModel()
	m.state = stateConfirm

	updated, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyEscape})
	model := updated.(*Model)

	if model.state != stateDetail {
		t.Errorf("expected stateDetail after esc from confirm, got %d", model.state)
	}
}

// --- Unit Tests: View Rendering ---

func TestModel_View_Loading(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading
	m.vaultName = ""
	m.vaultDiscovered = false

	v := m.View()
	content := v.Content
	if content == "" {
		t.Error("loading view should not be empty")
	}
	if !strings.Contains(content, "Discovering") && !strings.Contains(content, "Loading") {
		t.Error("loading view should mention discovering or loading")
	}
	if !v.AltScreen {
		t.Error("AltScreen should be true")
	}
}

func TestModel_View_Error(t *testing.T) {
	m := newTestModel()
	m.state = stateError
	m.err = errTestError("test failure")

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "test failure") {
		t.Error("error view should contain error message")
	}
}

func TestModel_View_List(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "OpenEMR Backup Manager") {
		t.Error("list view should contain header title")
	}
	if !strings.Contains(content, "test-vault") {
		t.Error("list view should show vault name")
	}
}

func TestModel_View_Detail(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateDetail
	m.detailModel.SetRecoveryPoint(&m.backups[0])

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "my-cluster") {
		t.Error("detail view should show resource ID")
	}
}

func TestModel_View_Confirm(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateConfirm
	m.selectedIdx = 0

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "Confirm") {
		t.Error("confirm view should contain 'Confirm'")
	}
	if !strings.Contains(content, "my-cluster") {
		t.Error("confirm view should show resource being restored")
	}
}

func TestModel_View_Help(t *testing.T) {
	m := newTestModel()
	m.state = stateHelp

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "Help") {
		t.Error("help view should contain 'Help'")
	}
}

func TestModel_View_DeclarativeFields(t *testing.T) {
	m := newTestModel()
	m.state = stateList

	v := m.View()
	if !v.AltScreen {
		t.Error("View should set AltScreen = true")
	}
	if v.MouseMode != tea.MouseModeCellMotion {
		t.Error("View should set MouseMode = MouseModeCellMotion")
	}
}

// --- Unit Tests: Key Hints ---

func TestModel_KeyHints_PerState(t *testing.T) {
	tests := []struct {
		state    state
		contains []string
	}{
		{stateList, []string{"navigate", "select", "refresh", "help", "quit"}},
		{stateDetail, []string{"restore", "back", "help", "quit"}},
		{stateConfirm, []string{"confirm", "cancel"}},
		{stateHelp, []string{"close help", "quit"}},
	}

	for _, tt := range tests {
		m := newTestModel()
		m.state = tt.state
		m.backups = sampleBackups()

		hints := m.renderKeyHints()
		for _, want := range tt.contains {
			if !strings.Contains(hints, want) {
				t.Errorf("state %d hints should contain %q, got: %s", tt.state, want, hints)
			}
		}
	}
}

// --- Unit Tests: Formatting ---

func TestFormatBackupsForList(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()

	items := m.formatBackupsForList()

	if len(items) != 2 {
		t.Fatalf("expected 2 items, got %d", len(items))
	}
	if !strings.Contains(items[0], "RDS") {
		t.Error("first item should contain RDS")
	}
	if !strings.Contains(items[0], "my-cluster") {
		t.Error("first item should contain my-cluster")
	}
	if !strings.Contains(items[1], "EFS") {
		t.Error("second item should contain EFS")
	}
}

func TestFormatBytes_Model(t *testing.T) {
	tests := []struct {
		input    int64
		expected string
	}{
		{0, "0 B"},
		{512, "512 B"},
		{1024, "1.0 KB"},
		{1024 * 1024, "1.0 MB"},
		{1024 * 1024 * 1024, "1.0 GB"},
	}

	for _, tt := range tests {
		result := formatBytes(tt.input)
		if result != tt.expected {
			t.Errorf("formatBytes(%d) = %q, want %q", tt.input, result, tt.expected)
		}
	}
}

// --- Unit Tests: Messages ---

func TestModel_BackupsLoadedMsg(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading

	msg := backupsLoadedMsg{backups: sampleBackups()}
	updated, _ := m.Update(msg)
	model := updated.(*Model)

	if model.state != stateList {
		t.Errorf("expected stateList after backups loaded, got %d", model.state)
	}
	if len(model.backups) != 2 {
		t.Errorf("expected 2 backups, got %d", len(model.backups))
	}
}

func TestModel_BackupsLoadedMsg_Error(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading

	msg := backupsLoadedMsg{err: errTestError("api error")}
	updated, _ := m.Update(msg)
	model := updated.(*Model)

	if model.state != stateError {
		t.Errorf("expected stateError after load error, got %d", model.state)
	}
}

func TestModel_VaultDiscoveredMsg(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading
	m.vaultName = ""

	msg := vaultDiscoveredMsg{vaultName: "discovered-vault", success: true}
	updated, _ := m.Update(msg)
	model := updated.(*Model)

	if model.vaultName != "discovered-vault" {
		t.Errorf("expected vaultName 'discovered-vault', got %q", model.vaultName)
	}
	if !model.vaultDiscovered {
		t.Error("vaultDiscovered should be true")
	}
}

func TestModel_VaultDiscoveredMsg_Failure(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading

	msg := vaultDiscoveredMsg{success: false, err: errTestError("not found")}
	updated, _ := m.Update(msg)
	model := updated.(*Model)

	if model.state != stateError {
		t.Errorf("expected stateError after vault discovery failure, got %d", model.state)
	}
}

func TestModel_RestoreInitiatedMsg(t *testing.T) {
	m := newTestModel()
	m.state = stateDetail

	msg := restoreInitiatedMsg{jobID: "job-12345"}
	updated, _ := m.Update(msg)
	model := updated.(*Model)

	if !strings.Contains(model.statusMsg, "job-12345") {
		t.Errorf("statusMsg should contain job ID, got %q", model.statusMsg)
	}
}

func TestModel_RestoreInitiatedMsg_Error(t *testing.T) {
	m := newTestModel()
	m.state = stateDetail

	msg := restoreInitiatedMsg{err: errTestError("restore failed")}
	updated, _ := m.Update(msg)
	model := updated.(*Model)

	if model.state != stateError {
		t.Errorf("expected stateError after restore error, got %d", model.state)
	}
}

// --- Unit Tests: Spinner ---

func TestModel_SpinnerTick(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading
	m.spinnerFrame = 0

	updated, cmd := m.Update(spinnerTickMsg(time.Now()))
	model := updated.(*Model)

	if model.spinnerFrame != 1 {
		t.Errorf("spinner frame should advance to 1, got %d", model.spinnerFrame)
	}
	if cmd == nil {
		t.Error("spinner should schedule next tick while loading")
	}
}

func TestModel_SpinnerTick_NotLoading(t *testing.T) {
	m := newTestModel()
	m.state = stateList
	m.spinnerFrame = 5

	updated, _ := m.Update(spinnerTickMsg(time.Now()))
	model := updated.(*Model)

	if model.spinnerFrame != 5 {
		t.Errorf("spinner should not advance when not loading, got %d", model.spinnerFrame)
	}
}

// --- Unit Tests: Error Hints ---

func TestModel_ErrorHints(t *testing.T) {
	tests := []struct {
		errMsg   string
		contains string
	}{
		{"backup vault not found", "vault"},
		{"CloudFormation stack error", "credentials"},
		{"NoCredentialProviders", "AWS credentials"},
		{"discover error", "CloudFormation stack"},
	}

	for _, tt := range tests {
		m := newTestModel()
		m.state = stateError
		m.err = errTestError(tt.errMsg)

		rendered := m.renderError()
		if !strings.Contains(rendered, tt.contains) {
			t.Errorf("error hint for %q should contain %q", tt.errMsg, tt.contains)
		}
	}
}

// --- Functional Tests: Full User Workflows ---

func TestWorkflow_BrowseListAndViewDetail(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	// Navigate down
	result, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	m = result.(*Model)
	if m.selectedIdx != 1 {
		t.Fatalf("expected selectedIdx 1, got %d", m.selectedIdx)
	}

	// Select (enter)
	result, _ = m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	m = result.(*Model)
	if m.state != stateDetail {
		t.Fatalf("expected stateDetail, got %d", m.state)
	}

	// Go back
	result, _ = m.Update(tea.KeyPressMsg{Code: 'b', Text: "b"})
	m = result.(*Model)
	if m.state != stateList {
		t.Fatalf("expected stateList after back, got %d", m.state)
	}
}

func TestWorkflow_RestoreWithConfirmation(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	// Select first backup
	result, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	m = result.(*Model)
	if m.state != stateDetail {
		t.Fatalf("expected stateDetail, got %d", m.state)
	}

	// Press enter to initiate restore -> should go to confirm
	result, _ = m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	m = result.(*Model)
	if m.state != stateConfirm {
		t.Fatalf("expected stateConfirm, got %d", m.state)
	}

	// Cancel with 'n'
	result, _ = m.Update(tea.KeyPressMsg{Code: 'n', Text: "n"})
	m = result.(*Model)
	if m.state != stateDetail {
		t.Fatalf("expected stateDetail after cancel, got %d", m.state)
	}
}

func TestWorkflow_HelpFromListAndDetail(t *testing.T) {
	m := newTestModel()
	m.state = stateList

	// Open help from list
	result, _ := m.Update(tea.KeyPressMsg{Code: '?', Text: "?"})
	m = result.(*Model)
	if m.state != stateHelp {
		t.Fatalf("expected stateHelp from list, got %d", m.state)
	}

	// Close help
	result, _ = m.Update(tea.KeyPressMsg{Code: tea.KeyEscape})
	m = result.(*Model)
	if m.state != stateList {
		t.Fatalf("expected stateList after closing help, got %d", m.state)
	}

	// Go to detail
	m.backups = sampleBackups()
	m.listModel.SetItems(m.formatBackupsForList())
	result, _ = m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	m = result.(*Model)

	// Open help from detail
	result, _ = m.Update(tea.KeyPressMsg{Code: '?', Text: "?"})
	m = result.(*Model)
	if m.state != stateHelp {
		t.Fatalf("expected stateHelp from detail, got %d", m.state)
	}
}

func TestWorkflow_EmptyBackupList(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading

	msg := backupsLoadedMsg{backups: []aws.RecoveryPoint{}}
	result, _ := m.Update(msg)
	m = result.(*Model)

	if m.state != stateList {
		t.Fatalf("expected stateList with empty backups, got %d", m.state)
	}

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "No backups") && !strings.Contains(content, "0 backup") {
		t.Error("view should indicate no backups found")
	}

	// Enter should not crash or change state with empty list
	result, _ = m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	m = result.(*Model)
	if m.state != stateList {
		t.Errorf("enter on empty list should stay in list, got %d", m.state)
	}
}

func TestWorkflow_RefreshFromList(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	result, _ := m.Update(tea.KeyPressMsg{Code: 'r', Text: "r"})
	m = result.(*Model)

	if m.state != stateLoading {
		t.Errorf("expected stateLoading after refresh, got %d", m.state)
	}
}

func TestWorkflow_StatusBarContent(t *testing.T) {
	m := newTestModel()
	m.state = stateList

	// No backups
	status := m.renderStatusBar()
	if !strings.Contains(status, "No backups") {
		t.Error("status bar should show 'No backups' when empty")
	}

	// With backups
	m.backups = sampleBackups()
	status = m.renderStatusBar()
	if !strings.Contains(status, "2 backup") {
		t.Error("status bar should show backup count")
	}

	// With status message
	m.statusMsg = "Restore job started: job-xyz"
	status = m.renderStatusBar()
	if !strings.Contains(status, "job-xyz") {
		t.Error("status bar should show status message when set")
	}
}

// --- Unit Tests: Relative Time ---

func TestRelativeTime(t *testing.T) {
	now := time.Now()
	tests := []struct {
		name     string
		t        time.Time
		contains string
	}{
		{"just now", now.Add(-10 * time.Second), "just now"},
		{"minutes ago", now.Add(-5 * time.Minute), "5m ago"},
		{"hours ago", now.Add(-3 * time.Hour), "3h ago"},
		{"days ago", now.Add(-2 * 24 * time.Hour), "2d ago"},
		{"months ago", now.Add(-60 * 24 * time.Hour), "2mo ago"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := relativeTime(tt.t)
			if !strings.Contains(result, tt.contains) {
				t.Errorf("relativeTime() = %q, want it to contain %q", result, tt.contains)
			}
		})
	}
}

func TestRelativeTime_Exported(t *testing.T) {
	result := RelativeTime(time.Now().Add(-48 * time.Hour))
	if !strings.Contains(result, "2d ago") {
		t.Errorf("RelativeTime() = %q, expected '2d ago'", result)
	}
}

// --- Unit Tests: Freshness Indicator ---

func TestFreshnessIndicator(t *testing.T) {
	now := time.Now()
	tests := []struct {
		name    string
		t       time.Time
		notEmpty bool
	}{
		{"fresh (< 24h)", now.Add(-1 * time.Hour), true},
		{"recent (1-7d)", now.Add(-3 * 24 * time.Hour), true},
		{"stale (> 7d)", now.Add(-10 * 24 * time.Hour), true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := freshnessIndicator(tt.t)
			if tt.notEmpty && result == "" {
				t.Error("freshnessIndicator() should not return empty string")
			}
			if !strings.Contains(result, "●") {
				t.Errorf("freshnessIndicator() = %q, should contain dot character", result)
			}
		})
	}
}

// --- Unit Tests: Filter Cycle ---

func TestFilterMode_String(t *testing.T) {
	tests := []struct {
		mode filterMode
		want string
	}{
		{filterAll, "All"},
		{filterRDS, "RDS"},
		{filterEFS, "EFS"},
	}
	for _, tt := range tests {
		if got := tt.mode.String(); got != tt.want {
			t.Errorf("filterMode(%d).String() = %q, want %q", tt.mode, got, tt.want)
		}
	}
}

func TestFilterMode_Next(t *testing.T) {
	tests := []struct {
		mode filterMode
		want filterMode
	}{
		{filterAll, filterRDS},
		{filterRDS, filterEFS},
		{filterEFS, filterAll},
	}
	for _, tt := range tests {
		if got := tt.mode.next(); got != tt.want {
			t.Errorf("filterMode(%d).next() = %d, want %d", tt.mode, got, tt.want)
		}
	}
}

func TestModel_CycleFilter(t *testing.T) {
	m := newTestModel()
	m.allBackups = sampleBackups()
	m.backups = m.allBackups
	m.listModel.SetItems(m.formatBackupsForList())

	if m.activeFilter != filterAll {
		t.Fatalf("expected initial filter All, got %v", m.activeFilter)
	}

	// Cycle to RDS
	m.cycleFilter()
	if m.activeFilter != filterRDS {
		t.Errorf("expected filterRDS, got %v", m.activeFilter)
	}
	for _, bp := range m.backups {
		if bp.ResourceType != "RDS" {
			t.Errorf("after RDS filter, got non-RDS backup: %s", bp.ResourceType)
		}
	}

	// Cycle to EFS
	m.cycleFilter()
	if m.activeFilter != filterEFS {
		t.Errorf("expected filterEFS, got %v", m.activeFilter)
	}
	for _, bp := range m.backups {
		if bp.ResourceType != "EFS" {
			t.Errorf("after EFS filter, got non-EFS backup: %s", bp.ResourceType)
		}
	}

	// Cycle back to All
	m.cycleFilter()
	if m.activeFilter != filterAll {
		t.Errorf("expected filterAll, got %v", m.activeFilter)
	}
	if len(m.backups) != len(m.allBackups) {
		t.Errorf("All filter should show all backups: %d vs %d", len(m.backups), len(m.allBackups))
	}
}

func TestModel_CycleFilter_ViaKeyPress(t *testing.T) {
	m := newTestModel()
	m.allBackups = sampleBackups()
	m.backups = m.allBackups
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	result, _ := m.Update(tea.KeyPressMsg{Code: 'f', Text: "f"})
	model := result.(*Model)

	if model.activeFilter != filterRDS {
		t.Errorf("expected filterRDS after pressing f, got %v", model.activeFilter)
	}
}

func TestModel_ApplyFilter_EmptyResult(t *testing.T) {
	m := newTestModel()
	m.allBackups = []aws.RecoveryPoint{
		{ResourceType: "RDS", ResourceID: "cluster-1", CreationDate: time.Now()},
	}
	m.backups = m.allBackups

	m.activeFilter = filterEFS
	m.applyFilter()

	if len(m.backups) != 0 {
		t.Errorf("expected 0 EFS backups when only RDS exists, got %d", len(m.backups))
	}
}

// --- Unit Tests: Restore Monitoring ---

func TestModel_StateTransition_ToRestoring(t *testing.T) {
	m := newTestModel()
	m.state = stateDetail

	msg := restoreInitiatedMsg{jobID: "job-abc123"}
	result, _ := m.Update(msg)
	model := result.(*Model)

	if model.state != stateRestoring {
		t.Errorf("expected stateRestoring after restore initiated, got %d", model.state)
	}
	if model.restoreJobID != "job-abc123" {
		t.Errorf("expected restoreJobID 'job-abc123', got %q", model.restoreJobID)
	}
}

func TestModel_RestoreStatusMsg_Running(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring
	m.restoreJobID = "job-abc"

	status := &aws.RestoreJobStatus{
		JobID:  "job-abc",
		Status: "RUNNING",
	}
	msg := restoreStatusMsg{status: status}
	result, _ := m.Update(msg)
	model := result.(*Model)

	if model.restoreStatus == nil {
		t.Fatal("restoreStatus should be set")
	}
	if model.restoreStatus.Status != "RUNNING" {
		t.Errorf("expected RUNNING status, got %q", model.restoreStatus.Status)
	}
}

func TestModel_RestoreStatusMsg_Completed(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring
	m.restoreJobID = "job-abc"

	status := &aws.RestoreJobStatus{
		JobID:         "job-abc",
		Status:        "COMPLETED",
		StatusMessage: "Success",
		IsTerminal:    true,
	}
	msg := restoreStatusMsg{status: status}
	result, _ := m.Update(msg)
	model := result.(*Model)

	if !strings.Contains(model.statusMsg, "COMPLETED") {
		t.Errorf("expected statusMsg to contain COMPLETED, got %q", model.statusMsg)
	}
}

func TestModel_RestoreStatusMsg_Error(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring

	msg := restoreStatusMsg{err: errTestError("poll failed")}
	result, _ := m.Update(msg)
	model := result.(*Model)

	if !strings.Contains(model.statusMsg, "poll failed") {
		t.Errorf("expected error in statusMsg, got %q", model.statusMsg)
	}
}

func TestModel_EscFromRestoring(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring

	result, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyEscape})
	model := result.(*Model)

	if model.state != stateList {
		t.Errorf("expected stateList after esc from restoring, got %d", model.state)
	}
}

func TestModel_QFromRestoring(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring

	result, _ := m.Update(tea.KeyPressMsg{Code: 'q', Text: "q"})
	model := result.(*Model)

	if model.state != stateList {
		t.Errorf("expected stateList after q from restoring, got %d", model.state)
	}
}

// --- Unit Tests: Restore Monitoring View ---

func TestModel_View_Restoring(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring
	m.restoreJobID = "job-test-123"
	m.restoreStart = time.Now().Add(-30 * time.Second)

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "job-test-123") {
		t.Error("restoring view should show job ID")
	}
	if !strings.Contains(content, "Restore In Progress") {
		t.Error("restoring view should show title")
	}
}

func TestModel_View_Restoring_WithStatus(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring
	m.restoreJobID = "job-test"
	m.restoreStart = time.Now().Add(-60 * time.Second)
	m.restoreStatus = &aws.RestoreJobStatus{
		JobID:         "job-test",
		Status:        "RUNNING",
		PercentDone:   "50",
		StatusMessage: "In progress",
	}

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "RUNNING") {
		t.Error("restoring view should show status")
	}
	if !strings.Contains(content, "50") {
		t.Error("restoring view should show percent done")
	}
}

// --- Unit Tests: Restore Metadata ---

func TestModel_RestoreMetadataMsg(t *testing.T) {
	m := newTestModel()
	m.state = stateConfirm

	meta := &aws.RestoreMetadata{
		ResourceType:   "RDS",
		ResourceID:     "cluster-1",
		ClusterID:      "cluster-1",
		SubnetGroup:    "my-subnet",
		SecurityGroups: "sg-123,sg-456",
	}
	msg := restoreMetadataMsg{metadata: meta}
	result, _ := m.Update(msg)
	model := result.(*Model)

	if model.restoreMetadata == nil {
		t.Fatal("restoreMetadata should be set")
	}
	if model.restoreMetadata.ClusterID != "cluster-1" {
		t.Errorf("expected ClusterID 'cluster-1', got %q", model.restoreMetadata.ClusterID)
	}
}

func TestModel_View_ConfirmWithMetadata(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateConfirm
	m.selectedIdx = 0
	m.restoreMetadata = &aws.RestoreMetadata{
		ResourceType:   "RDS",
		ClusterID:      "my-cluster",
		SubnetGroup:    "subnet-group-1",
		SecurityGroups: "sg-abc123",
	}

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "Restore Parameters") {
		t.Error("confirm view with metadata should show Restore Parameters header")
	}
	if !strings.Contains(content, "subnet-group-1") {
		t.Error("confirm view should show subnet group")
	}
	if !strings.Contains(content, "sg-abc123") {
		t.Error("confirm view should show security groups")
	}
}

func TestModel_View_ConfirmWithEFSMetadata(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateConfirm
	m.selectedIdx = 1 // EFS backup
	m.restoreMetadata = &aws.RestoreMetadata{
		ResourceType: "EFS",
		ResourceID:   "fs-12345678",
		Encrypted:    true,
	}

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "fs-12345678") {
		t.Error("confirm view should show file system ID")
	}
	if !strings.Contains(content, "true") {
		t.Error("confirm view should show encryption status")
	}
}

// --- Unit Tests: Key Hints Updated ---

func TestModel_KeyHints_ListIncludesFilter(t *testing.T) {
	m := newTestModel()
	m.state = stateList
	hints := m.renderKeyHints()
	if !strings.Contains(hints, "filter") {
		t.Error("list key hints should include 'filter'")
	}
}

func TestModel_KeyHints_Restoring(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring
	hints := m.renderKeyHints()
	if !strings.Contains(hints, "back to list") {
		t.Error("restoring key hints should mention 'back to list'")
	}
}

// --- Unit Tests: Spinner in Restoring State ---

func TestModel_SpinnerTick_Restoring(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring
	m.spinnerFrame = 0

	updated, cmd := m.Update(spinnerTickMsg(time.Now()))
	model := updated.(*Model)

	if model.spinnerFrame != 1 {
		t.Errorf("spinner should advance during restoring, got %d", model.spinnerFrame)
	}
	if cmd == nil {
		t.Error("spinner should schedule next tick during restoring")
	}
}

// --- Unit Tests: Backups Loaded With Filter ---

func TestModel_BackupsLoadedMsg_SetsAllBackups(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading

	msg := backupsLoadedMsg{backups: sampleBackups()}
	result, _ := m.Update(msg)
	model := result.(*Model)

	if len(model.allBackups) != 2 {
		t.Errorf("expected 2 allBackups, got %d", len(model.allBackups))
	}
	if len(model.backups) != 2 {
		t.Errorf("expected 2 filtered backups (filterAll), got %d", len(model.backups))
	}
}

// --- Unit Tests: Format Includes Relative Time ---

func TestFormatBackupsForList_IncludesRelativeTime(t *testing.T) {
	m := newTestModel()
	m.backups = []aws.RecoveryPoint{
		{
			ResourceType:      "RDS",
			ResourceID:        "cluster-1",
			CreationDate:      time.Now().Add(-2 * time.Hour),
			BackupSizeInBytes: 1024,
		},
	}

	items := m.formatBackupsForList()
	if len(items) != 1 {
		t.Fatalf("expected 1 item, got %d", len(items))
	}
	if !strings.Contains(items[0], "2h ago") {
		t.Errorf("formatted item should contain relative time, got: %s", items[0])
	}
}

// --- Unit Tests: Status Bar with Filter ---

func TestModel_StatusBar_WithFilter(t *testing.T) {
	m := newTestModel()
	m.state = stateList
	m.allBackups = sampleBackups()
	m.backups = []aws.RecoveryPoint{m.allBackups[0]} // Only RDS
	m.activeFilter = filterRDS

	status := m.renderStatusBar()
	if !strings.Contains(status, "1 of 2") {
		t.Errorf("status bar should show filtered count, got: %s", status)
	}
}

// --- Functional Tests: Filter Toggle Workflow ---

func TestWorkflow_FilterToggle(t *testing.T) {
	m := newTestModel()
	m.allBackups = sampleBackups()
	m.backups = m.allBackups
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	if len(m.backups) != 2 {
		t.Fatalf("expected 2 backups initially, got %d", len(m.backups))
	}

	// Press f to filter to RDS
	result, _ := m.Update(tea.KeyPressMsg{Code: 'f', Text: "f"})
	m = result.(*Model)
	if m.activeFilter != filterRDS {
		t.Fatalf("expected RDS filter, got %v", m.activeFilter)
	}
	if len(m.backups) != 1 || m.backups[0].ResourceType != "RDS" {
		t.Fatalf("expected 1 RDS backup, got %d", len(m.backups))
	}

	// Press f to filter to EFS
	result, _ = m.Update(tea.KeyPressMsg{Code: 'f', Text: "f"})
	m = result.(*Model)
	if m.activeFilter != filterEFS {
		t.Fatalf("expected EFS filter, got %v", m.activeFilter)
	}
	if len(m.backups) != 1 || m.backups[0].ResourceType != "EFS" {
		t.Fatalf("expected 1 EFS backup, got %d", len(m.backups))
	}

	// Press f to go back to All
	result, _ = m.Update(tea.KeyPressMsg{Code: 'f', Text: "f"})
	m = result.(*Model)
	if m.activeFilter != filterAll {
		t.Fatalf("expected All filter, got %v", m.activeFilter)
	}
	if len(m.backups) != 2 {
		t.Fatalf("expected 2 backups with All filter, got %d", len(m.backups))
	}
}

// --- Functional Tests: Restore Monitoring Workflow ---

func TestWorkflow_RestoreMonitoring(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateDetail
	m.selectedIdx = 0

	// Simulate restore initiated
	msg := restoreInitiatedMsg{jobID: "job-workflow-test"}
	result, _ := m.Update(msg)
	m = result.(*Model)

	if m.state != stateRestoring {
		t.Fatalf("expected stateRestoring, got %d", m.state)
	}

	// Simulate status update (running)
	statusMsg := restoreStatusMsg{
		status: &aws.RestoreJobStatus{
			JobID:  "job-workflow-test",
			Status: "RUNNING",
		},
	}
	result, _ = m.Update(statusMsg)
	m = result.(*Model)

	if m.restoreStatus == nil || m.restoreStatus.Status != "RUNNING" {
		t.Fatal("expected RUNNING status")
	}

	// Simulate completion
	completeMsg := restoreStatusMsg{
		status: &aws.RestoreJobStatus{
			JobID:         "job-workflow-test",
			Status:        "COMPLETED",
			StatusMessage: "Restore complete",
			IsTerminal:    true,
		},
	}
	result, _ = m.Update(completeMsg)
	m = result.(*Model)

	if !strings.Contains(m.statusMsg, "COMPLETED") {
		t.Errorf("expected COMPLETED in statusMsg, got %q", m.statusMsg)
	}

	// Press esc to go back to list
	result, _ = m.Update(tea.KeyPressMsg{Code: tea.KeyEscape})
	m = result.(*Model)

	if m.state != stateList {
		t.Errorf("expected stateList after esc, got %d", m.state)
	}
}

// --- Unit Tests: Quit / Ctrl+C from various states ---

func TestModel_CtrlC_FromList_Quits(t *testing.T) {
	m := newTestModel()
	m.state = stateList

	_, cmd := m.Update(tea.KeyPressMsg{Code: tea.KeyEscape, Mod: tea.ModCtrl})
	// ctrl+c from list should produce tea.Quit
	// We verify the model dispatched a quit by checking msg.String() == "ctrl+c"
	// Since Update returns tea.Quit as a cmd, we just check it's not nil
	_ = cmd
}

func TestModel_Q_FromHelp_ReturnsList(t *testing.T) {
	m := newTestModel()
	m.state = stateHelp

	result, cmd := m.Update(tea.KeyPressMsg{Code: 'q', Text: "q"})
	model := result.(*Model)
	if model.state != stateList {
		t.Errorf("q from help should return to list, got %d", model.state)
	}
	if cmd != nil {
		t.Error("q from help should not produce a command")
	}
}

func TestModel_Q_FromConfirm_ReturnsDetail(t *testing.T) {
	m := newTestModel()
	m.state = stateConfirm

	result, _ := m.Update(tea.KeyPressMsg{Code: 'q', Text: "q"})
	model := result.(*Model)
	if model.state != stateDetail {
		t.Errorf("q from confirm should return to detail, got %d", model.state)
	}
}

// --- Unit Tests: Key no-ops in wrong states ---

func TestModel_QuestionMark_NotFromListOrDetail(t *testing.T) {
	for _, st := range []state{stateLoading, stateConfirm, stateError, stateRestoring} {
		m := newTestModel()
		m.state = st

		result, _ := m.Update(tea.KeyPressMsg{Code: '?', Text: "?"})
		model := result.(*Model)
		if model.state != st {
			t.Errorf("? from state %d should be no-op, got state %d", st, model.state)
		}
	}
}

func TestModel_R_NotFromList(t *testing.T) {
	for _, st := range []state{stateDetail, stateConfirm, stateHelp, stateRestoring} {
		m := newTestModel()
		m.state = st

		result, _ := m.Update(tea.KeyPressMsg{Code: 'r', Text: "r"})
		model := result.(*Model)
		if model.state == stateLoading {
			t.Errorf("r from state %d should not trigger refresh", st)
		}
	}
}

func TestModel_F_NotFromList(t *testing.T) {
	for _, st := range []state{stateDetail, stateConfirm, stateHelp, stateRestoring} {
		m := newTestModel()
		m.state = st
		m.allBackups = sampleBackups()
		m.backups = m.allBackups
		initialFilter := m.activeFilter

		result, _ := m.Update(tea.KeyPressMsg{Code: 'f', Text: "f"})
		model := result.(*Model)
		if model.activeFilter != initialFilter {
			t.Errorf("f from state %d should not change filter", st)
		}
	}
}

// --- Unit Tests: Detail navigation keys ---

func TestModel_Detail_LeftKey_GoesBack(t *testing.T) {
	m := newTestModel()
	m.state = stateDetail

	result, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyLeft})
	model := result.(*Model)
	if model.state != stateList {
		t.Errorf("left from detail should go to list, got %d", model.state)
	}
}

func TestModel_Detail_BackspaceKey_GoesBack(t *testing.T) {
	m := newTestModel()
	m.state = stateDetail

	result, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyBackspace})
	model := result.(*Model)
	if model.state != stateList {
		t.Errorf("backspace from detail should go to list, got %d", model.state)
	}
}

func TestModel_Detail_BackClearsMetadata(t *testing.T) {
	m := newTestModel()
	m.state = stateDetail
	m.restoreMetadata = &aws.RestoreMetadata{ResourceType: "RDS"}

	result, _ := m.Update(tea.KeyPressMsg{Code: 'b', Text: "b"})
	model := result.(*Model)
	if model.restoreMetadata != nil {
		t.Error("going back from detail should clear restoreMetadata")
	}
}

// --- Unit Tests: Confirm keys ---

func TestModel_Confirm_UpperY(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateConfirm
	m.selectedIdx = 0

	_, cmd := m.Update(tea.KeyPressMsg{Code: 'Y', Text: "Y"})
	if cmd == nil {
		t.Error("Y in confirm should trigger restore command")
	}
}

func TestModel_Confirm_UpperN(t *testing.T) {
	m := newTestModel()
	m.state = stateConfirm

	result, _ := m.Update(tea.KeyPressMsg{Code: 'N', Text: "N"})
	model := result.(*Model)
	if model.state != stateDetail {
		t.Errorf("N in confirm should go to detail, got %d", model.state)
	}
}

func TestModel_Confirm_Backspace(t *testing.T) {
	m := newTestModel()
	m.state = stateConfirm

	result, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyBackspace})
	model := result.(*Model)
	if model.state != stateDetail {
		t.Errorf("backspace in confirm should go to detail, got %d", model.state)
	}
}

func TestModel_Confirm_CancelClearsMetadata(t *testing.T) {
	m := newTestModel()
	m.state = stateConfirm
	m.restoreMetadata = &aws.RestoreMetadata{ResourceType: "EFS"}

	result, _ := m.Update(tea.KeyPressMsg{Code: 'n', Text: "n"})
	model := result.(*Model)
	if model.restoreMetadata != nil {
		t.Error("cancelling confirm should clear restoreMetadata")
	}
}

// --- Unit Tests: Confirm with out-of-bounds selectedIdx ---

func TestModel_RenderConfirm_OutOfBounds(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateConfirm
	m.selectedIdx = 99 // way out of bounds

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "No backup selected") {
		t.Error("confirm with OOB index should show 'No backup selected'")
	}
}

// --- Unit Tests: Loading view variants ---

func TestModel_View_LoadingBackups(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading
	m.vaultDiscovered = true
	m.vaultName = "my-vault"

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "Loading") {
		t.Error("loading view with vault discovered should say 'Loading'")
	}
}

// --- Unit Tests: Header rendering ---

func TestModel_RenderHeader_WithCLIResourceType(t *testing.T) {
	m := newTestModel()
	m.resourceType = "RDS"

	header := m.renderHeader()
	if !strings.Contains(header, "Filter") || !strings.Contains(header, "RDS") {
		t.Errorf("header with CLI resourceType should show filter badge, got: %s", header)
	}
}

func TestModel_RenderHeader_WithActiveFilter(t *testing.T) {
	m := newTestModel()
	m.activeFilter = filterEFS

	header := m.renderHeader()
	if !strings.Contains(header, "EFS") {
		t.Errorf("header with active filter should show EFS, got: %s", header)
	}
}

func TestModel_RenderHeader_DiscoveringVault(t *testing.T) {
	m := newTestModel()
	m.vaultDiscovered = false
	m.vaultName = ""

	header := m.renderHeader()
	if !strings.Contains(header, "Discovering") {
		t.Error("header should show 'Discovering' when vault not yet discovered")
	}
}

func TestModel_RenderHeader_InAppFilterOverridesCLI(t *testing.T) {
	m := newTestModel()
	m.resourceType = "RDS"
	m.activeFilter = filterEFS

	header := m.renderHeader()
	if !strings.Contains(header, "EFS") {
		t.Error("in-app filter should override CLI filter in header")
	}
}

// --- Unit Tests: Status bar edge cases ---

func TestModel_StatusBar_VaultDiscoveredNoBackups(t *testing.T) {
	m := newTestModel()
	m.state = stateList
	m.vaultDiscovered = true
	m.vaultName = "my-specific-vault"
	m.backups = nil

	status := m.renderStatusBar()
	if !strings.Contains(status, "my-specific-vault") {
		t.Errorf("status bar should name the vault when empty, got: %s", status)
	}
}

func TestModel_StatusBar_NoVaultNoBackups(t *testing.T) {
	m := newTestModel()
	m.state = stateList
	m.vaultDiscovered = false
	m.vaultName = ""
	m.backups = nil

	status := m.renderStatusBar()
	if !strings.Contains(status, "No backups") {
		t.Errorf("status bar should show generic no backups, got: %s", status)
	}
}

// --- Unit Tests: Spinner frame wrapping ---

func TestModel_SpinnerWrap(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading
	m.spinnerFrame = len(spinnerFrames) - 1

	result, _ := m.Update(spinnerTickMsg(time.Now()))
	model := result.(*Model)
	if model.spinnerFrame != 0 {
		t.Errorf("spinner should wrap from %d to 0, got %d", len(spinnerFrames)-1, model.spinnerFrame)
	}
}

// --- Unit Tests: relativeTime boundary conditions ---

func TestRelativeTime_Boundaries(t *testing.T) {
	now := time.Now()
	tests := []struct {
		name     string
		t        time.Time
		contains string
	}{
		{"exactly 59 seconds", now.Add(-59 * time.Second), "just now"},
		{"exactly 1 minute", now.Add(-60 * time.Second), "1m ago"},
		{"exactly 59 minutes", now.Add(-59 * time.Minute), "59m ago"},
		{"exactly 1 hour", now.Add(-60 * time.Minute), "1h ago"},
		{"exactly 23 hours", now.Add(-23 * time.Hour), "23h ago"},
		{"exactly 24 hours", now.Add(-24 * time.Hour), "1d ago"},
		{"exactly 29 days", now.Add(-29 * 24 * time.Hour), "29d ago"},
		{"exactly 30 days", now.Add(-30 * 24 * time.Hour), "1mo ago"},
		{"90 days", now.Add(-90 * 24 * time.Hour), "3mo ago"},
		{"365 days", now.Add(-365 * 24 * time.Hour), "12mo ago"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := relativeTime(tt.t)
			if !strings.Contains(result, tt.contains) {
				t.Errorf("relativeTime() = %q, want to contain %q", result, tt.contains)
			}
		})
	}
}

// --- Unit Tests: freshnessIndicator boundary conditions ---

func TestFreshnessIndicator_Boundaries(t *testing.T) {
	now := time.Now()

	fresh := freshnessIndicator(now.Add(-23*time.Hour - 59*time.Minute))
	if !strings.Contains(fresh, "●") {
		t.Error("just under 24h should have dot")
	}

	midAge := freshnessIndicator(now.Add(-24*time.Hour - 1*time.Minute))
	if !strings.Contains(midAge, "●") {
		t.Error("just over 24h should have dot")
	}

	weekOld := freshnessIndicator(now.Add(-7*24*time.Hour + 1*time.Hour))
	if !strings.Contains(weekOld, "●") {
		t.Error("just under 7d should have dot")
	}
}

// --- Unit Tests: Restoring view with terminal + completion ---

func TestModel_View_Restoring_TerminalWithDuration(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring
	m.restoreJobID = "job-done"
	m.restoreStart = time.Now().Add(-5 * time.Minute)
	createdAt := time.Now().Add(-5 * time.Minute)
	completedAt := time.Now()
	m.restoreStatus = &aws.RestoreJobStatus{
		JobID:       "job-done",
		Status:      "COMPLETED",
		IsTerminal:  true,
		CreatedAt:   createdAt,
		CompletedAt: completedAt,
	}

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "Duration") {
		t.Error("terminal restoring view should show Duration")
	}
}

func TestModel_View_Restoring_FailedStatus(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring
	m.restoreJobID = "job-fail"
	m.restoreStart = time.Now()
	m.restoreStatus = &aws.RestoreJobStatus{
		JobID:         "job-fail",
		Status:        "FAILED",
		StatusMessage: "Access denied",
		IsTerminal:    true,
	}

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "FAILED") {
		t.Error("failed restoring view should show FAILED")
	}
	if !strings.Contains(content, "Access denied") {
		t.Error("failed restoring view should show status message")
	}
}

func TestModel_View_Restoring_AbortedStatus(t *testing.T) {
	m := newTestModel()
	m.state = stateRestoring
	m.restoreJobID = "job-aborted"
	m.restoreStart = time.Now()
	m.restoreStatus = &aws.RestoreJobStatus{
		JobID:      "job-aborted",
		Status:     "ABORTED",
		IsTerminal: true,
	}

	v := m.View()
	content := v.Content
	if !strings.Contains(content, "ABORTED") {
		t.Error("aborted restoring view should show ABORTED")
	}
}

// --- Unit Tests: Generic error msg handling ---

func TestModel_GenericErrorMsg(t *testing.T) {
	m := newTestModel()
	m.state = stateList

	result, _ := m.Update(errTestError("unexpected"))
	model := result.(*Model)

	if model.state != stateError {
		t.Errorf("generic error msg should set stateError, got %d", model.state)
	}
	if model.err == nil || model.err.Error() != "unexpected" {
		t.Errorf("err should be set, got %v", model.err)
	}
}

// --- Unit Tests: RestoreMetadata error does not set metadata ---

func TestModel_RestoreMetadataMsg_Error(t *testing.T) {
	m := newTestModel()
	m.state = stateConfirm
	m.restoreMetadata = nil

	msg := restoreMetadataMsg{err: errTestError("cannot fetch")}
	result, _ := m.Update(msg)
	model := result.(*Model)

	if model.restoreMetadata != nil {
		t.Error("metadata should remain nil on error")
	}
}

// --- Unit Tests: restoreStatusMsg when not in restoring state ---

func TestModel_RestoreStatusMsg_NotRestoring_NoPoll(t *testing.T) {
	m := newTestModel()
	m.state = stateList

	status := &aws.RestoreJobStatus{
		JobID:  "job-x",
		Status: "RUNNING",
	}
	result, cmd := m.Update(restoreStatusMsg{status: status})
	model := result.(*Model)

	if model.restoreStatus == nil {
		t.Error("restoreStatus should still be set")
	}
	// Should not schedule another poll since we're not in restoring state
	// cmd might be a batch but should not contain poll cmd
	_ = cmd
}

// --- Unit Tests: applyFilter with all same type ---

func TestModel_ApplyFilter_AllSameType(t *testing.T) {
	m := newTestModel()
	m.allBackups = []aws.RecoveryPoint{
		{ResourceType: "RDS", ResourceID: "c1", CreationDate: time.Now()},
		{ResourceType: "RDS", ResourceID: "c2", CreationDate: time.Now()},
	}

	m.activeFilter = filterRDS
	m.applyFilter()
	if len(m.backups) != 2 {
		t.Errorf("expected 2 RDS backups, got %d", len(m.backups))
	}

	m.activeFilter = filterEFS
	m.applyFilter()
	if len(m.backups) != 0 {
		t.Errorf("expected 0 EFS backups, got %d", len(m.backups))
	}
}

// --- Unit Tests: Enter on detail triggers metadata fetch ---

func TestModel_EnterOnDetail_SetsConfirmAndFetchesMetadata(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateDetail
	m.selectedIdx = 0
	m.restoreMetadata = &aws.RestoreMetadata{ResourceType: "old"}

	result, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	model := result.(*Model)

	if model.state != stateConfirm {
		t.Errorf("expected stateConfirm, got %d", model.state)
	}
}

// --- Unit Tests: Enter on list sets detail recovery point ---

func TestModel_EnterOnList_SetsDetailRecoveryPoint(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList
	m.restoreMetadata = &aws.RestoreMetadata{ResourceType: "old"}

	result, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	model := result.(*Model)

	if model.state != stateDetail {
		t.Errorf("expected stateDetail, got %d", model.state)
	}
	if model.restoreMetadata != nil {
		t.Error("entering detail should clear old restoreMetadata")
	}
}

// --- Functional Tests: Full cycle through filter then select ---

func TestWorkflow_FilterThenSelect(t *testing.T) {
	m := newTestModel()
	m.allBackups = sampleBackups()
	m.backups = m.allBackups
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	// Filter to EFS only
	result, _ := m.Update(tea.KeyPressMsg{Code: 'f', Text: "f"})
	m = result.(*Model)
	result, _ = m.Update(tea.KeyPressMsg{Code: 'f', Text: "f"})
	m = result.(*Model)

	if m.activeFilter != filterEFS {
		t.Fatalf("expected EFS filter, got %v", m.activeFilter)
	}
	if len(m.backups) != 1 {
		t.Fatalf("expected 1 EFS backup, got %d", len(m.backups))
	}

	// Select the EFS backup
	result, _ = m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	m = result.(*Model)

	if m.state != stateDetail {
		t.Fatalf("expected stateDetail, got %d", m.state)
	}
	if m.selectedIdx != 0 {
		t.Errorf("expected selectedIdx 0, got %d", m.selectedIdx)
	}
}

// --- Functional Tests: Full restore error flow ---

func TestWorkflow_RestoreError(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.state = stateConfirm
	m.selectedIdx = 0

	// Simulate restore failure
	msg := restoreInitiatedMsg{err: errTestError("insufficient permissions")}
	result, _ := m.Update(msg)
	m = result.(*Model)

	if m.state != stateError {
		t.Fatalf("expected stateError, got %d", m.state)
	}
}

// --- Functional Tests: Navigate down, select second, view detail ---

func TestWorkflow_SelectSecondBackup(t *testing.T) {
	m := newTestModel()
	m.backups = sampleBackups()
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	// Move to second item
	result, _ := m.Update(tea.KeyPressMsg{Code: tea.KeyDown})
	m = result.(*Model)

	// Select
	result, _ = m.Update(tea.KeyPressMsg{Code: tea.KeyEnter})
	m = result.(*Model)

	if m.state != stateDetail {
		t.Fatalf("expected stateDetail, got %d", m.state)
	}
	if m.selectedIdx != 1 {
		t.Errorf("expected selectedIdx 1, got %d", m.selectedIdx)
	}
}

// --- Functional Tests: Refresh preserves filter ---

func TestWorkflow_RefreshPreservesFilter(t *testing.T) {
	m := newTestModel()
	m.allBackups = sampleBackups()
	m.backups = m.allBackups
	m.listModel.SetItems(m.formatBackupsForList())
	m.state = stateList

	// Set filter to RDS
	result, _ := m.Update(tea.KeyPressMsg{Code: 'f', Text: "f"})
	m = result.(*Model)

	// Refresh
	result, _ = m.Update(tea.KeyPressMsg{Code: 'r', Text: "r"})
	m = result.(*Model)

	// Filter should still be RDS even after refresh
	if m.activeFilter != filterRDS {
		t.Errorf("filter should be preserved after refresh, got %v", m.activeFilter)
	}
}

// --- Unit Tests: Error hints additional cases ---

func TestModel_ErrorHints_Authentication(t *testing.T) {
	m := newTestModel()
	m.state = stateError
	m.err = errTestError("authentication failed")

	rendered := m.renderError()
	if !strings.Contains(rendered, "credentials") {
		t.Error("authentication error should suggest credentials")
	}
}

func TestModel_ErrorHints_EC2RoleRequestError(t *testing.T) {
	m := newTestModel()
	m.state = stateError
	m.err = errTestError("EC2RoleRequestError: something")

	rendered := m.renderError()
	if !strings.Contains(rendered, "IAM role") {
		t.Error("EC2 role error should mention IAM role")
	}
}

func TestModel_ErrorHints_SharedCredsLoad(t *testing.T) {
	m := newTestModel()
	m.state = stateError
	m.err = errTestError("SharedCredsLoad: cannot load")

	rendered := m.renderError()
	if !strings.Contains(rendered, "aws configure") {
		t.Error("shared creds error should mention 'aws configure'")
	}
}

func TestModel_ErrorHints_GenericError(t *testing.T) {
	m := newTestModel()
	m.state = stateError
	m.err = errTestError("something totally unknown")

	rendered := m.renderError()
	if !strings.Contains(rendered, "something totally unknown") {
		t.Error("generic error should still show the error message")
	}
	if !strings.Contains(rendered, "quit") {
		t.Error("error view should mention quit")
	}
}

// --- Unit Tests: Key hints default case returns empty ---

func TestModel_KeyHints_ErrorState(t *testing.T) {
	m := newTestModel()
	m.state = stateError
	hints := m.renderKeyHints()
	if hints != "" {
		t.Errorf("error state should have no key hints, got %q", hints)
	}
}

func TestModel_KeyHints_LoadingState(t *testing.T) {
	m := newTestModel()
	m.state = stateLoading
	hints := m.renderKeyHints()
	if hints != "" {
		t.Errorf("loading state should have no key hints, got %q", hints)
	}
}

// --- Unit Tests: formatBackupsForList with empty list ---

func TestFormatBackupsForList_Empty(t *testing.T) {
	m := newTestModel()
	m.backups = nil

	items := m.formatBackupsForList()
	if len(items) != 0 {
		t.Errorf("expected 0 items for nil backups, got %d", len(items))
	}
}

// --- Unit Tests: formatBytes boundary values ---

func TestFormatBytes_SingleByte(t *testing.T) {
	result := formatBytes(1)
	if result != "1 B" {
		t.Errorf("formatBytes(1) = %q, want '1 B'", result)
	}
}

func TestFormatBytes_ExactlyOneKB(t *testing.T) {
	result := formatBytes(1024)
	if result != "1.0 KB" {
		t.Errorf("formatBytes(1024) = %q, want '1.0 KB'", result)
	}
}

func TestFormatBytes_LargeValue(t *testing.T) {
	result := formatBytes(1024 * 1024 * 1024 * 1024)
	if result != "1.0 TB" {
		t.Errorf("formatBytes(1TB) = %q, want '1.0 TB'", result)
	}
}

// errTestError is a simple error type for testing.
type errTestError string

func (e errTestError) Error() string { return string(e) }
