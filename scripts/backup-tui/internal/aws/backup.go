// Package aws provides AWS service clients for backup operations.
// This file implements the BackupClient, which handles interactions with
// AWS Backup, RDS, CloudFormation, and STS services.
package aws

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/backup"
	"github.com/aws/aws-sdk-go-v2/service/cloudformation"
	"github.com/aws/aws-sdk-go-v2/service/cloudformation/types"
	"github.com/aws/aws-sdk-go-v2/service/rds"
	"github.com/aws/aws-sdk-go-v2/service/sts"
)

// BackupClient provides methods for interacting with AWS Backup service
// and related AWS services (RDS, CloudFormation, STS) needed for backup
// management and restoration operations.
//
// The client is initialized with AWS credentials and region, and maintains
// service clients for Backup, RDS, CloudFormation, and STS services.
type BackupClient struct {
	client    *backup.Client         // AWS Backup service client
	cfn       *cloudformation.Client // CloudFormation service client for stack queries
	rds       *rds.Client            // RDS service client for cluster details
	sts       *sts.Client            // STS service client for account ID
	region    string                 // AWS region
	accountID string                 // Cached AWS account ID
}

// NewBackupClient creates a new BackupClient with AWS service clients
// configured for the specified region.
//
// This function:
// 1. Loads AWS configuration (credentials, region)
// 2. Creates service clients for Backup, RDS, CloudFormation, and STS
// 3. Retrieves and caches the AWS account ID for IAM role ARN construction
//
// Parameters:
//   - ctx: Context for cancellation and timeout
//   - region: AWS region name (e.g., "us-west-2")
//
// Returns:
//   - *BackupClient: Initialized backup client
//   - error: Error if initialization fails (credentials, network, etc.)
//
// Example:
//
//	client, err := NewBackupClient(ctx, "us-west-2")
//	if err != nil {
//	    return fmt.Errorf("failed to create backup client: %w", err)
//	}
func NewBackupClient(ctx context.Context, region string) (*BackupClient, error) {
	cfg, err := loadAWSConfig(ctx, region)
	if err != nil {
		return nil, err
	}

	stsClient := sts.NewFromConfig(cfg)

	// Get account ID - required for constructing IAM role ARNs
	identity, err := stsClient.GetCallerIdentity(ctx, &sts.GetCallerIdentityInput{})
	if err != nil {
		return nil, fmt.Errorf("failed to get caller identity: %w", err)
	}
	accountID := aws.ToString(identity.Account)

	return &BackupClient{
		client:    backup.NewFromConfig(cfg),
		cfn:       cloudformation.NewFromConfig(cfg),
		rds:       rds.NewFromConfig(cfg),
		sts:       stsClient,
		region:    region,
		accountID: accountID,
	}, nil
}

// DiscoverStackName discovers the CloudFormation stack name by listing
// stacks and finding one that matches the OpenEMR pattern (starts with "OpenemrEcs").
//
// This is useful when the stack name is not explicitly provided, allowing
// the TUI to automatically find the correct stack using current AWS credentials.
//
// Parameters:
//   - ctx: Context for cancellation and timeout
//
// Returns:
//   - string: Stack name if found (empty string if multiple or none found)
//   - error: Error if API call fails or multiple stacks found
//
// Example:
//
//	stackName, err := client.DiscoverStackName(ctx)
//	// Returns: "OpenemrEcsStack", nil
func (c *BackupClient) DiscoverStackName(ctx context.Context) (string, error) {
	input := &cloudformation.ListStacksInput{
		StackStatusFilter: []types.StackStatus{
			types.StackStatusCreateComplete,
			types.StackStatusUpdateComplete,
			types.StackStatusUpdateRollbackComplete,
		},
	}

	result, err := c.cfn.ListStacks(ctx, input)
	if err != nil {
		return "", fmt.Errorf("failed to list CloudFormation stacks: %w", err)
	}

	var matchingStacks []string
	for _, summary := range result.StackSummaries {
		stackName := aws.ToString(summary.StackName)
		// Match stacks that start with "OpenemrEcs" (case-sensitive)
		if strings.HasPrefix(stackName, "OpenemrEcs") {
			matchingStacks = append(matchingStacks, stackName)
		}
	}

	if len(matchingStacks) == 0 {
		return "", fmt.Errorf("no CloudFormation stacks found matching pattern 'OpenemrEcs*'")
	}

	if len(matchingStacks) > 1 {
		return "", fmt.Errorf("multiple CloudFormation stacks found matching pattern 'OpenemrEcs*': %v. Please specify stack name with -stack flag", matchingStacks)
	}

	return matchingStacks[0], nil
}

