# Architecture Documentation

This document provides a detailed overview of the OpenEMR on AWS Fargate architecture, including component relationships, data flows, and design decisions.

## Table of Contents
- [Overview](#overview)
- [Component Architecture](#component-architecture)
- [Network Architecture](#network-architecture)
- [Security Architecture](#security-architecture)
- [Data Flow](#data-flow)
- [Scaling Architecture](#scaling-architecture)
- [Disaster Recovery](#disaster-recovery)

## Overview

The OpenEMR on AWS Fargate deployment is a fully serverless, HIPAA-eligible architecture that provides:
- **Zero server management**: All compute is handled by AWS Fargate
- **Automatic scaling**: Resources scale based on demand
- **High availability**: Multi-AZ deployment with automatic failover
- **Security**: End-to-end encryption and comprehensive audit logging
- **Cost efficiency**: Pay only for resources you use

## Component Architecture

### Compute Layer

#### ECS Fargate Tasks
- **Purpose**: Run OpenEMR application containers
- **Architecture**: ARM64 (Graviton) for cost efficiency
- **Resources**: 2 vCPU, 4GB RAM per task (configurable)
- **Scaling**: 2-100 tasks (configurable)
- **Deployment**: Multi-AZ for high availability

**Key Features:**
- Automatic container orchestration
- Health check monitoring
- Automatic task replacement on failure
- Integration with Application Load Balancer

### Storage Layer

#### Amazon EFS (Elastic File System)
Two EFS file systems are used:

1. **Sites File System**
   - **Purpose**: Shared OpenEMR sites directory
   - **Contents**: Patient data, documents, configurations
   - **Access**: All ECS tasks mount this volume
   - **Backup**: Automated daily/weekly/monthly backups with 7-year retention

2. **SSL Certificates File System**
   - **Purpose**: Shared SSL/TLS certificates
   - **Contents**: Self-signed certificates for ALB-to-container communication
   - **Maintenance**: Automatically regenerated every 2 days

#### Aurora Serverless v2 (MySQL)
- **Purpose**: Primary database for OpenEMR
- **Engine**: Aurora MySQL (configurable version)
- **Scaling**: 0.5-256 ACUs (Aurora Capacity Units)
  - **Minimum**: 0.5 ACU always-on for instant connections (~$44/month)
  - **Maximum**: Configurable (default 256 ACU)
- **Deployment**: Multi-AZ with automatic failover
- **Encryption**: Enabled at rest and in transit
- **SSL/TLS**: Required for all connections (`require_secure_transport=ON`)
- **Backup**: Automated daily/weekly/monthly backups with 7-year retention

**Database Configuration:**
- Comprehensive audit logging
- Performance Insights enabled
- Slow query logging
- Automatic scaling based on workload

### Caching Layer

#### ElastiCache Serverless (Valkey/Redis)
- **Purpose**: Session management and application caching
- **Protocol**: Redis-compatible (Valkey)
- **Encryption**: TLS enabled for all connections
- **Deployment**: Serverless (no capacity management required)
- **Access**: Private subnets only

### Network Layer

#### Application Load Balancer (ALB)
- **Purpose**: Traffic distribution and SSL termination
- **Protocol**: HTTPS (443) with optional HTTP (80) redirect
- **Features**:
  - SSL/TLS termination
  - Health checks
  - Automatic traffic distribution
  - Integration with AWS WAF

#### AWS WAF
- **Purpose**: Web application firewall
- **Protection**: Common web exploits, bot attacks
- **Integration**: Positioned between internet and ALB

#### VPC Architecture
- **CIDR**: 10.0.0.0/16 (configurable)
- **Subnets**:
  - Public subnets: ALB, NAT Gateways
  - Private subnets: ECS tasks, RDS, ElastiCache
- **NAT Gateways**: 2 (one per AZ) for outbound internet access
- **Security Groups**: Restrictive rules following least privilege

### Security Layer

#### AWS Secrets Manager
- **Purpose**: Secure credential storage and rotation
- **Secrets**:
  - Database credentials (auto-generated)
  - OpenEMR admin credentials (auto-generated)
  - SMTP credentials (if SES configured)

#### AWS KMS
- **Purpose**: Encryption key management
- **Usage**:
  - EFS encryption
  - RDS encryption
  - Secrets Manager encryption
  - CloudWatch Logs encryption

#### SSL/TLS Certificates
- **Client to ALB**: AWS Certificate Manager (required, via Route53 domain or certificate ARN)
- **ALB to Containers**: HTTPS with self-signed certificates (auto-generated and shared via EFS)
- **MySQL Connections**: TLS with RDS CA certificate bundle (auto-downloaded)
- **Redis Connections**: TLS with Amazon Root CA (auto-downloaded)

**Note:** A certificate is required for deployment. Either provide `route53_domain` (for automated certificate management) or `certificate_arn` (for an existing ACM certificate). HTTP is never exposed - all traffic uses HTTPS.

## Network Architecture

### Ingress Flow
```
Internet
  ↓
AWS WAF (if enabled)
  ↓
Application Load Balancer (HTTPS 443)
  ↓
ECS Fargate Tasks (HTTPS 443)
  ↓
OpenEMR Application
```

### Database Access Flow
```
ECS Fargate Task
  ↓
Security Group (port 3306)
  ↓
Aurora MySQL (TLS encrypted)
```

### Cache Access Flow
```
ECS Fargate Task
  ↓
Security Group (port 6379)
  ↓
ElastiCache Valkey (TLS encrypted)
```

## Security Architecture

### Encryption in Transit
- **Client to ALB**: TLS 1.2+ (required - certificate must be provided via `route53_domain` or `certificate_arn`)
- **ALB to Containers**: HTTPS (port 443) with self-signed certificates (auto-generated)
- **Database**: TLS with RDS CA certificate (required)
- **Cache**: TLS with Amazon Root CA (required)
- **HTTP never exposed**: Port 80 is never opened, ensuring all traffic is encrypted

### Encryption at Rest
- **EFS**: KMS-encrypted
- **RDS**: KMS-encrypted
- **Secrets Manager**: KMS-encrypted
- **CloudWatch Logs**: KMS-encrypted (if ECS Exec enabled)

### Network Security
- **Security Groups**: Restrictive rules, least privilege
- **Private Subnets**: Database and cache in isolated subnets
- **NAT Gateways**: Secure outbound internet access
- **No Direct Internet Access**: ECS tasks cannot receive direct internet traffic

### Access Control
- **IAM Roles**: Least privilege for all services
- **Secrets Manager**: Automatic credential rotation
- **VPC Isolation**: Resources isolated in private subnets

## Data Flow

### Application Startup
1. Container starts and downloads SSL certificates
2. Certificates copied to OpenEMR certificates directory
3. OpenEMR configuration script runs
4. Database connection established (with SSL)
5. Application becomes healthy and receives traffic

### User Request Flow
1. User makes HTTPS request
2. AWS WAF filters request (if enabled)
3. ALB routes to healthy ECS task
4. OpenEMR processes request
5. Database queries executed (TLS encrypted)
6. Cache accessed (TLS encrypted)
7. Response returned to user

### Data Persistence
- **Patient Data**: Stored in Aurora MySQL (encrypted)
- **Documents**: Stored in EFS (encrypted)
- **Sessions**: Stored in ElastiCache (TLS encrypted)
- **Backups**: Automated to S3 (encrypted)

## Scaling Architecture

### Horizontal Scaling (ECS Tasks)
- **Trigger**: CPU or memory utilization thresholds
- **Scale Out**: Add tasks when utilization > threshold
- **Scale In**: Remove tasks when utilization < threshold
- **Range**: 2-100 tasks (configurable)
- **Time**: ~2-5 minutes for scale events

### Database Scaling (Aurora Serverless v2)
- **Trigger**: Automatic based on workload
- **Scale Out**: Increase ACUs as load increases
- **Scale In**: Decrease ACUs during low usage (minimum 0.5 ACU)
- **Range**: 0.5-256 ACUs (configurable)
- **Time**: Seconds to minutes
- **Note**: Minimum capacity prevents cold start delays (3-5 minutes) when scaling from zero

### Cache Scaling (ElastiCache Serverless)
- **Trigger**: Automatic based on demand
- **Management**: Fully automated by AWS
- **No Configuration**: Required

## Disaster Recovery

### Backup Strategy
- **RDS**: Daily, weekly, and monthly backups (7-year retention)
- **EFS**: Daily, weekly, and monthly backups (7-year retention)
- **Storage**: AWS Backup service
- **Recovery Time**: Minutes to hours (depending on data size)

### High Availability
- **Multi-AZ**: All critical components in multiple availability zones
- **Automatic Failover**: RDS and ALB handle failover automatically
- **Task Replacement**: ECS automatically replaces unhealthy tasks
- **Load Distribution**: ALB distributes traffic across healthy tasks

### Monitoring and Alerting
- **CloudWatch**: Comprehensive metrics and logs
- **Health Checks**: Application and infrastructure level
- **Alarms**: Configurable for critical metrics
- **Logs**: Centralized in CloudWatch Logs

## Design Decisions

### Why Fargate?
- **No Server Management**: Eliminates EC2 instance management
- **Cost Efficiency**: Pay only for running tasks
- **Automatic Scaling**: Built-in autoscaling capabilities
- **Security**: Managed by AWS with automatic patching

### Why Aurora Serverless v2?
- **Automatic Scaling**: Scales from 0.5 ACU to max capacity based on demand
- **Always Available**: Minimum 0.5 ACU ensures instant connections (no cold starts)
- **Cost Efficiency**: Pay for actual capacity used (minimum ~$44/month base)
- **High Availability**: Multi-AZ with automatic failover
- **Performance**: Better than provisioned for variable workloads

### Why ElastiCache Serverless?
- **No Management**: Fully managed by AWS
- **Automatic Scaling**: Scales automatically with demand
- **Cost Efficiency**: Pay only for usage
- **Compatibility**: Redis-compatible (Valkey)

### Why EFS?
- **Shared Storage**: Multiple containers can access same data
- **Automatic Scaling**: Grows and shrinks automatically
- **Durability**: Highly durable and available
- **Backup Integration**: Native AWS Backup support

## Cost Optimization

### Right-Sizing
- **Graviton Processors**: ARM64 for 20% cost savings
- **Serverless Services**: Pay only for usage
- **Automatic Scaling**: Scale down during low usage

### Reserved Capacity
- **NAT Gateways**: Consider VPC endpoints for cost savings
- **Data Transfer**: Optimize data transfer patterns

### Monitoring
- **CloudWatch**: Monitor costs and usage
- **Cost Explorer**: Track spending by service
- **Budgets**: Set up cost alerts

## Performance Considerations

### Database Performance
- **Connection Pooling**: Enabled in OpenEMR
- **Query Optimization**: Performance Insights for analysis
- **Read Replicas**: Automatic read scaling with Aurora

### Application Performance
- **Caching**: ElastiCache for session and application cache
- **CDN**: Consider CloudFront for static assets
- **Compression**: Enable at ALB level

### Network Performance
- **Global Accelerator**: Optional for global users
- **VPC Endpoints**: Reduce NAT Gateway costs
- **Connection Keep-Alive**: Optimize connection reuse

