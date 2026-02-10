# [Operation Name] Runbook

**Document Type**: Operational Runbook  
**Last Updated**: YYYY-MM-DD  
**Status**: Active  
**Owner**: [Team Name]  
**Review Cycle**: After major changes

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Pre-Operation Checklist](#pre-operation-checklist)
- [Procedure](#procedure)
- [Validation](#validation)
- [Rollback](#rollback)
- [Troubleshooting](#troubleshooting)
- [Post-Operation Tasks](#post-operation-tasks)
- [Related Documentation](#related-documentation)

---

## Overview

### Purpose

[One sentence describing what this runbook accomplishes]

### When to Use

[Describe the scenarios when this runbook should be followed]

- Scenario 1
- Scenario 2

### Expected Duration

- **Normal case**: [X minutes/hours]
- **With issues**: [Y minutes/hours]

### Risk Level

**Risk**: [Low|Medium|High]

**Impact**: [Description of impact if something goes wrong]

---

## Prerequisites

### Required Access

- [ ] AWS Console access with role: [role name]
- [ ] GitHub repository access: [repo name]
- [ ] Terraform state bucket access
- [ ] [Other access requirements]

### Required Tools

- [ ] AWS CLI v2.x+
- [ ] Terraform v1.11+
- [ ] [Other tools]

### Required Knowledge

- Understanding of [concept 1]
- Familiarity with [system 2]

### Environment Requirements

- [ ] [Staging|Production] environment available
- [ ] No ongoing deployments
- [ ] [Other environment requirements]

### Communication

- [ ] Notify team in [Slack channel]
- [ ] Open incident ticket: [Jira/GitHub issue]
- [ ] Schedule maintenance window (for production)

---

## Pre-Operation Checklist

Before starting, verify:

- [ ] **Backup created**: [Type of backup and location]
- [ ] **Team notified**: [Notification method]
- [ ] **Maintenance window**: [If applicable]
- [ ] **Rollback plan ready**: Review rollback section below
- [ ] **Monitoring active**: Dashboards open and alerting configured
- [ ] [Other pre-checks]

---

## Procedure

### Step 1: [Action Title]

**Objective**: [What this step accomplishes]

**Command/Actions**:
```bash
# Description of what this does
command to run
```

**Expected Output**:
```
Expected output here
```

**Verification**:
- [ ] Output shows [expected result]
- [ ] No errors in logs

**If this fails**: [Brief troubleshooting or link to troubleshooting section]

**Duration**: ~X minutes

---

### Step 2: [Action Title]

[Repeat structure for each step]

**Command/Actions**:
```bash
command to run
```

**Expected Output**:
```
Expected output here
```

**Verification**:
- [ ] Checkpoint 1
- [ ] Checkpoint 2

**Duration**: ~X minutes

---

### Step 3: [Action Title]

[Continue for all steps]

---

## Validation

### Functional Validation

Run these checks to confirm the operation succeeded:

#### Check 1: [Validation Name]

```bash
# Command to verify
validation command
```

**Expected Result**: [Description]

- [ ] Validation passed

#### Check 2: [Validation Name]

```bash
validation command
```

**Expected Result**: [Description]

- [ ] Validation passed

### Health Checks

- [ ] Service health: [Check method]
- [ ] Database connection: [Check method]  
- [ ] API endpoints responding: [Check method]
- [ ] No errors in CloudWatch logs

### Performance Checks

- [ ] Response times normal: [Metric and threshold]
- [ ] Error rates normal: [Metric and threshold]
- [ ] Resource utilization acceptable

---

## Rollback

### When to Rollback

Rollback ifany of the following occur:
- Validation checks fail
- Critical errors in logs
- Service is unresponsive
- [Other rollback triggers]

### Rollback Procedure

#### Step 1: [Rollback Action]

```bash
# Rollback command
command to restore previous state
```

**Verification**:
- [ ] Service restored
- [ ] Original functionality working

#### Step 2: [Next Rollback Action]

[Continue until system is restored]

### Post-Rollback

After rollback:
- [ ] Notify team of rollback
- [ ] Document what went wrong
- [ ] Create incident report
- [ ] Plan next attempt (if applicable)

---

## Troubleshooting

### Issue 1: [Problem Description]

**Symptoms**: [What you see]

**Possible Causes**:
- Cause A
- Cause B

**Resolution**:
1. Check [something]
2. Run [diagnostic command]
3. Fix by [action]

---

### Issue 2: [Problem Description]

[Repeat structure for common issues]

### Getting Help

If issues persist:
- Check [troubleshooting guide](link)
- Contact: [team contact]
- Escalation: [escalation procedure]

---

## Post-Operation Tasks

After successful completion:

### Immediate Tasks
- [ ] Close maintenance window
- [ ] Update monitoring dashboards
- [ ] Notify team of completion
- [ ] Close incident ticket

### Documentation
- [ ] Record completion in [log/system]
- [ ] Update status document if applicable
- [ ] Note any deviations from runbook
- [ ] Document any issues encountered

### Follow-Up
- [ ] Monitor for [X hours/days]
- [ ] Review metrics for anomalies
- [ ] Schedule next occurrence (if recurring)

---

## Metrics and Success Criteria

### Success Criteria

Operation is successful when:
- All validation checks pass
- No errors in logs for X minutes
- Performance metrics within normal range
- [Other criteria]

### Key Metrics to Monitor

| Metric | Normal Range | Alert Threshold |
|--------|--------------|-----------------|
| Response time | < X ms | > Y ms |
| Error rate | < 0.1% | > 1% |
| [Other metric] | [Range] | [Threshold] |

---

## Related Documentation

**Prerequisites:**
- [Required setup guide](link)
- [Configuration reference](link)

**Related Procedures:**
- [Related runbook 1](link)
- [Related runbook 2](link)

**Troubleshooting:**
- [Troubleshooting guide](link)

**Architecture:**
- [System architecture](link)

---

## Changelog

| Date | Version | Changes | Author |
|------|---------|---------|--------|
| YYYY-MM-DD | 1.0 | Initial version | [Name] |
| YYYY-MM-DD | 1.1 | Added troubleshooting section | [Name] |

---

## Appendix

### Commands Reference

Quick reference for commonly used commands:

```bash
# Check status
status command

# View logs
log viewing command

# Restart service
restart command
```

### Emergency Contacts

- **On-call engineer**: [Contact method]
- **Team lead**: [Contact]
- **Escalation**: [Contact]

---

**Maintained by:** [Team Name]  
**Questions:** Contact [team email or Slack channel]  
**Emergency:** [Emergency contact procedure]