// DiscoverVaultByStack discovers a backup vault by searching for vaults
// whose name contains the specified stack name.
//
// This is useful when the exact vault name is unknown, as AWS Backup
// vaults created by CDK typically include the stack name in their name.
//
// Parameters:
//   - ctx: Context for cancellation and timeout
//   - stackName: CloudFormation stack name to search for
//
// Returns:
//   - string: Backup vault name if found
//   - error: Error if vault not found or AWS API call fails
//
// Example:
//
//	vaultName, err := client.DiscoverVaultByStack(ctx, "OpenemrEcsStack")
//	// Returns: "OpenemrEcsStack-vault-abc123", nil
func (c *BackupClient) DiscoverVaultByStack(ctx context.Context, stackName string) (string, error) {
	input := &backup.ListBackupVaultsInput{}
	result, err := c.client.ListBackupVaults(ctx, input)
	if err != nil {
		return "", fmt.Errorf("failed to list backup vaults: %w", err)
	}

	// Look for vault with stack name in the name
	// This matches the CDK naming convention: {StackName}-vault-{Suffix}
	searchPattern := stackName
	for _, vault := range result.BackupVaultList {
		if strings.Contains(*vault.BackupVaultName, searchPattern) {
			return *vault.BackupVaultName, nil
		}
	}

	return "", fmt.Errorf("backup vault not found for stack: %s", stackName)
}

// ListRecoveryPoints lists all recovery points in the specified backup vault,
// optionally filtered by resource type (RDS, EFS, etc.).
//
// This function handles pagination automatically, returning all recovery points
// across multiple pages if necessary.
//
// Parameters:
//   - ctx: Context for cancellation and timeout
//   - vaultName: Name of the backup vault to query
//   - resourceType: Optional filter by resource type (empty string = all types)
//
// Returns:
//   - []RecoveryPoint: List of recovery points with metadata
//   - error: Error if API call fails
//
// Example:
//
//	points, err := client.ListRecoveryPoints(ctx, "my-vault", "RDS")
//	// Returns only RDS recovery points
func (c *BackupClient) ListRecoveryPoints(ctx context.Context, vaultName, resourceType string) ([]RecoveryPoint, error) {
	if vaultName == "" {
		return nil, fmt.Errorf("vault name cannot be empty")
	}

	input := &backup.ListRecoveryPointsByBackupVaultInput{
		BackupVaultName: aws.String(vaultName),
		// Don't set MaxResults - let paginator handle it automatically
	}

	var allPoints []RecoveryPoint
	paginator := backup.NewListRecoveryPointsByBackupVaultPaginator(c.client, input)

	// Iterate through all pages of results
	// Note: If the vault exists but has no recovery points, this loop will
	// execute once (empty page) and return an empty slice, which is correct.
	var totalPointsSeen int
	var pagesProcessed int
	for paginator.HasMorePages() {
		pagesProcessed++
		page, err := paginator.NextPage(ctx)
		if err != nil {
			return nil, fmt.Errorf("failed to list recovery points from vault %s (after %d pages, %d points): %w", vaultName, pagesProcessed, totalPointsSeen, err)
		}

		// Track total points seen before filtering (for debugging)
		totalPointsSeen += len(page.RecoveryPoints)

		// Process each recovery point in the current page
		// If page.RecoveryPoints is empty, no backups exist in this page
		for _, point := range page.RecoveryPoints {
			// Filter by resource type if specified
			pointResourceType := aws.ToString(point.ResourceType)
			if resourceType != "" && pointResourceType != resourceType {
				continue
			}

			// Include all recovery points regardless of status
			// AWS Backup recovery points can have various statuses:
			// COMPLETED, AVAILABLE, PARTIAL, DELETING, DELETED, EXPIRED
			// We'll show all except DELETED (which shouldn't be returned by the API anyway)
			pointStatus := string(point.Status)
			if pointStatus == "DELETED" {
				// Skip deleted points (though API shouldn't return these)
				continue
			}

			// Convert AWS Backup recovery point to our RecoveryPoint struct
			rp := RecoveryPoint{
				RecoveryPointARN: aws.ToString(point.RecoveryPointArn),
				CreationDate:     aws.ToTime(point.CreationDate),
				Status:           pointStatus,
				ResourceType:     pointResourceType,
				ResourceID:       extractResourceID(aws.ToString(point.ResourceArn)),
			}

			if point.BackupSizeInBytes != nil {
				rp.BackupSizeInBytes = *point.BackupSizeInBytes
			}

			allPoints = append(allPoints, rp)
		}
	}

	// If we saw recovery points but filtered them all out, that's unusual
	// This can help diagnose filtering issues (all points DELETING/EXPIRED or filtered by resource type)
	_ = totalPointsSeen // Tracked for potential future debugging/logging

	return allPoints, nil
}

