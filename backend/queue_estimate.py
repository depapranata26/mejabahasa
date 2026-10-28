#!/usr/bin/env python3
"""Guest wait estimate — thin wrapper over the coordinator plan.

Previously this module ran its own SEQUENTIAL kitchen model (one
station, tickets summed in order). Since the floor coordinator exists,
the guest estimate now comes from the SAME parallel station plan
(backend/coordinator.py): dapur/bar/waiter, greedy least-loaded
assignment. One kitchen model, so the guest screen and the coordinator
screen can never contradict each other.

Kept as a module so server.py and agent.py imports do not change.
"""
from backend import coordinator as _coord

DEFAULT_PREP_MINUTES = _coord.DEFAULT_PREP_MINUTES
UNFINISHED_TICKET_STATUSES = _coord.UNFINISHED_TICKET_STATUSES

load_prep = _coord.load_prep
estimate_for_order = _coord.estimate_for_order
estimate_backlog = _coord.estimate_backlog
estimate_new_order = _coord.estimate_new_order
