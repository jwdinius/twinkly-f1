// The tracer page's inline module has to at least parse.
//
// Everything else in this repo is exercised by a test; the page is exercised by
// opening it. A syntax error there is not a subtle failure — the whole module
// fails to evaluate, so the canvas stays black and the panel keeps saying
// "loading the circuit manifest…" with nothing in the terminal to explain it.
// Catching that costs one `node --check`.
//
// This deliberately checks *parsing* only. Asserting behaviour would mean a DOM
// and a server; the geometry that would be worth asserting lives in
// `scripts/tracer/*.js`, which the other suites here test directly.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const PAGE = join(REPO, 'scripts', 'trace_track_limits.html');

function moduleSource() {
  const html = readFileSync(PAGE, 'utf8');
  const match = html.match(/<script type="module">([\s\S]*?)<\/script>/);
  assert.ok(match, 'the page has no <script type="module"> block');
  return match[1];
}

test('the page carries exactly one module script', () => {
  const html = readFileSync(PAGE, 'utf8');
  const count = (html.match(/<script type="module">/g) || []).length;
  assert.equal(count, 1);
});

test('the inline module parses as an ES module', () => {
  // Written beside the page so its relative `./tracer/frame.js` import would
  // resolve — `--check` does not follow imports, but a future check that does
  // will need the file in the right place.
  const scratch = join(REPO, 'scripts', '.tracer-page-syntax-check.mjs');
  writeFileSync(scratch, moduleSource());
  try {
    execFileSync(process.execPath, ['--check', scratch], { stdio: 'pipe' });
  } catch (err) {
    assert.fail(`the tracer page's module does not parse:\n${err.stderr}`);
  } finally {
    unlinkSync(scratch);
  }
});

test('the module imports the shared frame transform rather than inlining one', () => {
  // Python is the authority for the projection and a golden fixture binds the
  // JS to it (ADR-0004). A second copy pasted into the page would not be bound
  // to anything.
  assert.match(moduleSource(), /import\s*\{[\s\S]*?\}\s*from\s*'\.\/tracer\/frame\.js'/);
});
