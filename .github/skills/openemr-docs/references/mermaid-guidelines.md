# Mermaid Diagram Guidelines for OpenEMR on ECS

Standards for creating and maintaining Mermaid diagrams in OpenEMR on ECS documentation.

## General Principles

1. **One concept per diagram** - Keep diagrams focused
2. **Consistent styling** - Use standard themes and colors
3. **No init directives** - NEVER use `%%{init:...}%%` blocks (breaks GitHub rendering)
4. **Validate before commit** - Always run mermaid-diagram-validator
5. **Preview before commit** - Always run mermaid-diagram-preview
6. **Text descriptions** - Supplement diagrams with text for accessibility

## GitHub Rendering Compliance (CRITICAL)

### Why Init Directives Break GitHub

**NEVER use `%%{init:}` directives** in mermaid diagrams for this repository.

**The Problem:**
GitHub's mermaid renderer has a critical incompatibility with init directives:
- Node styles fail to apply (no colors, no borders)
- `classDef` statements are completely ignored
- Diagrams render as plain text boxes without visual distinction
- All custom theming is lost

**The Solution:**
Remove init directives entirely. `classDef` statements work perfectly on GitHub **without** init directives.

❌ **WRONG (breaks on GitHub):**
```mermaid
%%{init: {'theme':'base'}}%%
flowchart TD
    node1[Start]:::startNode
    classDef startNode fill:#1e3a8a,stroke:#1e40af,color:#fff
```

✅ **CORRECT (renders properly on GitHub):**
```mermaid
flowchart TD
    node1[Start]:::startNode
    classDef startNode fill:#1e3a8a,stroke:#1e40af,color:#fff
```

### Standard Color Scheme for Workflow Diagrams

**MANDATORY:** All GitHub Actions workflow diagrams must use this standard theme:

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

**Node Class Reference:**

| Class | Color | Hex Code | Usage |
|-------|-------|----------|-------|
| `triggerNode` | Deep Blue | `#1e3a8a` | Workflow triggers (workflow_dispatch, push, schedule) |
| `startNode` | Deep Blue | `#1e3a8a` | Initial setup jobs (checkout, validate inputs) |
| `criticalNode` | Dark Red | `#991b1b` | Critical operations (deployments, database changes) |
| `warningNode` | Dark Orange | `#854d0e` | Caution steps (security scans, approval gates) |
| `successNode` | Dark Green | `#166534` | Build, test, verification steps |
| `failureNode` | Darkest Red | `#7f1d1d` | Failure handlers, rollback steps |
| `endNode` | Dark Green | `#166534` | Final success states |

**Example with Standard Colors:**

```mermaid
flowchart TD
    trigger[workflow_dispatch]:::triggerNode
    validate[Validate Inputs]:::startNode
    build[Build Docker Image]:::successNode
    scan[Security Scan]:::warningNode
    deploy[Deploy to ECS]:::criticalNode
    verify[Health Check]:::successNode
    success[Deployment Complete]:::endNode
    rollback[Rollback]:::failureNode
    
    trigger --> validate
    validate --> build
    build --> scan
    scan --> deploy
    deploy --> verify
    verify -->|Pass| success
    verify -->|Fail| rollback
    
    classDef triggerNode fill:#1e3a8a,stroke:#1e40af,color:#fff
    classDef startNode fill:#1e3a8a,stroke:#1e40af,color:#fff
    classDef criticalNode fill:#991b1b,stroke:#7f1d1d,color:#fff
    classDef warningNode fill:#854d0e,stroke:#78350f,color:#fff
    classDef successNode fill:#166534,stroke:#14532d,color:#fff
    classDef failureNode fill:#7f1d1d,stroke:#450a0a,color:#fff
    classDef endNode fill:#166534,stroke:#14532d,color:#fff
```

## Diagram Types

### Flowchart

Use for decision trees, workflows, and process flows:

```mermaid
flowchart TD
    Start[Start Deployment] --> Check{Environment?}
    Check -->|Staging| ValidateStaging[Validate Staging Config]
    Check -->|Production| ValidateProd[Validate Production Config]
    ValidateStaging --> Deploy[Deploy to ECS]
    ValidateProd --> Deploy
    Deploy --> Verify[Run Smoke Tests]
    Verify --> End[Complete]
```

**When to use:**
- Deployment workflows
- Decision logic
- Error handling flows
- Multi-step processes

### Sequence Diagram

Use for interactions between systems/components:

```mermaid
sequenceDiagram
    participant GHA as GitHub Actions
    participant ECR as AWS ECR
    participant ECS as AWS ECS
    participant RDS as AWS RDS
    
    GHA->>ECR: Push Docker image
    ECR-->>GHA: Image digest
    GHA->>ECS: Update task definition
    ECS->>RDS: Health check
    RDS-->>ECS: OK
    ECS->>ECS: Start new tasks
```

