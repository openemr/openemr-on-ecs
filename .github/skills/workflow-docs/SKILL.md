---
name: workflow-documentation
description: Creates, updates, reviews, and maintains comprehensive documentation for GitHub Actions workflows in the OpenEMR on ECS repository. Automatically invoked when working with workflow docs, CI/CD documentation, deployment guides, or files in .github/workflows/docs/. Handles workflow analysis, timing metrics, cost analysis, troubleshooting guides, and Mermaid diagrams for GitHub Actions YAML files.
---

# Workflow Documentation Guidelines

When creating or updating GitHub Actions workflow documentation, follow these standards to ensure consistency across all workflow docs.

## When This Skill Is Invoked

This skill automatically activates when you:
- Create new workflow documentation
- Update existing workflow docs
- Review workflow documentation for completeness
- Analyze GitHub Actions workflows
- Work with files in `.github/workflows/docs/`

**Automatic Behavior:**
1. **For New Docs:** Follow all 15 required sections with real GitHub run data
2. **For Updates/Reviews:** Check existing docs against all requirements, identify missing sections, and add them
3. **Quality Standard:** All docs must meet A+ quality (use `deploy-staging.md` as reference)
4. **Batch Processing:** Process multiple docs in batches of 3, commit after each batch
5. **Real Data Only:** Always fetch actual GitHub Actions run data, never estimate
6. **Validate Mermaid Diagrams:** Invoke mermaid-compliance skill to validate/fix all diagrams

## Step 0: Project Initialization (Multi-Workflow Review Only)

**CRITICAL:** When reviewing multiple workflow docs, ALWAYS complete this initialization phase FIRST:

### 0.1 Create Complete Inventory

Before starting ANY batch work:

```bash
# Get complete list of workflow YAML files
find .github/workflows -name "*.yml" -type f | sort > /tmp/all-workflows.txt

# Count total workflows
wc -l /tmp/all-workflows.txt

# Get complete list of documentation files
find .github/workflows/docs -name "*.md" -type f | grep -v SKILL | grep -v README | sort > /tmp/all-docs.txt

# Count total docs
wc -l /tmp/all-docs.txt
```

**Record the counts:**
- Total workflow YAML files: ___
- Total documentation files: ___
- Missing documentation: ___ (workflows without docs)
- Extra documentation: ___ (docs without workflows)

### 0.2 Create Master Checklist

Create a tracking checklist BEFORE starting batches:

```markdown
## Workflow Review Checklist

- [ ] workflow-1.yml → workflow-1.md
- [ ] workflow-2.yml → workflow-2.md
- [ ] workflow-3.yml → workflow-3.md
...
- [ ] workflow-N.yml → workflow-N.md

**Progress:** 0 / N workflows reviewed
```

### 0.3 Organize Batches from Master List

Create batches by selecting from the master checklist, NOT from memory:

```markdown
**Batch 1:** (workflows 1-3)
- [ ] workflow-1.yml
- [ ] workflow-2.yml  
- [ ] workflow-3.yml

**Batch 2:** (workflows 4-6)
- [ ] workflow-4.yml
- [ ] workflow-5.yml
- [ ] workflow-6.yml
```

### 0.4 Final Verification Step

After completing all batches, VERIFY:

```bash
# Count reviewed workflows
grep -c "✅" review-checklist.md

# Compare against total
echo "Reviewed: X / Total: Y"

# Find any unchecked items
grep "^- \[ \]" review-checklist.md
```

**DO NOT declare completion until:**
- ✅ Reviewed count == Total workflow count
- ✅ No unchecked items in master checklist
- ✅ All batches accounted for in commit history

**LESSON LEARNED:** Never organize batches thematically without first having a master inventory. Always create a complete checklist first to avoid missing files.

## Step 1: Review the Workflow File

Before creating or updating documentation, ALWAYS:

