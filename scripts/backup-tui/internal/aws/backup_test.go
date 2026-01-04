package aws

import (
	"strings"
	"testing"
)

func TestExtractResourceID(t *testing.T) {
	tests := []struct {
		name     string
		arn      string
		expected string
	}{
		{
			name:     "RDS cluster ARN",
			arn:      "arn:aws:rds:us-west-2:123456789012:cluster:my-cluster",
			expected: "cluster", // parts[5] is "cluster" (7 parts total when split by ":")
		},
		{
			name:     "RDS instance ARN",
			arn:      "arn:aws:rds:us-west-2:123456789012:db:my-instance",
			expected: "db", // parts[5] is "db" (7 parts total when split by ":")
		},
		{
			name:     "EFS file system ARN",
			arn:      "arn:aws:elasticfilesystem:us-west-2:123456789012:file-system/fs-12345678",
			expected: "fs-12345678",
		},
		{
			name:     "EFS file system ARN with full path",
			arn:      "arn:aws:elasticfilesystem:us-west-2:123456789012:file-system/fs-12345678/backup/backup-abc123",
			expected: "backup-abc123", // Returns the last part after "/"
		},
		{
			name:     "Invalid ARN format",
			arn:      "not-an-arn",
			expected: "not-an-arn",
		},
		{
			name:     "Short ARN",
			arn:      "arn:aws:rds:us-west-2:123456789012:db",
			expected: "db",
		},
		{
			name:     "Empty string",
			arn:      "",
			expected: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := extractResourceID(tt.arn)
			if result != tt.expected {
				t.Errorf("extractResourceID(%q) = %q, want %q", tt.arn, result, tt.expected)
			}
		})
	}
}

func TestExtractResourceID_EFS(t *testing.T) {
	// Special test for EFS ARNs which have a different format
	arn := "arn:aws:elasticfilesystem:us-west-2:123456789012:file-system/fs-12345678"
	result := extractResourceID(arn)

	// For EFS, the resource ID should be the file system ID (fs-12345678)
	expected := "fs-12345678"
	if result != expected {
		t.Errorf("extractResourceID(%q) = %q, want %q", arn, result, expected)
	}

	// Should start with "fs-"
	if !strings.HasPrefix(result, "fs-") {
		t.Errorf("extractResourceID(%q) = %q, should start with 'fs-'", arn, result)
	}
}

func TestExtractResourceID_RDS(t *testing.T) {
	// Special test for RDS ARNs
	arn := "arn:aws:rds:us-west-2:123456789012:cluster:my-test-cluster"
	result := extractResourceID(arn)

	expected := "cluster"
	if result != expected {
		t.Errorf("extractResourceID(%q) = %q, want %q", arn, result, expected)
	}
}
