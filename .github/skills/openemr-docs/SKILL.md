---
name: openemr-docs
description: Manage documentation in the OpenEMR on ECS repository. Use when creating new documentation, updating existing docs, maintaining doc structure, generating Mermaid diagrams, validating documentation quality, or organizing the docs/ folder tree. Enforces consistent structure, formatting standards, and best practices for technical documentation including runbooks, architecture docs, troubleshooting guides, and integration guides.
---

# OpenEMR on ECS Documentation Management

## Overview

This skill enables creation, updating, and maintenance of high-quality technical documentation in the OpenEMR on ECS repository. It provides templates, validation tools, and standards enforcement to ensure consistent, professional documentation.

## Core Workflow

### Step 0: Systematic Directory Review (MANDATORY FOR MULTIPLE FILES)

**⚠️ CRITICAL REQUIREMENT:** When reviewing multiple files in a directory, you MUST review EACH file with the same level of rigor. This is NOT a suggestion - it is mandatory.

**DO NOT:**
- ❌ Update first files comprehensively and later files with only date updates
- ❌ Apply thorough verification to some files and skip verification for others
- ❌ Assume later files don't need the same scrutiny as earlier files
- ❌ Create a two-tier review process

**DO:**
- ✅ List all files in the directory being reviewed
- ✅ Create a review checklist before starting
- ✅ Apply Step 1 verification procedures to EVERY file
- ✅ Document verification findings for each file
- ✅ Commit changes for each file (or group if related)
- ✅ Provide user with summary of findings from ALL files

**Example Systematic Approach:**
```
📋 Files to Review in docs/environment/:
  1. ENVIRONMENT-VARIABLES-TERRAFORM-ANALYSIS.md
  2. ENV-TO-GITHUB-CONFIG-MAPPING.md
  3. ENVIRONMENT-CONFIGURATION-AUDIT.md
  4. AWS-ENVIRONMENT-VARIABLES-3-LAYER-ARCHITECTURE.md
  5. RUNTIME-CONFIGURATION.md

✅ Verification Checklist for Each:
  □ Ground truth age (when last verified against source code)
  □ Regional configuration accuracy (CDK context/config)
  □ All table/list entries verified across CDK sources
  □ No stale references to old values
  □ Code examples reflect actual current configuration
  □ Audit results align with deployed state
  □ Cross-references to other docs are correct

📝 Review Results:
  1. FILE_NAME → Issue found: [describe], Fixed: [describe]
  2. FILE_NAME → Issue found: [describe], Fixed: [describe]
  ... (complete for all files)
```

---

### Step 1: Verify Ground Truth (CRITICAL - MANDATORY VERIFICATION)

**⚠️ CRITICAL REQUIREMENT:** BEFORE creating or updating ANY documentation, ALWAYS verify against actual deployed code and infrastructure. This step is NOT optional - documentation must reflect reality, not assumptions.

**DO NOT skip this step. DO NOT assume documentation is still accurate. DO NOT update without verifying truth sources.**

The following are mandatory verification procedures based on documentation type:

---

#### A. Environment Variables & CDK Configuration Documentation

**MANDATORY VERIFICATION SOURCES:**

1. **CDK Code (openemr-on-ecs repository)**
   ```bash
   # Navigate to openemr-on-ecs repo
   cd /path/to/openemr-on-ecs
   
   # Check CDK stack definitions
   grep -r "environment\|secrets" openemr_ecs/*.py
   
   # Check ECS task definition environment configuration
   cat openemr_ecs/compute.py
   cat openemr_ecs/database.py
   
   # Check constants and configuration
   cat openemr_ecs/constants.py
   ```
   
   **What to verify:**
   - ✅ Variable names, types, defaults in CDK stacks
   - ✅ Values for different environments
   - ✅ How variables flow through CDK constructs
   - ✅ Which variables are secrets vs plain environment variables
   - ✅ How computed values are generated
   - ❌ Do NOT trust old documentation - read actual code

2. **GitHub Actions Workflows & Variables**
   ```bash
   cd /path/to/openemr-on-ecs
   
   # List GitHub environment variables (if configured)
   gh variable list --repo owner/openemr-on-ecs
   
   # Check workflow files for environment variable usage
   grep -r "env:" .github/workflows/*.yml
   
   # Verify GitHub secrets are set (names only, not values)
   gh secret list --repo owner/openemr-on-ecs
   ```
   
   **What to verify:**
   - ✅ Which variables come from GitHub
   - ✅ Which come from CDK code
   - ✅ Environment-specific overrides
   - ✅ Sensitive vs non-sensitive configurations
   - ❌ Do NOT assume GitHub secrets are set correctly