// StartRestoreJob initiates a restore job from a recovery point.
//
// This function handles the complexity of preparing restore metadata based on
// resource type:
// - For RDS: Queries CloudFormation and RDS to get cluster details, subnet groups, and security groups
// - For EFS: Uses the file system ID directly
//
// Parameters:
//   - ctx: Context for cancellation and timeout
//   - rp: Recovery point to restore from
//   - stackName: CloudFormation stack name (used for RDS metadata lookup)
//   - vaultName: Backup vault name (used to discover the IAM role from the backup plan)
//
// Returns:
//   - string: Restore job ID if successful
//   - error: Error if restore job cannot be started
//
// Note: The restore job runs asynchronously. Use AWS Backup APIs to monitor
// the job status after this function returns.
//
// Example:
//
//	jobID, err := client.StartRestoreJob(ctx, recoveryPoint, "OpenemrEcsStack", "my-vault")
func (c *BackupClient) StartRestoreJob(ctx context.Context, rp RecoveryPoint, stackName, vaultName string) (string, error) {
	// Discover the IAM role from the backup plan that uses this vault
	roleArn, err := c.getBackupPlanRoleArn(ctx, vaultName)
	if err != nil {
		return "", fmt.Errorf("failed to get backup plan role ARN: %w", err)
	}

	input := &backup.StartRestoreJobInput{
		RecoveryPointArn: aws.String(rp.RecoveryPointARN),
		IamRoleArn:       aws.String(roleArn),
		Metadata:         make(map[string]string),
	}

	// Add metadata based on resource type
	switch rp.ResourceType {
	case "RDS":
		// For RDS, we need to get cluster details from stack outputs and RDS API
		dbClusterID, err := c.getRDSClusterIDFromStack(ctx, stackName)
		if err != nil {
			return "", fmt.Errorf("failed to get RDS cluster ID from stack: %w", err)
		}

		// Get subnet group and security groups from RDS cluster
		subnetGroup, securityGroups, err := c.getRDSClusterDetails(ctx, dbClusterID)
		if err != nil {
			return "", fmt.Errorf("failed to get RDS cluster details: %w", err)
		}

		// RDS restore metadata requires:
		// - DBClusterIdentifier: The target cluster identifier
		// - DBSubnetGroupName: The subnet group to use for the restored cluster
		// - VpcSecurityGroupIds: Comma-separated list of security group IDs
		input.Metadata["DBClusterIdentifier"] = dbClusterID
		input.Metadata["DBSubnetGroupName"] = subnetGroup
		input.Metadata["VpcSecurityGroupIds"] = securityGroups
	case "EFS":
		// EFS restore metadata:
		// - file-system-id: The target file system ID (restores in-place)
		// - newFileSystem: "false" to restore to existing file system
		// - Encrypted: "true" to maintain encryption
		input.Metadata["file-system-id"] = rp.ResourceID
		input.Metadata["newFileSystem"] = "false"
		input.Metadata["Encrypted"] = "true"
	}

	result, err := c.client.StartRestoreJob(ctx, input)
	if err != nil {
		return "", fmt.Errorf("failed to start restore job: %w", err)
	}

	return aws.ToString(result.RestoreJobId), nil
}

