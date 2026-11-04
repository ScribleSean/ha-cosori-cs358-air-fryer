"""Validate repository contracts that do not require a live hub or CI runner."""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/cosori_fryer"


def test_metadata():
    strings = json.loads((COMPONENT / "strings.json").read_text())
    assert strings == json.loads((COMPONENT / "translations/en.json").read_text())
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["integration_type"] == "device"
    assert manifest["requirements"] == []
    assert json.loads((ROOT / "hacs.json").read_text())["name"]
    assert json.loads((COMPONENT / "icons.json").read_text())["entity"]
    assert (
        yaml.safe_load((COMPONENT / "quality_scale.yaml").read_text())["rules"]["reauthentication-flow"]
        == "done"
    )


def test_service_contract():
    fields = yaml.safe_load((COMPONENT / "services.yaml").read_text())["start_cook"]["fields"]
    for field in ("temperature", "minutes", "preheat"):
        assert fields[field]["required"]


def test_ci_pins_and_history():
    uses = []
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        data = yaml.safe_load(path.read_text())
        for job in data["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])
                    uses.append(step["uses"])
                if path.stem == "gitleaks" and step.get("uses", "").startswith("actions/checkout@"):
                    assert step["with"]["fetch-depth"] == 0
    assert any("hassfest" in action for action in uses)
    assert any("hacs/action" in action for action in uses)


def test_dashboard_preheat_visibility():
    cards = yaml.safe_load((ROOT / "examples/dashboard.yaml").read_text())["cards"]
    assert cards[1]["conditions"][0]["state"] == "preheat_end"
    assert cards[0]["entities"][2]["format"] == "relative"
