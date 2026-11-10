from __future__ import annotations

import base64
import concurrent.futures

import pytest

from lager import api as api_module
from lager.api import App
from lager.database import Database

from .conftest import READ_KEY, WRITE_KEY, Client, make_settings

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64).decode()
HTML = base64.b64encode(b"<html><script>alert(1)</script></html>").decode()


def new_component(client: Client, **overrides) -> int:
    body = {"name": "BC547", "category": "Transistorer", "quantity": 10, "location": "A1"}
    body.update(overrides)
    res = client.post("/components", body)
    assert res.status == 201, res.body
    return res.json()["id"]


# ------------------------------------------------------------------ auth
class TestAuth:
    def test_health_is_public_and_reveals_nothing(self, client):
        res = client.get("/", key=None)
        assert res.status == 200
        assert res.json() == {"status": "ok", "service": "lager-api"}

    @pytest.mark.parametrize("method,path", [
        ("GET", "/components"), ("GET", "/components/1"), ("GET", "/components/1/image"),
        ("POST", "/components"), ("PUT", "/components/1"), ("PATCH", "/components/1/quantity"),
        ("DELETE", "/components/1"), ("POST", "/analyze-base64"), ("GET", "/log"),
        ("GET", "/ha/summary"), ("POST", "/mcp"), ("GET", "/me"),
    ])
    def test_every_endpoint_requires_a_key(self, client, method, path):
        res = client.request(method, path, key=None)
        assert res.status == 401
        assert "www-authenticate" in res.headers

    def test_wrong_key_rejected(self, client):
        assert client.get("/components", key="x" * 40).status == 401

    def test_bearer_and_x_api_key_both_work(self, client):
        assert client.get("/components").status == 200
        res = client.get("/components", key=None, headers={"X-API-Key": WRITE_KEY})
        assert res.status == 200

    def test_read_key_can_read_but_not_write(self, client):
        assert client.get("/components", key=READ_KEY).status == 200
        assert client.get("/ha/summary", key=READ_KEY).status == 200
        assert client.post("/components", {"name": "x", "category": "Annet"}, key=READ_KEY).status == 403
        assert client.post("/analyze-base64", {"image": PNG}, key=READ_KEY).status == 403

    def test_query_key_only_accepted_for_mcp(self, client):
        assert client.get(f"/components?key={WRITE_KEY}", key=None).status == 401
        res = client.post(f"/mcp?key={READ_KEY}", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, key=None)
        assert res.status == 200

    def test_me_reports_scope(self, client):
        assert client.get("/me").json()["scope"] == "write"
        assert client.get("/me", key=READ_KEY).json()["scope"] == "read"

    def test_fails_closed_without_configured_key(self, db):
        app = App(make_settings(api_key="", read_key=""), db)
        res = Client(app).get("/components", key="anything")
        assert res.status == 503

    def test_fails_closed_with_short_key(self, db):
        app = App(make_settings(api_key="short"), db)
        assert Client(app).get("/components", key="short").status == 503

    def test_api_prefix_is_stripped(self, client):
        assert client.get("/api/components").status == 200
        assert client.get("/api").status == 200