3. **AWS Secrets Manager (Deployed Truth)**
   ```bash
   # Set AWS credentials for target environment
   export AWS_PROFILE=openemr-profile
   
   # List secrets
   aws secretsmanager list-secrets --region us-east-1 \
     --query 'SecretList[?contains(Name, `openemr`)].Name' --output text
   
   # Get actual secret content (keys only)
   aws secretsmanager get-secret-value \
     --secret-id openemr-db-secret \
     --region us-east-1 \
     --query 'SecretString' --output text | jq 'keys'
   ```
   
   **What to verify:**
   - ✅ Actual values currently in deployed Secrets Manager
   - ✅ Which specific environment variables are stored
   - ✅ When secrets were last updated
   - ❌ Do NOT assume CDK values match deployed secrets

4. **Running Container Environment Variables**
   ```bash
   # Get running task ARN
   aws ecs list-tasks \
     --cluster openemr-cluster \
     --region us-east-1 \
     --query 'taskArns[0]' --output text
   
   # Get task details (includes environment from task definition)
   aws ecs describe-tasks \
     --cluster openemr-cluster \
     --tasks arn:aws:ecs:us-east-1:ACCOUNT_ID:task/openemr-cluster/xxx \
     --region us-east-1 \
     --query 'tasks[0].containers[0].environment' --output json
   
   # Get configured secrets references
   aws ecs describe-tasks \
     --cluster openemr-cluster \
     --tasks arn:aws:ecs:us-east-1:ACCOUNT_ID:task/openemr-cluster/xxx \
     --region us-east-1 \
     --query 'tasks[0].containers[0].secrets' --output json
   ```
   
   **What to verify:**
   - ✅ Currently running values (actual truth today)
   - ✅ Next revision values (after next deployment)
   - ✅ Secret ARN references and extraction syntax
   - ✅ When last updated (compare task definition revisions)
   - ❌ Do NOT assume containers have latest values

5. **AWS Resources Status**
   ```bash
   # Check RDS instance configuration
   aws rds describe-db-instances \
     --region us-east-1 \
     --query 'DBInstances[?contains(DBInstanceIdentifier, `openemr`)]
   
   # Check S3 buckets
   aws s3api list-buckets --query 'Buckets[?contains(Name, `openemr`)].Name'
   
   # Check ElastiCache cluster
   aws elasticache describe-cache-clusters \
     --region us-east-1 \
     --query 'CacheClusters[?contains(CacheClusterId, `openemr`)]'
   ```
   
   **What to verify:**
   - ✅ RDS endpoints match documentation (MUST check for changes)
   - ✅ S3 bucket names are correct
   - ✅ ElastiCache endpoint and configuration
   - ✅ Database names, ports, regions
   - ❌ Do NOT use old ARNs without verifying current ones

---

#### B. Deployment/Workflow Documentation

**MANDATORY VERIFICATION SOURCES:**

```bash
cd /path/to/openemr-on-ecs

# Check actual workflow steps
cat .github/workflows/deploy.yml

# Review workflow run history
gh run list --repo owner/openemr-on-ecs \
  --workflow deploy.yml -L 5

# Check actual workflow run logs
gh run view --repo owner/openemr-on-ecs \
  <run-id> --log
```

**What to verify:**
- ✅ Exact workflow steps (copy from YAML)
- ✅ Environment variables actually passed
- ✅ Recent failures/successes
- ✅ Actual deployment times and resource changes
- ✅ Secrets used (by name only)
- ❌ Do NOT document theoretical workflow

---

#### C. Architecture/Infrastructure Documentation

**MANDATORY VERIFICATION SOURCES:**

```bash
# List all deployed resources
aws ec2 describe-vpcs --region us-east-1 \
  --query 'Vpcs[?Tags[?Key==`Project` && Value==`OpenEMR`]]' --output json

aws ecs describe-clusters --clusters openemr-cluster \
  --region us-east-1

aws ecs list-container-instances \
  --cluster openemr-cluster --region us-east-1

# Verify security group rules
aws ec2 describe-security-groups \
  --region us-east-1 \
  --filters Name=group-name,Values=openemr-* \
  --query 'SecurityGroups[].{Name:GroupName,Rules:IpPermissions}'

# Check load balancer configuration
aws elbv2 describe-load-balancers \
  --region us-east-1 \
  --query 'LoadBalancers[?contains(LoadBalancerName, `openemr`)]'
```

