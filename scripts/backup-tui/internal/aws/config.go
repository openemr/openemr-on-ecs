// Package aws provides AWS service clients and configuration management
// for the backup TUI application.
package aws

import (
	"context"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
)

// loadAWSConfig loads AWS configuration for the specified region.
// This function uses the default credential chain, which checks:
// 1. Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, etc.)
// 2. AWS credentials file (~/.aws/credentials)
// 3. IAM role credentials (if running on EC2/ECS/Lambda)
// 4. AWS SSO credentials
//
// Parameters:
//   - ctx: Context for cancellation and timeout
//   - region: AWS region name (e.g., "us-west-2")
//
// Returns:
//   - aws.Config: Configured AWS config with the specified region
//   - error: Error if configuration fails
//
// Note: This function should be called once per application startup to
// create a shared config that can be used for all AWS service clients.
func loadAWSConfig(ctx context.Context, region string) (aws.Config, error) {
	cfg, err := awsconfig.LoadDefaultConfig(ctx, awsconfig.WithRegion(region))
	if err != nil {
		return aws.Config{}, err
	}
	return cfg, nil
}
