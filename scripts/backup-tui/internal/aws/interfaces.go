package aws

import (
	"context"

	"github.com/aws/aws-sdk-go-v2/service/backup"
	"github.com/aws/aws-sdk-go-v2/service/cloudformation"
	"github.com/aws/aws-sdk-go-v2/service/rds"
)

// CloudFormationAPI defines the CloudFormation operations used by BackupClient.
type CloudFormationAPI interface {
	ListStacks(ctx context.Context, params *cloudformation.ListStacksInput, optFns ...func(*cloudformation.Options)) (*cloudformation.ListStacksOutput, error)
	DescribeStacks(ctx context.Context, params *cloudformation.DescribeStacksInput, optFns ...func(*cloudformation.Options)) (*cloudformation.DescribeStacksOutput, error)
}

// BackupAPI defines the AWS Backup operations used by BackupClient.
type BackupAPI interface {
	ListBackupVaults(ctx context.Context, params *backup.ListBackupVaultsInput, optFns ...func(*backup.Options)) (*backup.ListBackupVaultsOutput, error)
	ListRecoveryPointsByBackupVault(ctx context.Context, params *backup.ListRecoveryPointsByBackupVaultInput, optFns ...func(*backup.Options)) (*backup.ListRecoveryPointsByBackupVaultOutput, error)
	StartRestoreJob(ctx context.Context, params *backup.StartRestoreJobInput, optFns ...func(*backup.Options)) (*backup.StartRestoreJobOutput, error)
	ListBackupPlans(ctx context.Context, params *backup.ListBackupPlansInput, optFns ...func(*backup.Options)) (*backup.ListBackupPlansOutput, error)
	GetBackupPlan(ctx context.Context, params *backup.GetBackupPlanInput, optFns ...func(*backup.Options)) (*backup.GetBackupPlanOutput, error)
	ListBackupSelections(ctx context.Context, params *backup.ListBackupSelectionsInput, optFns ...func(*backup.Options)) (*backup.ListBackupSelectionsOutput, error)
}

// RDSAPI defines the RDS operations used by BackupClient.
type RDSAPI interface {
	DescribeDBClusters(ctx context.Context, params *rds.DescribeDBClustersInput, optFns ...func(*rds.Options)) (*rds.DescribeDBClustersOutput, error)
}
