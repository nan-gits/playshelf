def test_dashboard_page(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Playshelf</title>" in response.text
    assert "{{" not in response.text  # every placeholder was filled
    assert '<script src="/static/app.js">' in response.text


def test_static_assets(client) -> None:
    for path, media_type in (
        ("/static/app.js", "text/javascript"),
        ("/static/style.css", "text/css"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] == media_type


def test_dashboard_has_import_ui(client) -> None:
    html = client.get("/").text
    for marker in ('id="upload"', 'id="upload-input"', 'id="imports"', 'id="drop-zone"'):
        assert marker in html
    assert 'id="history"' in html
