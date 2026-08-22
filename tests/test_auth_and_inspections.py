from .conftest import login


def test_inspection_access_is_limited_to_owner_members_and_admin(client, inspector_headers):
    created = client.post(
        "/api/v1/inspections",
        json={"title": "商场检查", "unit_name": "示例商场", "dynamic_attributes": {}},
        headers=inspector_headers,
    )
    assert created.status_code == 201
    inspection_id = created.json()["id"]

    client.post("/api/v1/auth/logout", headers=inspector_headers)
    outsider_headers = login(client, "outsider", "outside-password")
    denied = client.get(f"/api/v1/inspections/{inspection_id}")
    assert denied.status_code == 404

    client.post("/api/v1/auth/logout", headers=outsider_headers)
    login(client, "admin", "admin-password")
    allowed = client.get(f"/api/v1/inspections/{inspection_id}")
    assert allowed.status_code == 200


def test_optimistic_lock_rejects_stale_inspection_update(client, inspector_headers):
    inspection = client.post(
        "/api/v1/inspections", json={"title": "初始检查"}, headers=inspector_headers
    ).json()
    first = client.patch(
        f"/api/v1/inspections/{inspection['id']}",
        json={"title": "第一次修改", "row_version": inspection["row_version"]},
        headers=inspector_headers,
    )
    assert first.status_code == 200
    stale = client.patch(
        f"/api/v1/inspections/{inspection['id']}",
        json={"title": "过期修改", "row_version": inspection["row_version"]},
        headers=inspector_headers,
    )
    assert stale.status_code == 409


def test_cookie_authenticated_mutation_requires_csrf_header(client, inspector_headers):
    rejected = client.post("/api/v1/inspections", json={"title": "缺少 CSRF"})
    assert rejected.status_code == 403
    accepted = client.post(
        "/api/v1/inspections", json={"title": "带 CSRF"}, headers=inspector_headers
    )
    assert accepted.status_code == 201
