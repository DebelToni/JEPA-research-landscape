/** Tool-free selected-account Codex adapter. Configuration is runtime-only.
 * PI_MODULES_DIR: directory containing pi-ai/ and pi-coding-agent/.
 * JEPA_PROFILE_PATH: JSON object {model,thinking,serviceTier?}, centrally resolved by caller.
 * JEPA_PROVIDER: exact selected account provider ID; JEPA_AUTH_PATH: Pi auth.json.
 * JEPA_MODELS_PATH / JEPA_MODELS_STORE_PATH: optional isolated config and cached model catalog.
 * JEPA_TRANSPORT: sse|websocket|auto (default sse).
 * stdin: {system,prompt,max_tokens?,timeout_ms?}; stdout: {text,usage,stopReason,model,provider,...}.
 * No personal session, resource loader, extensions, tools, rotation or paid fallback.
 */
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

export function selectedProvider(base, providerId) {
  const oauth = base?.auth?.oauth;
  if (!oauth) throw new Error('Codex OAuth unavailable');
  const models = () => base.getModels().map(m => ({...m, provider: providerId}));
  return {...base, id: providerId, name: providerId,
    auth: {oauth: {name: providerId,
      login: async () => {throw new Error('Interactive login forbidden');},
      refresh: async (credential, signal) => {
        const next = await oauth.refresh(credential, signal);
        return {...next, accountId: next.accountId ?? credential.accountId, providerId};
      },
      toAuth: credential => oauth.toAuth(credential)}},
    getModels: models, getAllModels: models,
    stream: base.stream.bind(base), streamSimple: base.streamSimple.bind(base)};
}

export async function complete(runtime, model, input, selection, transport = 'sse') {
  if (!['sse', 'websocket', 'auto'].includes(transport)) throw new Error('Invalid transport');
  if (typeof input.system !== 'string' || typeof input.prompt !== 'string') throw new Error('Invalid prompt');
  const timeout = input.timeout_ms ?? 180000;
  const maxTokens = input.max_tokens ?? 12000;
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > 3600000 ||
      !Number.isInteger(maxTokens) || maxTokens < 1 || maxTokens > 32000) throw new Error('Invalid call bounds');
  const started = new Date().toISOString();
  const result = await runtime.completeSimple(model, {
    systemPrompt: input.system,
    messages: [{role: 'user', content: input.prompt, timestamp: Date.now()}], tools: []
  }, {toolChoice: 'none', transport, maxRetries: 0, maxTokens,
    reasoning: selection.thinking, signal: AbortSignal.timeout(timeout),
    onPayload: payload => selection.serviceTier ? {...payload, service_tier: selection.serviceTier} : payload});
  const metadata = {usage: result.usage ?? null, stopReason: result.stopReason,
    model: model.id, provider: model.provider, thinking: selection.thinking,
    serviceTier: selection.serviceTier ?? null, transport, diagnostic: result.stopReason === "error" ? "provider_response_error" : null, started_at: started, ended_at: new Date().toISOString()};
  if (result.content.some(p => p.type === 'toolCall')) return {...metadata, text: '', rejected: 'tool_call'};
  return {...metadata, text: result.content.filter(p => p.type === 'text').map(p => p.text).join('')};
}

export async function main(input, env = process.env) {
  for (const key of ['PI_MODULES_DIR', 'JEPA_PROFILE_PATH', 'JEPA_PROVIDER', 'JEPA_AUTH_PATH'])
    if (!env[key]) throw new Error(`Missing configuration: ${key}`);
  const selection = JSON.parse(await readFile(env.JEPA_PROFILE_PATH, 'utf8'));
  if (!selection.model || !selection.thinking) throw new Error('Resolved profile required');
  const load = name => import(pathToFileURL(resolve(env.PI_MODULES_DIR, name)).href);
  const {ModelRuntime} = await load('pi-coding-agent/dist/index.js');
  const {clampThinkingLevel} = await load('pi-ai/dist/models.js');
  const runtime = await ModelRuntime.create({authPath: env.JEPA_AUTH_PATH,
    modelsPath: env.JEPA_MODELS_PATH ?? null, modelsStorePath: env.JEPA_MODELS_STORE_PATH,
    refreshOnCreate: true, allowModelNetwork: false});
  const provider = env.JEPA_PROVIDER;
  const base = runtime.getProvider('openai-codex');
  if (provider !== 'openai-codex') runtime.registerNativeProvider(selectedProvider(base, provider));
  const credential = (await runtime.listCredentials()).find(c => c.providerId === provider);
  if (credential?.type !== 'oauth') throw new Error('Selected OAuth credential unavailable');
  const model = runtime.getModel(provider, selection.model);
  if (!model || model.api !== 'openai-codex-responses') throw new Error('Selected Codex model unavailable');
  if (clampThinkingLevel(model, selection.thinking) !== selection.thinking)
    throw new Error('Unsupported thinking level');
  return complete(runtime, model, input, selection, env.JEPA_TRANSPORT ?? 'sse');
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    let raw = '';
    for await (const part of process.stdin) {raw += part; if (raw.length > 2000000) throw new Error('Input too large');}
    process.stdout.write(JSON.stringify(await main(JSON.parse(raw))));
  } catch {console.error('Selected-account extraction call failed; no retry or fallback.'); process.exitCode = 1;}
}