**What to verify:**
- ✅ VPC configuration and subnets
- ✅ ECS cluster, services, and task counts
- ✅ Security group rules (ingress/egress)
- ✅ Load balancer targets and health
- ✅ Actual integrations visible in deployed resources
- ❌ Do NOT draw diagrams without verifying deployed resources

---

#### Reference Validation Checks

**MANDATORY FOR ALL DOCUMENTATION:**

1. **Run reference validator** to ensure README is accurate:
   ```bash
   python .github/skills/openemr-docs/scripts/validate_references.py . docs
   ```
   
2. **Fix any errors:**
   - Files referenced but missing → Update README or find archived alternative
   - Archived files in active sections → Move to archive or restore to active
   - Broken links → Correct paths or remove references
   
3. **Review warnings:**
   - Orphaned files → Add to README or archive
   - Files in wrong sections → Move to correct folder

**Code-First Documentation Principle:**
- ❌ Do NOT trust old documentation
- ✅ Deployed code and infrastructure = Source of Truth (TODAY)
- ✅ Documentation reflects current reality, not plans or history
- ✅ When in doubt, check the actual implementation
- ✅ Update docs AFTER deployment or code changes, never before
- ✅ Flag discrepancies between code and docs as BLOCKING
- ✅ **Always keep README.md in sync** with actual file locations
- ⚠️ **VERIFY EVERY CLAIM** - Don't assume previous documentation is accurate

---

### Step 1: Understand the Request

Determine what type of documentation task:
- **Creating new documentation** - Choose appropriate template
- **Updating existing documentation** - Read current doc, understand standards
- **Generating diagrams** - Use Mermaid guidelines
- **Validating documentation** - Run validation tools
- **Organizing docs** - Review structure and index

### Step 2: Select Document Type

For new documentation, choose the appropriate type:
- **Architecture Document** - System design and technical decisions
- **Operational Runbook** - Step-by-step procedures
- **Troubleshooting Guide** - Problem diagnosis and resolution
- **Integration Guide** - Service integration instructions

See [references/document-types.md](references/document-types.md) for detailed guidance.

### Step 3: Apply Standards

All documentation must follow:
- **Structure standards** - Single H1, TOC for >3 sections, clear headers
- **Quality standards** - Clear writing, code examples, proper links
- **Mermaid standards** - No init directives, consistent styling, validated diagrams

See [references/documentation-standards.md](references/documentation-standards.md) for complete standards.

### Step 4: Create or Update

**For new documentation:**
1. **FIRST: Review actual code/infrastructure** (Step 0 requirements)
2. Copy appropriate template from `assets/templates/`
3. Fill in all sections with **information from actual deployed resources**
4. Replace all placeholder text with **real values from code/AWS**
5. Add relevant diagrams using Mermaid (based on actual architecture)
6. Include actual resource names, ARNs, and configurations
7. Validate links and formatting

**For updates:**
1. **FIRST: Review actual code/infrastructure** to identify what changed
2. Read existing document to understand current state
3. Compare documentation against deployed reality
4. Apply changes to reflect actual deployed state
5. Update "Last Updated" metadata
6. Preserve existing structure unless restructuring is needed
7. Ensure changes align with standards AND deployed code

**Verification Checklist by Documentation Type:**

**Deployment Documentation:**
- [ ] Reviewed `.github/workflows/` files
- [ ] Verified workflow steps match documented steps
- [ ] Confirmed environment variables match workflow
- [ ] Tested deployment commands against actual environment
- [ ] Documented actual resource identifiers (ECR repos, ECS services, etc.)

**Infrastructure Documentation:**
- [ ] Reviewed CDK stacks in openemr_ecs/
- [ ] Verified resource configurations match CDK code
- [ ] Checked CloudFormation stacks for deployed resources
- [ ] Documented actual AWS resource names and ARNs
- [ ] Verified network architecture matches VPC configuration

**Architecture Documentation:**
- [ ] Inspected deployed AWS resources (ECS, RDS, ElastiCache)
- [ ] Verified all components in diagrams actually exist
- [ ] Documented actual integrations and data flows
- [ ] Confirmed security group rules and IAM policies
- [ ] Validated against AWS Console or CLI output

