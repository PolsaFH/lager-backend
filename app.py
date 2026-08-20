import json
import cgi
import os
import sys
from database import (init_db, get_all_components, add_component, update_component,
                      update_component_quantity, delete_component, get_component_by_id,
                      get_activity_log)
from ai import analyze_image

try:
    init_db()
except Exception as e:
    pass

def send_json(start_response, data, status="200 OK"):
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, PATCH, PUT, DELETE, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type"),
    ]
    start_response(status, headers)
    return [body]

def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")

    if path.startswith("/api"):
        path = path[4:] or "/"

    if method == "OPTIONS":
        return send_json(start_response, {})

    # GET /
    if method == "GET" and path == "/":
        return send_json(start_response, {"status": "ok", "message": "Lager API kjører"})

    # GET /components
    if method == "GET" and path == "/components":
        components = get_all_components()
        return send_json(start_response, {"components": components})

    # GET /components/{id}/image
    if method == "GET" and path.startswith("/components/") and path.endswith("/image"):
        try:
            component_id = int(path.split("/")[2])
            component = get_component_by_id(component_id)
            if component and component.get("image"):
                import base64 as b64lib
                image_data = b64lib.b64decode(component["image"])
                content_type = component.get("image_content_type", "image/jpeg")
                headers = [
                    ("Content-Type", content_type),
                    ("Content-Length", str(len(image_data))),
                    ("Access-Control-Allow-Origin", "*"),
                ]
                start_response("200 OK", headers)
                return [image_data]
            else:
                return send_json(start_response, {"error": "Ikke funnet"}, "404 Not Found")
        except Exception as e:
            return send_json(start_response, {"error": str(e)}, "500 Internal Server Error")

    # GET /components/{id}
    if method == "GET" and path.startswith("/components/"):
        try:
            component_id = int(path.split("/")[2])
            component = get_component_by_id(component_id)
            if component:
                return send_json(start_response, component)
            else:
                return send_json(start_response, {"error": "Ikke funnet"}, "404 Not Found")
        except:
            return send_json(start_response, {"error": "Ugyldig ID"}, "400 Bad Request")

    # POST /components
    if method == "POST" and path == "/components":
        try:
            import base64 as b64lib
            length = int(environ.get("CONTENT_LENGTH", 0))
            body = json.loads(environ["wsgi.input"].read(length))
            name = body.get("name", "")
            category = body.get("category", "")
            quantity = int(body.get("quantity", 0))
            location = body.get("location", "")
            description = body.get("description", "")
            specs = body.get("specs", "")
            image_data = None
            image_content_type = None
            if body.get("image"):
                image_data = b64lib.b64decode(body["image"])
                image_content_type = body.get("mime_type", "image/jpeg")
            component_id = add_component(
                name=name, category=category, quantity=quantity,
                location=location, description=description, specs=specs,
                image_data=image_data, image_content_type=image_content_type
            )
            return send_json(start_response, {"id": component_id, "message": "Komponent lagt til"}, "201 Created")
        except Exception as e:
            return send_json(start_response, {"error": str(e)}, "500 Internal Server Error")

    # PUT /components/{id} — rediger komponent
    if method == "PUT" and path.startswith("/components/"):
        try:
            component_id = int(path.split("/")[2])
            length = int(environ.get("CONTENT_LENGTH", 0))
            body = json.loads(environ["wsgi.input"].read(length))
            success = update_component(
                component_id=component_id,
                name=body.get("name", ""),
                category=body.get("category", ""),
                quantity=int(body.get("quantity", 0)),
                location=body.get("location", ""),
                description=body.get("description", ""),
                specs=body.get("specs", ""),
            )
            if success:
                return send_json(start_response, {"message": "Komponent oppdatert"})
            else:
                return send_json(start_response, {"error": "Ikke funnet"}, "404 Not Found")
        except Exception as e:
            return send_json(start_response, {"error": str(e)}, "500 Internal Server Error")

    # PATCH /components/{id}/quantity
    if method == "PATCH" and "/quantity" in path:
        try:
            component_id = int(path.split("/")[2])
            length = int(environ.get("CONTENT_LENGTH", 0))
            body = json.loads(environ["wsgi.input"].read(length))
            quantity = int(body.get("quantity", 0))
            success = update_component_quantity(component_id, quantity)
            if success:
                return send_json(start_response, {"message": "Antall oppdatert"})
            else:
                return send_json(start_response, {"error": "Ikke funnet"}, "404 Not Found")
        except Exception as e:
            return send_json(start_response, {"error": str(e)}, "500 Internal Server Error")

    # DELETE /components/{id}
    if method == "DELETE" and path.startswith("/components/"):
        try:
            component_id = int(path.split("/")[2])
            success = delete_component(component_id)
            if success:
                return send_json(start_response, {"message": "Komponent slettet"})
            else:
                return send_json(start_response, {"error": "Ikke funnet"}, "404 Not Found")
        except Exception as e:
            return send_json(start_response, {"error": str(e)}, "500 Internal Server Error")

    # POST /analyze-base64
    if method == "POST" and path == "/analyze-base64":
        try:
            import base64 as b64lib
            length = int(environ.get("CONTENT_LENGTH", 0))
            body = json.loads(environ["wsgi.input"].read(length))
            image_base64 = body.get("image", "")
            mime_type = body.get("mime_type", "image/jpeg")
            image_data = b64lib.b64decode(image_base64)
            result = analyze_image(image_data, mime_type)
            return send_json(start_response, result)
        except Exception as e:
            return send_json(start_response, {"error": str(e)}, "500 Internal Server Error")

    # GET /log
    if method == "GET" and path == "/log":
        log = get_activity_log(50)
        return send_json(start_response, {"log": log})

    # GET /ha/summary
    if method == "GET" and path == "/ha/summary":
        components = get_all_components()
        summary = {c["name"]: c["quantity"] for c in components}
        return send_json(start_response, {
            "total_components": len(components),
            "inventory": summary
        })

    # MCP JSON-RPC 2.0 — Streamable HTTP transport
    # mcp-remote sender POST til /mcp med JSON-RPC body
    if path == "/mcp":
        if method == "POST":
            return handle_mcp(environ, start_response)
        # GET /mcp — returner tom 200 så mcp-remote ikke faller tilbake til SSE
        if method == "GET":
            return send_json(start_response, {"status": "mcp endpoint"})

    return send_json(start_response, {"error": "Ikke funnet"}, "404 Not Found")