**When to use:**
- API interactions
- Authentication flows
- Deployment sequences
- Service communication

### Architecture Diagram (C4)

Use for system architecture and components:

```mermaid
graph TB
    subgraph AWS["AWS Cloud"]
        subgraph VPC["VPC"]
            ALB[Application Load Balancer]
            ECS[ECS Fargate Cluster]
            RDS[(Aurora PostgreSQL)]
            Redis[(ElastiCache Redis)]
        end
        S3[S3 Bucket]
        CloudFront[CloudFront CDN]
    end
    
    Users[Users] --> CloudFront
    CloudFront --> ALB
    ALB --> ECS
    ECS --> RDS
    ECS --> Redis
    ECS --> S3
```

**When to use:**
- AWS infrastructure layout
- Component relationships
- Network topology
- Multi-tier architectures

### State Diagram

Use for status transitions and state machines:

```mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> Running: Start
    Running --> Success: Complete
    Running --> Failed: Error
    Failed --> Pending: Retry
    Success --> [*]
    Failed --> [*]: Give Up
```

**When to use:**
- Deployment states
- Task statuses
- Workflow states
- Resource lifecycles

### Gantt Chart

Use for project timelines and scheduling:

```mermaid
gantt
    title Implementation Roadmap
    dateFormat YYYY-MM-DD
    section Phase 1
    ECR Setup           :done, 2025-11-01, 7d
    IAM Configuration   :done, 2025-11-08, 5d
    section Phase 2
    ECS Infrastructure  :active, 2025-11-13, 14d
    RDS Setup          :2025-11-20, 10d
```

**When to use:**
- Project roadmaps
- Implementation timelines
- Maintenance schedules

## Styling Standards

### Colors for Workflow Diagrams

**Use the standard workflow color scheme** (see GitHub Rendering Compliance section above):

- **Trigger/Start**: `#1e3a8a` (deep blue)
- **Success/Build**: `#166534` (dark green)
- **Critical/Deploy**: `#991b1b` (dark red)
- **Warning/Caution**: `#854d0e` (dark orange)
- **Failure/Rollback**: `#7f1d1d` (darkest red)

### Colors for Other Diagram Types

For non-workflow diagrams (architecture, sequence, etc.), use semantic colors:

- **Success/Active**: `#28a745` (green)
- **Warning**: `#ffc107` (yellow)
- **Error/Critical**: `#dc3545` (red)
- **Info/Default**: `#007bff` (blue)
- **Neutral**: `#6c757d` (gray)

### Node Styles

```mermaid
flowchart LR
    A[Standard Box]
    B([Rounded Box])
    C[(Database)]
    D{{Decision}}
    E>Asymmetric]
    F[/Parallelogram/]
```

**Conventions:**
- **Databases**: Use cylinder `[()]`
- **Decisions**: Use diamond `{{}}`
- **Services**: Use rounded box `([])`
- **Actions**: Use standard box `[]`
- **External systems**: Use parallelogram `[//]`

### Arrow Types

- **Solid arrow** `-->`: Standard flow
- **Dotted arrow** `-.->`: Optional or async
- **Thick arrow** `==>`: Primary path
- **Arrow with text** `-->|label|`: Labeled transition

## Size and Complexity

### Recommended Limits

- **Max nodes**: 15-20 per diagram
- **Max depth**: 4-5 levels
- **Max width**: Readable at 800px wide

### When to Split Diagrams

Split into multiple diagrams when:
- More than 20 nodes
- Representing multiple distinct flows
- Diagram becomes hard to read
- Multiple zoom levels needed

## Validation Requirements

### Before Committing

1. **Run validator tool**:
   ```bash
   python scripts/validate_docs.py /path/to/repo
   ```

2. **Preview in VS Code** using mermaid extension

3. **Check for errors**:
   - No init directives
   - Valid syntax
   - Proper node connections
   - Readable labels

### Common Issues

❌ **Don't use init directives:**
```mermaid
%%{init: {'theme':'base'}}%%
graph TD
    A --> B
```

✅ **Use clean syntax with classDef:**
```mermaid
graph TD
    A[Node A]:::successNode --> B[Node B]:::successNode
    classDef successNode fill:#166534,stroke:#14532d,color:#fff
```

❌ **Don't overcomplicate:**
```mermaid
graph TD
    A --> B --> C --> D --> E --> F --> G --> H --> I --> J --> K --> L
```

✅ **Split into logical sections:**
```mermaid
graph TD
    subgraph Phase1
        A --> B --> C
    end
    subgraph Phase2
        D --> E --> F
    end
    Phase1 --> Phase2
```

### Common Syntax Issues

