from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "config" / "operational_function_catalog.yaml"


def _load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def test_operational_function_catalog_has_28_unique_entries():
    catalog = _load_yaml(CATALOG_PATH)
    functions = catalog["functions"]

    assert len(functions) == 28
    assert [item["function_id"] for item in functions] == [
        f"KC-{index:02d}" for index in range(1, 29)
    ]
    assert len({item["function_code"] for item in functions}) == 28


def test_algorithm_card_mappings_reference_the_canonical_catalog():
    functions = _load_yaml(CATALOG_PATH)["functions"]
    catalog_by_id = {item["function_id"]: item for item in functions}
    allowed_roles = {"primary", "supporting", "orchestrator", "handoff"}
    allowed_coverage = {"full", "partial", "advisory", "external_handoff"}
    covered_ids = set()

    for card_path in sorted((ROOT / "examples").glob("*/1.0.0/algorithm_card.yaml")):
        card = _load_yaml(card_path)
        for mapping in card.get("operational_functions", []):
            function_id = mapping["function_id"]
            assert function_id in catalog_by_id, f"{card_path}: unknown {function_id}"
            canonical = catalog_by_id[function_id]
            assert mapping["function_code"] == canonical["function_code"], card_path
            assert mapping["function_name"] == canonical["function_name"], card_path
            assert mapping.get("role", "primary") in allowed_roles, card_path
            assert mapping.get("coverage_level", "full") in allowed_coverage, card_path
            covered_ids.add(function_id)

    assert covered_ids == set(catalog_by_id), (
        "Every frontend function point must have at least one declared algorithm mapping; "
        f"missing={sorted(set(catalog_by_id) - covered_ids)}"
    )


def test_attack_function_is_only_an_external_handoff():
    mappings = []
    for card_path in sorted((ROOT / "examples").glob("*/1.0.0/algorithm_card.yaml")):
        card = _load_yaml(card_path)
        mappings.extend(
            mapping
            for mapping in card.get("operational_functions", [])
            if mapping["function_id"] == "KC-23"
        )

    assert mappings
    assert all(mapping["role"] == "handoff" for mapping in mappings)
    assert all(
        mapping["coverage_level"] == "external_handoff" for mapping in mappings
    )
