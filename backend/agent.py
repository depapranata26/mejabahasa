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

try:
    sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
    from dynamic_credentials import add_surrogate_to_request, read_json_response
except ImportError:
    # Non-vault environment (e.g. VPS): pakai BASENTEN_API_KEY langsung.
    import os

    def _get_key():
        key = os.environ.get("BASENTEN_API_KEY", "")
        if not key:
            for p in ("/home/Depapranata/mejabahasa/.baseten_key",
                      os.path.expanduser("~/mejabahasa/.baseten_key"),
                      ".baseten_key"):
                try:
                    with open(p) as f:
                        key = f.read().strip()
                    if key:
                        break
                except OSError:
                    continue
        return key

    def add_surrogate_to_request(req, *a, **k):
        req.add_header("Authorization", f"Bearer {_get_key()}")

    def read_json_response(resp):
        return json.loads(resp.read().decode("utf-8"))

try:
    from backend import tools as tools_mod
    from backend import queue_estimate as queue_mod
    from backend import coordinator as coord_mod
except ImportError:  # direct script execution from inside backend/
    import tools as tools_mod
    import queue_estimate as queue_mod
    import coordinator as coord_mod

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
   Never tell the guest an order is recorded, created, noted, or saved
   until create_order has actually returned — while you are still asking
   questions, say plainly nothing has been created yet.
5. Never call a tool with an id you have not seen in a tool result.
6. If a tool call returns an error, tell the guest what went wrong in their
   language and ask how they want to proceed — do not retry with a guessed
   value.
7. ALLERGY SAFETY (mandatory, never skip): BEFORE calling create_order,
   you MUST ask the guest about allergies, dietary restrictions, diet
   preferences, AND disliked ingredients, in the guest's language. Ask
   this question ONCE per draft — never repeat it twice for the same
   order. If the guest replies affirmatively without naming any allergen
   ("lanjut", "oke", "ya", "yes", "setuju", "tidak ada", "nggak ada",
   "aman", "gak ada"), treat it as NO allergies and proceed to create
   the draft immediately. Do NOT loop back to the allergy question.
7b. DISLIKES ("tidak suka" / "tanpa"): when the guest says they dislike
   an ingredient or wants a dish without it ("tidak suka bawang",
   "tanpa telur", "no onion"), check the dish's ingredients (from
   get_menu). If the ingredient is removable (garnish, topping, sauce
   on the side), add a custom note like "tanpa bawang" to that item in
   create_order/update_order and confirm. If it is a core ingredient
   that cannot be removed (e.g. "tanpa ayam" on Ayam Goreng), say so
   plainly and offer an alternative dish without it. Record dislikes
   in dietary_notes so they persist for the session.
7c. INGREDIENT QUESTIONS: when the guest asks what is in a dish
   ("ada udang nggak?", "isinya apa?", "does it contain nuts?"),
   answer from the dish's ingredients/allergens data (via get_menu)
   in the guest's language. Never guess ingredients you have not
   seen — if unsure, say the kitchen can confirm.
8. If a create_order call fails with an allergy conflict, warn the guest
   clearly in their language (name the dish AND the allergen) and offer
   alternatives from the menu that do not contain it — never push the
   conflicting dish through.
9. Pass the guest's allergy notes, dietary notes, and each item's custom
   note (e.g. "kurang pedas", "tanpa bawang", "tambah sambal") to
   create_order, so the kitchen ticket carries them prominently.
10. You do not know the guest's table number (it is set by the system from
    their QR code). Never invent, state, or change a table number in your
    reply or in any tool argument. If the guest opened the app without
    scanning a table QR, the app shows them a "Scan QR Meja" prompt after
    the draft is created — the UI handles the table number, not you.
    Just remind them to enter the table number from the QR on their
    table, or offer takeaway / waiting list (rule 17).
11. BUDGET: when the guest mentions a budget, call compose_meal (never
    invent a combination or a price) and offer the resulting combination;
    if it returns no combination, say so plainly and ask whether the
    guest wants to raise the budget or drop a course.
12. NEGOTIATION: when a tool reports insufficient stock with an available
    quantity and alternatives, negotiate — offer the available quantity,
    an alternative, or cancellation. Never silently reduce a quantity,
    and never promise stock you do not have. When the guest agrees to a
    change to a DRAFT order, call update_order (drafts only). The active
    draft's order_id is given in the session context when one exists —
    use exactly that id, never guess one.
13. TIME WINDOWS: get_menu reports available_now per item. Never order an
    item outside its window — if a guest asks for one, say plainly when
    it becomes available and offer an available alternative.
14. WAIT ESTIMATES: never invent a wait time. When a guest asks how long
    food takes or when their order will be ready, call estimate_wait
    (order_id for an existing order, items for a hypothetical one, or
    nothing for the current kitchen backlog) and quote its number as an
    estimate ("perkiraan"), never as a promise.
15. COORDINATOR: when anyone asks who cooks, makes, or delivers what,
    or when a table will be ready, call coordinator_plan (table_no to
    narrow to one table) and quote its assignments and targets. Never
    invent staff assignments, task splits, or ready times.