MCP_TOOLS = [
    {
        "name": "list_components",
        "description": "Henter alle komponenter i lageret med navn, kategori, antall, lokasjon og tekniske spesifikasjoner.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filtrer på kategori, f.eks. 'Transistorer'. La være tom for alle."
                }
            },
            "required": []
        }
    },
    {
        "name": "search_components",
        "description": "Søker etter komponenter basert på navn, spesifikasjoner, beskrivelse eller lokasjon.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Søkeord, f.eks. 'NPN transistor', '10kΩ', 'ESP32'"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_low_stock",
        "description": "Returnerer komponenter med lavt antall på lager. Nyttig for å finne ut hva som bør kjøpes inn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "integer",
                    "description": "Grense — komponenter under dette antallet vises. Standard: 5."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_summary",
        "description": "Gir oppsummering av lageret: totalt antall, fordeling per kategori.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_activity_log",
        "description": "Henter aktivitetsloggen — viser når komponenter ble lagt til, redigert, antall endret eller slettet. Bruk dette for å finne ut når noe ble registrert i lageret.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Antall hendelser å hente. Standard: 20."
                },
                "component_name": {
                    "type": "string",
                    "description": "Filtrer på komponentnavn, f.eks. 'servo'. La være tom for alle."
                }
            },
            "required": []
        }
    }
]


def mcp_tool_result(content_text):
    return {"content": [{"type": "text", "text": content_text}]}


