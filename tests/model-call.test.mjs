import test from 'node:test';
import assert from 'node:assert/strict';
import {complete, selectedProvider, main} from '../scripts/model-call.mjs';

test('tool-free call bounded selected model metadata', async () => {
  const model = {id: 'test', provider: 'chosen'};
  const runtime = {async completeSimple(m, context, options) {
    assert.equal(m, model);
    assert.deepEqual(context.tools, []);
    assert.equal(context.messages.length, 1);
    assert.equal(options.toolChoice, 'none');
    assert.equal(options.maxRetries, 0);
    assert.equal(options.transport, 'sse');
    assert.equal(options.reasoning, 'low');
    assert.equal(options.onPayload({a: 1}).service_tier, 'priority');
    assert.ok(options.signal instanceof AbortSignal);
    return {content: [{type: 'text', text: '{}'}], stopReason: 'stop', usage: {input: 12, output: 2}};
  }};
  const result = await complete(runtime, model, {system: 'safe', prompt: 'untrusted', timeout_ms: 1000},
    {thinking: 'low', serviceTier: 'priority'});
  assert.equal(result.text, '{}');
  assert.equal(result.provider, 'chosen');
  assert.equal(result.usage.input, 12);
});

test('tool calls rejected and stop/usage preserved', async () => {
  const runtime = {completeSimple: async () => ({content: [{type: 'toolCall', name: 'shell'}],
    stopReason: 'toolUse', usage: {output: 10}})};
  const result = await complete(runtime, {}, {system: '', prompt: ''}, {thinking: 'low'});
  assert.equal(result.rejected, 'tool_call');
  assert.equal(result.text, '');
  assert.equal(result.stopReason, 'toolUse');
  assert.equal(result.usage.output, 10);
});

test('selection missing fails closed', async () => {
  await assert.rejects(main({}, {}), /Missing configuration/);
});

test('no retries when provider fails', async () => {
  let count = 0;
  await assert.rejects(complete({completeSimple: async () => {count++; throw new Error('throttled');}},
    {}, {system: '', prompt: ''}, {thinking: 'low'}), /throttled/);
  assert.equal(count, 1);
});

test('invalid limits/transport rejected', async () => {
  await assert.rejects(complete({}, {}, {system: '', prompt: '', timeout_ms: 0}, {}));
  await assert.rejects(complete({}, {}, {system: '', prompt: ''}, {}, 'arbitrary'));
});

test('selected provider delegates only OAuth and preserves account identity', async () => {
  const base = {id: 'openai-codex', auth: {oauth: {
    refresh: async () => ({access: 'synthetic'}), toAuth: value => value}},
    getModels: () => [{id: 'test', provider: 'openai-codex'}], stream() {}, streamSimple() {}};
  const provider = selectedProvider(base, 'selected');
  assert.equal(provider.getModels()[0].provider, 'selected');
  const next = await provider.auth.oauth.refresh({accountId: 'fixture'}, undefined);
  assert.equal(next.accountId, 'fixture');
  assert.equal(next.providerId, 'selected');
  await assert.rejects(provider.auth.oauth.login());
});
