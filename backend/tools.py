"""Agent tool specifications and stubs for MejaBahasa.

These are the ONLY actions the planner may take, and every call must be
recorded in ``agent_action_log`` (see data/schema.sql). Guardrails from the
PRD, enforced by the implementations landing on day 2-3:

1. Never guess: on any ambiguity, ask back in the guest's language instead
   of calling a mutating tool.
2. Price / stock / menu ONLY from the database. Never invent values.
3. An order becomes real only after guest confirmation AND staff ACC
   (human-in-the-loop). ``create_order`` stores a draft; ``request_acc``
   asks staff; ``send_kitchen_ticket`` fires only for ACC-accepted orders.
4. Every tool call is logged with its input and output for audit.

Day-1 status: specifications + stubs. Implementations connect to SQLite
(day 2: ordering core; day 3: clarification + ACC + kitchen ticket).
"""

TOOL_SPECS = [
    {
        "name": "get_menu",
        "description": (
            "List menu items from the database, optionally filtered by "
            "category. The ONLY source of menu truth; never invent dishes "
            "or prices."
        ),
        "parameters": {
            "category": "optional: makanan_utama | minuman | penutup",
        },
        "returns": "list of {id, name, category, price_idr, stock, description}",
    },
    {
        "name": "check_stock",
        "description": (
            "Check whether a menu item has enough stock for a requested "
            "quantity. Reads the database; never assume availability."
        ),
        "parameters": {
            "menu_item_id": "integer, from get_menu",
            "qty": "integer > 0",
        },
        "returns": "{available: bool, stock: int}",
    },
    {
        "name": "create_order",
        "description": (
            "Store a DRAFT order (table_no, guest language, items). A draft "
            "is not a real order: it must be confirmed by the guest and "
            "then ACC-accepted by staff before any kitchen ticket is sent."
        ),
        "parameters": {
            "table_no": "string, e.g. 'A12'",
            "language": "zh | ko | ja | id",
            "items": "list of {menu_item_id, qty, note?}",
        },
        "returns": "{order_id, status: 'draft'}",
    },
    {
        "name": "request_acc",
        "description": (
            "Ask staff to approve (ACC) a guest-confirmed order. Moves the "
            "order to 'acc_pending' and surfaces it on the staff dashboard."
        ),
        "parameters": {"order_id": "integer"},
        "returns": "{order_id, status: 'acc_pending'}",
    },
    {
        "name": "send_kitchen_ticket",
        "description": (
            "Send the Indonesian kitchen ticket for an ACC-ACCEPTED order "
            "only. Refused for any other status. The ticket lists table "
            "number, items, quantities, and special notes in Indonesian."
        ),
        "parameters": {"order_id": "integer"},
        "returns": "{ticket_id, status: 'open'}",
    },
]


def _not_implemented(name):
    raise NotImplementedError(
        f"{name} is a day-1 stub; implementation lands with the ordering "
        "core (day 2) and ACC + kitchen ticket (day 3)."
    )


def get_menu(category=None):
    """Stub: list menu items from the database."""
    _not_implemented("get_menu")


def check_stock(menu_item_id, qty):
    """Stub: check stock for one menu item."""
    _not_implemented("check_stock")


def create_order(table_no, items, language):
    """Stub: store a draft order (guest confirmation still required)."""
    _not_implemented("create_order")


def request_acc(order_id):
    """Stub: move a guest-confirmed order to acc_pending."""
    _not_implemented("request_acc")


def send_kitchen_ticket(order_id):
    """Stub: send the kitchen ticket for an ACC-accepted order only."""
    _not_implemented("send_kitchen_ticket")
