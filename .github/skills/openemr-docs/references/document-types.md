# Document Types and Templates

Guide to different documentation types in the OpenEMR on ECS repository and when to use each.

## Document Type Selection

### Architecture Documents

**Purpose**: Describe system design, components, and technical decisions

**When to use:**
- Documenting AWS infrastructure layout
- Explaining service integrations
- Recording architectural decisions (ADRs)
- Planning major system changes

**Required sections:**
- Overview
- Architecture diagram
- Component descriptions
- Integration points
- Trade-offs and decisions
- Security considerations
- Future considerations

**Template**: See `assets/templates/architecture-doc-template.md`

**Examples:**
- `docs/architecture/ARCHITECTURE.md`
- `docs/deployment/DEPLOYMENT-PLAN.md`

### Operational Runbooks

**Purpose**: Step-by-step procedures for common operational tasks

**When to use:**
- Deployment procedures
- Disaster recovery processes
- Scaling operations
- Security incident response
- Maintenance tasks

**Required sections:**
- Prerequisites
- Step-by-step procedures
- Validation steps
- Rollback procedures
- Expected outcomes
- Troubleshooting

**Template**: See `assets/templates/runbook-template.md`

**Examples:**
- `docs/operations/disaster-recovery-runbook.md`
- `docs/operations/backup-restore-procedures.md`

### Troubleshooting Guides

**Purpose**: Help diagnose and resolve common issues

**When to use:**
- Documenting known issues and solutions
- Creating debugging procedures
- Recording incident learnings
- Building knowledge base

**Required sections:**
- Symptoms/Problem description
- Possible causes
- Diagnostic steps
- Resolution procedures
- Prevention measures
- Related issues

**Template**: See `assets/templates/troubleshooting-guide-template.md`

**Examples:**
- `docs/troubleshooting/troubleshooting-guide.md`
- `docs/troubleshooting/common-issues.md`

### Integration Guides

**Purpose**: Explain how to integrate with services or systems

**When to use:**
- Setting up AWS service integrations
- Configuring third-party services
- Connecting systems
- API integration documentation

**Required sections:**
- Service overview
- Prerequisites
- Configuration steps
- Authentication/authorization
- API reference or examples
- Testing and validation
- Error handling
- Maintenance

**Template**: See `assets/templates/integration-guide-template.md`

**Examples:**
- `docs/aws/RDS-SECRETS-MANAGER-INTEGRATION.md`
- `docs/aws/S3-INTEGRATION.md`

### Configuration Reference

**Purpose**: Document configuration options and variables

**When to use:**
- Documenting Terraform variables
- Environment variable reference
- Configuration file formats
- Setting options and parameters

**Required sections:**
- Overview
- Configuration approach
- Parameter reference (tables)
- Examples
- Best practices
- Troubleshooting

**Examples:**
- `docs/deployment/CONFIGURATION-REFERENCE.md`
- `docs/deployment/ENVIRONMENT-VARIABLES.md`

### How-To Guides

**Purpose**: Task-oriented instructions for specific goals

**When to use:**
- Teaching specific tasks
- Onboarding developers
- Setup procedures
- Feature usage guides

**Required sections:**
- Goal/objective
- Prerequisites
- Step-by-step instructions
- Verification steps
- Next steps

**Examples:**
- `docs/deployment/GETTING-STARTED.md`
- `CONTRIBUTING.md`

### Analysis Documents

**Purpose**: Provide research, analysis, and recommendations

**When to use:**
- Cost analysis reports
- Compliance assessments
- Technical investigations
- Gap analyses
- Performance benchmarks

**Required sections:**
- Executive summary
- Methodology
- Findings/analysis
- Metrics or data
- Recommendations
- Next steps

**Examples:**
- `docs/architecture/SECURITY-ANALYSIS.md`
- `docs/operations/COST-ANALYSIS.md`

### Status and Planning Documents

**Purpose**: Track project progress and future plans

**When to use:**
- Implementation roadmaps
- Project status tracking
- Feature planning
- Migration plans

**Required sections:**
- Current status
- Objectives
- Timeline/roadmap
- Progress tracking
- Blockers/risks
- Next steps

