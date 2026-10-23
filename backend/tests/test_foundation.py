from concurrent.futures import ThreadPoolExecutor

PAYLOAD = {"name": "芝士浓奶铁种草", "brandName": "麦咖啡", "platforms": ["douyin", "xiaohongshu"]}


def create(client, payload=None, key="test-key-0001"):
    return client.post("/api/v1/projects", json=payload or PAYLOAD, headers={"Idempotency-Key": key})


def test_health_context_and_no_fake_statistics(client):
    assert client.get("/health").json()["status"] == "ok"
    context = client.get("/api/v1/context").json()
    assert context["data"]["mode"] == "demo"
    assert context["requestId"]
    stats = client.get("/api/v1/dashboard").json()["data"]
    assert stats["activeProjects"] == 0
    assert stats["pendingAuditTasks"] is None
    assert not stats["taskStatisticsAvailable"]


def test_create_persists_project_and_audit(client):
    response = create(client)
    assert response.status_code == 201
    project = response.json()["data"]
    assert project["taskCount"] == 0
    assert project["mode"] == "demo"
    assert project["brand"]["name"] == "麦咖啡"
    detail = client.get(f'/api/v1/projects/{project["id"]}').json()["data"]
    assert detail == {**project, "briefs": []}  # 详情=Project 字段+briefs 投影；列表/创建响应不带该字段
    assert client.get("/api/v1/dashboard").json()["data"]["activeProjects"] == 1
    from app.core.database import connection
    with connection(client.app.state.settings.database_path) as db:
        assert db.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1


def test_idempotency_and_payload_conflict(client):
    first = create(client).json()["data"]
    assert create(client).json()["data"]["id"] == first["id"]
    changed = create(client, {**PAYLOAD, "name": "另一个项目"})
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert client.get("/api/v1/projects").json()["total"] == 1


def test_concurrent_idempotency(client):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: create(client, key="same-parallel-key"), range(4)))
    assert all(r.status_code == 201 for r in results)
    assert len({r.json()["data"]["id"] for r in results}) == 1


def test_duplicate_project_rolls_back_new_write(client):
    create(client)
    duplicate = create(client, key="test-key-0002")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PROJECT_EXISTS"
    assert client.get("/api/v1/projects").json()["total"] == 1


def test_filters_pagination_and_literal_search(client):
    create(client)
    create(client, {"name": "B站评测", "brandName": "另一个品牌", "platforms": ["bilibili"]}, "test-key-0002")
    query = client.get("/api/v1/projects", params={"q": "奶铁", "platform": "douyin", "pageSize": 10}).json()
    assert query["total"] == 1 and query["pageSize"] == 10
    assert client.get("/api/v1/projects", params={"q": "%"}).json()["total"] == 0
    assert client.get("/api/v1/projects", params={"page": 2}).json()["data"] == []
    assert client.get("/api/v1/projects?pageSize=7").status_code == 400


def test_whitespace_platform_and_brand_validation(client):
    for payload in [{**PAYLOAD, "name": "   "}, {**PAYLOAD, "platforms": []},
                    {**PAYLOAD, "platforms": ["douyin", "douyin"]}, {**PAYLOAD, "brandId": "extra"}]:
        response = create(client, payload)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert client.get("/api/v1/projects").json()["total"] == 0


def test_missing_project_and_module_stubs_fail_explicitly(client):
    missing = client.get("/api/v1/projects/missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    # 视频列表和模型设置已经实现；未实现的人工模块仍明确返回501。
    assert client.get('/api/v1/tasks/new/videos').status_code == 404
    assert client.get('/api/v1/settings/models').status_code == 200
    for endpoint in ["/api/v1/human-reviews"]:
        response = client.get(endpoint)
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "MODULE_NOT_IMPLEMENTED"
        assert response.headers["x-request-id"] == response.json()["requestId"]


def test_database_migrations_are_repeatable(client):
    from app.core.database import migrate
    create(client)
    migrate(client.app.state.settings.database_path)
    assert client.get("/api/v1/projects").json()["total"] == 1


def test_openapi_includes_shared_states(client):
    schema = client.get("/openapi.json").json()
    assert "ReviewSnapshot" in schema["components"]["schemas"]
    assert "incomplete" in schema["components"]["schemas"]["Conclusion"]["enum"]