1. **Read the complete workflow YAML file** (`.github/workflows/<workflow-name>.yml`)
2. **Understand all jobs, steps, inputs, and outputs** - Don't assume or guess
3. **Identify all AWS permissions required** - Check what services each step uses
4. **Note all environment variables and secrets** referenced
5. **Understand job dependencies** - Which jobs run in sequence vs parallel
6. **Check for conditional logic** - If/else branches, success/failure paths
7. **Identify safety mechanisms** - Confirmation gates, validation checks

**CRITICAL VERIFICATION REQUIREMENTS:**

When reviewing or updating existing documentation, YOU MUST verify these sections match the actual workflow YAML:

| Section | What to Verify | Where in YAML |
|---------|----------------|---------------|
| **Triggers** | Events, branches, paths | `on:` block |
| **Inputs** | Names, types, required, defaults, descriptions | `workflow_dispatch.inputs:` |
| **Jobs** | Job names, runner types, steps, outputs | Top-level `jobs:` |
| **Mermaid Diagram** | Job dependencies, conditional branches | `jobs.*.needs:` and `jobs.*.if:` |
| **Data Flow** | Job outputs, dependencies, inter-job communication | `jobs.*.needs:` and `jobs.*.outputs:` |
| **Secrets** | All secret references | `${{ secrets.* }}` in steps |
| **Variables** | All variable references | `${{ vars.* }}` in steps |
| **AWS Permissions** | All AWS CLI/SDK actions | `aws` commands and SDK calls in steps |

**If any section doesn't match the workflow YAML, UPDATE IT before marking the doc as complete.**

## Step 2: Check GitHub Workflow Run History

Before documenting metrics and timing, ALWAYS check actual workflow execution data:

1. **List recent runs:**
   ```bash
   gh run list --workflow=<workflow-name>.yml --limit 20
   ```

2. **Get detailed run information:**
   ```bash
   gh run view <run-id>
   ```

3. **Extract metrics from runs:**
   - **Average Duration** - Calculate from multiple successful runs
   - **Success Rate** - Count successful vs failed runs
   - **Last Successful Run** - Get the most recent successful run number and date
   - **First Run vs Subsequent Runs** - Note if initial runs take longer
   - **Common Failure Patterns** - Check failed runs for troubleshooting section

4. **Document actual timing data** - Don't guess! Use real numbers from workflow runs

If the workflow has never been run, state "Not run yet" in the status header and provide estimated timings based on similar workflows.

## Step 3: Required File Structure

Every workflow documentation file must include these sections in order:

1. **Title** (h1) - Workflow name
2. **Status Header Block** - Operational metadata
3. **Overview** - Purpose and use cases  
4. **Triggers** - When the workflow executes
5. **Inputs** - workflow_dispatch and workflow_call inputs
6. **Visual Workflow** - Mermaid flowchart diagram
7. **Jobs Breakdown** - Detailed job documentation
8. **Data Flow** - How data moves between jobs and external systems
9. **Verification** - Post-execution checks
10. **Troubleshooting** - Common issues and solutions (minimum 3-5 scenarios)
11. **Metrics** - Performance and timing data
12. **Cost Analysis** - GitHub Actions minutes and AWS resource costs
13. **Security Considerations** - Security controls and best practices
14. **Related Workflows** - Dependencies and connections
15. **Support & Maintenance** - Contact information

## Status Header Format

Every doc must start with this block immediately after the title:

```markdown
**Status:** ✅ OPERATIONAL  
**Workflow File:** `workflow-name.yml`  
**Category:** Deployment | Infrastructure | Validation | Monitoring  
**Last Updated:** YYYY-MM-DD  
**Last Successful Run:** #RUN_NUMBER on YYYY-MM-DD  
**Average Duration:** ~X minutes
```

Status indicators:
- ✅ OPERATIONAL - Working as expected
- ⚠️ CAUTION REQUIRED - Potentially destructive (staging)
- 🚨 CRITICAL OPERATION - Highly destructive (production)
- 🔧 DIAGNOSTIC TOOL - Debugging workflows

## Mermaid Diagram Requirements

**CRITICAL:** Every workflow documentation MUST include an accurate mermaid diagram.

**For mermaid diagram standards, validation, and fixing:** See `.github/skills/mermaid-compliance/SKILL.md`

