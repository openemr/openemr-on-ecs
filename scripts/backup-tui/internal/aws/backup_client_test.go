package aws

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/backup"
	backuptypes "github.com/aws/aws-sdk-go-v2/service/backup/types"
	"github.com/aws/aws-sdk-go-v2/service/cloudformation"
	cfntypes "github.com/aws/aws-sdk-go-v2/service/cloudformation/types"
	"github.com/aws/aws-sdk-go-v2/service/rds"
	rdstypes "github.com/aws/aws-sdk-go-v2/service/rds/types"
)

// ---------------------------------------------------------------------------
// Mock implementations
// ---------------------------------------------------------------------------

type mockCFN struct {
	listStacksOutput    *cloudformation.ListStacksOutput
	listStacksErr       error
	describeStackOutput *cloudformation.DescribeStacksOutput
	describeStackErr    error
}

func (m *mockCFN) ListStacks(_ context.Context, _ *cloudformation.ListStacksInput, _ ...func(*cloudformation.Options)) (*cloudformation.ListStacksOutput, error) {
	return m.listStacksOutput, m.listStacksErr
}

func (m *mockCFN) DescribeStacks(_ context.Context, _ *cloudformation.DescribeStacksInput, _ ...func(*cloudformation.Options)) (*cloudformation.DescribeStacksOutput, error) {
	return m.describeStackOutput, m.describeStackErr
}

type mockBackup struct {
	listVaultsOutput      *backup.ListBackupVaultsOutput
	listVaultsErr         error
	listRPOutput          *backup.ListRecoveryPointsByBackupVaultOutput
	listRPErr             error
	startRestoreOutput    *backup.StartRestoreJobOutput
	startRestoreErr       error
	describeRestoreOutput *backup.DescribeRestoreJobOutput
	describeRestoreErr    error
	listPlansOutput       *backup.ListBackupPlansOutput
	listPlansErr          error
	getPlanOutput         *backup.GetBackupPlanOutput
	getPlanErr            error
	listSelectionsOut     *backup.ListBackupSelectionsOutput
	listSelectionsErr     error
}

func (m *mockBackup) ListBackupVaults(_ context.Context, _ *backup.ListBackupVaultsInput, _ ...func(*backup.Options)) (*backup.ListBackupVaultsOutput, error) {
	return m.listVaultsOutput, m.listVaultsErr
}

func (m *mockBackup) ListRecoveryPointsByBackupVault(_ context.Context, _ *backup.ListRecoveryPointsByBackupVaultInput, _ ...func(*backup.Options)) (*backup.ListRecoveryPointsByBackupVaultOutput, error) {
	return m.listRPOutput, m.listRPErr
}

func (m *mockBackup) StartRestoreJob(_ context.Context, _ *backup.StartRestoreJobInput, _ ...func(*backup.Options)) (*backup.StartRestoreJobOutput, error) {
	return m.startRestoreOutput, m.startRestoreErr
}

func (m *mockBackup) DescribeRestoreJob(_ context.Context, _ *backup.DescribeRestoreJobInput, _ ...func(*backup.Options)) (*backup.DescribeRestoreJobOutput, error) {
	return m.describeRestoreOutput, m.describeRestoreErr
}

func (m *mockBackup) ListBackupPlans(_ context.Context, _ *backup.ListBackupPlansInput, _ ...func(*backup.Options)) (*backup.ListBackupPlansOutput, error) {
	return m.listPlansOutput, m.listPlansErr
}

func (m *mockBackup) GetBackupPlan(_ context.Context, _ *backup.GetBackupPlanInput, _ ...func(*backup.Options)) (*backup.GetBackupPlanOutput, error) {
	return m.getPlanOutput, m.getPlanErr
}

func (m *mockBackup) ListBackupSelections(_ context.Context, _ *backup.ListBackupSelectionsInput, _ ...func(*backup.Options)) (*backup.ListBackupSelectionsOutput, error) {
	return m.listSelectionsOut, m.listSelectionsErr
}

type mockRDS struct {
	describeClustersOutput *rds.DescribeDBClustersOutput
	describeClustersErr    error
}

func (m *mockRDS) DescribeDBClusters(_ context.Context, _ *rds.DescribeDBClustersInput, _ ...func(*rds.Options)) (*rds.DescribeDBClustersOutput, error) {
	return m.describeClustersOutput, m.describeClustersErr
}

func newTestClient(cfnMock *mockCFN, backupMock *mockBackup, rdsMock *mockRDS) *BackupClient {
	return &BackupClient{
		client:    backupMock,
		cfn:       cfnMock,
		rds:       rdsMock,
		region:    "us-west-2",
		accountID: "123456789012",
	}
}

// ---------------------------------------------------------------------------
// DiscoverStackName
// ---------------------------------------------------------------------------