**Operational Documentation:**
- [ ] Tested all commands against actual environments
- [ ] Verified resource identifiers are current
- [ ] Confirmed procedures work with deployed infrastructure
- [ ] Included actual examples from recent operations
- [ ] Validated troubleshooting steps resolve real issues

### Step 5: Validate

Before finalizing:

**1. Validate Documentation Files:**
```bash
python scripts/validate_docs.py /path/to/repo docs
```

Checks:
- Markdown syntax
- Internal links validity
- Mermaid diagram syntax
- Header structure

**2. NEW: Validate Documentation References:**
```bash
python scripts/validate_references.py /path/to/repo docs
```

Checks for:
- ❌ **Files referenced in README but don't exist** → Update README or restore files
- ❌ **Archived files listed in active sections** → Move to archive or move back to active
- ❌ **Dead links and broken anchors** → Fix paths or remove references
- ⚠️ **Files that exist but aren't documented** → Add to README or archive
- ⚠️ **Files in wrong folders** → Move to correct section

**Example Output:**
```
❌ ERRORS (2):
1. Archived file 'OLD-DOCUMENT.md' appears to be listed in active sections
   Line: 15
2. Referenced file 'missing-file.md' does not exist

⚠️ WARNINGS:
1. File 'operations/SOME-DOCUMENT.md' exists but is not referenced
```

**3. Mermaid Validation:**
1. Use **mermaid-diagram-validator** tool to validate diagrams
2. Use **mermaid-diagram-preview** to preview  
3. Check for common syntax errors (unclosed brackets, missing IDs)

**4. Manual Checks:**
- Single H1 header
- Table of contents for longer docs
- All internal links valid
- Code blocks have language tags
- Mermaid diagrams validated
- Metadata included (dates, versions)
- No references to archived/missing files
- All files in docs/ folders are listed in README

### Step 6: Ensure All Files Are Linked

**CRITICAL:** All documented files must be clickable links, not plain text.

**1. Check for plain text file references:**
```bash
python scripts/linkify_docs.py /path/to/repo docs
```

**2. Create links for all file references:**
```bash
python scripts/linkify_docs.py /path/to/repo docs --update
```

This converts:
```markdown
# Before
- **MY-DOCUMENT.md** - Description

# After  
- [**MY-DOCUMENT.md**](folder/MY-DOCUMENT.md) - Description
```

**3. Verify all links work:**
- Click through each link in GitHub preview
- Verify relative paths resolve correctly
- Test from both desktop and mobile

**When links are important:**
- ✅ Users can navigate docs directly from README
- ✅ Improves discoverability and UX
- ✅ Search engines can crawl documentation
- ✅ Better GitHub integration (preview on hover)

### Step 7: Update Index

If creating new documentation in docs/ folder:
1. Update `docs/README.md` to include new document
2. Add to appropriate category section
3. Provide brief description
4. Update "By Topic" navigation if applicable

---

## Document Templates

Use these templates for consistent documentation:

### Architecture Document
**Location**: `assets/templates/architecture-doc-template.md`

**When to use:**
- Documenting AWS infrastructure
- Explaining system design
- Recording architectural decisions
- Planning major changes

**Key sections:**
- Architecture diagrams
- Component descriptions
- Integration points
- Security considerations
- Trade-offs and decisions

---

### Operational Runbook
**Location**: `assets/templates/runbook-template.md`

**When to use:**
- Deployment procedures
- Disaster recovery processes
- Maintenance tasks
- Operational procedures

**Key sections:**
- Prerequisites
- Step-by-step procedures with commands
- Validation steps
- Rollback procedures
- Troubleshooting

---

### Troubleshooting Guide
**Location**: `assets/templates/troubleshooting-guide-template.md`

**When to use:**
- Documenting known issues
- Creating debugging procedures
- Recording incident learnings
- Building knowledge base

**Key sections:**
- Quick diagnostics
- Common issues with symptoms/resolution
- Component-specific issues
- Root cause analysis
- Prevention measures

---

### Integration Guide
**Location**: `assets/templates/integration-guide-template.md`

**When to use:**
- AWS service integrations
- Third-party service setup
- API integration documentation

**Key sections:**
- Setup and configuration
- Authentication/authorization
- API reference
- Code examples
- Error handling
- Monitoring

---

## Mermaid Diagram Guidelines

When creating diagrams:

