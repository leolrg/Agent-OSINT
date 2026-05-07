import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../components/StepsDrawer.tsx', import.meta.url), 'utf8');

test('StepsDrawer fetches the same-origin Next proxy', () => {
  assert.match(
    source,
    /fetch\(`\/api\/scans\/\$\{scanId\}\/steps`/,
    'steps should go through the Next API route so auth is proxied consistently',
  );
  assert.doesNotMatch(
    source,
    /NEXT_PUBLIC_API_BASE/,
    'the browser should not call the Python API directly for authenticated steps',
  );
});
