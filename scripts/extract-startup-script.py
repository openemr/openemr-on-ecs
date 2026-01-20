#!/usr/bin/env python3
"""Extract the startup script from compute.py for shellcheck analysis."""

import ast
import sys


def extract_startup_script():
    """Extract startup commands from compute.py and write to a shell script."""
    try:
        with open("openemr_ecs/compute.py", "r") as f:
            tree = ast.parse(f.read())
    except FileNotFoundError:
        print("Error: openemr_ecs/compute.py not found", file=sys.stderr)
        return False
    except SyntaxError as e:
        print(f"Error: Failed to parse compute.py: {e}", file=sys.stderr)
        return False

    # Find the startup_commands assignment using AST
    startup_commands = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "startup_commands":
                    if isinstance(node.value, ast.List):
                        startup_commands = []
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                startup_commands.append(elt.value)
                        break
            if startup_commands is not None:
                break

    if startup_commands is None:
        print("Error: Could not find startup_commands list in compute.py", file=sys.stderr)
        return False

    # Join strings with newlines to create the script
    script = "\n".join(startup_commands)

    # Write to file
    try:
        with open("/tmp/startup_script.sh", "w") as f:
            f.write("#!/bin/sh\n")
            f.write("set -e\n")
            f.write("set -x\n")
            f.write(script)
            f.write("\n")
    except IOError as e:
        print(f"Error: Failed to write startup script: {e}", file=sys.stderr)
        return False

    print(f"Extracted {len(startup_commands)} commands to /tmp/startup_script.sh")
    return True


if __name__ == "__main__":
    sys.exit(0 if extract_startup_script() else 1)
