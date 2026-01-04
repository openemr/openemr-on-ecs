# OpenEMR ECS CDK Modules

This directory contains the modular CDK components that define the OpenEMR on AWS infrastructure.

## Table of Contents

- [Overview](#overview)
- [Module Structure](#module-structure)
- [Core Modules](#core-modules)
- [Module Dependencies](#module-dependencies)
- [Development Guidelines](#development-guidelines)

## Overview

The `openemr_ecs` package is organized into specialized modules, each responsible for a specific aspect of the infrastructure. This modular approach provides:

- **Separation of Concerns**: Each module has a clear, focused responsibility
- **Maintainability**: Easier to understand and modify individual components
- **Testability**: Modules can be tested independently
- **Reusability**: Components can be reused across different stacks

## Module Structure

```
openemr_ecs/
├── __init__.py           # Package initialization
├── stack.py              # Main stack orchestrator
├── constants.py          # Shared constants and versions
├── utils.py              # Utility functions
├── validation.py         # Configuration validation
├── network.py            # Network infrastructure
├── storage.py            # Storage resources
├── database.py           # Database components
├── compute.py            # Compute resources
├── security.py           # Security components
├── analytics.py          # Analytics environment
├── monitoring.py         # Monitoring and alarms
└── cleanup.py            # Stack cleanup automation
```

## Core Modules

### `stack.py`

**Purpose**: Main CDK stack orchestrator that coordinates all modules.

**Responsibilities**:
- Initializes and connects all component modules
- Manages resource dependencies and creation order
- Validates context configuration
- Creates CloudFormation outputs
- Handles stack-level concerns (termination protection, cleanup)

**Key Classes**:
- `OpenemrEcsStack`: Main stack class

### `constants.py`

**Purpose**: Centralized constants and version definitions.

**Contents**:
- OpenEMR container version
- Aurora MySQL engine version
- Lambda runtime versions
- Default port numbers
- EMR Serverless release labels

**Usage**: Imported by other modules to ensure consistent versioning.

### `utils.py`

**Purpose**: Shared utility functions used across modules.

**Functions**:
- `get_resource_suffix()`: Generates consistent resource suffixes
- `is_true()`: Context value boolean parsing

### `validation.py`

**Purpose**: Pre-deployment validation of CDK context parameters.

**Features**:
- Validates configuration values
- Checks for conflicting settings
- Ensures required parameters are present
- Provides clear error messages

**Validation Functions**:
- `validate_context()`: Main validation entry point
- `validate_fargate_cpu_memory()`: CPU/memory compatibility
- `validate_route53_and_certificate_config()`: DNS/certificate configuration
- `validate_email_forwarding_config()`: SES email configuration

### `network.py`

**Purpose**: Network infrastructure components.

**Class**: `NetworkComponents`

**Creates**:
- VPC with public and private subnets
- Security groups (database, cache, load balancer)
- Application Load Balancer (ALB)
- Optional Global Accelerator
- VPC Flow Logs

**Key Methods**:
- `create_vpc()`: Creates VPC with subnets
- `create_security_groups()`: Creates security groups
- `create_alb()`: Creates load balancer

### `storage.py`

**Purpose**: Storage infrastructure components.

**Class**: `StorageComponents`

**Creates**:
- EFS file systems (sites and SSL)
- S3 buckets (logs, backups)
- AWS Backup plan and vault
- Optional CloudTrail logging

**Key Methods**:
- `create_efs_volumes()`: Creates EFS file systems
- `create_backup_plan()`: Configures AWS Backup
- `create_elb_log_bucket()`: Creates S3 bucket for ALB logs

### `database.py`

**Purpose**: Database and cache infrastructure.

**Class**: `DatabaseComponents`

**Creates**:
- RDS Aurora MySQL Serverless v2 cluster
- ElastiCache Serverless Valkey (Redis) cluster
- Database parameter groups
- SSL/TLS configuration

**Key Methods**:
- `create_db_instance()`: Creates RDS Aurora cluster
- `create_valkey_cluster()`: Creates Valkey/Redis cluster

### `compute.py`

**Purpose**: Compute resources for running OpenEMR.

**Class**: `ComputeComponents`

**Creates**:
- ECS cluster
- Fargate service definition
- Container task definitions
- Auto-scaling configuration
- Health checks

**Key Methods**:
- `create_ecs_cluster()`: Creates ECS cluster
- `create_openemr_service()`: Creates Fargate service

### `security.py`

**Purpose**: Security and compliance components.

**Class**: `SecurityComponents`

**Creates**:
- AWS WAF web application firewall
- ACM certificates
- Route53 DNS records
- SES email configuration
- SSL/TLS materials generation

**Key Methods**:
- `create_waf()`: Creates WAF rules
- `create_dns_and_certificates()`: Sets up DNS and certificates
- `configure_ses()`: Configures email service
- `create_and_maintain_tls_materials()`: Manages SSL certificates

### `analytics.py`

**Purpose**: Optional analytics and machine learning environment.

**Class**: `AnalyticsComponents`

**Creates**:
- SageMaker Studio domain
- EMR Serverless cluster
- VPC endpoints for analytics services
- IAM roles and policies

**Key Methods**:
- `create_serverless_analytics_environment()`: Full analytics setup

### `monitoring.py`

**Purpose**: Monitoring and alerting infrastructure.

**Class**: `MonitoringComponents`

**Creates**:
- CloudWatch alarms
- SNS topics for notifications
- Alarm configurations for ECS, ALB, and deployments

**Key Methods**:
- `create_alarms_topic()`: Creates SNS topic for alarms
- `create_ecs_service_alarms()`: ECS service alarms
- `create_alb_health_alarms()`: ALB health alarms

### `cleanup.py`

**Purpose**: Automated cleanup during stack deletion.

**Class**: `CleanupComponents`

**Features**:
- Disables RDS deletion protection
- Deactivates SES rule sets
- Deletes backup recovery points
- Ensures clean stack deletion

**Key Methods**:
- `create_cleanup_resource()`: Creates cleanup custom resource

## Module Dependencies

```
stack.py
├── network.py (no dependencies)
├── storage.py (no dependencies)
├── database.py (depends on: network)
├── compute.py (depends on: network, storage, database, security)
├── security.py (depends on: network, storage)
├── analytics.py (depends on: network, storage, database)
├── monitoring.py (no dependencies)
└── cleanup.py (no dependencies)
```

## Development Guidelines

### Adding New Modules

1. **Create module file**: `new_module.py`
2. **Define class**: `NewComponents(Construct)`
3. **Implement methods**: Clear, focused methods with docstrings
4. **Import in stack.py**: Add import and instantiation
5. **Update documentation**: Add to this README

### Module Best Practices

1. **Single Responsibility**: Each module should have one clear purpose
2. **Dependency Injection**: Pass dependencies as parameters, don't create them
3. **Documentation**: Include comprehensive docstrings
4. **Error Handling**: Validate inputs and provide clear error messages
5. **Naming**: Use descriptive, consistent naming conventions

### Code Style

- Follow PEP 8 Python style guide
- Use type hints where appropriate
- Include docstrings for all public methods
- Keep functions focused and small
- Use meaningful variable names

### Testing

Modules can be tested independently:

```python
from aws_cdk import Stack, App
from openemr_ecs.network import NetworkComponents

app = App()
stack = Stack(app, "TestStack")
network = NetworkComponents(stack, "10.0.0.0/16")
vpc = network.create_vpc()
```

## Related Documentation

- [stack.py](stack.py) - Main stack implementation
- [README.md](../README.md) - Project overview
- [ARCHITECTURE.md](../ARCHITECTURE.md) - Architecture details
- [DETAILS.md](../DETAILS.md) - Configuration details