**Quick Summary:** When working with workflow diagrams:
1. Use `mermaid-compliance` skill for diagram validation and fixes
2. Diagrams must match actual workflow YAML exactly (job names, dependencies, conditionals)
3. NEVER use `%%{init:}` directives (breaks GitHub rendering)
4. Always validate with `mermaid-diagram-validator` tool
5. Always preview with `mermaid-diagram-preview` tool

**Standard workflow diagram theme:**

```mermaid
flowchart TD
    classDef triggerNode fill:#1e3a8a,stroke:#1e40af,color:#fff
    classDef startNode fill:#1e3a8a,stroke:#1e40af,color:#fff
    classDef criticalNode fill:#991b1b,stroke:#7f1d1d,color:#fff
    classDef warningNode fill:#854d0e,stroke:#78350f,color:#fff
    classDef successNode fill:#166534,stroke:#14532d,color:#fff
    classDef failureNode fill:#7f1d1d,stroke:#450a0a,color:#fff
    classDef endNode fill:#166534,stroke:#14532d,color:#fff
```

For detailed requirements, examples, and fixing procedures, refer to the mermaid-compliance skill.

### Data Sources for Accuracy

**Use these sources for accurate documentation (NEVER guess or estimate):**

1. **Workflow timing data:**
   ```bash
   # Run timing extraction script
   .github/skills/workflow-docs/scripts/extract-workflow-metrics.sh --markdown
   
   # Or check GitHub Actions runs
   gh run list --workflow=<workflow-name>.yml --limit 10
   gh run view <run-id>
   ```

2. **Success rates:**
   ```bash
   # Check last 10 runs and calculate percentage
   gh run list --workflow=<workflow-name>.yml --json conclusion --limit 10
   ```

3. **AWS resource names:**
   ```bash
   # Verify actual AWS resources exist with exact names
   aws ecs describe-clusters --clusters openemr-cluster
   aws ecr describe-repositories --region us-east-1
   aws rds describe-db-instances --region us-east-1
   ```

4. **Timing benchmarks reference:**
   - Use `docs/workflow-timing-benchmarks.md` as source of truth
   - If file doesn't exist, generate it: `.github/skills/workflow-docs/scripts/extract-workflow-metrics.sh --markdown > docs/workflow-timing-benchmarks.md`

### Cross-Reference Validation

**When updating workflow docs, check if these files also need updates:**

- `README.md` - High-level deployment flow diagram
- `DETAILED-WORKFLOW-SEQUENCE.md` - Complete sequence diagrams
- `.github/workflows/README.md` - Workflow status table
- `docs/production-promotion-sequence-diagrams.md` - Promotion flow diagrams
- Related workflow documentation in `.github/workflows/docs/`

**Ensure consistency across all locations:**
- Same workflow should have same timing across all docs
- Approval requirements should match across all diagrams
- AWS resource names should be consistent

### Common Diagram Errors to Fix

1. **Missing jobs:** Diagram shows 3 jobs, YAML has 5 → Add missing jobs
2. **Wrong names:** Diagram says "Build", YAML says "build-and-push" → Use exact name
3. **Missing dependencies:** Diagram shows parallel, YAML has `needs:` → Add arrow
4. **Outdated flow:** Workflow changed but diagram not updated → Re-read YAML
5. **Generic labels:** Diagram says "Job1", "Job2" → Use descriptive names

**ENFORCEMENT:** When reviewing or updating workflow docs, ALWAYS check the Mermaid diagram against the actual YAML. Update the diagram before marking the doc as complete.

## Final Documentation Quality Checks

**Before completing any workflow documentation, verify ALL of the following:**

### 1. Mermaid Diagram Validation

- [ ] **Diagram validated** with `mermaid-diagram-validator` tool (no syntax errors)
- [ ] **Diagram previewed** with `mermaid-diagram-preview` tool (renders correctly)
- [ ] **Complex diagrams tested** at https://mermaid.live (for subgraphs, sequence diagrams)
- [ ] **Node styling works** - All `classDef` and `class` statements apply correctly (no `%%{init:}` directive)
- [ ] **Diagram matches YAML** - All jobs, dependencies, conditionals accurate

