"""Validate the synthetic probe and exercise the real importer without a model.

Evaluation truth is checked separately. Only ordinary CSV/schema files are
passed to Dataset; its temporary DuckDB is removed after inspection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

import generate_fruit_probe_data as generator


PROJECT = Path(__file__).resolve().parents[1]


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _separate(left, right):
    """Reject both nested outputs and outputs that replace an input ancestor."""
    return left != right and left not in right.parents and right not in left.parents


def inspect_dataset(root, evaluation=None, report=None):
    root = Path(root).resolve()
    evaluation = (Path(evaluation).resolve() if evaluation is not None
                  else root.with_name(root.name + "_evaluation"))
    report = (Path(report).resolve() if report is not None
              else evaluation / "import_report.json")
    if not _separate(root, evaluation) or not _separate(root, report):
        raise ValueError("evaluation and report must be outside the input directory")
    if report.exists():
        raise FileExistsError(f"Report already exists: {report}")
    manifest_path = root / "synthetic_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("synthetic") is not True
            or manifest.get("generator") != generator.VERSION):
        raise ValueError("Only this generator's explicitly synthetic probe is accepted")
    if manifest.get("table_count") != 23:
        raise ValueError("The probe must contain 23 tables")
    # Symlinks could cause an ostensibly synthetic input to import outside data.
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("Synthetic input must not contain symlinks")
    before = {str(path.relative_to(root)): _hash(path) for path in paths}
    started = time.monotonic()
    validation = generator.validate(root, evaluation)
    validated_seconds = time.monotonic() - started

    # Deliberately do not import pipeline, load a runtime config, or load dotenv.
    sys.path.insert(0, str(PROJECT / "code"))
    from ontology_r2.storage import Dataset

    table_counts, column_counts = {}, {}
    totals = {"null_cells": 0, "empty_string_cells": 0,
              "whitespace_only_cells": 0, "usable_cells": 0,
              "columns_with_json_prefix": 0, "columns_mixing_json_prefix_and_other": 0,
              "columns_mixing_numeric_and_nonnumeric": 0}
    imported_at = time.monotonic()
    with TemporaryDirectory(prefix="fruit-probe-import-") as work:
        data = Dataset(root, Path(work), memory="512MB",
                       profiling_config={"input_scope": "complete_export"})
        try:
            if len(data.tables) != 23:
                raise ValueError("Imported table count differs from the manifest")
            for name, table in data.tables.items():
                rows, columns = table["rows"], table["column_names"]
                if rows != manifest["counts"].get(table["table_name"]):
                    raise ValueError(f"Imported row count mismatch: {name}")
                profiles = table["profiles"]
                if {item["column"] for item in profiles} != set(columns) or len(profiles) != len(columns):
                    raise ValueError(f"Incomplete field profiles: {name}")
                for item in profiles:
                    if item["row_count"] != rows or item["scan_scope"] != "full_input":
                        raise ValueError(f"Incomplete profile population: {name}")
                    if any(stat["rows_scanned"] != rows or stat["status"] != "complete"
                           for stat in item["statistics"].values()):
                        raise ValueError(f"Incomplete aggregate scan: {name}")
                    if sum(item[key] for key in ("null_count", "empty_count", "whitespace_only_count", "usable_count")) != rows:
                        raise ValueError(f"Null/usable partition mismatch: {name}")
                    totals["null_cells"] += item["null_count"]
                    totals["empty_string_cells"] += item["empty_count"]
                    totals["whitespace_only_cells"] += item["whitespace_only_count"]
                    totals["usable_cells"] += item["usable_count"]
                    totals["columns_with_json_prefix"] += int(item["json_start_count"] > 0)
                    totals["columns_mixing_json_prefix_and_other"] += int(0 < item["json_start_count"] < item["usable_count"])
                    totals["columns_mixing_numeric_and_nonnumeric"] += int(0 < item["numeric_shape_count"] < item["usable_count"])
                table_counts[name] = rows
                column_counts[name] = len(columns)
            if sum(table_counts.values()) != manifest["row_count"]:
                raise ValueError("Imported total row count differs from the manifest")
            expected_files = {
                str(path.relative_to(root)) for path in paths
                if ((path.parent == root / "data" and path.suffix == ".csv")
                    or (path.parent in {root / "schema" / part for part in
                                        ("tables", "constraints", "foreign_keys")}
                        and path.suffix == ".yaml"))}
            if set(data.files) != expected_files:
                raise ValueError("Dataset imported files outside the expected CSV/schema contract")
            if any(not _separate((root / path).resolve(), evaluation) for path in data.files):
                raise ValueError("Evaluation files entered the Dataset source manifest")
            snapshot_id, imported_files = data.snapshot_id, dict(data.files)
        finally:
            data.close()
    import_seconds = time.monotonic() - imported_at
    after_paths = sorted(path for path in root.rglob("*") if path.is_file())
    after = {str(path.relative_to(root)): _hash(path) for path in after_paths}
    if before != after:
        raise ValueError("Input files changed during inspection")
    code_paths = [Path(__file__).resolve(), Path(generator.__file__).resolve(),
                  PROJECT / "scripts/generate_fruit_data.py",
                  PROJECT / "code/ontology_r2/storage.py",
                  PROJECT / "code/ontology_r2/profiling.py"]
    result = {
        "status": "passed", "synthetic": True, "generator": generator.VERSION,
        "input_root": str(root), "evaluation_root": str(evaluation),
        "scope": "Fixture consistency and real import/full-input aggregate profiles only; ontology extraction quality not evaluated",
        "llm_calls": 0, "runtime_config_loaded": False, "dotenv_loaded": False,
        "table_count": len(table_counts), "row_count": sum(table_counts.values()),
        "column_count": sum(column_counts.values()),
        "logical_cells": sum(table_counts[name] * column_counts[name] for name in table_counts),
        "input_total_bytes": sum(path.stat().st_size for path in paths),
        "imported_csv_schema_bytes": sum((root / path).stat().st_size for path in imported_files),
        "snapshot_id": snapshot_id, "table_rows": table_counts, "table_columns": column_counts,
        "profile_totals": totals,
        "profile_notes": ["Shape counters are lexical, not semantic type decisions",
                          "JSON prefix does not prove valid JSON",
                          "Aggregate counts cover all imported rows; distinct samples remain bounded"],
        "input_unchanged": True, "dataset_source_files": imported_files,
        "evaluation_isolation": {
            "oracle_used_by_fixture_validator": True,
            "oracle_passed_to_dataset": False,
            "dataset_source_manifest_excludes_evaluation": True,
            "dataset_source_manifest_contains_only_csv_and_schema": True},
        "code_sha256": {str(path.relative_to(PROJECT)): _hash(path) for path in code_paths},
        "fixture_validation": validation,
        "duration_seconds": {"fixture_validation": round(validated_seconds, 3),
                             "import_and_profiles": round(import_seconds, 3),
                             "total": round(time.monotonic() - started, 3)},
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = inspect_dataset(args.input, args.evaluation, args.report)
    print(json.dumps({key: result[key] for key in (
        "status", "table_count", "row_count", "column_count", "input_total_bytes",
        "snapshot_id", "llm_calls", "duration_seconds")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
