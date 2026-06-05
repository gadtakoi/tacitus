#!/usr/bin/env python3
"""Generate SBOM artifacts for Python and frontend."""

import subprocess
import sys
from pathlib import Path


def generate_python_sbom() -> None:
    """Generate CycloneDX SBOM for the Python environment."""
    output = Path("sbom-python.json")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cyclonedx_py",
            "environment",
            "--output-file",
            str(output),
        ],
        check=True,
    )
    print(f"Python SBOM written to {output}")


def generate_frontend_sbom() -> None:
    """Generate CycloneDX SBOM for the frontend."""
    frontend = Path("frontend")
    subprocess.run(
        ["npm", "run", "sbom"],
        cwd=frontend,
        check=True,
    )
    print(f"Frontend SBOM written to {frontend / 'sbom.json'}")


if __name__ == "__main__":
    generate_python_sbom()
    generate_frontend_sbom()
