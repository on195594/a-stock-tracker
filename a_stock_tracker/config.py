"""Research references are data, independent of personal follows and retired pools."""

import json
from pathlib import Path

from a_stock_tracker.paths import CONFIG_DIR
from a_stock_tracker.research import ScreenError, base_code, normalize_code


def read_anchors(path: Path | None = None) -> list[dict[str, str]]:
    path = path if path is not None else CONFIG_DIR / "anchors.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not raw:
            raise ValueError("empty references")
        result = []
        seen = set()
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ValueError("invalid reference")
            code = item.get("code")
            if not isinstance(code, str) or not item["name"].strip():
                raise ValueError("invalid reference")
            code = normalize_code(code)
            if code in seen:
                raise ValueError("duplicate reference")
            seen.add(code)
            reason = item.get("discovery_unavailable", "")
            if not isinstance(reason, str):
                raise ValueError("invalid discovery availability")
            result.append(
                {
                    "code": base_code(code),
                    "ts_code": code,
                    "name": item["name"].strip(),
                    **({"discovery_unavailable": reason} if reason else {}),
                }
            )
        return result
    except (OSError, ValueError, TypeError, ScreenError) as exc:
        raise ScreenError("参照清单不可用，请检查 config/anchors.json") from exc