16. REPLY FORMATTING (strict): the guest chat UI does NOT render
    markdown. NEVER use **bold**, ## headers, backticks, or any markdown
    syntax — it shows literally and looks broken. Write plain chat text:
    short lines, one blank line between sections, emoji as section
    markers (🍽️ food, 🥤 drinks, 🍰 dessert, ⚠️ warnings). Lists: one
    item per line as "• Name - Rp45.000 (short desc)". Keep every reply
    compact and scannable on a phone screen.
17. DINE-IN vs TAKEAWAY vs WAITING LIST: after the allergy question
    (rule 7) and BEFORE create_order, ask the guest in their language:
    dine-in (makan di tempat) or takeaway (bawa pulang)? Pass their
    answer as order_type to create_order ('dine_in' default). If the
    guest says they have no table yet: offer two options — (a) takeaway,
    or (b) join the waiting list (antrean meja). If they choose the
    waiting list, ask for their name and party size (phone optional),
    then call join_waiting_list and tell them their queue number
    plainly. Never invent a queue number — only quote the tool result.

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
            "description": "Store a DRAFT order (guest language code, items). A draft is NOT a real order yet. The table number is set by the system from the guest's QR code — do not pass table_no yourself. Before calling this, you MUST have asked the guest about allergies, dietary restrictions, and diet preferences, and you MUST pass their answers here. Also ask dine-in vs takeaway (rule 17) and pass order_type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_type": {
                        "type": "string",
                        "enum": ["dine_in", "takeaway"],
                        "description": "dine_in = makan di tempat (default), takeaway = bawa pulang.",
                    },
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
    {
        "type": "function",
        "function": {
            "name": "compose_meal",
            "description": "Compose a meal (one main + one drink + one dessert) under the guest's budget, from database prices only, excluding items that conflict with the guest's allergies. Call this when the guest mentions a budget.",
            "parameters": {
                "type": "object",
                "properties": {
                    "budget_idr": {"type": "integer"},
                    "allergy_notes": {"type": "string"},
                    "dietary_notes": {"type": "string"},
                },
                "required": ["budget_idr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_order",
            "description": "Revise a DRAFT order's items after the guest agrees to a change (guest changed their mind, or a negotiation after insufficient stock). The new list replaces the old one. Drafts only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                    "allergy_notes": {"type": "string"},
                    "dietary_notes": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "menu_item_id": {"type": "integer"},
                                "qty": {"type": "integer"},
                                "custom_note": {"type": "string"},
                            },
                            "required": ["menu_item_id", "qty"],
                        },
                    },
                },
                "required": ["order_id", "items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_wait",
            "description": "Estimate how long until food is ready, computed from the LIVE kitchen ticket queue (tickets ahead + demo prep times). NEVER guess or invent a wait time — always call this when the guest asks how long something takes. Pass order_id for an existing order, items for a hypothetical new order, or nothing for the current kitchen backlog. Quote the result as an estimate (perkiraan), never as a promise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "menu_item_id": {"type": "integer"},
                                "qty": {"type": "integer"},
                            },
                            "required": ["menu_item_id", "qty"],
                        },
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "join_waiting_list",
            "description": "Register the guest in the table waiting list when they have no table yet and want to wait for one. Returns their queue number. Ask for their name and party size first. Only call when the guest explicitly agrees to join the waiting list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Guest's name.",
                    },
                    "party_size": {
                        "type": "integer",
                        "description": "Number of people.",
                    },
                    "phone": {
                        "type": "string",
                        "description": "Optional phone/WhatsApp number so staff can call them.",
                    },
                },
                "required": ["name", "party_size"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "coordinator_plan",
            "description": "The floor coordinator plan: which staff member cooks, makes, or delivers what, for which table, and each table's target ready time. Computed deterministically from live tickets — never invent assignments or times. Pass table_no to narrow the view to one table (targets stay queue-aware). Call this when staff ask who does what, or when anyone asks when a table's food will be ready.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_no": {"type": "string"},
                },
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
        order_type=args.get("order_type", "dine_in"),
    ),
    "join_waiting_list": lambda args, db: tools_mod.join_waiting_list(
        args["name"], args["party_size"],
        phone=args.get("phone", ""), db_path=db),
    "compose_meal": lambda args, db: tools_mod.compose_meal(
        args["budget_idr"],
        allergy_notes=args.get("allergy_notes", ""),
        dietary_notes=args.get("dietary_notes", ""),
        db_path=db,
    ),
    "update_order": lambda args, db: tools_mod.update_order(
        args["order_id"], args["items"], db_path=db,
        allergy_notes=args.get("allergy_notes"),
        dietary_notes=args.get("dietary_notes"),
    ),
    "estimate_wait": lambda args, db: (
        queue_mod.estimate_for_order(db, args["order_id"])
        if args.get("order_id") is not None
        else queue_mod.estimate_new_order(db, args["items"])
        if args.get("items")
        else queue_mod.estimate_backlog(db)
    ) or {"ok": False, "error": "order tidak ditemukan"},
    "coordinator_plan": lambda args, db: coord_mod.build_plan(
        db, table_no=args.get("table_no")),
}

