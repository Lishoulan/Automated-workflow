from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_TEXT_ENCODING = "utf-8"
DEFAULT_JSON_ENCODING = "utf-8-sig"


def read_text(path: Path, *, encoding: str = DEFAULT_TEXT_ENCODING, errors: str = "replace") -> str:
    return path.read_text(encoding=encoding, errors=errors)


def write_text(path: Path, content: str, *, encoding: str = DEFAULT_TEXT_ENCODING) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)


def read_json(path: Path, *, encoding: str = DEFAULT_JSON_ENCODING) -> Any:
    return json.loads(path.read_text(encoding=encoding))


def write_json(path: Path, payload: Any, *, encoding: str = DEFAULT_TEXT_ENCODING) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding=encoding)
