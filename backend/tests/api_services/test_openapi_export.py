import json
from pathlib import Path

from app.api.cli import main

READ_PATH_OPERATIONS = {
    "clients_channel_attach",
    "competitors_set_role",
    "early_signals_list",
    "hygiene_list",
    "creators_list",
    "settings_llm_get",
    "settings_llm_put",
}


def test_openapi_export_writes_the_frontend_document(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert main(["openapi", "--out", str(out)]) == 0
    spec = json.loads(out.read_text())
    operations = {op["operationId"] for methods in spec["paths"].values() for op in methods.values()}
    assert operations >= READ_PATH_OPERATIONS
    provider = spec["components"]["schemas"]["LLMProviderOut"]["properties"]
    assert "api_key" not in provider and "api_key_set" in provider
    assert "score" not in spec["components"]["schemas"]["CompetitorView"]["properties"]
