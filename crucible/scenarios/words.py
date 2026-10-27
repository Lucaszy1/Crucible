"""Word pools for generated scenarios: hosts, accounts, files, pages.

Pools are large enough that draws without replacement never collide
inside one scenario (at most 10 hosts). Draws are seeded per
scenario, so names vary between scenarios but repeat for one seed.
Passwords come only from the toy pools below, never random strings.
"""

from __future__ import annotations

ROLES = (
    "web portal",
    "app",
    "api",
    "database",
    "file share",
    "backup",
    "workstation",
    "jump host",
)

SEGMENTS = ("dmz", "internal", "data")

# Business weight per role, mirroring the classic scenario's weights
# (web-like 3, data store 5, workstation 1).
ROLE_WEIGHTS = {
    "web portal": 3,
    "app": 3,
    "api": 3,
    "database": 5,
    "file share": 2,
    "backup": 2,
    "workstation": 1,
    "jump host": 2,
}

# Plausible segments per role. The entry host always sits in dmz and
# the crown jewel host always sits in data; the rest follow this map.
ROLE_SEGMENTS = {
    "web portal": ("dmz",),
    "app": ("internal",),
    "api": ("dmz", "internal"),
    "database": ("data",),
    "file share": ("internal", "data"),
    "backup": ("data",),
    "workstation": ("internal",),
    "jump host": ("internal",),
}

# Service name per role for the host's service list.
ROLE_SERVICES = {
    "web portal": ("webapp",),
    "app": ("reportapi",),
    "api": ("gateway",),
    "database": ("fakestore",),
    "file share": ("fileshare",),
    "backup": ("vaultsync",),
    "workstation": ("desktop",),
    "jump host": ("bastion",),
}

# Roles whose hosts answer HTTP requests (entry probing needs one).
HTTP_ROLES = ("web portal", "api")

# Roles with no sign-in gate: sign-in attempts there log note=no-gate
# exactly like the classic workstation.
NO_GATE_ROLES = ("workstation",)

HOST_WORDS = (
    "harbor", "meadow", "granite", "willow", "cobalt", "prairie",
    "ember", "fjord", "grove", "harborlight", "inlet", "juniper",
    "kelp", "lantern", "magpie", "northfield", "otter", "pillow",
    "quartz", "ridgeline", "sorrel", "tarn", "upland", "vetch",
    "wren", "yarrow", "zephyr", "alder", "birch", "clover",
    "dune", "elm", "flint", "gale", "heath", "iris",
    "jade", "kestrel", "lark", "maple", "nimbus", "ochre",
    "pine", "quill", "rover", "sedge", "thistle", "umber",
)

ACCOUNT_WORDS = (
    "archivist", "scheduler", "monitor", "deployer", "reporter",
    "indexer", "collector", "notifier", "syncer", "auditor-svc",
    "metrics", "backup-op", "importer", "exporter", "cleaner",
    "watcher", "provisioner", "registrar", "courier", "librarian",
    "analyst-svc", "planner", "tracker", "factotum", "steward",
    "herald", "keeper", "mender", "runner", "scribe",
    "tender", "usher",
)

DIR_WORDS = (
    "conf", "data", "state", "var", "opt", "srv", "etc",
    "spool", "cache", "store", "records", "shared",
    "vault", "drops", "incoming", "archive",
)

# Config file names. Every one trips a generic sensitive-file word
# (config, credential, .ini, .env) so the config-read step always
# shows up to the generic sensors, exactly like the classic config.
CONFIG_FILES = (
    "app-config.ini",
    "service-credentials.env",
    "conn-config.ini",
    "db-credentials.env",
    "worker-config.ini",
    "api-credentials.env",
    "sync-config.ini",
    "store-credentials.env",
    "agent-config.ini",
    "relay-credentials.env",
    "task-config.ini",
    "cache-credentials.env",
)

# Backup or export file names, also sensitive-marked: each is a
# backup of credential material, so a read still trips the generic
# sensitive-file sensor.
BACKUP_FILES = (
    "creds-snapshot.env",
    "accounts-export.ini",
    "vault-backup.env",
    "secrets-export.ini",
    "keys-snapshot.env",
    "logins-export.ini",
    "identity-backup.env",
    "access-export.ini",
    "passwd-snapshot.env",
    "tokens-export.ini",
)

JEWEL_FILES = (
    "ledger-secret.txt",
    "master-secret.txt",
    "vault-secret.txt",
    "crown-secret.txt",
    "core-secret.txt",
    "prime-secret.txt",
    "trust-secret.txt",
    "atlas-secret.txt",
    "beacon-secret.txt",
    "cipher-secret.txt",
    "delta-secret.txt",
    "ember-secret.txt",
)

# Exposed admin pages. Every one contains "admin" so a request trips
# the generic privileged-page sensor.
ADMIN_PAGES = (
    "/admin",
    "/admin/status",
    "/admin/health",
    "/admin/console",
    "/admin/panel",
    "/admin/debug",
    "/admin/config",
    "/admin/metrics",
)

# Benign pages that never trip any sensor rule.
BENIGN_PAGES = (
    "/",
    "/status",
    "/health",
    "/metrics",
    "/version",
    "/ping",
)

# Harmless admin-looking decoy pages: they contain "admin", so a
# probe trips the privileged-page sensor, but they leak nothing.
DECOY_ADMIN_PAGES = (
    "/admin-old",
    "/old-admin",
    "/admin-note",
    "/admin-moved",
    "/admin-help",
    "/admin-legacy",
)

# Tiny guessable pool for default/weak credentials. Red guesses from
# this pool; leaked passwords never come from it.
WEAK_PASSWORDS = ("changeme", "letmein", "welcome1", "admin123")

# Background noise sign-in names. Never equal to real accounts.
NOISE_USERS = (
    "guest", "operator", "temp", "intern", "visitor", "contractor",
)


def leaked_password(word: str, number: int) -> str:
    """Build one toy leaked password: guess-proof but obviously fake."""
    return f"toy-{word}-{number:04d}"
