from pathlib import Path

import yaml

from cluster_forge.models import Inventory


def load_inventory(path: Path | None = None) -> Inventory:
    if path is None:
        path = Path(__file__).resolve().parent.parent.parent / "servers.yaml"
    raw = yaml.safe_load(path.read_text())
    return Inventory.model_validate(raw)
