#!/usr/bin/env python3
"""
Check workflow documentation compliance against SKILL.md requirements.

Usage:
    python3 check-workflow-docs-compliance.py [docs_directory]
    
Default docs_directory: .github/workflows/docs/

Returns:
    0 if all docs are 100% compliant
    1 if any docs are missing sections
"""

import re
import sys
from pathlib import Path

# Files to skip (meta documentation, not workflow docs)
SKIP_FILES = {
    'TERRAFORM_DESTROY_IAM_FIX.md',
    'production-promotion-process.md',
    'production-promotion-sequence-diagrams.md',
    'scale-operations-usage.md',
    'promote-to-production-usage.md'
}

# Patterns to skip (compliance reports, etc.)
SKIP_PATTERNS = [
    r'^REVIEW-REPORT.*\.md$',
    r'^COMPLIANCE-REVIEW.*\.md$',
    r'^WORKFLOW-DOCS-COMPLETENESS-REVIEW.*\.md$'
]

# 15 required sections from SKILL.md
REQUIRED_SECTIONS = [
    (r'Purpose|Overview', 'Purpose/Overview'),
    (r'Trigger', 'Triggers'),
    (r'Input', 'Inputs'),
    (r'Secret|Variables', 'Secrets/Variables'),
    (r'Environment|Job Dependency', 'Diagram'),
    (r'Jobs', 'Jobs'),
    (r'Output|Artifacts', 'Outputs'),
    (r'Data Flow', 'Data Flow'),
    (r'Verification|Prerequisites', 'Verification'),
    (r'Troubleshooting', 'Troubleshooting'),
    (r'Metrics|Performance|Timing', 'Metrics'),
    (r'Cost', 'Cost Analysis'),
    (r'Related', 'Related Docs'),
    (r'Security', 'Security Considerations'),
    (r'Support', 'Support & Maintenance')
]

def check_document(doc_path):
    """Check a single document for required sections"""
    with open(doc_path) as f:
        content = f.read()
    
    found_sections = []
    missing_sections = []
    
    for pattern, name in REQUIRED_SECTIONS:
        if re.search(rf'^## .*{pattern}', content, re.M | re.I):
            found_sections.append(name)
        else:
            missing_sections.append(name)
    
    return found_sections, missing_sections

def main():
    # Determine docs directory
    if len(sys.argv) > 1:
        docs_dir = Path(sys.argv[1])
    else:
        # Try to find it relative to current directory
        current = Path.cwd()
        if (current / '.github/workflows/docs').exists():
            docs_dir = current / '.github/workflows/docs'
        elif current.name == 'docs' and current.parent.name == 'workflows':
            docs_dir = current
        else:
            print("Error: Cannot find .github/workflows/docs directory")
            print("Usage: python3 check-workflow-docs-compliance.py [docs_directory]")
            return 1
    
    if not docs_dir.exists():
        print(f"Error: Directory {docs_dir} not found")
        return 1
    
    # Get all markdown files
    def should_skip(filename):
        """Check if file should be skipped based on SKIP_FILES or SKIP_PATTERNS"""
        if filename in SKIP_FILES:
            return True
        for pattern in SKIP_PATTERNS:
            if re.match(pattern, filename):
                return True
        return False
    
    docs = sorted([f for f in docs_dir.glob('*.md') if not should_skip(f.name)])
    
    if not docs:
        print(f"Error: No workflow documentation files found in {docs_dir}")
        return 1
    
    print(f"Workflow Documentation Compliance Report")
    print(f"Directory: {docs_dir}")
    print(f"Checking {len(docs)} workflow docs against SKILL.md (15 sections required)\n")
    print("=" * 80)
    
    all_results = {}
    for doc_path in docs:
        found, missing = check_document(doc_path)
        all_results[doc_path.name] = (found, missing)
        
        pct = int(len(found) / 15 * 100)
        status = '✅' if pct == 100 else '⚠️' if pct >= 80 else '❌'
        
        print(f"{status} {doc_path.name}: {len(found)}/15 ({pct}%)")
        if missing:
            # Print missing sections in groups of 4
            for i in range(0, len(missing), 4):
                chunk = missing[i:i+4]
                prefix = "   Missing:" if i == 0 else "           "
                print(f"{prefix} {', '.join(chunk)}")
    
    print("\n" + "=" * 80)
    
    # Summary statistics
    total = len(all_results)
    complete = sum(1 for _, (f, m) in all_results.items() if len(m) == 0)
    partial = sum(1 for _, (f, m) in all_results.items() if 0 < len(m) <= 3)
    needs_work = total - complete - partial
    
    print(f"\n📊 Summary:")
    print(f"   Total workflow docs: {total}")
    print(f"   ✅ 100% complete (15/15): {complete} ({int(complete/total*100)}%)")
    print(f"   ⚠️  Nearly complete (12-14/15): {partial} ({int(partial/total*100)}%)")
    print(f"   ❌ Needs work (<12/15): {needs_work} ({int(needs_work/total*100)}%)")
    
    # Detailed breakdown of what's missing across all docs
    if complete < total:
        print(f"\n📋 Most Common Missing Sections:")
        missing_counts = {}
        for _, (_, missing) in all_results.items():
            for section in missing:
                missing_counts[section] = missing_counts.get(section, 0) + 1
        
        for section, count in sorted(missing_counts.items(), key=lambda x: -x[1]):
            print(f"   {section}: {count} docs missing")
    
    if complete == total:
        print(f"\n🎉 All workflow documentation is 100% compliant!")
        return 0
    else:
        print(f"\n⚠️  {total - complete} docs need updates to reach 100% compliance")
        return 1

if __name__ == '__main__':
    sys.exit(main())
