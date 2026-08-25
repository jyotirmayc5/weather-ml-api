import json
import pathlib

repo_root = pathlib.Path(__file__).resolve().parents[3]
export_path = repo_root / "archive" / "n8n_export.json"
out_dir = pathlib.Path(__file__).resolve().parent

TARGETS = {
    "Normalize NWS Data": "normalize_nws_data.js",
    "Forecast HIGH": "forecast_high.js",
    "Code in JavaScript": "eod_actuals_pressure.js",
    "Convert to F": "convert_to_f.js",
    "Return Observations": "return_observations.js",
}

with open(export_path, encoding="utf-8") as f:
    export = json.load(f)

found = {}
for node in export["nodes"]:
    name = node.get("name")
    if name in TARGETS:
        js_code = node["parameters"]["jsCode"]
        out_path = out_dir / TARGETS[name]
        out_path.write_text(js_code, encoding="utf-8")
        found[name] = out_path.name

for name in TARGETS:
    status = found.get(name, "NOT FOUND")
    print(f"{name} -> {status}")