func TestDiscoverStackName_SingleMatch(t *testing.T) {
	cfnMock := &mockCFN{
		listStacksOutput: &cloudformation.ListStacksOutput{
			StackSummaries: []cfntypes.StackSummary{
				{StackName: aws.String("OpenemrEcsStack")},
				{StackName: aws.String("OtherStack")},
			},
		},
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	name, err := c.DiscoverStackName(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if name != "OpenemrEcsStack" {
		t.Errorf("got %q, want %q", name, "OpenemrEcsStack")
	}
}

func TestDiscoverStackName_NoMatch(t *testing.T) {
	cfnMock := &mockCFN{
		listStacksOutput: &cloudformation.ListStacksOutput{
			StackSummaries: []cfntypes.StackSummary{
				{StackName: aws.String("UnrelatedStack")},
			},
		},
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	_, err := c.DiscoverStackName(context.Background())
	if err == nil {
		t.Fatal("expected error for no matches")
	}
}

func TestDiscoverStackName_MultipleMatches(t *testing.T) {
	cfnMock := &mockCFN{
		listStacksOutput: &cloudformation.ListStacksOutput{
			StackSummaries: []cfntypes.StackSummary{
				{StackName: aws.String("OpenemrEcsStack")},
				{StackName: aws.String("OpenemrEcsStackDev")},
			},
		},
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	_, err := c.DiscoverStackName(context.Background())
	if err == nil {
		t.Fatal("expected error for multiple matches")
	}
}

func TestDiscoverStackName_APIError(t *testing.T) {
	cfnMock := &mockCFN{
		listStacksErr: fmt.Errorf("access denied"),
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	_, err := c.DiscoverStackName(context.Background())
	if err == nil {
		t.Fatal("expected error from API failure")
	}
}

// ---------------------------------------------------------------------------
// DiscoverVaultByStack
// ---------------------------------------------------------------------------

func TestDiscoverVaultByStack_Found(t *testing.T) {
	backupMock := &mockBackup{
		listVaultsOutput: &backup.ListBackupVaultsOutput{
			BackupVaultList: []backuptypes.BackupVaultListMember{
				{BackupVaultName: aws.String("OpenemrEcsStack-vault-abc123")},
				{BackupVaultName: aws.String("other-vault")},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	name, err := c.DiscoverVaultByStack(context.Background(), "OpenemrEcsStack")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if name != "OpenemrEcsStack-vault-abc123" {
		t.Errorf("got %q, want %q", name, "OpenemrEcsStack-vault-abc123")
	}
}

func TestDiscoverVaultByStack_NotFound(t *testing.T) {
	backupMock := &mockBackup{
		listVaultsOutput: &backup.ListBackupVaultsOutput{
			BackupVaultList: []backuptypes.BackupVaultListMember{
				{BackupVaultName: aws.String("other-vault")},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	_, err := c.DiscoverVaultByStack(context.Background(), "OpenemrEcsStack")
	if err == nil {
		t.Fatal("expected error when vault not found")
	}
}

func TestDiscoverVaultByStack_APIError(t *testing.T) {
	backupMock := &mockBackup{
		listVaultsErr: fmt.Errorf("throttling"),
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	_, err := c.DiscoverVaultByStack(context.Background(), "OpenemrEcsStack")
	if err == nil {
		t.Fatal("expected error from API")
	}
}

// ---------------------------------------------------------------------------
// ListRecoveryPoints
// ---------------------------------------------------------------------------

func TestListRecoveryPoints_EmptyVault(t *testing.T) {
	backupMock := &mockBackup{
		listRPOutput: &backup.ListRecoveryPointsByBackupVaultOutput{
			RecoveryPoints: []backuptypes.RecoveryPointByBackupVault{},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	points, err := c.ListRecoveryPoints(context.Background(), "my-vault", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(points) != 0 {
		t.Errorf("expected 0 points, got %d", len(points))
	}
}

func TestListRecoveryPoints_FiltersResourceType(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		listRPOutput: &backup.ListRecoveryPointsByBackupVaultOutput{
			RecoveryPoints: []backuptypes.RecoveryPointByBackupVault{
				{
					RecoveryPointArn: aws.String("arn:aws:backup:us-west-2:123:recovery-point:rds-1"),
					ResourceType:     aws.String("RDS"),
					ResourceArn:      aws.String("arn:aws:rds:us-west-2:123:cluster:my-cluster"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatusCompleted,
				},
				{
					RecoveryPointArn: aws.String("arn:aws:backup:us-west-2:123:recovery-point:efs-1"),
					ResourceType:     aws.String("EFS"),
					ResourceArn:      aws.String("arn:aws:elasticfilesystem:us-west-2:123:file-system/fs-123"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatusCompleted,
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	points, err := c.ListRecoveryPoints(context.Background(), "my-vault", "RDS")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(points) != 1 {
		t.Fatalf("expected 1 RDS point, got %d", len(points))
	}
	if points[0].ResourceType != "RDS" {
		t.Errorf("expected RDS, got %s", points[0].ResourceType)
	}
}

func TestListRecoveryPoints_AllTypes(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		listRPOutput: &backup.ListRecoveryPointsByBackupVaultOutput{
			RecoveryPoints: []backuptypes.RecoveryPointByBackupVault{
				{
					RecoveryPointArn: aws.String("arn:1"),
					ResourceType:     aws.String("RDS"),
					ResourceArn:      aws.String("arn:aws:rds:us-west-2:123:cluster:c"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatusCompleted,
				},
				{
					RecoveryPointArn: aws.String("arn:2"),
					ResourceType:     aws.String("EFS"),
					ResourceArn:      aws.String("arn:aws:elasticfilesystem:us-west-2:123:file-system/fs-1"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatusCompleted,
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	points, err := c.ListRecoveryPoints(context.Background(), "my-vault", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(points) != 2 {
		t.Errorf("expected 2 points, got %d", len(points))
	}
}

func TestListRecoveryPoints_SkipsDeleted(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		listRPOutput: &backup.ListRecoveryPointsByBackupVaultOutput{
			RecoveryPoints: []backuptypes.RecoveryPointByBackupVault{
				{
					RecoveryPointArn: aws.String("arn:1"),
					ResourceType:     aws.String("RDS"),
					ResourceArn:      aws.String("arn:aws:rds:us-west-2:123:cluster:c"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatus("DELETED"),
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	points, err := c.ListRecoveryPoints(context.Background(), "my-vault", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(points) != 0 {
		t.Errorf("expected 0 points (deleted should be skipped), got %d", len(points))
	}
}

func TestListRecoveryPoints_EmptyVaultName(t *testing.T) {
	c := newTestClient(&mockCFN{}, &mockBackup{}, &mockRDS{})

	_, err := c.ListRecoveryPoints(context.Background(), "", "")
	if err == nil {
		t.Fatal("expected error for empty vault name")
	}
}

func TestListRecoveryPoints_IncludesBackupSize(t *testing.T) {
	now := time.Now()
	var size int64 = 1024 * 1024 * 100
	backupMock := &mockBackup{
		listRPOutput: &backup.ListRecoveryPointsByBackupVaultOutput{
			RecoveryPoints: []backuptypes.RecoveryPointByBackupVault{
				{
					RecoveryPointArn:  aws.String("arn:1"),
					ResourceType:      aws.String("EFS"),
					ResourceArn:       aws.String("arn:aws:elasticfilesystem:us-west-2:123:file-system/fs-1"),
					CreationDate:      &now,
					Status:            backuptypes.RecoveryPointStatusCompleted,
					BackupSizeInBytes: &size,
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	points, err := c.ListRecoveryPoints(context.Background(), "my-vault", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(points) != 1 {
		t.Fatalf("expected 1 point, got %d", len(points))
	}
	if points[0].BackupSizeInBytes != size {
		t.Errorf("expected size %d, got %d", size, points[0].BackupSizeInBytes)
	}
}

// ---------------------------------------------------------------------------
// getRDSClusterIDFromStack
// ---------------------------------------------------------------------------

func TestGetRDSClusterIDFromStack_Found(t *testing.T) {
	cfnMock := &mockCFN{
		describeStackOutput: &cloudformation.DescribeStacksOutput{
			Stacks: []cfntypes.Stack{
				{
					Outputs: []cfntypes.Output{
						{
							OutputKey:   aws.String("DatabaseEndpoint"),
							OutputValue: aws.String("my-cluster.xxx.us-west-2.rds.amazonaws.com"),
						},
					},
				},
			},
		},
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	id, err := c.getRDSClusterIDFromStack(context.Background(), "TestStack")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if id != "my-cluster" {
		t.Errorf("got %q, want %q", id, "my-cluster")
	}
}

func TestGetRDSClusterIDFromStack_NoStacks(t *testing.T) {
	cfnMock := &mockCFN{
		describeStackOutput: &cloudformation.DescribeStacksOutput{
			Stacks: []cfntypes.Stack{},
		},
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	_, err := c.getRDSClusterIDFromStack(context.Background(), "TestStack")
	if err == nil {
		t.Fatal("expected error for missing stack")
	}
}

func TestGetRDSClusterIDFromStack_MissingOutput(t *testing.T) {
	cfnMock := &mockCFN{
		describeStackOutput: &cloudformation.DescribeStacksOutput{
			Stacks: []cfntypes.Stack{
				{
					Outputs: []cfntypes.Output{
						{
							OutputKey:   aws.String("SomeOtherOutput"),
							OutputValue: aws.String("value"),
						},
					},
				},
			},
		},
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	_, err := c.getRDSClusterIDFromStack(context.Background(), "TestStack")
	if err == nil {
		t.Fatal("expected error for missing DatabaseEndpoint output")
	}
}

// ---------------------------------------------------------------------------
// getRDSClusterDetails
// ---------------------------------------------------------------------------

func TestGetRDSClusterDetails_Success(t *testing.T) {
	rdsMock := &mockRDS{
		describeClustersOutput: &rds.DescribeDBClustersOutput{
			DBClusters: []rdstypes.DBCluster{
				{
					DBSubnetGroup: aws.String("my-subnet-group"),
					VpcSecurityGroups: []rdstypes.VpcSecurityGroupMembership{
						{VpcSecurityGroupId: aws.String("sg-111")},
						{VpcSecurityGroupId: aws.String("sg-222")},
					},
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, &mockBackup{}, rdsMock)

	subnet, sgs, err := c.getRDSClusterDetails(context.Background(), "my-cluster")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if subnet != "my-subnet-group" {
		t.Errorf("subnet: got %q, want %q", subnet, "my-subnet-group")
	}
	if sgs != "sg-111,sg-222" {
		t.Errorf("security groups: got %q, want %q", sgs, "sg-111,sg-222")
	}
}

func TestGetRDSClusterDetails_NotFound(t *testing.T) {
	rdsMock := &mockRDS{
		describeClustersOutput: &rds.DescribeDBClustersOutput{
			DBClusters: []rdstypes.DBCluster{},
		},
	}
	c := newTestClient(&mockCFN{}, &mockBackup{}, rdsMock)

	_, _, err := c.getRDSClusterDetails(context.Background(), "missing-cluster")
	if err == nil {
		t.Fatal("expected error for missing cluster")
	}
}

// ---------------------------------------------------------------------------
// getBackupPlanRoleArn
// ---------------------------------------------------------------------------

func TestGetBackupPlanRoleArn_EmptyVault(t *testing.T) {
	c := newTestClient(&mockCFN{}, &mockBackup{}, &mockRDS{})
	_, err := c.getBackupPlanRoleArn(context.Background(), "")
	if err == nil {
		t.Fatal("expected error for empty vault name")
	}
}

// ---------------------------------------------------------------------------
// GetRestoreJobStatus
// ---------------------------------------------------------------------------

func TestGetRestoreJobStatus_Success(t *testing.T) {
	now := time.Now()
	completed := now.Add(5 * time.Minute)
	backupMock := &mockBackup{
		describeRestoreOutput: &backup.DescribeRestoreJobOutput{
			RestoreJobId:   aws.String("job-123"),
			Status:         "COMPLETED",
			ResourceType:   aws.String("RDS"),
			PercentDone:    aws.String("100"),
			StatusMessage:  aws.String("Restore completed"),
			CreationDate:   &now,
			CompletionDate: &completed,
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	status, err := c.GetRestoreJobStatus(context.Background(), "job-123")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if status.JobID != "job-123" {
		t.Errorf("expected job ID job-123, got %q", status.JobID)
	}
	if status.Status != "COMPLETED" {
		t.Errorf("expected COMPLETED, got %q", status.Status)
	}
	if !status.IsTerminal {
		t.Error("COMPLETED should be terminal")
	}
	if status.PercentDone != "100" {
		t.Errorf("expected 100%%, got %q", status.PercentDone)
	}
}

func TestGetRestoreJobStatus_Running(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		describeRestoreOutput: &backup.DescribeRestoreJobOutput{
			RestoreJobId: aws.String("job-running"),
			Status:       "RUNNING",
			ResourceType: aws.String("EFS"),
			PercentDone:  aws.String("50"),
			CreationDate: &now,
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	status, err := c.GetRestoreJobStatus(context.Background(), "job-running")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if status.IsTerminal {
		t.Error("RUNNING should not be terminal")
	}
}

func TestGetRestoreJobStatus_APIError(t *testing.T) {
	backupMock := &mockBackup{
		describeRestoreErr: fmt.Errorf("access denied"),
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	_, err := c.GetRestoreJobStatus(context.Background(), "job-err")
	if err == nil {
		t.Fatal("expected error from API failure")
	}
}

func TestGetRestoreJobStatus_Failed(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		describeRestoreOutput: &backup.DescribeRestoreJobOutput{
			RestoreJobId:  aws.String("job-fail"),
			Status:        "FAILED",
			StatusMessage: aws.String("Insufficient permissions"),
			CreationDate:  &now,
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	status, err := c.GetRestoreJobStatus(context.Background(), "job-fail")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !status.IsTerminal {
		t.Error("FAILED should be terminal")
	}
	if status.StatusMessage != "Insufficient permissions" {
		t.Errorf("expected status message, got %q", status.StatusMessage)
	}
}

// ---------------------------------------------------------------------------
// GetRestoreMetadata
// ---------------------------------------------------------------------------

func TestGetRestoreMetadata_RDS(t *testing.T) {
	cfnMock := &mockCFN{
		describeStackOutput: &cloudformation.DescribeStacksOutput{
			Stacks: []cfntypes.Stack{
				{
					Outputs: []cfntypes.Output{
						{
							OutputKey:   aws.String("DatabaseEndpoint"),
							OutputValue: aws.String("my-cluster.xxx.us-west-2.rds.amazonaws.com"),
						},
					},
				},
			},
		},
	}
	rdsMock := &mockRDS{
		describeClustersOutput: &rds.DescribeDBClustersOutput{
			DBClusters: []rdstypes.DBCluster{
				{
					DBSubnetGroup: aws.String("my-subnet"),
					VpcSecurityGroups: []rdstypes.VpcSecurityGroupMembership{
						{VpcSecurityGroupId: aws.String("sg-111")},
					},
				},
			},
		},
	}
	c := newTestClient(cfnMock, &mockBackup{}, rdsMock)

	rp := RecoveryPoint{ResourceType: "RDS", ResourceID: "my-cluster"}
	meta, err := c.GetRestoreMetadata(context.Background(), rp, "TestStack")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if meta.ClusterID != "my-cluster" {
		t.Errorf("expected ClusterID 'my-cluster', got %q", meta.ClusterID)
	}
	if meta.SubnetGroup != "my-subnet" {
		t.Errorf("expected SubnetGroup 'my-subnet', got %q", meta.SubnetGroup)
	}
	if meta.SecurityGroups != "sg-111" {
		t.Errorf("expected SecurityGroups 'sg-111', got %q", meta.SecurityGroups)
	}
}

func TestGetRestoreMetadata_EFS(t *testing.T) {
	c := newTestClient(&mockCFN{}, &mockBackup{}, &mockRDS{})

	rp := RecoveryPoint{ResourceType: "EFS", ResourceID: "fs-12345"}
	meta, err := c.GetRestoreMetadata(context.Background(), rp, "TestStack")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !meta.Encrypted {
		t.Error("EFS metadata should have Encrypted = true")
	}
	if meta.NewFileSystem {
		t.Error("EFS metadata should have NewFileSystem = false")
	}
}

func TestGetRestoreMetadata_RDS_StackError(t *testing.T) {
	cfnMock := &mockCFN{
		describeStackErr: fmt.Errorf("stack not found"),
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	rp := RecoveryPoint{ResourceType: "RDS", ResourceID: "cluster-1"}
	_, err := c.GetRestoreMetadata(context.Background(), rp, "MissingStack")
	if err == nil {
		t.Fatal("expected error for missing stack")
	}
}

func TestGetBackupPlanRoleArn_Fallback(t *testing.T) {
	backupMock := &mockBackup{
		listPlansOutput: &backup.ListBackupPlansOutput{
			BackupPlansList: []backuptypes.BackupPlansListMember{},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	arn, err := c.getBackupPlanRoleArn(context.Background(), "my-vault")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	expected := "arn:aws:iam::123456789012:role/service-role/AWSBackupDefaultServiceRole"
	if arn != expected {
		t.Errorf("got %q, want %q", arn, expected)
	}
}

// ---------------------------------------------------------------------------
// GetRestoreJobStatus - additional cases
// ---------------------------------------------------------------------------

func TestGetRestoreJobStatus_Aborted(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		describeRestoreOutput: &backup.DescribeRestoreJobOutput{
			RestoreJobId:  aws.String("job-aborted"),
			Status:        "ABORTED",
			StatusMessage: aws.String("User cancelled"),
			CreationDate:  &now,
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	status, err := c.GetRestoreJobStatus(context.Background(), "job-aborted")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !status.IsTerminal {
		t.Error("ABORTED should be terminal")
	}
	if status.Status != "ABORTED" {
		t.Errorf("expected ABORTED, got %q", status.Status)
	}
	if status.StatusMessage != "User cancelled" {
		t.Errorf("expected 'User cancelled', got %q", status.StatusMessage)
	}
}

func TestGetRestoreJobStatus_Pending(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		describeRestoreOutput: &backup.DescribeRestoreJobOutput{
			RestoreJobId: aws.String("job-pending"),
			Status:       "PENDING",
			CreationDate: &now,
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	status, err := c.GetRestoreJobStatus(context.Background(), "job-pending")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if status.IsTerminal {
		t.Error("PENDING should not be terminal")
	}
	if status.Status != "PENDING" {
		t.Errorf("expected PENDING, got %q", status.Status)
	}
}

func TestGetRestoreJobStatus_AllFieldsPopulated(t *testing.T) {
	now := time.Now()
	completed := now.Add(10 * time.Minute)
	backupMock := &mockBackup{
		describeRestoreOutput: &backup.DescribeRestoreJobOutput{
			RestoreJobId:   aws.String("job-full"),
			Status:         "COMPLETED",
			ResourceType:   aws.String("RDS"),
			PercentDone:    aws.String("100"),
			StatusMessage:  aws.String("All good"),
			CreationDate:   &now,
			CompletionDate: &completed,
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	status, err := c.GetRestoreJobStatus(context.Background(), "job-full")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if status.JobID != "job-full" {
		t.Errorf("JobID: got %q, want 'job-full'", status.JobID)
	}
	if status.ResourceType != "RDS" {
		t.Errorf("ResourceType: got %q, want 'RDS'", status.ResourceType)
	}
	if status.PercentDone != "100" {
		t.Errorf("PercentDone: got %q, want '100'", status.PercentDone)
	}
	if status.StatusMessage != "All good" {
		t.Errorf("StatusMessage: got %q, want 'All good'", status.StatusMessage)
	}
	if status.CreatedAt.IsZero() {
		t.Error("CreatedAt should not be zero")
	}
	if status.CompletedAt.IsZero() {
		t.Error("CompletedAt should not be zero")
	}
}

func TestGetRestoreJobStatus_NilOptionalFields(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		describeRestoreOutput: &backup.DescribeRestoreJobOutput{
			RestoreJobId: aws.String("job-minimal"),
			Status:       "RUNNING",
			CreationDate: &now,
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	status, err := c.GetRestoreJobStatus(context.Background(), "job-minimal")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if status.ResourceType != "" {
		t.Errorf("nil ResourceType should result in empty string, got %q", status.ResourceType)
	}
	if status.PercentDone != "" {
		t.Errorf("nil PercentDone should result in empty string, got %q", status.PercentDone)
	}
	if status.StatusMessage != "" {
		t.Errorf("nil StatusMessage should result in empty string, got %q", status.StatusMessage)
	}
}

// ---------------------------------------------------------------------------
// GetRestoreMetadata - additional cases
// ---------------------------------------------------------------------------

func TestGetRestoreMetadata_UnknownResourceType(t *testing.T) {
	c := newTestClient(&mockCFN{}, &mockBackup{}, &mockRDS{})

	rp := RecoveryPoint{ResourceType: "S3", ResourceID: "my-bucket"}
	meta, err := c.GetRestoreMetadata(context.Background(), rp, "TestStack")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if meta.ResourceType != "S3" {
		t.Errorf("expected ResourceType 'S3', got %q", meta.ResourceType)
	}
	if meta.ResourceID != "my-bucket" {
		t.Errorf("expected ResourceID 'my-bucket', got %q", meta.ResourceID)
	}
}

func TestGetRestoreMetadata_RDS_RDSDescribeError(t *testing.T) {
	cfnMock := &mockCFN{
		describeStackOutput: &cloudformation.DescribeStacksOutput{
			Stacks: []cfntypes.Stack{
				{
					Outputs: []cfntypes.Output{
						{
							OutputKey:   aws.String("DatabaseEndpoint"),
							OutputValue: aws.String("my-cluster.xxx.us-west-2.rds.amazonaws.com"),
						},
					},
				},
			},
		},
	}
	rdsMock := &mockRDS{
		describeClustersErr: fmt.Errorf("cluster not found"),
	}
	c := newTestClient(cfnMock, &mockBackup{}, rdsMock)

	rp := RecoveryPoint{ResourceType: "RDS", ResourceID: "my-cluster"}
	_, err := c.GetRestoreMetadata(context.Background(), rp, "TestStack")
	if err == nil {
		t.Fatal("expected error when RDS describe fails")
	}
}

func TestGetRestoreMetadata_EFS_HasDefaults(t *testing.T) {
	c := newTestClient(&mockCFN{}, &mockBackup{}, &mockRDS{})

	rp := RecoveryPoint{ResourceType: "EFS", ResourceID: "fs-abc"}
	meta, err := c.GetRestoreMetadata(context.Background(), rp, "TestStack")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if meta.ResourceType != "EFS" {
		t.Errorf("expected ResourceType 'EFS', got %q", meta.ResourceType)
	}
	if meta.ResourceID != "fs-abc" {
		t.Errorf("expected ResourceID 'fs-abc', got %q", meta.ResourceID)
	}
	if !meta.Encrypted {
		t.Error("EFS should default to Encrypted=true")
	}
	if meta.NewFileSystem {
		t.Error("EFS should default to NewFileSystem=false")
	}
}

// ---------------------------------------------------------------------------
// ListRecoveryPoints - additional cases
// ---------------------------------------------------------------------------

func TestListRecoveryPoints_APIError(t *testing.T) {
	backupMock := &mockBackup{
		listRPErr: fmt.Errorf("service unavailable"),
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	_, err := c.ListRecoveryPoints(context.Background(), "my-vault", "")
	if err == nil {
		t.Fatal("expected error from API failure")
	}
}

func TestListRecoveryPoints_MixedStatuses(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		listRPOutput: &backup.ListRecoveryPointsByBackupVaultOutput{
			RecoveryPoints: []backuptypes.RecoveryPointByBackupVault{
				{
					RecoveryPointArn: aws.String("arn:completed"),
					ResourceType:     aws.String("RDS"),
					ResourceArn:      aws.String("arn:aws:rds:us-west-2:123:cluster:c"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatusCompleted,
				},
				{
					RecoveryPointArn: aws.String("arn:partial"),
					ResourceType:     aws.String("RDS"),
					ResourceArn:      aws.String("arn:aws:rds:us-west-2:123:cluster:c2"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatusPartial,
				},
				{
					RecoveryPointArn: aws.String("arn:deleted"),
					ResourceType:     aws.String("RDS"),
					ResourceArn:      aws.String("arn:aws:rds:us-west-2:123:cluster:c3"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatus("DELETED"),
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	points, err := c.ListRecoveryPoints(context.Background(), "my-vault", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// COMPLETED and PARTIAL included, DELETED skipped
	if len(points) != 2 {
		t.Errorf("expected 2 points (completed + partial), got %d", len(points))
	}
}

func TestListRecoveryPoints_EFSOnly(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		listRPOutput: &backup.ListRecoveryPointsByBackupVaultOutput{
			RecoveryPoints: []backuptypes.RecoveryPointByBackupVault{
				{
					RecoveryPointArn: aws.String("arn:rds"),
					ResourceType:     aws.String("RDS"),
					ResourceArn:      aws.String("arn:aws:rds:us-west-2:123:cluster:c"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatusCompleted,
				},
				{
					RecoveryPointArn: aws.String("arn:efs"),
					ResourceType:     aws.String("EFS"),
					ResourceArn:      aws.String("arn:aws:elasticfilesystem:us-west-2:123:file-system/fs-1"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatusCompleted,
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	points, err := c.ListRecoveryPoints(context.Background(), "my-vault", "EFS")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(points) != 1 {
		t.Fatalf("expected 1 EFS point, got %d", len(points))
	}
	if points[0].ResourceType != "EFS" {
		t.Errorf("expected EFS, got %s", points[0].ResourceType)
	}
}

func TestListRecoveryPoints_NoMatchingType(t *testing.T) {
	now := time.Now()
	backupMock := &mockBackup{
		listRPOutput: &backup.ListRecoveryPointsByBackupVaultOutput{
			RecoveryPoints: []backuptypes.RecoveryPointByBackupVault{
				{
					RecoveryPointArn: aws.String("arn:rds"),
					ResourceType:     aws.String("RDS"),
					ResourceArn:      aws.String("arn:aws:rds:us-west-2:123:cluster:c"),
					CreationDate:     &now,
					Status:           backuptypes.RecoveryPointStatusCompleted,
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	points, err := c.ListRecoveryPoints(context.Background(), "my-vault", "EFS")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(points) != 0 {
		t.Errorf("expected 0 EFS points when only RDS exist, got %d", len(points))
	}
}

func TestListRecoveryPoints_NilCreationDate(t *testing.T) {
	backupMock := &mockBackup{
		listRPOutput: &backup.ListRecoveryPointsByBackupVaultOutput{
			RecoveryPoints: []backuptypes.RecoveryPointByBackupVault{
				{
					RecoveryPointArn: aws.String("arn:nil-date"),
					ResourceType:     aws.String("RDS"),
					ResourceArn:      aws.String("arn:aws:rds:us-west-2:123:cluster:c"),
					CreationDate:     nil,
					Status:           backuptypes.RecoveryPointStatusCompleted,
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	points, err := c.ListRecoveryPoints(context.Background(), "my-vault", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(points) != 1 {
		t.Fatalf("expected 1 point, got %d", len(points))
	}
	if !points[0].CreationDate.IsZero() {
		t.Error("nil creation date should result in zero time")
	}
}

// ---------------------------------------------------------------------------
// DiscoverVaultByStack - additional cases
// ---------------------------------------------------------------------------

func TestDiscoverVaultByStack_EmptyList(t *testing.T) {
	backupMock := &mockBackup{
		listVaultsOutput: &backup.ListBackupVaultsOutput{
			BackupVaultList: []backuptypes.BackupVaultListMember{},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	_, err := c.DiscoverVaultByStack(context.Background(), "OpenemrEcsStack")
	if err == nil {
		t.Fatal("expected error when vault list is empty")
	}
}

func TestDiscoverVaultByStack_CaseSensitive(t *testing.T) {
	backupMock := &mockBackup{
		listVaultsOutput: &backup.ListBackupVaultsOutput{
			BackupVaultList: []backuptypes.BackupVaultListMember{
				{BackupVaultName: aws.String("openemrecsstack-vault-abc")},
			},
		},
	}
	c := newTestClient(&mockCFN{}, backupMock, &mockRDS{})

	// The stack name has different casing
	_, err := c.DiscoverVaultByStack(context.Background(), "OpenemrEcsStack")
	if err == nil {
		t.Fatal("expected error for case-sensitive vault name mismatch")
	}
}

// ---------------------------------------------------------------------------
// DiscoverStackName - additional cases
// ---------------------------------------------------------------------------

func TestDiscoverStackName_EmptyList(t *testing.T) {
	cfnMock := &mockCFN{
		listStacksOutput: &cloudformation.ListStacksOutput{
			StackSummaries: []cfntypes.StackSummary{},
		},
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	_, err := c.DiscoverStackName(context.Background())
	if err == nil {
		t.Fatal("expected error for empty stack list")
	}
}

// ---------------------------------------------------------------------------
// getRDSClusterDetails - additional cases
// ---------------------------------------------------------------------------

func TestGetRDSClusterDetails_APIError(t *testing.T) {
	rdsMock := &mockRDS{
		describeClustersErr: fmt.Errorf("throttled"),
	}
	c := newTestClient(&mockCFN{}, &mockBackup{}, rdsMock)

	_, _, err := c.getRDSClusterDetails(context.Background(), "cluster-1")
	if err == nil {
		t.Fatal("expected error from API failure")
	}
}

func TestGetRDSClusterDetails_SingleSecurityGroup(t *testing.T) {
	rdsMock := &mockRDS{
		describeClustersOutput: &rds.DescribeDBClustersOutput{
			DBClusters: []rdstypes.DBCluster{
				{
					DBSubnetGroup: aws.String("subnet-1"),
					VpcSecurityGroups: []rdstypes.VpcSecurityGroupMembership{
						{VpcSecurityGroupId: aws.String("sg-only")},
					},
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, &mockBackup{}, rdsMock)

	_, sgs, err := c.getRDSClusterDetails(context.Background(), "cluster")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if sgs != "sg-only" {
		t.Errorf("expected 'sg-only', got %q", sgs)
	}
}

func TestGetRDSClusterDetails_NoSecurityGroups(t *testing.T) {
	rdsMock := &mockRDS{
		describeClustersOutput: &rds.DescribeDBClustersOutput{
			DBClusters: []rdstypes.DBCluster{
				{
					DBSubnetGroup:     aws.String("subnet-1"),
					VpcSecurityGroups: []rdstypes.VpcSecurityGroupMembership{},
				},
			},
		},
	}
	c := newTestClient(&mockCFN{}, &mockBackup{}, rdsMock)

	_, sgs, err := c.getRDSClusterDetails(context.Background(), "cluster")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if sgs != "" {
		t.Errorf("expected empty sgs, got %q", sgs)
	}
}

// ---------------------------------------------------------------------------
// getRDSClusterIDFromStack - additional cases
// ---------------------------------------------------------------------------

func TestGetRDSClusterIDFromStack_APIError(t *testing.T) {
	cfnMock := &mockCFN{
		describeStackErr: fmt.Errorf("forbidden"),
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	_, err := c.getRDSClusterIDFromStack(context.Background(), "TestStack")
	if err == nil {
		t.Fatal("expected error from API failure")
	}
}

func TestGetRDSClusterIDFromStack_EndpointParsing(t *testing.T) {
	cfnMock := &mockCFN{
		describeStackOutput: &cloudformation.DescribeStacksOutput{
			Stacks: []cfntypes.Stack{
				{
					Outputs: []cfntypes.Output{
						{
							OutputKey:   aws.String("DatabaseEndpoint"),
							OutputValue: aws.String("complex-cluster-name.cluster-abc123.us-east-1.rds.amazonaws.com"),
						},
					},
				},
			},
		},
	}
	c := newTestClient(cfnMock, &mockBackup{}, &mockRDS{})

	id, err := c.getRDSClusterIDFromStack(context.Background(), "TestStack")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if id != "complex-cluster-name" {
		t.Errorf("got %q, want 'complex-cluster-name'", id)
	}
}
