#!/usr/bin/env node
'use strict';
/*
 * run_mock.js -- ES3 gate + strict After Effects mock run of a generated .jsx (match_cuts Stage 7).
 *
 *   node run_mock.js <script.jsx> <footage_meta.json> <scenario> <record_out.json>
 *
 * footage_meta.json = {basename: {width, height, fps_num, fps_den, frames, has_audio}}
 * scenario          = default | media_missing | new_project_null | no_marker_property | quantize_time |
 *                     fps_misread_down | fps_misread_up | fps_display_rounded | frame_count_off | save_fails_existing |
 *                     save_silent_fail | rel_missing_abs_present   (see ae_mock.js)
 *
 * ES3 gate (the script is ExtendScript = ECMAScript 3, run by AE CC 2019+):
 *   1. ASCII only (ExtendScript decodes BOM-less files with the platform code page);
 *   2. '#' directive lines (#target) become '//#' comments (same line numbers);
 *   3. acorn 8.16.0 (vendored as acorn.js, MIT licence in ACORN_LICENSE) parses with {ecmaVersion: 3, allowReserved: 'never'} -- rejects let/const,
 *      arrows, template strings, trailing commas, getters, reserved words as property names (seg.in);
 *   4. token-level ban of ES5+ runtime APIs ExtendScript lacks: .forEach( .map( .filter( .reduce(
 *      .some( .every( .indexOf( .lastIndexOf( .trim( .bind( JSON, Object.keys/create/defineProperty,
 *      Array.isArray, Date.now, and the NaN / Infinity identifiers (strings and comments are ignored);
 * then runs the script in a vm context whose ES5+ builtins were deleted, with the strict AE DOM mock.
 * Writes the record (JSON) to <record_out.json>; the exit code is 0 unless the runner itself crashed.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const acorn = require(path.join(__dirname, 'acorn.js'));
const aeMock = require(path.join(__dirname, 'ae_mock.js'));

const BANNED_NAMES = new Set(['JSON', 'NaN', 'Infinity']);
const BANNED_METHODS = new Set(['forEach', 'map', 'filter', 'reduce', 'reduceRight', 'some', 'every', 'indexOf',
  'lastIndexOf', 'trim', 'bind']);
const BANNED_PAIRS = new Set(['Object.keys', 'Object.create', 'Object.defineProperty', 'Array.isArray', 'Date.now']);

function es3Gate(buf) {
  for (let i = 0; i < buf.length; i++) {
    if (buf[i] > 0x7f) {
      const line = buf.slice(0, i).toString('latin1').split('\n').length;
      return { ok: false, error: 'non-ASCII byte 0x' + buf[i].toString(16) + ' at offset ' + i + ' (line ' + line + ')' };
    }
  }
  const src = buf.toString('latin1');
  const code = src.replace(/^#.*$/mg, (m) => '//' + m);
  const opts = { ecmaVersion: 3, allowReserved: 'never', sourceType: 'script', locations: true };
  try {
    acorn.parse(code, opts);
  } catch (e) {
    return { ok: false, error: 'not ECMAScript 3: ' + e.message };
  }
  const toks = [];
  try {
    for (const t of acorn.tokenizer(code, opts)) toks.push(t);
  } catch (e) {
    return { ok: false, error: 'tokenizer: ' + e.message };
  }
  for (let i = 0; i < toks.length; i++) {
    const t = toks[i];
    if (t.type.label !== 'name') continue;
    const line = t.loc ? t.loc.start.line : '?';
    if (BANNED_NAMES.has(t.value)) return { ok: false, error: 'forbidden identifier ' + t.value + ' at line ' + line };
    const prev = toks[i - 1], next = toks[i + 1], prev2 = toks[i - 2];
    if (prev && prev.type.label === '.') {
      if (next && next.type.label === '(' && BANNED_METHODS.has(t.value)) {
        return { ok: false, error: 'forbidden ES5+ call .' + t.value + '( at line ' + line };
      }
      if (prev2 && prev2.type.label === 'name' && BANNED_PAIRS.has(prev2.value + '.' + t.value)) {
        return { ok: false, error: 'forbidden ES5+ API ' + prev2.value + '.' + t.value + ' at line ' + line };
      }
    }
  }
  return { ok: true, code: code };
}

function main() {
  const args = process.argv.slice(2);
  if (args.length < 4) {
    process.stderr.write('usage: node run_mock.js <script.jsx> <footage_meta.json> <scenario> <record_out.json>\n');
    process.exit(2);
  }
  const jsxPath = path.resolve(args[0]);
  const metaPath = args[1];
  const scenario = args[2];
  const outPath = args[3];
  let out;
  try {
    const gate = es3Gate(fs.readFileSync(jsxPath));
    if (!gate.ok) {
      out = { record_type: 'ae_mock', status: 'gate_failed', gate_error: gate.error, scenario: scenario, jsx_path: jsxPath,
        alerts: [], mock_errors: [], saved: [], calls: {}, comps: [], footage: [] };
    } else {
      const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
      const ctx = vm.createContext({});
      vm.runInContext(aeMock.POISON_ES5, ctx);
      const mock = aeMock.createMock({ ctx: ctx, jsxPath: jsxPath, meta: meta, scenario: scenario });
      mock.install();
      try {
        vm.runInContext(gate.code, ctx, { filename: jsxPath, timeout: 240000 });
        mock.rec.status = 'ok';
      } catch (e) {
        mock.rec.status = 'script_error';
        mock.rec.error = String((e && e.message) || e);
        mock.rec.stack = String((e && e.stack) || '').split('\n').slice(0, 8).join('\n');
      }
      out = mock.finish();
    }
  } catch (e) {
    out = { record_type: 'ae_mock', status: 'mock_crash', error: String((e && e.stack) || e), scenario: scenario,
      jsx_path: jsxPath, alerts: [], mock_errors: [], saved: [], calls: {}, comps: [], footage: [] };
  }
  fs.writeFileSync(outPath, JSON.stringify(out));
}

if (require.main === module) main();

module.exports = { es3Gate: es3Gate };
