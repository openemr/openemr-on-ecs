#!/usr/bin/env python3
"""
Automatically create markdown links for documentation file references in README.md.

Converts plain filenames to clickable links with proper relative paths.

Usage:
    python linkify_docs.py <repo_root> [docs_path] [--update]

Examples:
    python linkify_docs.py . docs
    python linkify_docs.py . docs --update
"""

import re
import os
import sys
from pathlib import Path
from typing import Dict, List


class DocLinkifier:
    def __init__(self, repo_root: str, docs_path: str = "docs"):
        self.repo_root = Path(repo_root)
        self.docs_path = self.repo_root / docs_path
        self.readme_path = self.docs_path / "README.md"
        
        self.file_map = {}  # {filename -> relative_path}
        self.changes = []
        self._build_file_map()
    
    def _build_file_map(self):
        """Build a map of all .md files in docs directory."""
        for root, dirs, files in os.walk(self.docs_path):
            dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', '.github'}]
            
            for file in files:
                if file.endswith('.md'):
                    full_path = Path(root) / file
                    rel_path = full_path.relative_to(self.docs_path)
                    self.file_map[file] = str(rel_path)
    
    def linkify(self, update: bool = False) -> bool:
        """Process README and create links for file references."""
        if not self.readme_path.exists():
            print(f"❌ README.md not found at {self.readme_path}")
            return False
        
        print(f"📖 Processing {self.readme_path}")
        with open(self.readme_path, 'r') as f:
            content = f.read()
        
        original_content = content
        lines = content.split('\n')
        new_lines = []
        
        for i, line in enumerate(lines, 1):
            new_line = self._process_line(line, i)
            new_lines.append(new_line)
        
        new_content = '\n'.join(new_lines)
        
        if new_content == original_content:
            print("✅ No changes needed - all links are properly formatted!")
            return True
        
        print(f"\n📝 Found {len(self.changes)} lines to linkify:")
        for line_num, before, after in self.changes[:10]:  # Show first 10
            print(f"\n  Line {line_num}:")
            print(f"    Before: {before[:70]}")
            print(f"    After:  {after[:70]}")
        
        if len(self.changes) > 10:
            print(f"\n  ... and {len(self.changes) - 10} more")
        
        if update:
            with open(self.readme_path, 'w') as f:
                f.write(new_content)
            print(f"\n✅ Updated {self.readme_path} with {len(self.changes)} links")
            return True
        else:
            print(f"\n💡 Run with --update flag to apply these changes")
            return False
    
    def _process_line(self, line: str, line_num: int) -> str:
        """Process a single line and convert file references to links."""
        # Skip lines that are already links or don't contain filenames
        if '[' in line and '](' in line:
            return line
        
        # Look for patterns like:
        # - **FILENAME.md** - Description
        # or
        # - **filename.md** - Description
        
        # Pattern: bullet point with bold filename
        pattern = r'^(\s*-\s+)\*\*([A-Z][A-Za-z0-9\-]*\.md)\*\*(\s+-\s+.+)$'
        match = re.match(pattern, line)
        
        if match:
            prefix = match.group(1)
            filename = match.group(2)
            suffix = match.group(3)
            
            # Find the file path
            if filename in self.file_map:
                filepath = self.file_map[filename]
                # Create relative link (docs/README.md -> file is relative to docs/)
                new_line = f"{prefix}[**{filename}**]({filepath}){suffix}"
                
                self.changes.append((line_num, line, new_line))
                return new_line
        
        return line
    
    def validate_links(self) -> Dict[str, List[str]]:
        """Validate that all file references in README are proper links."""
        if not self.readme_path.exists():
            return {'errors': [f"README.md not found at {self.readme_path}"]}
        
        with open(self.readme_path, 'r') as f:
            content = f.read()
        
        errors = []
        warnings = []
        
        # Find all markdown links
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        links = set()
        for match in re.finditer(link_pattern, content):
            text, path = match.groups()
            links.add(path)
        
        # Find all potential file references (bold filenames)
        # Matches both UPPERCASE and lowercase filenames: **disaster-recovery-runbook.md** OR **DBEAVER-RDS-CONNECTION-GUIDE.md**
        file_ref_pattern = r'\*\*([a-zA-Z][a-zA-Z0-9\-]*\.md)\*\*'
        plain_files = []
        
        for i, line in enumerate(content.split('\n'), 1):
            for match in re.finditer(file_ref_pattern, line):
                filename = match.group(1)
                # Check if this filename appears in a link
                if not any(filename in link for link in links):
                    # Check if it's not already in a link
                    if f'[**{filename}**]' not in line:
                        plain_files.append((i, filename, line.strip()[:70]))
        
        if plain_files:
            for line_num, filename, context in plain_files:
                errors.append(
                    f"Line {line_num}: File '{filename}' is not linked"
                    f"\n    Context: {context}..."
                )
        
        return {
            'errors': errors,
            'warnings': warnings,
            'valid_links': len(links),
            'plain_files': len(plain_files)
        }


def main():
    """Main entry point."""
    repo_root = sys.argv[1] if len(sys.argv) > 1 else "."
    docs_path = sys.argv[2] if len(sys.argv) > 2 else "docs"
    update = '--update' in sys.argv
    
    print("🔗 Documentation Link Generator\n")
    print("="*70)
    
    linkifier = DocLinkifier(repo_root, docs_path)
    
    # First, validate
    print("\n📋 Validating existing links...\n")
    validation = linkifier.validate_links()
    
    if validation['errors']:
        print(f"⚠️  Found {len(validation['errors'])} plain text file references:\n")
        for error in validation['errors']:
            print(f"  {error}")
    else:
        print("✅ All file references are properly linked!")
    
    print(f"\n📊 Statistics:")
    print(f"  Valid links: {validation['valid_links']}")
    print(f"  Plain file refs: {validation['plain_files']}")
    
    # Then, linkify
    print("\n" + "="*70)
    print("🔗 Processing file references...\n")
    
    success = linkifier.linkify(update=update)
    
    print("\n" + "="*70)
    if not success and not update:
        print(f"\n💡 To apply changes, run: python linkify_docs.py {repo_root} {docs_path} --update")
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
