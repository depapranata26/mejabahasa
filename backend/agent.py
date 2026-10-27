#!/usr/bin/env python3
"""Agent brain for MejaBahasa: a Baseten model driving the ordering tools.

The loop: guest message -> model (with OpenAI-style tools) -> tool calls
executed against SQLite -> model replies in the guest's language.

Notes on the provider wiring:
- ``backend/provider.py``'s ``complete()`` is text-only (no tool_calls), so
  this module calls the Baseten chat-completions endpoint directly with
  the SAME Secure Vault pattern proven by ``test_baseten.py``:
  ``dynamic_credentials`` swaps a surrogate for the real key only on an
  approved request to ``inference.baseten.co``. The key is never written
  to a file, a log, or the repo. A future refactor can move tool support
  into ``provider.py``; the interface contract stays the same.
- Tool-calling path: native OpenAI ``tool_calls`` first. Fallback: if the
  model returns no ``tool_calls``, parse a ```json action block from its
  text ({"action": <tool>, "args": {...}}). The path actually used is
  reported in the result ("path" field) so tests can tell which one ran.
"""

import json
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

try:
    from backend import tools as tools_mod
except ImportError:  # direct script execution from inside backend/
    import tools as tools_mod

MODEL = "meta-models/muse-spark-open-1026"
BASE_URL = "https://inference.baseten.co/v1"

SYSTEM_PROMPT = """You are MejaBahasa, a multilingual restaurant ordering agent.
A guest scanned a QR code on their table and is chatting from their own phone.

HARD RULES — violating any of these is a failure:
1. ALWAYS reply in the SAME language the guest just used. Detect it from
   their message; never ask them to pick a language.
2. NEVER guess. If a dish, quantity, or request is ambiguous or not clearly
   on the menu, ask back in the guest's language instead of acting.
3. Menu, prices, and stock come ONLY from the tools (the database). Never
   invent a dish, a price, or availability. Before creating an order you
   MUST call get_menu to resolve dish names to menu_item_ids.
4. Creating an order only stores a DRAFT. A draft becomes real only after
   the guest confirms and staff approves (ACC) — those are human steps the
   guest/staff take in the UI, not something you can skip or fake. When a
   draft is created, tell the guest what is in it and ask them to confirm.
5. Never call a tool with an id you have not seen in a tool result.
6. If a tool call returns an error, tell the guest what went wrong in their
   language and ask how they want to proceed — do not retry with a guessed
   value.
7. ALLERGY SAFETY (mandatory, never skip): BEFORE calling create_order,
   you MUST ask the guest about allergies, dietary restrictions, and
   diet preferences, in the guest's language. Do NOT create an order
   until the guest has answered — even if they already listed dishes.
   If they say they have none, proceed.
8. If a create_order call fails with an allergy conflict, warn the guest
   clearly in their language (name the dish AND the allergen) and offer
   alternatives from the menu that do not contain it — never push the
   conflicting dish through.
9. Pass the guest's allergy notes, dietary notes, and each item's custom
   note (e.g. "kurang pedas", "tanpa bawang", "tambah sambal") to
   create_order, so the kitchen ticket carries them prominently.
10. You do not know the guest's table number (it is set by the system from
    their QR code). Never invent, state, or change a table number in your
    reply or in any tool argument.

When you need data, call a tool. When you have everything, reply to the
guest concisely in their language.
"""

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_menu",
            "description": "List menu items from the database, optionally filtered by category. The ONLY source of menu truth.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["makanan_utama", "minuman", "penutup"],
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Check whether a menu item has enough stock for a quantity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "menu_item_id": {"type": "integer"},
                    "qty": {"type": "integer"},
                },
                "required": ["menu_item_id", "qty"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "Store a DRAFT order (guest language code, items). A draft is NOT a real order yet. The table number is set by the system from the guest's QR code — do not pass table_no yourself. Before calling this, you MUST have asked the guest about allergies, dietary restrictions, and diet preferences, and you MUST pass their answers here.",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["zh", "ko", "ja", "id"],
                    },
                    "allergy_notes": {
                        "type": "string",
                        "description": "Guest's allergies, e.g. 'alergi kacang'. Checked against every item's allergens; a conflict rejects the order.",
                    },
                    "dietary_notes": {
                        "type": "string",
                        "description": "Guest's dietary restrictions or diet, e.g. 'vegetarian'. Shown on the kitchen ticket.",
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "menu_item_id": {"type": "integer"},
                                "qty": {"type": "integer"},
                                "note": {"type": "string"},
                                "custom_note": {"type": "string", "description": "Per-item customization, e.g. 'kurang pedas', 'tanpa bawang', 'tambah sambal'."},
                            },
                            "required": ["menu_item_id", "qty"],
                        },
                    },
                },
                "required": ["language", "items"],
            },
        },
    },
]