1. **Choose appropriate diagram type:**
   - **Flowchart** - Workflows, decision trees, processes
   - **Sequence** - System interactions, API calls
   - **Architecture (graph)** - AWS infrastructure, components
   - **State** - Status transitions, workflows

2. **Follow standards:**
   - ❌ **Never use init directives** (`%%{init:...}%%`)
   - ✅ Use standard themes
   - ✅ Keep diagrams focused (<20 nodes)
   - ✅ Use consistent colors and styling

3. **Always validate:**
   - Use **mermaid-diagram-validator** tool before committing
   - Preview with **mermaid-diagram-preview** tool
   - Include text description for accessibility

See [references/mermaid-guidelines.md](references/mermaid-guidelines.md) for detailed examples and standards.

---

## Validation Tools

### Documentation Validator
**Script**: `scripts/validate_docs.py`

Checks markdown syntax, internal links, Mermaid diagrams, and header structure.

**Usage:**
```bash
python scripts/validate_docs.py /path/to/repo [docs_path]
```

---

### Reference Validator (NEW)
**Script**: `scripts/validate_references.py`

Ensures documentation references are consistent and accurate:
- Files referenced in README actually exist
- No archived files listed in active sections
- All docs in folders are documented in README
- No dead links or broken anchors
- Detects files moved to archive that still appear as active

**Usage:**
```bash
python scripts/validate_references.py /path/to/repo [docs_path]
```

**Example:** Catches issues like FRONTEND-S3-CLOUDFRONT-MIGRATION-PLAN.md being archived but still listed as active in README Architecture section.

**When to use:**
- After updating README.md file listings
- When archiving or moving documentation files
- Before committing documentation changes
- Quarterly during documentation reviews

---

### Documentation Link Generator
**Script**: `scripts/linkify_docs.py`

Automatically creates markdown links for all file references in README.

**Validates:**
- All documentation items are linked (not plain text)
- Proper markdown link syntax: `[text](path)`
- Relative paths resolve correctly

**Generates:**
- Clickable documentation items
- Proper folder-relative links
- GitHub preview tooltips

**Usage:**
```bash
# Preview changes without applying
python scripts/linkify_docs.py /path/to/repo [docs_path]

# Apply changes
python scripts/linkify_docs.py /path/to/repo [docs_path] --update
```

**When to use:**
- After updating README.md file listings  
- When adding new documentation files
- Before committing documentation changes
- To audit link completeness
**Script**: `scripts/generate_toc.py`

Generates or updates table of contents in markdown files.

**Usage:**
```bash
python scripts/generate_toc.py <markdown_file> [--update] [--start-level N]
```

**Example:**
```bash
python scripts/generate_toc.py docs/operations/runbook.md --update
```

---

## Documentation Standards Reference

For comprehensive standards, review these references:

### Structure and Quality
**Reference**: [references/documentation-standards.md](references/documentation-standards.md)

Covers:
- Document structure requirements
- Content quality standards
- Metadata standards
- File organization
- Cross-referencing
- Maintenance guidelines

### Diagram Standards
**Reference**: [references/mermaid-guidelines.md](references/mermaid-guidelines.md)

Covers:
- When to use each diagram type
- Styling standards
- Size and complexity limits
- Validation requirements
- Accessibility guidelines
- Examples by use case

### Document Types
**Reference**: [references/document-types.md](references/document-types. md)

Covers:
- Detailed description of each document type
- When to use each type
- Required sections for each type
- Examples from repositories
- Organization strategies
- Quality checklists

---

## Common Documentation Tasks

### Task 1: Create New Deployment Runbook

1. Copy `assets/templates/runbook-template.md`
2. Fill in operation name, prerequisites, procedures
3. Add validation and rollback steps
4. Include troubleshooting section
5. Validate with `validate_docs.py`
6. Update `docs/README.md` index

---

### Task 2: Document AWS Service Integration

1. Copy `assets/templates/integration-guide-template.md`
2. Document prerequisites and setup steps
3. Include authentication configuration
4. Add code examples in Python/Node.js
5. Document error handling
6. Add monitoring section
7. Validate and update index

---

### Task 3: Create Architecture Diagram

1. Choose appropriate diagram type (flowchart, sequence, architecture)
2. Follow Mermaid guidelines (no init directives)
3. Keep focused (<20 nodes)
4. Use consistent styling
5. Validate with **mermaid-diagram-validator**
6. Preview with **mermaid-diagram-preview**
7. Add text description
8. Embed in documentation