### 2. Data Accuracy Verification

- [ ] **Timing data sourced** from actual GitHub Actions runs (NOT estimated)
- [ ] **Success rates calculated** from last 10+ workflow runs
- [ ] **AWS resources verified** - ECS clusters, ECR repos, RDS instances exist with documented names
- [ ] **Environment specifics checked** - Account IDs, regions, domains are correct
- [ ] **Version variables validated** - LATEST_* vs DEPLOYED_* usage matches actual workflow

### 3. Cross-Reference Consistency

- [ ] **Related docs updated** - README.md, DETAILED-WORKFLOW-SEQUENCE.md, etc.
- [ ] **Timing consistent** - Same workflow has identical timing across all docs
- [ ] **Status consistent** - Workflow status matches across README.md and individual docs
- [ ] **Links work** - All markdown links point to existing files with correct paths

### 4. Metadata Updates

- [ ] **"Last Updated" date** - Changed to current date (YYYY-MM-DD format)
- [ ] **"Last Successful Run"** - Updated with recent run number and date
- [ ] **"Average Duration"** - Reflects actual timing from recent runs
- [ ] **Status indicators** - Match current operational state (✅ OPERATIONAL, etc.)

### 5. Content Completeness

- [ ] **All 15 required sections present** - Title through Support & Maintenance
- [ ] **Workflow YAML reviewed** - Documentation matches actual implementation
- [ ] **GitHub Actions run checked** - Metrics verified against real runs
- [ ] **No placeholders** - All "TBD", "TODO", "PLACEHOLDER" removed
- [ ] **No estimates** - All timing/metrics from actual data

**Quality Standard:** Use [deploy-staging.md](deploy-staging.md) as reference for A+ quality documentation.

## Job Documentation Format

For each job, include:

```markdown
### <Job Name> Job

**Purpose:** What this job does

**Runs On:** ubuntu-latest (or specific runner)

**Key Steps:**
1. **Step Name**
   - Action: `action@version` or command
   - Purpose: Why this step exists
   - Key Parameters: Important config

**AWS Permissions Required:**
- `service:Action` - Why needed
- `ecs:DescribeServices` - Verify service exists before deployment

**Environment Variables:**
- `VAR_NAME`: Description

**Outputs:**
- `output_name`: Description
```

## Inputs Table Format

```markdown
| Input Name | Type | Required | Default | Description | Example |
|------------|------|----------|---------|-------------|---------|
| `environment` | string | ✅ Yes | - | Target environment | `staging`, `production` |
| `confirm_destroy` | string | ✅ Yes | - | Type "DESTROY" to confirm | `DESTROY` |
```Data Flow Section

Document how data flows through the workflow:

```markdown
## Data Flow

1. **Input Sources:**
   - GitHub workflow inputs (workflow_dispatch, repository_dispatch)
   - GitHub Secrets Manager (secrets.*)
   - GitHub Variables (vars.*)
   - Previous job outputs

2. **Inter-Job Communication:**
   - `job-id.outputs.output-name` - Description of what's passed
   - Artifacts stored in GitHub Actions artifact storage

3. **External Integrations:**
   - AWS Parameter Store → Environment variables
   - AWS Secrets Manager → Application secrets
   - Slack API → Notifications
   - GitHub Issues API → Approval tracking

4. **Output Artifacts:**
   - CDK state →S3 backend (if applicable)
   - Container images → ECR repositories
   - Logs → CloudWatch
   - Deployment records → GitHub environment history
```

## Verification Section

Include immediate checks (< 1 min) and extended checks (5-10 min) with bash commands and expected results:

```bash
# Check workflow execution
gh run view <run-id>

# Verify resources
aws ecs describe-services --cluster <name> --services <name>
```

**Expected Results:**
- ✅ Workflow status: Completed
- ✅ Exit code: 0

## Troubleshooting Format