_TOOL_DISPATCH = {
    "get_menu": lambda args, db: tools_mod.get_menu(
        category=args.get("category"), db_path=db
    ),
    "check_stock": lambda args, db: tools_mod.check_stock(
        args["menu_item_id"], args["qty"], db_path=db
    ),
    "create_order": lambda args, db: tools_mod.create_order(
        # Server-side injection: the table number comes from the scanned QR
        # context, NEVER from the model. A model-invented table_no (e.g. 1)
        # must not reach the database — the dispatch overrides it always.
        _CTX["table_no"],
        args["items"],
        args["language"],
        db_path=db,
        allergy_notes=args.get("allergy_notes", ""),
        dietary_notes=args.get("dietary_notes", ""),
    ),
}

_CTX = {"table_no": "A12"}

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _call_model(messages, use_tools=True):
    """One chat-completions call. Returns (content, tool_calls list)."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1024,
    }
    if use_tools:
        payload["tools"] = OPENAI_TOOLS
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    add_surrogate_to_request(req, "custom.baseten", allowed_hosts=["inference.baseten.co"])
    try:
        with urllib.request.build_opener().open(req, timeout=180) as resp:
            data = read_json_response(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Baseten API error {exc.code}: {detail}") from exc
    msg = data["choices"][0]["message"]
    return msg.get("content") or "", msg.get("tool_calls") or []


def _execute(name, args, db_path):
    """Run one tool call, returning a JSON-able result or an error dict."""
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        return {"error": f"Tool '{name}' tidak dikenal."}
    try:
        return fn(args, db_path)
    except (ValueError, KeyError, TypeError) as exc:
        return {"error": str(exc)}


def run_turn(user_message, table_no="A12", history=None, db_path=None,
             max_iterations=4):
    """One guest turn: returns {reply, tool_calls, path}.

    ``path`` is "tool_calls" when the model used native tool_calls,
    "json_fallback" when it used a ```json action block, "direct" when it
    answered without any tool use.
    """
    _CTX["table_no"] = table_no
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_message})
    executed = []
    path = "direct"
    for _ in range(max_iterations):
        content, tool_calls = _call_model(messages, use_tools=True)
        if tool_calls:
            path = "tool_calls"
            assistant_msg = {"role": "assistant", "content": content,
                             "tool_calls": tool_calls}
            messages.append(assistant_msg)
            for call in tool_calls:
                fn = call["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = _execute(fn["name"], args, db_path)
                executed.append({"tool": fn["name"], "args": args,
                                 "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })
            continue
        # No native tool_calls: try the JSON action-block fallback.
        match = _JSON_BLOCK.search(content)
        if match:
            try:
                action = json.loads(match.group(1))
            except json.JSONDecodeError:
                action = None
            if action and action.get("action") in _TOOL_DISPATCH:
                path = "json_fallback"
                result = _execute(action["action"], action.get("args") or {},
                                  db_path)
                executed.append({"tool": action["action"],
                                 "args": action.get("args") or {},
                                 "result": result})
                messages.append({
                    "role": "user",
                    "content": (
                        "Tool result for "
                        f"{action['action']}: "
                        f"{json.dumps(result, ensure_ascii=False)}. "
                        "Now reply to the guest in their language."
                    ),
                })
                continue
        return {"reply": content, "tool_calls": executed, "path": path}
    return {"reply": messages[-1].get("content", "") if messages else "",
            "tool_calls": executed, "path": path}
