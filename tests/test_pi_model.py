"""pi inference contract, using only a synthetic provider and temporary paths."""

import subprocess
from pathlib import Path

import pytest

from a_stock_tracker.disclosures import model_command
from a_stock_tracker.research import ScreenError
from a_stock_tracker.research_service import request_info


def test_pi_configuration_fails_closed_without_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATION_PI_RUNTIME", str(tmp_path))
    monkeypatch.setenv("AUTOMATION_PI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOMATION_PI_EXTENSION", str(tmp_path / "provider.ts"))
    monkeypatch.setenv("TUSHARE_TOKEN", "synthetic")
    monkeypatch.setenv("RESEARCH_PROVIDER", "p")
    monkeypatch.setenv("RESEARCH_MODEL", "m")
    with pytest.raises(ScreenError, match="pi runtime"):
        model_command()
    assert not request_info()["modes"]["swing"]["available"]
    (tmp_path / "dist/core").mkdir(parents=True)
    (tmp_path / "dist/core/model-runtime.js").touch()
    (tmp_path / "auth.json").write_text("{}")
    (tmp_path / "provider.ts").touch()
    assert request_info()["modes"]["swing"]["available"]


def test_pi_one_call_no_tools_identity_and_output_limits():
    runner = Path(__file__).resolve().parents[1] / "scripts/automation_model_pi.mjs"
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            """
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {infer} = await import(pathToFileURL(process.argv[2]).href);
const model = {provider: 'p', id: 'm'};
const request = {provider: 'p', model: 'm', prompt: 'synthetic', timeout: 240};
let calls = 0;
let response = {provider:'p', model:'m', stopReason: 'stop', content: [{type:'text',text:'{"ok":true}'}], usage:{}};
const runtime = {completeSimple: async (selected, context, options) => {
  calls++;
  assert.equal(selected, model);
  assert.deepEqual(context.tools, []);
  assert.equal(context.messages.length, 1);
  assert.equal(context.messages[0].content, 'synthetic');
  assert.equal(options.maxTokens, 6000);
  assert.equal(options.transport, "sse");
  assert.equal(options.maxRetries, 0);
  assert.ok(options.signal instanceof AbortSignal);
  return response;
}};
assert.equal((await infer(runtime, model, request)).text, '{"ok":true}');
assert.equal(calls, 1);
await assert.rejects(infer(runtime, model, {...request, model:'other'}));
assert.equal(calls, 1);
for (const bad of [
  {stopReason:'stop', content:[{type:'toolCall',name:'bash'}]},
  {stopReason:'length', content:[{type:'text',text:'truncated'}]},
  {stopReason:'error', content:[]},
  {stopReason:'stop', content:[]},
]) {
  response = {provider:'p', model:'m', ...bad};
  await assert.rejects(infer(runtime, model, request));
}
assert.equal(calls, 5);
""",
            "synthetic-test",
            str(runner),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