_CTX = {"table_no": "A12"}

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _call_model(messages, use_tools=True, max_tokens=1024):
    """One chat-completions call. Returns (content, tool_calls list)."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
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
             max_iterations=4, draft_order_id=None):
    """One guest turn: returns {reply, tool_calls, path}.

    ``path`` is "tool_calls" when the model used native tool_calls,
    "json_fallback" when it used a ```json action block, "direct" when it
    answered without any tool use.

    ``draft_order_id`` is the session's active draft (the server passes
    the client's last_order_id). Without it the model can never learn a
    draft's id — history carries only reply texts — so update_order
    would be unreachable in production. The id is injected as a system
    message, never guessed by the model.
    """
    _CTX["table_no"] = table_no
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if draft_order_id is not None:
        messages.append({
            "role": "system",
            "content": (
                f"Session context: this guest has an active DRAFT order "
                f"with order_id {draft_order_id}. If the guest asks to "
                f"change their order, call update_order with exactly this "
                f"order_id. Do not invent any other order_id."
            ),
        })
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


_LANG_NAMES = {
    "en": "English", "id": "Bahasa Indonesia", "ms": "Bahasa Melayu",
    "zh": "Simplified Chinese", "ko": "Korean", "ja": "Japanese",
    "ar": "Arabic", "hi": "Hindi", "th": "Thai", "vi": "Vietnamese",
    "es": "Spanish", "fr": "French", "de": "German", "ru": "Russian",
    "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "tr": "Turkish",
    "bn": "Bengali", "fa": "Persian", "ur": "Urdu", "sw": "Swahili",
    "ha": "Hausa", "yo": "Yoruba", "ig": "Igbo", "am": "Amharic",
    "ne": "Nepali", "si": "Sinhala", "km": "Khmer", "lo": "Lao",
    "my": "Burmese", "ka": "Georgian", "hy": "Armenian", "az": "Azerbaijani",
    "kk": "Kazakh", "uz": "Uzbek", "uk": "Ukrainian", "pl": "Polish",
    "cs": "Czech", "hu": "Hungarian", "ro": "Romanian", "el": "Greek",
    "he": "Hebrew", "da": "Danish", "sv": "Swedish", "no": "Norwegian",
    "fi": "Finnish",
}


def _translate_menu_batch(src, lang_name):
    """Translate satu batch kecil. Kembalikan list of dicts."""
    prompt = (
        "Translate this restaurant menu from Indonesian to "
        f"{lang_name}. Output ONLY a raw JSON array, no markdown, no "
        "explanation, no other text. Each element: {\"id\": <same id>, "
        "\"name\": \"<translated dish name>\", \"description\": "
        "\"<translated description>\"}. Dish names: translate naturally "
        "but keep them recognizable (keep well-known dish names close to "
        "the original). Descriptions: translate fully and appetizingly, "
        "one short sentence each.\n\n"
        + json.dumps(src, ensure_ascii=False)
    )
    content, _ = _call_model(
        [{"role": "user", "content": prompt}], use_tools=False,
        max_tokens=2048)
    m = re.search(r"\[.*\]", content, re.DOTALL)
    if not m:
        cleaned = re.sub(r"```(?:json)?", "", content)
        m = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not m:
        print(f"[translate_menu] reply tak ter-parse ({lang_name}, "
              f"{len(content)} chars): {content[:400]!r}", flush=True)
        raise RuntimeError("translate_menu: no JSON array in model reply")
    return json.loads(m.group(0))


def translate_menu(items, target_lang):
    """Translate menu item names+descriptions via the model.

    ``items``: list of dicts with id/name/description. Returns a dict
    {id: {"name": ..., "description": ...}}. Raises on API failure —
    the caller decides the fallback. Diterjemahkan per batch 4 item
    agar respon model tidak terpotong.
    """
    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    src = [
        {"id": it["id"], "name": it.get("name", ""),
         "description": it.get("description", "")}
        for it in items
    ]
    out = {}
    for i in range(0, len(src), 4):
        for row in _translate_menu_batch(src[i:i + 4], lang_name):
            out[int(row["id"])] = {
                "name": row.get("name", ""),
                "description": row.get("description", ""),
            }
    return out


def translate_ui(strings, target_lang):
    """Translate short UI label strings via the model.

    ``strings``: {key: english_text}. Returns {key: translated}.
    Raises on API failure — the caller decides the fallback.
    """
    lang_name = _LANG_NAMES.get(target_lang, target_lang)
    prompt = (
        "Translate these short UI labels from English to "
        f"{lang_name}. Return ONLY a JSON object mapping each key to "
        "its translation, no other text. Keep each translation short "
        "(button-label length), natural, and in the script the language "
        "normally uses.\n\n"
        + json.dumps(strings, ensure_ascii=False)
    )
    content, _ = _call_model(
        [{"role": "user", "content": prompt}], use_tools=False,
        max_tokens=2048)
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        cleaned = re.sub(r"```(?:json)?", "", content)
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        raise RuntimeError("translate_ui: no JSON object in model reply")
    return json.loads(m.group(0))
