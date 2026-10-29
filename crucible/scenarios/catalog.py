"""Toy weakness catalog for generated scenarios.

Every weakness is a small declarative record: preconditions (what red
must already know or hold), reveals (what the action gives red), and
unlocks (what that enables next). No real exploit code, no malware:
each kind is a misconfiguration with logging side effects, mirroring
the classic scenario's admin page, default sign-in, and config read.
"""

from __future__ import annotations

WEAKNESSES = {
    "exposed_page": {
        "title": "Exposed admin page leaks the next hop",
        "needs": ["entry host reachable over HTTP"],
        "reveals": ["next hostname", "next service account name"],
        "unlocks": ["targeted sign-in attempts on the next host"],
        "log_lines": ["one HTTP request line on the entry host"],
    },
    "weak_creds": {
        "title": "Default or weak credential on a service account",
        "needs": ["hostname", "account name"],
        "reveals": ["authenticated session", "the weak password itself"],
        "unlocks": ["file reads on that host", "password reuse elsewhere"],
        "log_lines": ["one successful sign-in line"],
    },
    "config_creds": {
        "title": "Credential left in a readable config file",
        "needs": ["authenticated session on the config host"],
        "reveals": ["next hostname", "account", "non-guessable password"],
        "unlocks": ["sign-in on the next host"],
        "log_lines": ["one directory listing", "one config file read"],
    },
    "backup_creds": {
        "title": "Backup or export file with a next-hop login",
        "needs": ["authenticated session on the share host"],
        "reveals": ["next hostname", "account", "non-guessable password"],
        "unlocks": ["sign-in on the next host"],
        "log_lines": ["one directory listing", "one backup file read"],
    },
    "stale_reuse": {
        "title": "Stale account reusing a known password",
        "needs": ["account name", "password (guessed weak or learned)"],
        "reveals": ["authenticated session on another host"],
        "unlocks": ["file reads on that host"],
        "log_lines": ["one successful sign-in line"],
    },
}

# Step skeletons per difficulty. Each list names step kinds in order;
# the generator instantiates hosts, accounts, and files, and the
# simulator proves each step's output unlocks the next one. Every
# sign-in is load-bearing: weak steps gate file reads through their
# session, and every target comes only from the previous leak, so no
# step can be skipped. Stale reuse appears in dead ends, where
# ordering does not matter. Frontier chains two file-leak hops, so
# its crown jewel sits three hosts past the entry.
TEMPLATES = {
    "easy": [
        ["exposed_page", "weak_creds", "jewel_read"],
    ],
    "medium": [
        ["exposed_page", "exposed_page", "weak_creds", "jewel_read"],
    ],
    "hard": [
        ["exposed_page", "weak_creds", "config_creds", "signin_known",
         "jewel_read"],
        ["exposed_page", "weak_creds", "backup_creds", "signin_known",
         "jewel_read"],
    ],
    "frontier": [
        ["exposed_page", "weak_creds", "config_creds", "signin_known",
         "config_creds", "signin_known", "jewel_read"],
        ["exposed_page", "weak_creds", "backup_creds", "signin_known",
         "config_creds", "signin_known", "jewel_read"],
    ],
}
