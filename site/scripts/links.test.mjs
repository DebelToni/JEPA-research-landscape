import {test} from 'node:test';
import assert from 'node:assert/strict';
import {register} from 'tsx/esm/api';
register();
const {safeURL}=await import('../src/shared.tsx');
test('source URLs allow only credential-free absolute HTTP(S)',()=>{for(const s of ['javascript:alert(1)','data:text/html,hi','//evil.test','file:///etc/passwd','https://user:pass@example.org'])assert.equal(safeURL(s),undefined);assert.equal(safeURL('https://example.org/a'),'https://example.org/a');});
