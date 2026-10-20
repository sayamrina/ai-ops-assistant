"""Command line helpers: python -m app.cli [ingest | create-key NAME ROLE]"""
import json
import sys

from .config import get_settings
from .llm import build_llm
from .security import ROLE_LEVEL, hash_key, new_api_key
from .services import build_index


def main(argv: list[str]) -> None:
    if argv[:1] == ["ingest"]:
        settings = get_settings()
        index = build_index(settings, build_llm(settings))
        print(f"Indexed {len(index.chunks)} sections with provider '{settings.llm_provider}'")
    elif argv[:1] == ["create-key"] and len(argv) == 3 and argv[2] in ROLE_LEVEL:
        key = new_api_key()
        print("API key (shown once, give it to the user):", key)
        print("Add to OPS_API_KEYS:", json.dumps({hash_key(key): {"name": argv[1], "role": argv[2]}}))
    else:
        print(__doc__)
        print("Roles:", ", ".join(ROLE_LEVEL))


if __name__ == "__main__":
    main(sys.argv[1:])