---

### Task 4: Update Existing Documentation

1. Read current document completely
2. Identify sections needing updates
3. Maintain existing structure and style
4. Update metadata (Last Updated date)
5. Validate all links still work
6. Run validation tools
7. Commit with descriptive message

---

## Repository-Specific Conventions

### openemr-on-ecs

**Documentation Structure:**
```
docs/
├── README.md (comprehensive index)
├── architecture/     # Design specs
├── deployment/      # Deployment guides
├── operations/       # Runbooks, procedures
├── troubleshooting/  # Issue resolution
└── aws/              # AWS configuration
```

**Common Document Types:**
- CDK deployment guides
- AWS service configuration
- Operational runbooks
- Troubleshooting guides
- Architecture diagrams

**Ground Truth Sources for this Repository:**
- **CDK Stacks**: `openemr_ecs/*.py` files (stack.py, compute.py, database.py, etc.)
- **AWS Resources**: ECS clusters/services/tasks, RDS instances, ElastiCache, S3 buckets, ALBs
- **Configuration**: `cdk.json`, constants.py, environment variables
- **Scripts**: `scripts/` directory (deployment, backup, restore scripts)
- **Workflow files**: `.github/workflows/` (if present)

---

## File-Specific Review Checklists

When reviewing documentation files, use appropriate checklists based on document type:

### Deployment Documentation
- [ ] Verify ALL steps against actual CDK code
- [ ] Check AWS resource names match deployed resources
- [ ] Confirm CDK stack references are accurate
- [ ] Verify command examples work with current setup
- [ ] Validate troubleshooting steps

### Architecture Documentation
- [ ] Verify diagram accuracy against deployed AWS resources
- [ ] Check all components actually exist
- [ ] Confirm security group rules and IAM policies
- [ ] Validate network architecture matches VPC configuration

### Operational Documentation
- [ ] Test all commands against actual environments
- [ ] Verify resource identifiers are current
- [ ] Confirm procedures work with deployed infrastructure
- [ ] Include actual examples from recent operations

---

## Quality Checklist

Before finalizing any documentation:

- [ ] **Systematic Review Complete**
  - [ ] ALL files in directory reviewed with equal rigor
  - [ ] No file received only date-update treatment
  - [ ] Verification findings documented for each file
  - [ ] Issues found and fixed in all files

- [ ] **Ground Truth Verification (CRITICAL)**
  - [ ] Reviewed actual code in relevant repositories
  - [ ] Verified against deployed AWS infrastructure
  - [ ] Checked GitHub workflows (if deployment docs)
  - [ ] Inspected CDK stacks (if infrastructure docs)
  - [ ] Confirmed resource names/ARNs match deployed state
  - [ ] Tested commands/procedures against actual environments
  - [ ] Documentation reflects DEPLOYED reality, not plans
  - [ ] Regional configuration verified (CRITICAL)
  - [ ] **Run reference validator:** `python scripts/validate_references.py . docs`
  - [ ] **No errors in validation output** (warnings can be investigated but errors block)
  - [ ] **Same filename not in both active and archive sections** ⚠️ Common issue

- [ ] **Structure**
  - [ ] Single H1 header
  - [ ] Table of contents (if >3 sections)
  - [ ] Clear section headers
  - [ ] Logical organization

- [ ] **Content**
  - [ ] Clear and concise writing
  - [ ] Code examples with language tags
  - [ ] All placeholders replaced with ACTUAL values
  - [ ] Complete information provided from real sources

- [ ] **Links and Navigation**
  - [ ] All internal links valid and working
  - [ ] External links functional
  - [ ] Cross-references added
  - [ ] Related docs linked
  - [ ] **Every documented file is a clickable link**
  - [ ] No archived files listed in active sections
  - [ ] All referenced files exist at stated paths
  - [ ] Run link generator: `python scripts/linkify_docs.py . docs`
  - [ ] Run reference validator: `python scripts/validate_references.py . docs`

- [ ] **Diagrams**
  - [ ] Mermaid diagrams validated
  - [ ] No init directives
  - [ ] Text descriptions provided
  - [ ] Diagrams previewed

- [ ] **Metadata**
  - [ ] Last updated date
  - [ ] Document type specified
  - [ ] Owner/maintainer listed
  - [ ] Status indicated

- [ ] **Index**
  - [ ] docs/README.md updated
  - [ ] Document added to appropriate category
  - [ ] Brief description provided