// RecoveryPoint represents a backup recovery point with its metadata.
// This struct provides a simplified, application-friendly representation
// of AWS Backup recovery points, abstracting away AWS SDK-specific types.
type RecoveryPoint struct {
	RecoveryPointARN  string    // Full ARN of the recovery point
	CreationDate      time.Time // When the backup was created
	Status            string    // Recovery point status (COMPLETED, AVAILABLE, etc.)
	ResourceType      string    // Type of resource (RDS, EFS, etc.)
	ResourceID        string    // ID of the backed-up resource (extracted from ARN)
	BackupSizeInBytes int64     // Size of the backup in bytes
}

// getRDSClusterIDFromStack retrieves the RDS cluster identifier from
// CloudFormation stack outputs.
//
// This function looks for the "DatabaseEndpoint" output, which contains
// the RDS cluster endpoint. The cluster ID is extracted from the endpoint
// (it's the part before the first dot).
//
// Parameters:
//   - ctx: Context for cancellation and timeout
//   - stackName: CloudFormation stack name
//
// Returns:
//   - string: RDS cluster identifier
//   - error: Error if stack not found or output missing
//
// Example:
//
//	clusterID, err := client.getRDSClusterIDFromStack(ctx, "OpenemrEcsStack")
//	// Returns: "openemr-cluster-abc123", nil
func (c *BackupClient) getRDSClusterIDFromStack(ctx context.Context, stackName string) (string, error) {
	input := &cloudformation.DescribeStacksInput{
		StackName: aws.String(stackName),
	}

	result, err := c.cfn.DescribeStacks(ctx, input)
	if err != nil {
		return "", fmt.Errorf("failed to describe stack: %w", err)
	}

	if len(result.Stacks) == 0 {
		return "", fmt.Errorf("stack not found: %s", stackName)
	}

	stack := result.Stacks[0]

	// Look for DatabaseEndpoint output (standard CDK output name)
	for _, output := range stack.Outputs {
		if aws.ToString(output.OutputKey) == "DatabaseEndpoint" {
			endpoint := aws.ToString(output.OutputValue)
			// Extract cluster ID from endpoint
			// Format: cluster-id.xxx.region.rds.amazonaws.com
			parts := strings.Split(endpoint, ".")
			if len(parts) > 0 {
				return parts[0], nil
			}
			return endpoint, nil
		}
	}

	return "", fmt.Errorf("DatabaseEndpoint output not found in stack: %s", stackName)
}

// getRDSClusterDetails retrieves subnet group and security groups from
// an existing RDS cluster.
//
// This information is required for RDS restore operations, as the restored
// cluster needs to use the same network configuration as the original.
//
// Parameters:
//   - ctx: Context for cancellation and timeout
//   - clusterID: RDS cluster identifier
//
// Returns:
//   - string: Subnet group name
//   - string: Comma-separated security group IDs
//   - error: Error if cluster not found or API call fails
//
// Example:
//
//	subnetGroup, securityGroups, err := client.getRDSClusterDetails(ctx, "my-cluster")
//	// Returns: "my-subnet-group", "sg-123,sg-456", nil
func (c *BackupClient) getRDSClusterDetails(ctx context.Context, clusterID string) (string, string, error) {
	input := &rds.DescribeDBClustersInput{
		DBClusterIdentifier: aws.String(clusterID),
	}

	result, err := c.rds.DescribeDBClusters(ctx, input)
	if err != nil {
		return "", "", fmt.Errorf("failed to describe DB cluster: %w", err)
	}

	if len(result.DBClusters) == 0 {
		return "", "", fmt.Errorf("DB cluster not found: %s", clusterID)
	}

	cluster := result.DBClusters[0]
	subnetGroup := aws.ToString(cluster.DBSubnetGroup)

	// Collect security group IDs into a comma-separated string
	// AWS Backup metadata requires security groups as a comma-separated list
	var sgIDs []string
	for _, sg := range cluster.VpcSecurityGroups {
		if sg.VpcSecurityGroupId != nil {
			sgIDs = append(sgIDs, *sg.VpcSecurityGroupId)
		}
	}
	securityGroups := strings.Join(sgIDs, ",")

	return subnetGroup, securityGroups, nil
}

