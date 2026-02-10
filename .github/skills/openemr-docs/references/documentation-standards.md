# OpenEMR on ECS Documentation Standards

This reference defines the documentation standards for the OpenEMR on ECS repository.

## Document Structure

### Required Elements

Every documentation file must include:

1. **Title (H1 header)** - Single H1 at the top of the document
2. **Overview/Introduction** - Brief description of purpose and scope
3. **Table of Contents** - For documents longer than 3 sections (use marker: `## Table of Contents`)
4. **Body Content** - Well-organized sections with clear headers
5. **Metadata** (when applicable) - Last updated date, maintenance schedule

### Header Hierarchy

- **H1 (`#`)**: Document title only (one per document)
- **H2 (`##`)**: Major sections
- **H3 (`###`)**: Subsections
- **H4-H6**: Further nesting (use sparingly)

### Recommended Document Length

- **Runbooks**: 200-500 lines
- **Guides**: 300-800 lines
- **Reference docs**: 400-1000+ lines
- **READMEs**: 100-300 lines
- **Architecture docs**: 300-600 lines

Documents exceeding 1000 lines should be split into multiple files with clear navigation.

## Content Quality Standards

### Writing Style

- **Clear and concise**: Avoid jargon unless necessary
- **Action-oriented**: Use imperative mood for instructions
- **Scannable**: Use bullet points, numbered lists, tables
- **Consistent terminology**: Use same terms throughout docs
- **Complete sentences**: Avoid fragments in paragraphs

### Code Examples

Format code blocks with proper language tags:

```bash
# Good - includes language tag and comments
terraform init
terraform plan
```

### Links

- **Internal links**: Use relative paths from repo root
- **External links**: Use full URLs with descriptive text
- **Format**: `[descriptive text](path/to/file.md)`
- **Validation**: All links must be valid (no 404s)

### Tables

Use tables for structured reference data:

| Column 1 | Column 2 | Column 3 |
|----------|----------|----------|
| Data     | Data     | Data     |

Align columns for readability in source.

## Metadata Standards

### Last Updated

Include at top of frequently-updated docs:

```markdown
**Last Updated:** January 28, 2026
```

### Status Indicators

Use consistent emoji/text for status:

- ✅ **Completed/Verified**
- 🔄 **In Progress**
- ⚠️ **Needs Attention**
- ❌ **Blocked/Failed**
- 📊 **Metrics**
- 🔍 **Review Required**

### Versioning

Include version references when documenting:
- Application versions
- Infrastructure versions
- Terraform module versions
- AWS service versions

## Diagram Standards

See [mermaid-guidelines.md](mermaid-guidelines.md) for detailed Mermaid diagram standards.

### When to Use Diagrams

- **Architecture**: System components and relationships
- **Workflows**: Sequential processes or decision trees
- **Data flow**: Information movement between systems
- **Network topology**: AWS infrastructure layout
- **State machines**: Status transitions

### Diagram Placement

- Place diagrams near related text
- Include diagram title/caption
- Provide text description for accessibility
- Keep diagrams focused (one concept per diagram)

## File Organization

### Naming Conventions

- **Lowercase with hyphens**: `deployment-runbook.md`
- **Descriptive names**: Clearly indicate content
- **Avoid generic names**: Not `doc1.md` or `notes.md`
- **Version in name** (if versioned): `api-guide-v2.md`

### Directory Structure

Organize docs into logical categories:

```
docs/
├── README.md (index)
├── architecture/
├── operations/
├── aws-configuration/
├── compliance/
├── cost-analysis/
├── environment/
└── workflows/
```

### Index Maintenance

Maintain an index file (`docs/README.md`) that:
- Lists all major documentation files
- Provides brief descriptions
- Organizes by category
- Includes "By Topic" and "By Status" sections
- Shows documentation metrics

## Maintenance Guidelines

### Review Schedule

- **Quarterly**: Verify accuracy against live systems
- **After deployments**: Update affected documentation
- **After incidents**: Document learnings and runbooks
- **After audits**: Update compliance documentation

### Deprecation Process

When deprecating documentation:

1. Move to `docs/archive/` directory
2. Add deprecation notice at top with date
3. Update index to remove entry
4. Keep for 6 months then delete

### Version Control

- Commit documentation changes with descriptive messages
- Reference related code PRs in doc commits
- Tag documentation versions with releases
- Keep changelog for major doc updates

## Cross-Referencing

### Linking Between Documents

Use relative links from repo root:

```markdown
See [AWS Configuration Guide](docs/aws-configuration/setup-guide.md) for details.
```

### Document Dependencies

At the top of docs with dependencies:

```markdown
**Prerequisites:**
- [AWS Account Setup](aws-setup.md)
- [GitHub Actions Configuration](github-actions.md)
```

### Related Documents

At the bottom of docs:

```markdown
## Related Documentation

- [Deployment Runbook](operations/deployment-runbook.md)
- [Troubleshooting Guide](operations/troubleshooting-guide.md)
```

## Special Document Types

### Runbooks

Must include:
- Prerequisites
- Step-by-step procedures
- Validation steps
- Rollback procedures
- Expected outcomes

### Troubleshooting Guides

Must include:
- Symptom descriptions
- Root cause analysis
- Resolution steps
- Prevention measures

### Architecture Documents

Must include:
- Architecture diagrams
- Component descriptions
- Integration points
- Trade-offs and decisions
- Future considerations

### Integration Guides

Must include:
- Service overview
- Configuration steps
- Authentication/authorization
- API reference or examples
- Error handling

## Quality Checklist

Before finalizing documentation:

- [ ] Single H1 header present
- [ ] Table of contents (if >3 sections)
- [ ] All internal links valid
- [ ] All code blocks with language tags
- [ ] Mermaid diagrams validated
- [ ] Consistent terminology used
- [ ] Metadata included (dates, versions)
- [ ] Cross-references added
- [ ] Reviewed for clarity
- [ ] Grammar and spelling checked
