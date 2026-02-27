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
	listVaultsOutput    *backup.ListBackupVaultsOutput
	listVaultsErr       error
	listRPOutput        *backup.ListRecoveryPointsByBackupVaultOutput
	listRPErr           error
	startRestoreOutput  *backup.StartRestoreJobOutput
	startRestoreErr     error
	listPlansOutput     *backup.ListBackupPlansOutput
	listPlansErr        error
	getPlanOutput       *backup.GetBackupPlanOutput
	getPlanErr          error
	listSelectionsOut   *backup.ListBackupSelectionsOutput
	listSelectionsErr   error
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