**IMPORTANT:** Include minimum 3-5 comprehensive troubleshooting scenarios covering:
- Common failure patterns from actual workflow runs
- Infrastructure-specific issues (AWS, CDK, ECS, RDS)
- Permission and authentication problems
- Timing and race conditions
- Resource conflicts and state issues

For each issue include:
- **Symptoms:** Observable behavior
- **Root Causes:** Common causes (1-3 specific reasons)
- **Solutions:** Step-by-step debug commands with expected output
- **Prevention:** How to avoid this issue in the future

Example format:

```markdown
### Issue 1: CDK Deployment Shows Unexpected Changes

**Symptoms:**
- CDK diff shows resource modifications
- Changes appear even when nothing was modified

**Root Causes:**
1. Manual changes made in AWS console
2. CDK context out of sync
3. Environment variables changed

**Solutions:**

1. **Check for manual changes:**
   ```bash
   aws ecs describe-services --cluster openemr-cluster --services openemr-app
   # Compare with CDK output
   ```

2. **Clear CDK context and redeploy:**
   ```bash
   rm cdk.context.json
   cdk diff
   ```

3. **Review recent AWS CloudTrail events:**
   ```bash
   aws cloudtrail lookup-events --lookup-attributes AttributeKey=ResourceType,AttributeValue=ECS
   ```

**Prevention:**
- Use SCPs to restrict manual console changes
- Enable CloudTrail monitoring
- Regular deployment validation checks
```

## Cost Analysis Section

Document the cost implications of running the workflow:

```markdown
## Cost Analysis

### GitHub Actions Minutes

- **Workflow Duration:** ~10-12 minutes average
- **Runner Type:** ubuntu-latest (Linux)
- **Cost per Run:** 10 minutes × $0.008/minute = $0.08
- **Monthly Frequency:** ~20 deployments/month
- **Monthly Cost:** 20 × $0.08 = **$1.60/month**

### AWS Resource Costs (if applicable)

**ECS Task Costs:**
- Database migrations run on Fargate
- Migration duration: ~30 seconds
- Fargate pricing: $0.04048/vCPU-hour, $0.004445/GB-hour
- Cost per migration: ~$0.001 (negligible)

**Data Transfer:**
- ECR image pulls: ~500MB per deployment
- Data transfer within region: Free
- Cost: $0.00

**Total Estimated Cost:**
- **Per Run:** ~$0.08
- **Monthly:** ~$1.60
- **Annual:** ~$19.20

### Cost Optimization Tips

1. **Skip unnecessary steps** when possible (use conditionals)
2. **Cache dependencies** to reduce build time
3. **Run non-critical tests** in parallel to reduce wall-clock time
4. **Use repository_dispatch** triggers to avoid polling
```

## Security Considerations Section

Document security controls, access restrictions, and security best practices:

```markdown
## Security Considerations

### Authentication & Authorization

**AWS Access:**
- ✅ OIDC authentication (no long-lived credentials)
- ✅ IAM role: `GitHubActionsRole` with least privilege permissions
- ✅ Session duration: 1 hour (automatic expiration)
- ✅ MFA required for assume role (if applicable)

**GitHub Secrets:**
- ✅ Secrets encrypted at rest with GitHub encryption
- ✅ Secrets never logged or exposed in workflow output
- ✅ Secret rotation: Quarterly (AWS credentials), Monthly (API tokens)
- ✅ Access restricted to: `openemr-on-ecs` repository only

**Access Control:**
- ✅ Workflow dispatch: Restricted to organization members only
- ✅ Branch protection: Requires PR approval for workflow changes
- ✅ Environment protection rules: Requires manual approval for production

### Data Protection

**Sensitive Data Handling:**
- ✅ No sensitive data in workflow inputs or logs
- ✅ Database credentials stored in AWS Secrets Manager (encrypted)
- ✅ KMS encryption for Secrets Manager
- ✅ S3 bucket encryption: AES-256 at rest, TLS 1.2+ in transit

**Logging & Audit:**
- ✅ CloudWatch logs with appropriate retention
- ✅ GitHub Actions logs retained per policy
- ✅ AWS CloudTrail: All API calls logged
- ✅ Audit trail: Workflow run number, actor, timestamp recorded

**For Deployment Workflows:**
- ✅ Blue-green deployment (zero-downtime)
- ✅ Rollback capability within 5 minutes
- ✅ Database migrations: Pre-deployment snapshot (recovery capability)
- ✅ Health checks: Verify application before marking complete

### Secrets Management

**GitHub Secrets Required:**
- `AWS_ACCOUNT_ID` - AWS account IDs
- `GH_PAT` - GitHub Personal Access Token (rotate monthly)
- `SLACK_WEBHOOK` - Slack webhook URL (if applicable)

**AWS Secrets Manager:**
- Database credentials: Rotated automatically every 30-90 days
- API keys: Rotated quarterly or on suspected compromise

**Secret Rotation Procedures:**
1. Generate new secret in AWS Secrets Manager
2. Update application to use new secret
3. Verify application health with new secret
4. Revoke old secret after 24-hour grace period
5. Document rotation in audit log

### Incident Response

**Security Incident Procedures:**
1. **Detect:** CloudWatch alarms, notifications, manual report
2. **Contain:** Cancel workflow if still running, revoke credentials if compromised
3. **Investigate:** Review CloudTrail logs, GitHub Actions logs, application logs
4. **Remediate:** Rotate secrets, patch vulnerabilities, update IAM policies
5. **Document:** Create incident report

### Security Checks

**Pre-Deployment:**
- [ ] All secrets encrypted (AWS Secrets Manager + GitHub Secrets)
- [ ] IAM roles follow least privilege principle
- [ ] CloudWatch logging enabled for all resources
- [ ] Database encrypted at rest (KMS)
- [ ] Backup retention configured

**Post-Deployment:**
- [ ] Verify encryption in transit (TLS 1.2+)
- [ ] Check CloudTrail for unauthorized access attempts
- [ ] Review security group rules (no overly permissive rules)
- [ ] Audit IAM role usage (no unused permissions)

**Regular Reviews:**
- [ ] Access control review (who has access, do they still need it?)
- [ ] Secret rotation audit (all secrets rotated per schedule?)
- [ ] Security documentation review (policies up to date?)
```

## Support & Maintenance Section

Document who owns and supports the workflow:

```markdown
## Support & Maintenance

### Ownership & Contacts

**Primary Owner:** DevOps Team  
**On-Call Escalation:** #alerts-critical (Slack)  
**Business Hours Support:** #deployments (Slack)  
**After-Hours Emergencies:** Contact on-call engineer

### Maintainers

- **Workflow Logic:** @username1, @username2
- **Infrastructure (AWS CDK):** @username3, @username4
- **Application Code:** @username5, @username6

### Documentation

- **Runbook:** [link to runbook in Confluence/GitHub]
- **Architecture Diagrams:** [link to diagrams]
- **Postmortem History:** [link to incident reports]
- **Change Log:** See [CHANGELOG.md](../CHANGELOG.md)

### Monitoring & Alerts

**CloudWatch Dashboards:**
- [Deployment Dashboard](link) - Real-time deployment metrics
- [Application Health Dashboard](link) - Service health metrics

**Alerts:**
- Deployment failures → #alerts-critical
- Performance degradation → #alerts-warning
- Cost anomalies → #finops-alerts

### Scheduled Maintenance

**Workflow Updates:** Monthly (first Tuesday)  
**Dependency Updates:** Quarterly (CDK, actions)  
**Documentation Review:** Monthly (last Friday)

### Emergency Procedures

**Rollback:** See [Production Rollback Runbook](link)  
**Incident Response:** See [Incident Response Guide](link)  
**DR Procedures:** See [Disaster Recovery Plan](link)
```

## Reference Examples

See workflow documentation files in `.github/workflows/docs/` for patterns and examples of complete workflow documentation following these standards.

## Compliance Checking Tool

A Python script is available to automatically check all workflow documentation for compliance with these standards.

### Usage

```bash
cd /path/to/openemr-on-ecs
python3 .github/skills/workflow-docs/scripts/check-workflow-docs-compliance.py
```

