import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../components/ProgressStream.tsx', import.meta.url), 'utf8');

test('ProgressStream does not recreate EventSource when connection status changes', () => {
  const eventSourceIndex = source.indexOf('new EventSource');
  assert.notEqual(eventSourceIndex, -1, 'expected ProgressStream to create an EventSource');

  const dependencyStart = source.indexOf('}, [scanId', eventSourceIndex);
  assert.notEqual(dependencyStart, -1, 'expected stream effect dependency array after EventSource setup');

  const dependencyEnd = source.indexOf(']);', dependencyStart);
  assert.notEqual(dependencyEnd, -1, 'expected dependency array to close');

  const dependencyArray = source.slice(dependencyStart, dependencyEnd);
  assert.doesNotMatch(
    dependencyArray,
    /\bstatus\b/,
    'status changes should not close and reopen the active SSE connection',
  );
});
