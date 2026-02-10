#!/usr/bin/env python3
"""
Validate production deployment approval requirements.

Requirements:
- At least 2 total approvals
- At least 1 SRE team member approval

Usage:
    python3 validate-deployment-approval.py <issue_number> <comment_body>
"""

import os
import sys
import json
import subprocess
from typing import List, Dict, Set

# SRE team members (TODO: Move to environment variable or config)
SRE_MEMBERS = {'clong-viimed'}

def run_gh_command(cmd: List[str]) -> str:
    """Run gh CLI command and return output."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()

def get_approval_comments(issue_number: str) -> List[Dict]:
    """Get all 'approve-deployment' comments on the issue."""
    cmd = [
        'gh', 'api',
        '-H', 'Accept: application/vnd.github+json',
        '-H', 'X-GitHub-Api-Version: 2022-11-28',
        f'/repos/{os.environ["GITHUB_REPOSITORY"]}/issues/{issue_number}/comments',
        '--jq', '.[] | select(.body | test("^approve-deployment$")) | {user: .user.login, created_at: .created_at}'
    ]
    
    try:
        output = run_gh_command(cmd)
        if not output:
            return []
        
        # Parse JSON lines
        comments = []
        for line in output.strip().split('\n'):
            if line:
                comments.append(json.loads(line))
        return comments
    except subprocess.CalledProcessError:
        return []

def validate_approvals(issue_number: str) -> Dict:
    """Validate approval requirements and return status."""
    comments = get_approval_comments(issue_number)
    
    # Get unique approvers
    approvers: Set[str] = {comment['user'] for comment in comments}
    
    # Check for SRE approval
    has_sre_approval = bool(approvers & SRE_MEMBERS)
    
    # Calculate requirements
    approval_count = len(approvers)
    meets_count_requirement = approval_count >= 2
    meets_sre_requirement = has_sre_approval
    approved = meets_count_requirement and meets_sre_requirement
    
    return {
        'approved': approved,
        'approval_count': approval_count,
        'approvers': list(approvers),
        'has_sre_approval': has_sre_approval,
        'sre_approvers': list(approvers & SRE_MEMBERS),
        'meets_count_requirement': meets_count_requirement,
        'meets_sre_requirement': meets_sre_requirement
    }

def post_approval_comment(issue_number: str, result: Dict):
    """Post approval confirmation comment."""
    approvers_list = '\n'.join(f'- @{user}' for user in result['approvers'])
    
    body = f"""✅ **Deployment Approved**

**Approval Summary:**
- Total approvals: {result['approval_count']}/2 ✅
- SRE approval: ✅

**Approvers:**
{approvers_list}

🚀 Initiating production deployment..."""
    
    cmd = [
        'gh', 'issue', 'comment', issue_number,
        '--repo', os.environ['GITHUB_REPOSITORY'],
        '--body', body
    ]
    run_gh_command(cmd)

def post_insufficient_approvals_comment(issue_number: str, result: Dict):
    """Post comment about insufficient approvals."""
    count_status = '✅' if result['meets_count_requirement'] else '❌'
    sre_status = '✅' if result['meets_sre_requirement'] else '❌'
    
    if not result['meets_sre_requirement']:
        sre_members_str = ', '.join(f'@{user}' for user in SRE_MEMBERS)
        body = f"""⚠️ **Approval Requirements Not Met**

**Current Status:**
- Total approvals: {result['approval_count']}/2 {count_status}
- SRE approval: {sre_status}

**Required:**
- At least 2 total approvals
- At least 1 SRE team member approval

**SRE Team Members:** {sre_members_str}

Please ensure an SRE team member approves before deployment can proceed."""
    else:
        needed = 2 - result['approval_count']
        body = f"""⚠️ **Approval Requirements Not Met**

**Current Status:**
- Total approvals: {result['approval_count']}/2 {count_status}
- SRE approval: {sre_status}

**Required:**
- At least 2 total approvals
- At least 1 SRE team member approval

Need {needed} more approval(s)."""
    
    cmd = [
        'gh', 'issue', 'comment', issue_number,
        '--repo', os.environ['GITHUB_REPOSITORY'],
        '--body', body
    ]
    run_gh_command(cmd)

def main():
    if len(sys.argv) != 3:
        print("Usage: validate-deployment-approval.py <issue_number> <comment_body>", file=sys.stderr)
        sys.exit(1)
    
    issue_number = sys.argv[1]
    comment_body = sys.argv[2]
    
    # Check if this is an approval comment
    if comment_body.strip() != 'approve-deployment':
        print("Not an approval comment, skipping validation")
        sys.exit(0)
    
    # Validate approvals
    result = validate_approvals(issue_number)
    
    print(f"Approval validation result:")
    print(f"  Total approvals: {result['approval_count']}/2")
    print(f"  SRE approval: {result['has_sre_approval']}")
    print(f"  Approved: {result['approved']}")
    
    # Set GitHub Actions output
    with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
        f.write(f"approved={'true' if result['approved'] else 'false'}\n")
        f.write(f"approval_count={result['approval_count']}\n")
    
    # Post comment
    if result['approved']:
        post_approval_comment(issue_number, result)
        print("✅ All approval requirements met")
        sys.exit(0)
    else:
        post_insufficient_approvals_comment(issue_number, result)
        print("❌ Approval requirements not met")
        sys.exit(1)

if __name__ == '__main__':
    main()
