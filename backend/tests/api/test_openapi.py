from app.api.app import create_app
from tests.pipeline.support import fast_settings

CATALOGUE = {
    ("post", "/api/v1/clients"),
    ("get", "/api/v1/clients/{client_id}/recommendations"),
    ("put", "/api/v1/recommendations/{recommendation_id}/feedback"),
    ("get", "/api/v1/runs/{run_id}/events"),
    ("get", "/api/v1/runs/{run_id}/events.json"),
    ("post", "/api/v1/runs/{run_id}/stream-token"),
    ("get", "/api/v1/clients/{client_id}/events"),
    ("post", "/api/v1/clients/{client_id}/confirm-setup"),
    ("get", "/api/v1/integrations/youtube/callback"),
    ("get", "/api/v1/ops/queues"),
    ("get", "/healthz"),
    ("get", "/readyz"),
}


def test_openapi_has_stable_unique_operation_ids_and_problem_responses() -> None:
    spec = create_app(fast_settings()).openapi()
    operations = [
        (method, path, operation)
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
    ]
    operation_ids = [operation["operationId"] for _, _, operation in operations]
    assert len(operation_ids) == len(set(operation_ids))
    assert {(method, path) for method, path, _ in operations} >= CATALOGUE
    assert all(
        path.startswith("/api/v1/") for _, path, _ in operations if path not in {"/healthz", "/readyz"}
    )
    clients_create = spec["paths"]["/api/v1/clients"]["post"]
    assert clients_create["operationId"] == "clients_create"
    assert "application/problem+json" in clients_create["responses"]["422"]["content"]
    assert "Problem" in spec["components"]["schemas"]


def test_confirm_setup_documents_the_accepted_run_and_the_joined_one() -> None:
    spec = create_app(fast_settings()).openapi()
    responses = spec["paths"]["/api/v1/clients/{client_id}/confirm-setup"]["post"]["responses"]
    for status in ("200", "202"):
        schema = responses[status]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ConfirmSetupOut"}
