#!/usr/bin/env python3
"""
Validate documentation references for consistency and accuracy.

Checks:
- Files referenced in README.md exist at stated paths
- All docs in folders are referenced in README.md
- Dead links and orphaned files
"""

import re
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple


class ReferenceValidator:
    def __init__(self, repo_root: str, docs_path: str = "docs"):
        self.repo_root = Path(repo_root)
        self.docs_path = self.repo_root / docs_path
        self.readme_path = self.docs_path / "README.md"
        
        self.errors = []
        self.warnings = []
        self.referenced_files = {}
        self.actual_files = set()
    
    def validate(self) -> bool:
        """Run all validation checks."""
        print(f"📋 Validating documentation references in {self.docs_path}")
        print(f"Reading README from: {self.readme_path}\n")
        
        if not self.readme_path.exists():
            print(f"❌ README.md not found at {self.readme_path}")
            return False
        
        # Collect all actual files
        self._collect_actual_files()
        
        # Extract referenced files from README
        self._extract_references()
        
        # Run validation checks
        self._check_missing_files()
        self._check_orphaned_files()
        self._check_broken_links()
        
        # Report results
        return self._report_results()
    
    def _collect_actual_files(self):
        """Collect all .md files in docs directory."""
        for root, dirs, files in os.walk(self.docs_path):
            # Skip certain directories
            dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', '.github', 'archive'}]
            
            for file in files:
                if file.endswith('.md'):
                    full_path = Path(root) / file
                    rel_path = full_path.relative_to(self.docs_path)
                    self.actual_files.add(str(rel_path))
                    
    def _extract_references(self):
        """Extract all file references from README.md."""
        with open(self.readme_path, 'r') as f:
            content = f.read()
        
        # Find markdown links [text](path)
        link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        
        for match in re.finditer(link_pattern, content):
            text, path = match.groups()
            normalized_path = self._normalize_doc_path(path)
            if not normalized_path:
                continue
            self.referenced_files[normalized_path] = text

    def _normalize_doc_path(self, path: str) -> str:
        """Normalize doc paths for matching and existence checks."""
        cleaned = path.strip()

        # Skip external links and root-level docs
        if cleaned.startswith(('http://', 'https://', '../')):
            return ""

        # Strip anchor fragments
        if '#' in cleaned:
            cleaned = cleaned.split('#', 1)[0]

        cleaned = cleaned.strip()

        if not cleaned or cleaned.endswith('/'):
            return ""

        if cleaned.startswith('./'):
            cleaned = cleaned[2:]

        return Path(cleaned).as_posix()
    
    def _check_missing_files(self):
        """Check if referenced files actually exist."""
        for ref_path, ref_text in self.referenced_files.items():
            # Resolve the full path
            full_path = (self.docs_path / ref_path).resolve()
            
            # Check if file doesn't exist
            if not full_path.exists():
                self.errors.append({
                    'type': 'missing_file',
                    'ref': ref_path,
                    'message': f"Referenced file '{ref_path}' does not exist"
                })
    
    def _check_orphaned_files(self):
        """Check for files that exist but aren't referenced in README."""
        documented_files = set(self.referenced_files.keys())
        
        orphaned = self.actual_files - documented_files - {'README.md'}
        
        for orphaned_file in sorted(orphaned):
            # Skip nested directories and their contents
            if orphaned_file.count('/') > 1:
                continue
            
            self.warnings.append({
                'type': 'orphaned_file',
                'file': orphaned_file,
                'message': f"File '{orphaned_file}' exists but is not referenced in README.md"
            })
    
    def _check_broken_links(self):
        """Check for broken anchor links and relative paths."""
        # Find all link references
        for ref_path, ref_text in self.referenced_files.items():
            if '#' in ref_path:
                # Anchor reference - warn about verification
                file_part = ref_path.split('#')[0]
                if file_part and not (self.docs_path / file_part).exists():
                    self.errors.append({
                        'type': 'broken_anchor',
                        'ref': ref_path,
                        'message': f"Anchor link '{ref_path}' references missing file"
                    })
    
    def _report_results(self) -> bool:
        """Print validation results and return success status."""
        has_issues = bool(self.errors or self.warnings)
        
        print("\n" + "="*70)
        print("VALIDATION RESULTS")
        print("="*70)
        
        if self.errors:
            print(f"\n❌ ERRORS ({len(self.errors)}):\n")
            for i, error in enumerate(self.errors, 1):
                print(f"{i}. {error['message']}")
                if 'line' in error:
                    print(f"   Line: {error['line']}")
                if 'ref' in error:
                    print(f"   Reference: {error['ref']}")
                if 'file' in error:
                    print(f"   File: {error['file']}")
                if 'archive' in error:
                    print(f"   Archive: {error['archive']}")
                print()
        
        if self.warnings:
            print(f"\n⚠️  WARNINGS ({len(self.warnings)}):\n")
            for i, warning in enumerate(self.warnings, 1):
                print(f"{i}. {warning['message']}")
                if 'file' in warning:
                    print(f"   File: {warning['file']}")
                print()
        
        if not has_issues:
            print("\n✅ All documentation references are valid and consistent!\n")
            return True
        
        print("="*70)
        print(f"Summary: {len(self.errors)} errors, {len(self.warnings)} warnings")
        print("="*70 + "\n")
        
        return len(self.errors) == 0  # Success only if no errors (warnings OK)


def main():
    """Main entry point."""
    repo_root = sys.argv[1] if len(sys.argv) > 1 else "."
    docs_path = sys.argv[2] if len(sys.argv) > 2 else "docs"
    
    validator = ReferenceValidator(repo_root, docs_path)
    success = validator.validate()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