**Examples:**
- `IMPLEMENTATION-STATUS.md`
- `IMPLEMENTATION-PLAN-2025-10-31.md`

### Index/README Documents

**Purpose**: Navigate documentation and provide overview

**When to use:**
- Repository root README
- Documentation folder index
- Category/folder overviews

**Required sections:**
- Overview
- Structure description
- Document listings (organized by category)
- Quick navigation (by topic, status)
- Maintenance info

**Examples:**
- `docs/README.md`
- Root `README.md`

## Document Organization Strategies

### By Category

Group related documents in folders:

```
docs/
├── architecture/       # System design
├── deployment/        # Deployment guides
├── operations/        # Runbooks, procedures
├── aws/               # AWS setup
└── troubleshooting/   # Issue resolution
```

### By Lifecycle

Organize by document lifecycle:

```
docs/
├── planning/          # Future plans
├── active/            # Current docs
└── archive/           # Historical
```

### Hybrid Approach

Combine category and lifecycle:

```
docs/
├── README.md
├── architecture/
│   └── current/
│   └── proposed/
├── operations/
└── archive/
    └── 2025/
```

## Naming Conventions

### Pattern

```
[category-]descriptive-name[-version].md
```

**Examples:**
- `deployment-runbook.md`
- `rds-secrets-integration.md`
- `api-guide-v2.md`
- `cost-analysis-2025-12-12.md`

### Rules

- Lowercase with hyphens
- Descriptive and specific
- Include dates for reports/analyses
- Include version if versioned
- Avoid generic names (doc1.md, notes.md)
- Keep under 50 characters

## Cross-Referencing

### Linking Between Types

- **Architecture ↔ Runbooks**: Link architecture docs from deployment runbooks
- **Troubleshooting ↔ How-To**: Link guides from troubleshooting resolution steps
- **Status ↔ Planning**: Link implementation plans from status documents
- **Integration ↔ Configuration**: Link config reference from integration guides

### Creating Document Graphs

For complex topics, create relationship maps:

```markdown
## Related Documentation

**Prerequisites:**
- [AWS Account Setup](aws-setup.md)
- [GitHub Actions Setup](github-actions-setup.md)

**Related Guides:**
- [Deployment Runbook](operations/deployment-runbook.md)
- [Troubleshooting](operations/troubleshooting.md)

**Next Steps:**
- [Monitoring Setup](monitoring-setup.md)
- [Cost Analysis](../../docs/cost-analysis/COST-ANALYSIS.md)
```

## Metadata Standards

### Document Properties

Include at the top of each document:

```markdown
# Document Title

**Document Type**: [Architecture|Runbook|Guide|Analysis]
**Last Updated**: YYYY-MM-DD
**Status**: [Active|Deprecated|Draft]
**Owner**: [Team/Person]
**Review Cycle**: [Quarterly|Monthly|After Changes]
```

### Version Information

For versioned docs:

```markdown
**Version**: 2.1.0
**Last Updated**: 2026-01-15
**Changes**: See CHANGELOG.md
```

## Quality Checklist by Type

### All Documents
- [ ] Clear title (H1)
- [ ] Purpose/overview stated
- [ ] Table of contents (if >3 sections)
- [ ] All links valid
- [ ] Code blocks formatted
- [ ] Metadata included

### Runbooks
- [ ] Prerequisites listed
- [ ] Steps are numbered and clear
- [ ] Validation steps included
- [ ] Rollback procedures documented
- [ ] Expected duration noted

### Architecture Docs
- [ ] Architecture diagram present
- [ ] Components clearly described
- [ ] Security considerations included
- [ ] Trade-offs explained
- [ ] Alternatives considered

### Troubleshooting Guides
- [ ] Symptoms clearly described
- [ ] Multiple causes considered
- [ ] Diagnostic steps provided
- [ ] Resolution steps clear
- [ ] Prevention measures included

### Integration Guides
- [ ] Prerequisites complete
- [ ] Authentication documented
- [ ] Configuration examples provided
- [ ] Error handling covered
- [ ] Testing steps included
