"""Model Context Protocol server (JSON-RPC 2.0 over Streamable HTTP).

Lets Claude and other MCP clients query the inventory. Every tool is
read-only by design, so the endpoint only ever needs the read key.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from .database import Database

SERVER_INFO = {"name": "lager", "version": "2.0.0"}
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "list_components",
        "description": "Henter alle komponenter i lageret med navn, kategori, antall, lokasjon og tekniske spesifikasjoner.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filtrer på kategori, f.eks. 'Transistorer'. Tom for alle."},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "search_components",
        "description": "Søker etter komponenter i navn, kategori, spesifikasjoner, beskrivelse og lokasjon. Alle ordene må finnes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Søkeord, f.eks. 'NPN transistor', '10kΩ', 'ESP32'."},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_low_stock",
        "description": "Komponenter med lavt antall på lager. Nyttig for å finne ut hva som bør kjøpes inn.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "integer", "description": "Vis komponenter med antall under dette. Standard 5."},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_summary",
        "description": "Oppsummering av lageret: totalt antall og fordeling per kategori.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_activity_log",
        "description": "Aktivitetsloggen: når komponenter ble lagt til, redigert, fikk endret antall eller ble slettet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Antall hendelser (1-200). Standard 20."},
                "component_name": {"type": "string", "description": "Filtrer på komponentnavn, f.eks. 'servo'."},
            },
        },
        "annotations": {"readOnlyHint": True},
    },
]

ACTION_LABELS = {
    "lagt_til": "Lagt til",
    "redigert": "Redigert",
    "antall_endret": "Antall endret",
    "slettet": "Slettet",
}


class ToolError(Exception):
    pass


def _compact(c: Dict[str, Any]) -> Dict[str, Any]:
    entry = {"id": c["id"], "name": c["name"], "category": c["category"], "quantity": c["quantity"]}
    for key in ("location", "specs", "description"):
        if c.get(key):
            entry[key] = c[key]
    return entry


def _int_arg(args: Dict[str, Any], name: str, default: int, lo: int, hi: int) -> int:
    value = args.get(name, default)
    if isinstance(value, bool):
        raise ToolError(f"'{name}' må være et heltall")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ToolError(f"'{name}' må være et heltall")
    return max(lo, min(hi, value))


def _str_arg(args: Dict[str, Any], name: str) -> str:
    value = args.get(name, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolError(f"'{name}' må være tekst")
    return value.strip()[:200]


def _list_components(db: Database, args: Dict[str, Any]) -> Any:
    category = _str_arg(args, "category")
    items = db.list_components()
    if category:
        items = [c for c in items if c["category"].lower() == category.lower()]
    return {"components": [_compact(c) for c in items], "count": len(items)}


def _search_components(db: Database, args: Dict[str, Any]) -> Any:
    query = _str_arg(args, "query")
    if not query:
        raise ToolError("'query' er påkrevd")
    terms = query.lower().split()
    matches = []
    for c in db.list_components():
        haystack = " ".join(str(c.get(k) or "") for k in
                            ("name", "category", "specs", "description", "location")).lower()
        if all(t in haystack for t in terms):
            matches.append(_compact(c))
    return {"matches": matches, "count": len(matches), "query": query}


def _low_stock(db: Database, args: Dict[str, Any]) -> Any:
    threshold = _int_arg(args, "threshold", 5, 0, 1_000_000)
    low = [
        {"id": c["id"], "name": c["name"], "category": c["category"],
         "quantity": c["quantity"], "location": c.get("location", "")}
        for c in db.list_components() if c["quantity"] < threshold
    ]
    low.sort(key=lambda x: (x["quantity"], x["name"]))
    return {"low_stock": low, "count": len(low), "threshold": threshold}


def summarize(components: List[Dict[str, Any]]) -> Dict[str, Any]:
    count: Dict[str, int] = defaultdict(int)
    qty: Dict[str, int] = defaultdict(int)
    for c in components:
        cat = c["category"] or "Ukjent"
        count[cat] += 1
        qty[cat] += c["quantity"]
    return {
        "total_unique_components": len(components),
        "total_quantity": sum(c["quantity"] for c in components),
        "categories": [
            {"category": cat, "unique_components": count[cat], "total_quantity": qty[cat]}
            for cat in sorted(count)
        ],
    }


def _summary(db: Database, args: Dict[str, Any]) -> Any:
    return summarize(db.list_components())


def _activity(db: Database, args: Dict[str, Any]) -> Any:
    limit = _int_arg(args, "limit", 20, 1, 200)
    name = _str_arg(args, "component_name")
    entries = db.activity_log(limit, component_name=name)
    return {
        "log": [{
            "tidspunkt": e["created_at"],
            "komponent": e["component_name"],
            "hendelse": ACTION_LABELS.get(e["action"], e["action"]),
            "detaljer": e.get("details") or "",
        } for e in entries],
        "count": len(entries),
    }


HANDLERS: Dict[str, Callable[[Database, Dict[str, Any]], Any]] = {
    "list_components": _list_components,
    "search_components": _search_components,
    "get_low_stock": _low_stock,
    "get_summary": _summary,
    "get_activity_log": _activity,
}


def _text(data: Any, is_error: bool = False) -> Dict[str, Any]:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    result: Dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(db: Database, msg: Any) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message. Returns None for notifications."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")

    method = msg["method"]
    is_notification = "id" not in msg
    req_id = msg.get("id")
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return None if is_notification else _error(req_id, -32602, "Invalid params")

    if is_notification:
        return None  # notifications/initialized, notifications/cancelled, ...

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "Elektronikklager. Alle verktøy er skrivebeskyttet.",
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name) if isinstance(name, str) else None
        if handler is None:
            return _error(req_id, -32602, f"Ukjent verktøy: {name}")
        if not isinstance(args, dict):
            return _error(req_id, -32602, "arguments må være et objekt")
        try:
            return {"jsonrpc": "2.0", "id": req_id, "result": _text(handler(db, args))}
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": req_id, "result": _text(f"Feil: {exc}", is_error=True)}
    return _error(req_id, -32601, f"Method not found: {method}")
