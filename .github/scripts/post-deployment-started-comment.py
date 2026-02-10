#!/usr/bin/env python3
"""
Post a comment to a GitHub issue indicating deployment has started.
"""

import sys
import subprocess


def post_deployment_started_comment(issue_number: str, version: str, repo: str):
    """Post deployment started comment to issue."""
    workflows_url = f"https://github.com/{repo}/actions/workflows/deploy-production.yml"
    cloudwatch_url = "https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups"
    
    comment_body = f"""🚀 **Production Deployment Started**

Version `{version}` deployment has been initiated.

Monitor progress:
- [View workflow runs]({workflows_url})
- [Production CloudWatch Logs]({cloudwatch_url})

This issue will be updated when deployment completes."""
    
    try:
        subprocess.run([
            'gh', 'issue', 'comment', issue_number,
            '--repo', repo,
            '--body', comment_body
        ], check=True, capture_output=True, text=True)
        
        print(f"✅ Posted deployment started comment to issue #{issue_number}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to post comment: {e.stderr}", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 4:
        print("Usage: python post-deployment-started-comment.py <issue_number> <version> <repo>", file=sys.stderr)
        sys.exit(1)
    
    issue_number = sys.argv[1]
    version = sys.argv[2]
    repo = sys.argv[3]
    
    post_deployment_started_comment(issue_number, version, repo)


if __name__ == '__main__':
    main()