def run_mcp_tool(tool_name, params):
    from collections import defaultdict
    components = get_all_components()

    if tool_name == "list_components":
        category = params.get("category", "")
        result = components
        if category:
            result = [c for c in components if c["category"].lower() == category.lower()]
        clean = []
        for c in result:
            entry = {"name": c["name"], "category": c["category"], "quantity": c["quantity"]}
            if c.get("location"):    entry["location"] = c["location"]
            if c.get("specs"):       entry["specs"] = c["specs"]
            if c.get("description"): entry["description"] = c["description"]
            clean.append(entry)
        text = json.dumps({"components": clean, "count": len(clean)}, ensure_ascii=False, indent=2)
        return mcp_tool_result(text)

    elif tool_name == "search_components":
        query = params.get("query", "").lower()
        matches = []
        for c in components:
            searchable = " ".join([
                c.get("name", ""), c.get("category", ""),
                c.get("specs", "") or "", c.get("description", "") or "",
                c.get("location", "") or "",
            ]).lower()
            if query in searchable:
                entry = {"name": c["name"], "category": c["category"], "quantity": c["quantity"]}
                if c.get("location"):    entry["location"] = c["location"]
                if c.get("specs"):       entry["specs"] = c["specs"]
                if c.get("description"): entry["description"] = c["description"]
                matches.append(entry)
        text = json.dumps({"matches": matches, "count": len(matches), "query": query}, ensure_ascii=False, indent=2)
        return mcp_tool_result(text)

    elif tool_name == "get_low_stock":
        threshold = int(params.get("threshold", 5))
        low = [
            {"name": c["name"], "category": c["category"], "quantity": c["quantity"],
             "location": c.get("location", "")}
            for c in components if c["quantity"] < threshold
        ]
        low.sort(key=lambda x: x["quantity"])
        text = json.dumps({"low_stock": low, "count": len(low), "threshold": threshold}, ensure_ascii=False, indent=2)
        return mcp_tool_result(text)

    elif tool_name == "get_summary":
        cat_count = defaultdict(int)
        cat_qty = defaultdict(int)
        for c in components:
            cat_count[c["category"]] += 1
            cat_qty[c["category"]] += c["quantity"]
        total_qty = sum(c["quantity"] for c in components)
        summary = {
            "total_unique_components": len(components),
            "total_quantity": total_qty,
            "categories": [
                {"category": cat, "unique_components": cat_count[cat], "total_quantity": cat_qty[cat]}
                for cat in sorted(cat_count.keys())
            ]
        }
        return mcp_tool_result(json.dumps(summary, ensure_ascii=False, indent=2))

    elif tool_name == "get_activity_log":
        limit = int(params.get("limit", 20))
        component_name = params.get("component_name", "").lower()
        log = get_activity_log(limit)
        if component_name:
            log = [e for e in log if component_name in e.get("component_name", "").lower()]
        action_labels = {
            "lagt_til": "Lagt til",
            "redigert": "Redigert",
            "antall_endret": "Antall endret",
            "slettet": "Slettet",
        }
        formatted = []
        for e in log:
            formatted.append({
                "tidspunkt": e["created_at"],
                "komponent": e["component_name"],
                "hendelse": action_labels.get(e["action"], e["action"]),
                "detaljer": e.get("details", ""),
            })
        text = json.dumps({"log": formatted, "count": len(formatted)}, ensure_ascii=False, indent=2)
        return mcp_tool_result(text)

    else:
        return {"content": [{"type": "text", "text": f"Ukjent verktøy: {tool_name}"}], "isError": True}


def handle_mcp(environ, start_response):
    try:
        length = int(environ.get("CONTENT_LENGTH", 0))
        raw = environ["wsgi.input"].read(length)
        body = json.loads(raw)
    except Exception as e:
        return send_json(start_response, {
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32700, "message": f"Parse error: {e}"}
        })

    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    # initialize
    if method == "initialize":
        return send_json(start_response, {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lager", "version": "1.0.0"}
            }
        })

    # initialized (notification — no response needed)
    if method == "notifications/initialized":
        return send_json(start_response, {})

    # tools/list
    if method == "tools/list":
        return send_json(start_response, {
            "jsonrpc": "2.0", "id": req_id,
            "result": {"tools": MCP_TOOLS}
        })

    # tools/call
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_params = params.get("arguments", {})
        try:
            result = run_mcp_tool(tool_name, tool_params)
            return send_json(start_response, {
                "jsonrpc": "2.0", "id": req_id,
                "result": result
            })
        except Exception as e:
            return send_json(start_response, {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": f"Feil: {str(e)}"}], "isError": True}
            })

    # ping
    if method == "ping":
        return send_json(start_response, {"jsonrpc": "2.0", "id": req_id, "result": {}})

    return send_json(start_response, {
        "jsonrpc": "2.0", "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"}
    })