### Output

The script checks each workflow documentation file against all 15 required sections and reports:

- **Per-file compliance:** Shows which sections are missing from each file
- **Completion percentage:** Calculates X/15 sections for each doc
- **Summary statistics:** Total docs, fully compliant count, docs needing work

**Example output:**
```
Workflow Documentation Compliance Report
Directory: .github/workflows/docs
Checking workflow docs against SKILL.md (15 sections required)

================================================================================
✅ build-and-push.md: 15/15 (100%)
⚠️  deploy.md: 13/15 (87%)
   Missing sections:
   - Data Flow
   - Support & Maintenance

📊 Summary:
   Total workflow docs: X
   ✅ 100% complete (15/15): Y
   ⚠️  Nearly complete (12-14/15): Z
   ❌ Needs work (<12/15): 0 (0%)
```

### Integration with Review Process

**CRITICAL:** Always run the compliance checker BEFORE and AFTER batch updates:

1. **Before starting updates:**
   ```bash
   python3 .github/skills/workflow-docs/scripts/check-workflow-docs-compliance.py > compliance-before.txt
   ```
   This establishes baseline and identifies which docs need updates.

2. **After each batch:**
   ```bash
   python3 .github/skills/workflow-docs/scripts/check-workflow-docs-compliance.py
   ```
   Verify that the updated docs now show 15/15 sections.

3. **Final verification:**
   ```bash
   python3 .github/skills/workflow-docs/scripts/check-workflow-docs-compliance.py
   # Expected: "🎉 All workflow documentation is 100% compliant!"
   ```

### Compliance Report Generation

When creating a review report (like REVIEW-REPORT-YYYY-MM-DD.md), include the compliance checker output:

```markdown
## Compliance Verification

\```bash
$ python3 .github/skills/workflow-docs/scripts/check-workflow-docs-compliance.py

[Full output from compliance script]
\```

**Final Status:** X/X docs at 100% compliance
```

This provides automated validation that all documentation meets the required standards.

## Common Tasks & Expected Behavior

### Task: "Review and update all workflow docs"
**What the agent should do automatically:**
1. **Run compliance checker FIRST** to establish baseline:
   ```bash
   python3 .github/skills/workflow-docs/scripts/check-workflow-docs-compliance.py > compliance-baseline.txt
   ```
2. List all `.md` files in `.github/workflows/docs/`
3. For each doc, check for all 15 required sections (use compliance script output)
4. Identify missing sections (especially: Data Flow, Cost Analysis, Security Considerations, Troubleshooting)
4. Read corresponding workflow YAML file from `.github/workflows/<workflow-name>.yml`
5. **VERIFY YAML-DEPENDENT SECTIONS MATCH ACTUAL WORKFLOW (CRITICAL):**
   
   **Section 4 - Triggers:**
   - Compare documented triggers to actual `on:` block in YAML
   - Verify `workflow_dispatch`, `push`, `pull_request`, `schedule`, `repository_dispatch` events
   - Check branch filters (`branches:`, `branches-ignore:`)
   - Update if triggers don't match YAML
   
   **Section 5 - Inputs:**
   - Compare documented inputs to actual `workflow_dispatch.inputs:` in YAML
   - Verify input names, types, required status, default values, descriptions
   - Check for missing inputs or incorrect metadata
   - Update if inputs don't match YAML
   
   **Section 6 - Visual Workflow (Mermaid diagram):**
   - Compare diagram jobs to YAML jobs (exact job IDs)
   - Check job dependencies match `needs:` clauses
   - Verify conditional branches match `if:` clauses
   - Update diagram if mismatched (see Mermaid Diagram Requirements above)
   
   **Section 7 - Jobs Breakdown:**
   - Compare documented jobs to actual jobs in YAML
   - Verify job names, runner types (`runs-on:`), steps match YAML
   - Check job outputs (`outputs:`) are documented
   - Verify AWS permissions listed match actual AWS CLI/SDK calls in steps
   - Update if jobs don't match YAML
   
   **Section 8 - Data Flow:**
   - Verify job dependencies reflect actual `needs:` clauses
   - Check documented outputs match `outputs:` declarations in YAML
   - Verify secrets/vars references match actual `secrets.*` and `vars.*` in YAML
   - Update if data flow doesn't reflect actual workflow structure
   
   **Secrets & Variables (within Jobs section):**
   - List ALL `secrets.*` references from YAML (check all steps)
   - List ALL `vars.*` references from YAML (check all steps)
   - Verify no secrets/vars are missing or incorrectly documented
   - Update if secrets/vars don't match YAML

