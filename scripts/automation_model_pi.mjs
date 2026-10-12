// Use pi's provider runtime directly: no agent session, tools, skills or context files.
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export async function infer(runtime, model, request) {
  if (!model || model.provider !== request.provider || model.id !== request.model) {
    throw new Error("Model identity mismatch");
  }
  const response = await runtime.completeSimple(model, {
    messages: [{ role: "user", content: request.prompt, timestamp: Date.now() }],
    tools: [],
  }, {
    maxTokens: 6000,
    transport: "sse",
    maxRetries: 0,
    reasoning: "medium",
    signal: AbortSignal.timeout(request.timeout * 1000),
  });
  if (response.provider !== model.provider || response.model !== model.id ||
      response.stopReason !== "stop" || response.content.some(c => c.type === "toolCall")) {
    throw Object.assign(new Error("Model returned incomplete output or tool activity"), {usage: response.usage});
  }
  const text = response.content.filter(c => c.type === "text").map(c => c.text).join("");
  if (!text.trim()) throw Object.assign(new Error("Model returned no text"), {usage: response.usage});
  return { text, tool_count: 0, provider: model.provider, model: model.id, usage: response.usage };
}

async function main() {
  process.env.PI_OFFLINE = "1"; // Use the installed catalog, never discover extra models.
  const request = JSON.parse(await readFile(process.argv[2], "utf8"));
  const base = process.env.AUTOMATION_PI_RUNTIME;
  const load = name => import(pathToFileURL(join(base, "dist/core", name)).href);
  const { ModelRuntime } = await load("model-runtime.js");
  const { loadExtensions } = await load("extensions/loader.js");
  const loaded = await loadExtensions([process.env.AUTOMATION_PI_EXTENSION], base);
  if (loaded.errors.length || loaded.extensions.some(e => e.tools.size || e.handlers.size)) {
    throw new Error("Provider extension must not register tools or event handlers");
  }
  const runtime = await ModelRuntime.create({
    authPath: join(process.env.AUTOMATION_PI_AGENT_DIR, "auth.json"),
    modelsPath: null,
    allowModelNetwork: false,
    refreshOnCreate: false,
  });
  for (const entry of loaded.runtime.pendingProviderRegistrations) {
    if (entry.name === request.provider) runtime.registerProvider(entry.name, entry.config);
  }
  const model = runtime.getModel(request.provider, request.model);
  if (!model) throw new Error("Configured pi model not found");
  if (process.argv[3] === "--check") {
    // Metadata only; never resolve/refresh OAuth or send a model request during deployment.
    const auth = JSON.parse(await readFile(join(process.env.AUTOMATION_PI_AGENT_DIR, "auth.json"), "utf8"));
    if (!auth[request.provider]) throw new Error("Provider credentials not configured");
    console.log(JSON.stringify({ provider: model.provider, model: model.id, tool_count: 0 }));
    return;
  }
  console.log(JSON.stringify(await infer(runtime, model, request)));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    console.log(JSON.stringify({failed: true, usage: error.usage ?? null}));
    console.error("pi inference failed; provider details redacted");
    process.exitCode = 1;
  });
}