// getBackupPlanRoleArn discovers the IAM role ARN from the backup plan
// that uses the specified vault. This ensures restore operations use the
// correct role with proper permissions, rather than the default service role
// which may not have the necessary trust relationship.
//
// Parameters:
//   - ctx: Context for cancellation and timeout
//   - vaultName: Name of the backup vault
//
// Returns:
//   - string: IAM role ARN from the backup plan
//   - error: Error if the role cannot be discovered
func (c *BackupClient) getBackupPlanRoleArn(ctx context.Context, vaultName string) (string, error) {
	if vaultName == "" {
		return "", fmt.Errorf("vault name cannot be empty")
	}

	// List all backup plans
	listPlansInput := &backup.ListBackupPlansInput{}
	plansPaginator := backup.NewListBackupPlansPaginator(c.client, listPlansInput)

	for plansPaginator.HasMorePages() {
		plansPage, err := plansPaginator.NextPage(ctx)
		if err != nil {
			return "", fmt.Errorf("failed to list backup plans: %w", err)
		}

		// Check each plan to see if it uses our vault
		for _, plan := range plansPage.BackupPlansList {
			// Get the plan details to check which vault it uses
			getPlanInput := &backup.GetBackupPlanInput{
				BackupPlanId: plan.BackupPlanId,
			}
			planDetails, err := c.client.GetBackupPlan(ctx, getPlanInput)
			if err != nil {
				// Skip this plan if we can't get details
				continue
			}

			// Check if any rule in this plan targets our vault
			for _, rule := range planDetails.BackupPlan.Rules {
				if rule.TargetBackupVaultName != nil && *rule.TargetBackupVaultName == vaultName {
					// Found the plan that uses our vault, get its IAM role from backup selections
					listSelectionsInput := &backup.ListBackupSelectionsInput{
						BackupPlanId: plan.BackupPlanId,
					}
					selectionsPaginator := backup.NewListBackupSelectionsPaginator(c.client, listSelectionsInput)

					for selectionsPaginator.HasMorePages() {
						selectionsPage, err := selectionsPaginator.NextPage(ctx)
						if err != nil {
							continue
						}

						// Get the first selection's IAM role
						for _, selection := range selectionsPage.BackupSelectionsList {
							if selection.IamRoleArn != nil && *selection.IamRoleArn != "" {
								return *selection.IamRoleArn, nil
							}
						}
					}
				}
			}
		}
	}

	// Fallback to default service role if plan role not found
	// This should not happen in practice, but provides a fallback
	return fmt.Sprintf("arn:aws:iam::%s:role/service-role/AWSBackupDefaultServiceRole", c.accountID), nil
}

// extractResourceID extracts the resource ID from an AWS resource ARN.
//
// ARN format: arn:aws:service:region:account:resource-type/resource-id
// This function extracts the resource-id part, which is typically the
// last component after the final slash.
//
// Parameters:
//   - arn: AWS resource ARN
//
// Returns:
//   - string: Resource ID (or original ARN if parsing fails)
//
// Example:
//
//	extractResourceID("arn:aws:rds:us-west-2:123456789012:cluster:my-cluster")
//	// Returns: "my-cluster"
func extractResourceID(arn string) string {
	parts := strings.Split(arn, ":")
	if len(parts) >= 6 {
		resourcePart := parts[5]
		// Extract resource ID from ARN (format varies by service)
		// For RDS: cluster:cluster-id
		// For EFS: file-system/fs-xxxxx
		idParts := strings.Split(resourcePart, "/")
		if len(idParts) > 0 {
			// Return the last part (resource ID)
			return idParts[len(idParts)-1]
		}
		return resourcePart
	}
	return arn
}