# ------------------------------------------------------------------ CRUD
class TestComponents:
    def test_create_list_get(self, client):
        cid = new_component(client, image=PNG)
        items = client.get("/components").json()["components"]
        assert [c["id"] for c in items] == [cid]
        assert items[0]["has_image"] is True
        assert items[0]["created_at"].endswith("Z")  # UTC, unambiguous for clients
        one = client.get(f"/components/{cid}").json()
        assert one["name"] == "BC547" and "image" not in one

    def test_validation(self, client):
        assert client.post("/components", {"category": "Annet"}).status == 422
        assert client.post("/components", {"name": "x", "category": "Annet", "quantity": -1}).status == 422
        assert client.post("/components", {"name": "x", "category": "Annet", "quantity": True}).status == 422
        assert client.post("/components", {"name": "x" * 300, "category": "Annet"}).status == 422
        assert client.post("/components", {"name": 5, "category": "Annet"}).status == 422
        assert client.post("/components", raw=b"not json").status == 400
        assert client.post("/components", raw=b"[1,2]").status == 400

    def test_html_disguised_as_image_is_rejected(self, client):
        res = client.post("/components", {"name": "x", "category": "Annet", "image": HTML,
                                          "mime_type": "text/html"})
        assert res.status == 415

    def test_image_is_served_with_sniffed_type_and_etag(self, client):
        cid = new_component(client, image=JPEG, mime_type="text/html")
        res = client.get(f"/components/{cid}/image")
        assert res.status == 200
        assert res.headers["content-type"] == "image/jpeg"
        assert res.headers["x-content-type-options"] == "nosniff"
        etag = res.headers["etag"]
        again = client.get(f"/components/{cid}/image", headers={"If-None-Match": etag})
        assert again.status == 304 and again.body == b""

    def test_missing_image_404(self, client):
        cid = new_component(client)
        assert client.get(f"/components/{cid}/image").status == 404

    def test_too_large_body(self, client):
        big = "A" * (3 * 1024 * 1024)
        assert client.post("/components", raw=big.encode()).status == 413

    def test_update(self, client):
        cid = new_component(client)
        res = client.put(f"/components/{cid}", {"name": "BC548", "category": "Transistorer",
                                                "quantity": 4, "specs": "NPN"})
        assert res.status == 200
        assert res.json()["component"]["name"] == "BC548"
        assert client.put("/components/999999", {"name": "a", "category": "b"}).status == 404

    def test_quantity_absolute_and_delta(self, client):
        cid = new_component(client, quantity=5)
        assert client.patch(f"/components/{cid}/quantity", {"delta": 2}).json()["quantity"] == 7
        assert client.patch(f"/components/{cid}/quantity", {"delta": -100}).json()["quantity"] == 0
        assert client.patch(f"/components/{cid}/quantity", {"quantity": 3}).json()["quantity"] == 3
        assert client.patch(f"/components/{cid}/quantity", {"quantity": 1, "delta": 1}).status == 422
        assert client.patch(f"/components/{cid}/quantity", {}).status == 422
        assert client.patch("/components/424242/quantity", {"delta": 1}).status == 404

    def test_concurrent_deltas_are_not_lost(self, client, settings):
        cid = new_component(client, quantity=0)

        def bump(_):
            c = Client(App(settings, Database(settings)))
            return c.patch(f"/components/{cid}/quantity", {"delta": 1}).status

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(bump, range(20)))
        assert statuses == [200] * 20
        assert client.get(f"/components/{cid}").json()["quantity"] == 20

    def test_delete(self, client):
        cid = new_component(client)
        assert client.delete(f"/components/{cid}").status == 204
        assert client.delete(f"/components/{cid}").status == 404

    def test_bad_ids(self, client):
        assert client.get("/components/abc").status == 404
        assert client.get("/components/0").status == 404
        assert client.get("/components/-1").status == 404

    def test_method_not_allowed(self, client):
        res = client.request("PATCH", "/components")
        assert res.status == 405 and "GET" in res.headers["allow"]

    def test_activity_log_is_written_in_same_transaction(self, client):
        cid = new_component(client, quantity=1)
        client.patch(f"/components/{cid}/quantity", {"delta": 1})
        client.patch(f"/components/{cid}/quantity", {"delta": 0})  # no-op: not logged
        client.delete(f"/components/{cid}")
        actions = [e["action"] for e in client.get("/log").json()["log"]]
        assert actions == ["slettet", "antall_endret", "lagt_til"]
        assert client.get("/log?limit=1").json()["log"][0]["action"] == "slettet"
        assert client.get("/log?limit=0").status == 422


