"""One JSON-in/JSON-out inference in the installed Hermes interpreter; no tools."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys


def main() -> None:
    # Never let the parent agent's task or plugin context widen this worker's authority.
    for key in list(os.environ):
        if key.startswith("HERMES_KANBAN") or key in {"PYTHONPATH", "TUSHARE_TOKEN"}:
            os.environ.pop(key, None)
    os.environ.update(HERMES_SAFE_MODE="1", HERMES_IGNORE_RULES="1", HERMES_IGNORE_USER_CONFIG="1")
    from pathlib import Path

    request = json.loads(Path(sys.argv[-1]).read_text())
    agent = None
    with contextlib.redirect_stdout(io.StringIO()):
        import hermes_bootstrap  # noqa: F401 — activate the installed runtime before other imports
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from run_agent import AIAgent

        runtime = resolve_runtime_provider(
            requested=request["provider"], target_model=request["model"]
        )
        try:
            agent = AIAgent(
                api_key=runtime.get("api_key"),
                base_url=runtime.get("base_url"),
                provider=runtime.get("provider"),
                api_mode=runtime.get("api_mode"),
                model=request["model"],
                enabled_toolsets=[],
                quiet_mode=True,
                max_iterations=1,
                max_tokens=6000,
                run_budget_seconds=request["timeout"],
                skip_context_files=True,
                load_soul_identity=False,
                skip_memory=True,
                skip_background_review=True,
                session_db=None,
                save_trajectories=False,
                fallback_model=None,
                platform="tool",
                reasoning_config={"effort": "medium"},
            )
            if agent.tools or agent.valid_tool_names:
                raise RuntimeError("No-tool boundary violated")
            result = agent.run_conversation(request["prompt"])
            if agent.tools or any(m.get("tool_calls") for m in result.get("messages", [])):
                raise RuntimeError("Unexpected tool activity")
            text = result.get("final_response") or ""
            if not text or result.get("error"):
                raise RuntimeError("Model returned no usable answer")
            response = {
                "text": text,
                "tool_count": 0,
                "model": request["model"],
                "provider": runtime.get("provider"),
                "usage": {
                    k: v
                    for k, v in result.items()
                    if "token" in k or k in {"cost", "elapsed_seconds"}
                },
            }
        finally:
            if agent is not None:
                agent.close()
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider exceptions can contain URLs or credentials. Do not echo them.
        import traceback

        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "executable": sys.executable,
                    "version": sys.version,
                    "module": exc.name if isinstance(exc, ModuleNotFoundError) else None,
                    "locations": [
                        (f.filename, f.lineno, f.name)
                        for f in traceback.extract_tb(exc.__traceback__)
                    ],
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