| Issue | Problem | Fix |
|-------|---------|-----|
| Special characters in labels | Breaks parsing | Wrap in quotes: `node1["Label with : special"]` |
| Duplicate node IDs | Ambiguous references | Use unique IDs: `build1`, `build2` instead of `build`, `build` |
| Unclosed subgraphs | Invalid syntax | Add `end` for each `subgraph` declaration |
| Invalid arrow syntax | Won't render | Use `-->`, `-.->`, or `==>` not `->` or `~>` |
| Missing classDef | Styles don't apply | Define all classes before using `:::className` |
| Init directives | Breaks GitHub rendering | Remove completely - not needed for classDef |

**Example - Fixing Special Characters:**

❌ **WRONG:**
```mermaid
flowchart TD
    A[Step: Validate] --> B[Status: Success]
```

✅ **CORRECT:**
```mermaid
flowchart TD
    A["Step: Validate"] --> B["Status: Success"]
```

## Diagram Maintenance

### Update Triggers

Update diagrams when:
- Architecture changes
- New services added
- Workflows modified
- Infrastructure updates
- After major deployments

### Version Control

- Commit diagram changes with descriptive messages
- Reference related code PRs
- Keep old versions in git history
- Document diagram changes in commit messages

### Deprecation

When retiring diagrams:
1. Add deprecation notice above diagram
2. Point to replacement diagram
3. Keep for 1 release cycle
4. Remove in next major version

## Accessibility

### Text Descriptions

Always provide text description:

```markdown
The following diagram illustrates the ECS deployment workflow:

```mermaid
flowchart TD
    ...
```

This workflow shows how GitHub Actions builds Docker images, pushes to ECR, and updates ECS task definitions.
```

### Alt Text

For critical diagrams, provide detailed alt text describing:
- Main components
- Key relationships
- Flow direction
- Decision points

## Tools and Resources

### Validation

- **mermaid-diagram-validator tool**: Validate syntax before committing
- **mermaid-diagram-preview tool**: Preview rendering in VS Code
- **Mermaid Live Editor**: Test complex diagrams at [mermaid.live](https://mermaid.live/)
- **GitHub PR Preview**: Verify rendering in pull request
- **mermaid-compliance skill**: Automated compliance checking

### Testing Workflow

1. **Write diagram** with proper syntax and classes
2. **Validate** using mermaid-diagram-validator tool
3. **Preview** using mermaid-diagram-preview tool
4. **Test complex diagrams** at [mermaid.live](https://mermaid.live/)
   - Paste diagram code
   - Verify colors and styling
   - Test edge cases (long labels, many nodes)
5. **Commit** only after all validation passes

### Documentation

- [Mermaid Official Docs](https://mermaid.js.org/)
- [Mermaid Live Editor](https://mermaid.live/) - Test rendering online
- Repository mermaid-compliance skill - Automated compliance
- [GitHub Mermaid Support](https://github.blog/2022-02-14-include-diagrams-markdown-files-mermaid/) - Official GitHub documentation

## Examples by Use Case

### Deployment Workflow

```mermaid
flowchart TD
    Start[Trigger Deployment] --> Build[Build Docker Images]
    Build --> Push[Push to ECR]
    Push --> UpdateTask[Update Task Definition]
    UpdateTask --> Deploy{Deploy Type?}
    Deploy -->|Blue/Green| BlueGreen[Blue/Green Deployment]
    Deploy -->|Rolling| Rolling[Rolling Update]
    BlueGreen --> Verify[Health Checks]
    Rolling --> Verify
    Verify --> Success{Success?}
    Success -->|Yes| Complete[Mark Complete]
    Success -->|No| Rollback[Rollback]
    Rollback --> Alert[Alert Team]
```

### AWS Architecture

```mermaid
graph TB
    subgraph "Internet"
        Users[Users/Clients]
    end
    
    subgraph "AWS Cloud"
        Route53[Route53 DNS]
        
        subgraph "VPC us-east-1"
            ALB[Application Load Balancer]
            
            subgraph "Public Subnets"
                NAT[NAT Gateway]
            end
            
            subgraph "Private Subnets"
                ECS[ECS Fargate - OpenEMR]
                RDS[(RDS MySQL)]
                Redis[(ElastiCache Redis)]
            end
        end
        
        S3[S3 Buckets]
        Secrets[Secrets Manager]
    end
    
    Users --> Route53
    Route53 --> ALB
    ALB --> ECS
    ECS --> RDS
    ECS --> Redis
    ECS --> S3
    ECS --> Secrets
```

### Authentication Flow

```mermaid
sequenceDiagram
    participant User
    participant Frontend
    participant API
    participant Auth
    participant DB
    
    User->>Frontend: Enter credentials
    Frontend->>API: POST /auth/login
    API->>Auth: Validate credentials
    Auth->>DB: Query user
    DB-->>Auth: User data
    Auth-->>API: JWT token
    API-->>Frontend: Token + user info
    Frontend->>Frontend: Store token
    Frontend-->>User: Redirect to dashboard
```