# ------------------------------------------------------------------ errors
class TestErrors:
    def test_internal_errors_do_not_leak_details(self, client, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("Access denied for user 'secret'@'db.internal'")
        monkeypatch.setattr(client.app.db, "list_components", boom)
        res = client.get("/components")
        assert res.status == 500
        assert b"secret" not in res.body and b"db.internal" not in res.body

    def test_security_headers_present(self, client):
        res = client.get("/components")
        assert res.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in res.headers["content-security-policy"]
        assert res.headers["cache-control"] == "no-store"

    def test_no_cors_by_default(self, client):
        res = client.get("/components", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in res.headers

    def test_cors_allowlist(self, db):
        c = Client(App(make_settings(cors_origins=("https://app.example",)), db))
        ok = c.get("/components", headers={"Origin": "https://app.example"})
        assert ok.headers["access-control-allow-origin"] == "https://app.example"
        bad = c.get("/components", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in bad.headers
        pre = c.request("OPTIONS", "/components", key=None, headers={"Origin": "https://app.example"})
        assert pre.status == 204 and "Authorization" in pre.headers["access-control-allow-headers"]


# ------------------------------------------------------------------ AI
class TestAnalyze:
    @pytest.fixture(autouse=True)
    def fake_ai(self, monkeypatch):
        calls = []

        def fake(settings, image, media_type):
            calls.append(media_type)
            return {"name": "BC547", "category": "Transistorer", "description": "",
                    "specs": "NPN", "confidence": "high"}
        monkeypatch.setattr(api_module, "identify_component", fake)
        return calls

    def test_analyze(self, client, fake_ai):
        res = client.post("/analyze-base64", {"image": PNG, "mime_type": "image/jpeg"})
        assert res.status == 200 and res.json()["name"] == "BC547"
        assert fake_ai == ["image/png"]  # sniffed, not the client's claim

    def test_daily_limit(self, client, fake_ai):
        for _ in range(3):
            assert client.post("/analyze-base64", {"image": PNG}).status == 200
        res = client.post("/analyze-base64", {"image": PNG})
        assert res.status == 429 and "retry-after" in res.headers
        assert len(fake_ai) == 3
        assert client.app.db.ai_calls_today() == 3

    def test_invalid_image_costs_nothing(self, client, fake_ai):
        assert client.post("/analyze-base64", {"image": HTML}).status == 415
        assert client.post("/analyze-base64", {"image": "%%%"}).status == 422
        assert fake_ai == [] and client.app.db.ai_calls_today() == 0

    def test_ai_disabled(self, db):
        c = Client(App(make_settings(anthropic_api_key=""), db))
        assert c.post("/analyze-base64", {"image": PNG}).status == 503


# ------------------------------------------------------------------ HA + MCP
class TestIntegrations:
    def test_ha_summary(self, client):
        new_component(client, name="ESP32", category="Mikrokontrollere", quantity=2)
        new_component(client, name="10k", category="Motstander", quantity=100)
        data = client.get("/ha/summary", key=READ_KEY).json()
        assert data["total_components"] == 2 and data["total_quantity"] == 102
        assert data["low_stock_count"] == 1
        assert data["inventory"]["ESP32"] == 2

    def rpc(self, client, method, params=None, key=READ_KEY, id_=1):
        msg = {"jsonrpc": "2.0", "method": method}
        if id_ is not None:
            msg["id"] = id_
        if params is not None:
            msg["params"] = params
        return client.post("/mcp", msg, key=key)

    def test_initialize_and_list(self, client):
        res = self.rpc(client, "initialize", {"protocolVersion": "2025-03-26"}).json()
        assert res["result"]["protocolVersion"] == "2025-03-26"
        tools = self.rpc(client, "tools/list").json()["result"]["tools"]
        assert {t["name"] for t in tools} >= {"list_components", "search_components"}

    def test_notification_returns_202(self, client):
        res = self.rpc(client, "notifications/initialized", id_=None)
        assert res.status == 202 and res.body == b""

    def test_tools(self, client):
        new_component(client, name="ESP32-WROOM", category="Mikrokontrollere", quantity=2, specs="WiFi BT")
        import json as _json
        call = lambda name, args: _json.loads(self.rpc(client, "tools/call", {"name": name, "arguments": args})
                                              .json()["result"]["content"][0]["text"])
        assert call("search_components", {"query": "esp32 wifi"})["count"] == 1
        assert call("search_components", {"query": "esp32 zigbee"})["count"] == 0
        assert call("get_low_stock", {"threshold": 5})["count"] == 1
        assert call("get_summary", {})["total_quantity"] == 2
        assert call("list_components", {"category": "mikrokontrollere"})["count"] == 1
        assert call("get_activity_log", {"component_name": "esp"})["count"] == 1

    def test_tool_errors(self, client):
        bad = self.rpc(client, "tools/call", {"name": "get_low_stock", "arguments": {"threshold": "x"}}).json()
        assert bad["result"]["isError"] is True
        unknown = self.rpc(client, "tools/call", {"name": "drop_tables", "arguments": {}}).json()
        assert unknown["error"]["code"] == -32602
        assert self.rpc(client, "nope").json()["error"]["code"] == -32601

    def test_parse_error_and_batch(self, client):
        assert client.post("/mcp", raw=b"{oops", key=READ_KEY).json()["error"]["code"] == -32700
        batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        res = client.post("/mcp", batch, key=READ_KEY).json()
        assert res == [{"jsonrpc": "2.0", "id": 1, "result": {}}]

    def test_get_mcp_is_405(self, client):
        assert client.get("/mcp", key=READ_KEY).status == 405


# ------------------------------------------------------------------ thumbnails
class TestThumbnails:
    @staticmethod
    def real_jpeg(size=(1600, 1200)):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", size, (200, 30, 30)).save(buf, "JPEG", quality=95)
        return base64.b64encode(buf.getvalue()).decode(), len(buf.getvalue())

    def test_thumbnail_is_small_and_cached(self, client):
        from PIL import Image
        import io
        b64, full_len = self.real_jpeg()
        cid = new_component(client, image=b64)
        res = client.get(f"/components/{cid}/image?size=thumb")
        assert res.status == 200 and res.headers["content-type"] == "image/jpeg"
        assert len(res.body) < full_len
        assert max(Image.open(io.BytesIO(res.body)).size) <= 256
        assert client.app.db.get_thumbnail(cid) == res.body

    def test_thumbnail_generated_lazily_for_old_rows(self, client):
        b64, _ = self.real_jpeg()
        cid = new_component(client, image=b64)
        with client.app.db._cursor(dictionary=False) as cur:  # simulate a pre-thumbnail row
            cur.execute("UPDATE components SET thumbnail = NULL WHERE id = %s", (cid,))
        before = client.get(f"/components/{cid}").json()["updated_at"]
        assert client.get(f"/components/{cid}/image?size=thumb").status == 200
        assert client.app.db.get_thumbnail(cid) is not None
        assert client.get(f"/components/{cid}").json()["updated_at"] == before

    def test_unreadable_image_falls_back_to_full(self, client):
        cid = new_component(client, image=PNG)  # valid magic bytes, not a decodable PNG
        res = client.get(f"/components/{cid}/image?size=thumb")
        assert res.status == 200 and res.body == base64.b64decode(PNG)

    def test_thumbnail_404_without_image(self, client):
        cid = new_component(client)
        assert client.get(f"/components/{cid}/image?size=thumb").status == 404


# ------------------------------------------------------------------ shrinking
class TestShrinking:
    def test_large_upload_is_shrunk_and_rotated_jpeg(self, client):
        import io
        from PIL import Image
        b64, full_len = TestThumbnails.real_jpeg((4000, 3000))
        cid = new_component(client, image=b64)
        res = client.get(f"/components/{cid}/image")
        img = Image.open(io.BytesIO(res.body))
        assert max(img.size) == 1600 and img.format == "JPEG"
        assert len(res.body) < full_len

    def test_small_upload_is_untouched(self, client):
        b64, _ = TestThumbnails.real_jpeg((1280, 960))
        cid = new_component(client, image=b64)
        assert client.get(f"/components/{cid}/image").body == base64.b64decode(b64)

    def test_png_with_transparency_becomes_jpeg(self, client):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGBA", (3000, 2000), (255, 0, 0, 0)).save(buf, "PNG")
        cid = new_component(client, image=base64.b64encode(buf.getvalue()).decode())
        res = client.get(f"/components/{cid}/image")
        assert res.headers["content-type"] == "image/jpeg"

    def test_maintenance_script(self, client, settings, monkeypatch, capsys):
        import importlib.util
        from pathlib import Path
        b64_big, _ = TestThumbnails.real_jpeg((4000, 3000))
        b64_small, _ = TestThumbnails.real_jpeg((800, 600))
        big = new_component(client, name="big")
        small = new_component(client, name="small", image=b64_small)
        # Simulate a photo stored by an old server version (no shrinking).
        with client.app.db._cursor(dictionary=False) as cur:
            cur.execute("UPDATE components SET image = %s, image_content_type = 'image/jpeg' WHERE id = %s",
                        (base64.b64decode(b64_big), big))
        path = Path(__file__).resolve().parent.parent / "scripts" / "shrink_images.py"
        spec = importlib.util.spec_from_file_location("shrink_images", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        monkeypatch.setattr(mod, "load_settings", lambda: settings)

        monkeypatch.setattr("sys.argv", ["x"])
        assert mod.main() == 0
        assert len(client.app.db.get_image(big)[0]) == len(base64.b64decode(b64_big))  # dry run

        monkeypatch.setattr("sys.argv", ["x", "--apply"])
        assert mod.main() == 0
        assert len(client.app.db.get_image(big)[0]) < len(base64.b64decode(b64_big))
        assert client.app.db.get_thumbnail(big) is not None
        assert client.app.db.get_image(small)[0] == base64.b64decode(b64_small)
        assert "1 for store" in capsys.readouterr().out
