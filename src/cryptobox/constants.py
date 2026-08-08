from __future__ import annotations

import os

APP_NAME = "Cryptobox"
FORMAT_VERSION = 1
FILE_MAGIC = b"CRBOXF01"
HEADER_SIZE = 256
HEADER_MAC_SIZE = 32
DEFAULT_CHUNK_SIZE = 1024 * 1024
TAG_SIZE = 16
NONCE_PREFIX_SIZE = 4
CONTROL_DIR = ".cryptobox"
META_FILE = "vault.json"
INDEX_FILE = "index.sqlite3"
LOG_DIR = "logs"
TEMP_PREFIX = ".cryptobox-tmp-"

KDF_MEMORY_KIB = 64 * 1024
KDF_ITERATIONS = 3
KDF_LANES = 4
KDF_LENGTH = 32

DEFAULT_PORT = 8787
MAX_WORKERS = min(4, max(2, os.cpu_count() or 2))