6. Fetch real GitHub run data with `gh run list`
7. Add missing sections with real data
8. **Update sections that don't match workflow YAML** (Triggers, Inputs, Jobs, Data Flow, Mermaid)
9. Process in batches of 3, commit after each batch
10. Report completion summary

### Task: "Create docs for new-workflow.yml"
**What the agent should do automatically:**
1. Read `.github/workflows/new-workflow.yml` completely
2. **Extract actual workflow structure from YAML:**
   - Triggers: Exact `on:` block (events, branches, paths)
   - Inputs: All `workflow_dispatch.inputs:` with types, defaults, descriptions
   - Jobs: All job names, `runs-on:`, `needs:`, `if:` conditions
   - Secrets: All `secrets.*` references across all steps
   - Variables: All `vars.*` references across all steps
   - Outputs: All job `outputs:` declarations
   - AWS permissions: All AWS CLI/SDK actions called in steps
3. Fetch GitHub run history for timing data (`gh run list --workflow=...`)
4. Create doc with all 15 sections using ONLY data from YAML (no assumptions)
5. **Create accurate Mermaid diagram:**
   - Use exact job IDs from YAML
   - Show all `needs:` dependencies with arrows
   - Include `if:` conditional branches
   - Apply correct node classes (see Mermaid Diagram Requirements)
6. **Document triggers exactly as in YAML** (`on:` block)
7. **Document inputs exactly as in YAML** (`workflow_dispatch.inputs:`)
8. **Document all jobs with actual steps from YAML**
9. **Document data flow based on actual `needs:` and `outputs:` in YAML**
10. Calculate GitHub Actions costs based on runner types and estimated duration
11. Include minimum 3-5 troubleshooting scenarios
12. Document security controls and best practices
13. Commit with descriptive message

### Task: "Is workflow-name.md complete?"
**What the agent should do automatically:**
1. Read the doc and the workflow YAML
2. Check against all 15 required sections
3. Verify real GitHub run data (not estimates)
4. Check troubleshooting has 3+ scenarios
5. Verify Cost Analysis, Security Considerations, and Data Flow sections exist
6. Report what's missing or confirm completeness

## Writing Style

- Professional and technical tone
- Action-oriented (use imperatives: "Verify...", "Check...")
- Use **bold** for critical information
- Use `code formatting` for commands, file paths, variables
- Use ✅ ❌ ⚠️ emojis for status indicators
- Always include comments in bash code blocks explaining complex commands

## What to Avoid

- Skipping the Mermaid diagram
- Forgetting the status header block
- Using inconsistent section ordering
- Omitting verification steps or troubleshooting
- **Having fewer than 3 troubleshooting scenarios** - Include real issues from workflow runs
- **Skipping cost analysis** - Every workflow consumes GitHub Actions minutes
- **Skipping security considerations** - Every workflow handles sensitive data or credentials
- **Not documenting data flow** - Teams need to understand how data moves
- **CRITICAL: Documenting outdated information that doesn't match workflow YAML:**
  - Triggers that don't match actual `on:` block
  - Inputs that don't match `workflow_dispatch.inputs:`
  - Jobs that don't match actual job names/structure
  - Mermaid diagrams that don't match job dependencies
  - Secrets/vars that don't match actual references in YAML
  - Data flow that doesn't reflect actual `needs:` clauses
- Using vague descriptions
- Forgetting to document AWS permissions
- Leaving metrics section empty
- Using estimated timing instead of real GitHub run data
