import pytest


async def test_health_crosses_web_database_and_worker(client, worker):
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"web": "ok", "database": "ok", "worker": "ok"},
    }


@pytest.mark.settings(health_worker_timeout=0.3)
async def test_health_degrades_when_no_worker_answers(client):
    response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"web": "ok", "database": "ok", "worker": "timeout"},
    }
