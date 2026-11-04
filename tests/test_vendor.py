"""Prove private imports in a fresh interpreter with a conflicting stock module."""

import ast
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def test_private_library_ignores_top_level_pyvesync():
    code = """
import importlib
import pkgutil
import sys
from types import ModuleType
stock = ModuleType("pyvesync")
stock.__version__ = "unrelated-stock-library"
stock.VeSync = type("StockVeSync", (), {})
sys.modules["pyvesync"] = stock
package = importlib.import_module("custom_components.cosori_fryer")
for item in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
    importlib.import_module(item.name)
from custom_components.cosori_fryer.manager import FryerManager
from custom_components.cosori_fryer._vendor.pyvesync import VeSync
assert FryerManager.__bases__ == (VeSync,)
assert VeSync is not stock.VeSync
assert sys.modules["pyvesync"] is stock
assert not any(name.startswith("pyvesync.") for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)  # noqa: S603


def test_no_absolute_stock_library_imports():
    for path in (ROOT / "custom_components/cosori_fryer").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert (node.module or "").split(".")[0] != "pyvesync", path
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] != "pyvesync" for alias in node.names), path


def test_vendor_dependencies_are_in_ha_runtime_constraints():
    ha = distribution("homeassistant")
    constraints = Path(ha.locate_file("homeassistant/package_constraints.txt")).read_text()
    names = {
        canonicalize_name(Requirement(line).name)
        for line in constraints.splitlines()
        if line and not line.startswith(("#", "-"))
    }
    assert {"aiohttp", "mashumaro", "orjson"} <= names
    # Check actual runtime dependency closure, rather than accepting test-only installs.
    pending = ["homeassistant"]
    seen = set()
    while pending:
        name = canonicalize_name(pending.pop())
        if name in seen:
            continue
        seen.add(name)
        for text in distribution(name).requires or []:
            requirement = Requirement(text)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    assert {"aiohttp", "mashumaro", "orjson"} <= seen
