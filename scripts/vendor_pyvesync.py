"""Fetch and isolate the pinned pyvesync source. Run from any directory."""

import argparse
import ast
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE = "https://github.com/ScribleSean/pyvesync"
DEFAULT_COMMIT = "3169960496a7b49fc0121dde9779a74ab50b2cd1"
PACKAGE = "custom_components.cosori_fryer._vendor.pyvesync"
ROOT = Path(__file__).resolve().parents[1]


def rewrite(source: str) -> str:
    """Rewrite absolute import statements, including those under TYPE_CHECKING."""
    lines = source.splitlines(keepends=True)
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module == "pyvesync" or (node.module or "").startswith("pyvesync."))
        ):
            index = node.lineno - 1
            lines[index] = re.sub(r"\bfrom pyvesync\b", f"from {PACKAGE}", lines[index], count=1)
        elif isinstance(node, ast.Import):
            # No such imports in the pinned source. Fail rather than silently
            # changing the binding semantics of `import pyvesync.foo`.
            if any(alias.name.split(".")[0] == "pyvesync" for alias in node.names):
                raise ValueError("Handle this new plain pyvesync import before updating")
    result = "".join(lines)
    # Logger names and module-path lookups, so this copy's debug setting and
    # logs stay separate from core's pyvesync.
    result = result.replace("logging.getLogger('pyvesync')", f"logging.getLogger('{PACKAGE}')")
    result = result.replace("pkg_root = 'pyvesync'", f"pkg_root = '{PACKAGE}'")
    ast.parse(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit", nargs="?", default=DEFAULT_COMMIT)
    commit = parser.parse_args().commit
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        parser.error("commit must be a full lowercase Git commit SHA")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required to fetch the source")
    vendor = ROOT / "custom_components/cosori_fryer/_vendor"
    with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
        checkout = Path(temporary) / "source"
        subprocess.run([git, "init", "-q", str(checkout)], check=True)  # noqa: S603
        subprocess.run(  # noqa: S603
            [git, "-C", str(checkout), "fetch", "--quiet", "--depth=1", SOURCE, commit],
            check=True,
        )
        subprocess.run(  # noqa: S603
            [git, "-C", str(checkout), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
            check=True,
        )
        staged = Path(temporary) / "vendor"
        shutil.copytree(checkout / "src/pyvesync", staged / "pyvesync")
        for path in (staged / "pyvesync").rglob("*.py"):
            path.write_text(rewrite(path.read_text()))
        shutil.copyfile(checkout / "LICENSE", staged / "LICENSE")
        (staged / "__init__.py").write_text('"""Private dependencies for the Cosori integration."""\n')
        (staged / "README.md").write_text(
            f"# Bundled pyvesync\n\nSource: {SOURCE}\n\nCommit: `{commit}`\n\n"
            f"Regenerate from the repository root: `python scripts/vendor_pyvesync.py {commit}`\n\n"
            "MIT license: [LICENSE](LICENSE). Absolute imports are rewritten to "
            f"`{PACKAGE}` to isolate this copy from Home Assistant's core VeSync integration.\n"
            "HA 2026.10.4 package_constraints.txt pins aiohttp==3.14.5 and orjson==3.11.9, "
            "and requires mashumaro>=3.17.0. All three are in HA's runtime dependency closure "
            "(mashumaro through aiohasupervisor and webrtc-models). Verified by tests/test_vendor.py.\n"
        )
        if vendor.exists():
            shutil.rmtree(vendor)
        shutil.copytree(staged, vendor)


if __name__ == "__main__":
    main()
