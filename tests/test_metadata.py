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


def test_dashboard_card_is_built_in():
    card = yaml.safe_load((ROOT / "examples/dashboard.yaml").read_text())
    readme = (ROOT / "README.md").read_text()
    assert card["type"] == "tile"
    assert card["entity"].startswith("climate.")
    assert card["features"] == [{"type": "target-temperature"}]
    attributes = json.loads((COMPONENT / "strings.json").read_text())["entity"]["climate"]["fryer"]
    for content in card["state_content"]:
        assert content in attributes["state_attributes"]
    readme_card = readme.split("```yaml\ntype: tile\n", 1)[1].split("```", 1)[0]
    assert yaml.safe_load("type: tile\n" + readme_card) == card
