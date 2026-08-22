def test_knowledge_answer_is_grounded_and_empty_search_does_not_invent(client, admin_headers):
    data = "第十条 消防控制室应当落实值班制度。值班人员应当在岗。".encode()
    uploaded = client.post(
        "/api/v1/knowledge",
        data={"title": "内部检查规定", "category": "internal_policy"},
        files={"file": ("rule.txt", data, "text/plain")},
        headers=admin_headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    grounded = client.post(
        "/api/v1/knowledge/search",
        json={"query": "消防控制室值班制度", "limit": 5},
        headers=admin_headers,
    )
    assert grounded.status_code == 200
    assert grounded.json()["citations"]
    assert "消防控制室" in grounded.json()["citations"][0]["quote"]
    assert grounded.json()["citations"][0]["category"] == "internal_policy"

    empty = client.post(
        "/api/v1/knowledge/search",
        json={"query": "完全无关的量子航天条款", "limit": 5},
        headers=admin_headers,
    ).json()
    assert empty["citations"] == []
    assert "未找到可靠依据" in empty["answer"]
