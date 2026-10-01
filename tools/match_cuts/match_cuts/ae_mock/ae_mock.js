'use strict';
/*
 * ae_mock.js -- strict mock of the After Effects scripting DOM (the CC 2019 subset match_cuts uses),
 * for running generated build_ae_project.jsx files under Node (see run_mock.js).
 *
 * Strictness (DESIGN.md section 5 export_ae, run_jsx_in_mock):
 *   - unknown member GET and SET throw (AE returns undefined / silently ignores: a typo would go unnoticed);
 *     members absent in CC 2019 (allow-list) read as undefined, like AE;
 *   - read-only members throw on write; enum-typed members only accept values of the right enum;
 *   - integer / range checks (addComp / addSolid sizes in [4, 30000], 0 < duration <= 10800,
 *     1 <= fps <= 999, layer times within +-10800 s, opacity 0..100, colours 0..1, finite numbers only,
 *     Item.comment at most 15,999 bytes);
 *   - non-remapped footage / pre-comp layers are clamped to the source extent;
 *   - frameRate reads back as float32 (Math.fround), like AE;
 *   - property() accepts matchNames only (display names are localized in AE);
 *   - spatial values read back with 3 elements; spatial tangents must match the value dimension;
 *   - keys are stored in LAYER time: changing startTime / stretch after keys were written moves them;
 *   - 1-based collections; layers.add / addSolid insert at index 1; new layers start at the comp's
 *     current time (not 0) and with hostile render switches, so the JSX must set everything explicitly;
 *   - every array handed to the script is created in the script's (poisoned, ES3-like) realm;
 *   - File.exists / File.modified use the REAL file system (so a wrong relative path or a broken join
 *     is caught); only the saved .aep is virtual (the mock never writes a project file); text files the
 *     script writes (File.open("w") / write / writeln / close) are kept in the record's files_written;
 *   - expressions: Property.valueAtTime(t, false) evaluates exactly one, thisLayer.sourceTime(time) (layer
 *     time through Time Remap, the JSX's AE time check); any other sets expressionError, like AE.
 * Scenarios (run_mock.js): default | media_missing (the media files are reported missing, openDialog
 * returns null) | new_project_null | no_marker_property | quantize_time (startTime to 10 ms, stretch to
 * 1e-3 %) | fps_misread_down / fps_misread_up (AE reads the footage at rate * 1000/1001 or * 1001/1000)
 * | fps_display_rounded (AE reports the 2-decimal display rate, e.g. 29.97 for 30000/1001: < 1e-6 off)
 * | frame_count_off (AE sees one frame more than the probe in every clip) | save_fails_existing (a recreated_edit.aep
 * from an earlier run exists and save() throws) | save_silent_fail (the .aep exists and save() returns
 * without writing) | rel_missing_abs_present (media next to the script are missing, absolute paths exist).
 * Every value the script sets is recorded; finish() returns the record (plain JSON).
 */
const path = require('path');
const fs = require('fs');
const vm = require('vm');

const T_LIMIT = 10800;
const ITEM_COMMENT_MAX_BYTES = 15999;   // Item.comment (AE Scripting Guide)

const ENUMS = {
  BlendingMode: ['NORMAL', 'DISSOLVE', 'DANCING_DISSOLVE', 'DARKEN', 'MULTIPLY', 'COLOR_BURN',
    'CLASSIC_COLOR_BURN', 'LINEAR_BURN', 'DARKER_COLOR', 'ADD', 'LIGHTEN', 'SCREEN', 'COLOR_DODGE',
    'CLASSIC_COLOR_DODGE', 'LINEAR_DODGE', 'LIGHTER_COLOR', 'OVERLAY', 'SOFT_LIGHT', 'HARD_LIGHT',
    'LINEAR_LIGHT', 'VIVID_LIGHT', 'PIN_LIGHT', 'HARD_MIX', 'DIFFERENCE', 'CLASSIC_DIFFERENCE',
    'EXCLUSION', 'SUBTRACT', 'DIVIDE', 'HUE', 'SATURATION', 'COLOR', 'LUMINOSITY', 'STENCIL_ALPHA',
    'STENCIL_LUMA', 'SILHOUETE_ALPHA', 'SILHOUETTE_LUMA', 'ALPHA_ADD', 'LUMINESCENT_PREMUL'],
  KeyframeInterpolationType: ['LINEAR', 'BEZIER', 'HOLD'],
  PropertyValueType: ['NO_VALUE', 'ThreeD_SPATIAL', 'ThreeD', 'TwoD_SPATIAL', 'TwoD', 'OneD', 'COLOR',
    'CUSTOM_VALUE', 'MARKER', 'LAYER_INDEX', 'MASK_INDEX', 'SHAPE', 'TEXT_DOCUMENT'],
  FrameBlendingType: ['FRAME_MIX', 'NO_FRAME_BLEND', 'PIXEL_MOTION'],
  LayerQuality: ['BEST', 'DRAFT', 'WIREFRAME'],
  LayerSamplingQuality: ['BICUBIC', 'BILINEAR'],
  MaskMode: ['NONE', 'ADD', 'SUBTRACT', 'INTERSECT', 'LIGHTEN', 'DARKEN', 'DIFFERENCE'],
  ImportAsType: ['COMP_CROPPED_LAYERS', 'FOOTAGE', 'COMP', 'PROJECT'],
  FieldSeparationType: ['OFF', 'UPPER_FIELD_FIRST', 'LOWER_FIELD_FIRST'],
  PulldownPhase: ['OFF', 'WSSWW', 'SSWWW', 'SWWWS', 'WWWSS', 'WWSSW', 'WSSWW_24P_ADVANCE',
    'SSWWW_24P_ADVANCE', 'SWWWS_24P_ADVANCE', 'WWWSS_24P_ADVANCE', 'WWSSW_24P_ADVANCE'],
};

// Members that exist only in AE versions newer than CC 2019: they read as undefined in the mock.
const ABSENT_IN_CC2019 = ['AVLayer.setTrackMatte', 'AVLayer.removeTrackMatte', 'AVLayer.trackMatteLayer',
  'CompItem.motionBlurAdaptiveSampleLimit', 'Project.bitsPerChannelMode'];

// Display names that scripts commonly (and wrongly, because they are localized) pass to property().
const DISPLAY_NAMES = new Set(['Transform', 'Anchor Point', 'Position', 'Scale', 'Rotation', 'Opacity',
  'Time Remap', 'Audio', 'Audio Levels', 'Masks', 'Mask 1', 'Mask Path', 'Mask Feather', 'Effects',
  'Gaussian Blur', 'Blurriness', 'Repeat Edge Pixels', 'Marker']);

// ES5+ runtime APIs that ExtendScript (ES3) lacks: deleted inside the script's context.
const POISON_ES5 = `(function () {
  var AP = Array.prototype, SP = String.prototype, FP = Function.prototype, i;
  var a = ['forEach', 'map', 'filter', 'reduce', 'reduceRight', 'some', 'every', 'indexOf', 'lastIndexOf',
    'find', 'findIndex', 'findLast', 'findLastIndex', 'includes', 'fill', 'keys', 'values', 'entries', 'flat',
    'flatMap', 'at', 'copyWithin', 'toSorted', 'toReversed', 'toSpliced', 'with'];
  for (i = 0; i < a.length; i++) { delete AP[a[i]]; }
  var s = ['trim', 'trimStart', 'trimEnd', 'trimLeft', 'trimRight', 'startsWith', 'endsWith', 'includes',
    'repeat', 'padStart', 'padEnd', 'codePointAt', 'normalize', 'at', 'replaceAll', 'matchAll'];
  for (i = 0; i < s.length; i++) { delete SP[s[i]]; }
  delete FP.bind;
  delete Array.isArray; delete Array.from; delete Array.of;
  var o = ['keys', 'create', 'defineProperty', 'defineProperties', 'getPrototypeOf', 'freeze', 'seal',
    'assign', 'entries', 'values', 'getOwnPropertyNames', 'fromEntries'];
  for (i = 0; i < o.length; i++) { delete Object[o[i]]; }
  delete Date.now; delete Date.prototype.toISOString; delete Date.prototype.toJSON;
  delete this.JSON; delete this.Map; delete this.Set; delete this.WeakMap; delete this.WeakSet;
  delete this.Promise; delete this.Symbol; delete this.Proxy; delete this.Reflect;
  delete this.globalThis;
})();`;

function createMock(opts) {
  const ctx = opts.ctx;
  const jsxPath = path.resolve(opts.jsxPath);
  const meta = opts.meta || {};
  const scenario = opts.scenario || 'default';
  const CArray = vm.runInContext('Array', ctx);
  const CError = vm.runInContext('Error', ctx);
  const CDate = vm.runInContext('Date', ctx);
  const jsxDir = path.dirname(jsxPath);

  const rec = {
    record_type: 'ae_mock', scenario: scenario, jsx_path: jsxPath, status: 'running',
    alerts: [], logs: [], mock_errors: [], clamps: [], dialogs: [], saved: [], files_written: {},
    calls: { newProject: 0, openDialog: 0, beginUndoGroup: 0, endUndoGroup: 0, importFile: 0, save: 0,
      openInViewer: 0, alert: 0 },
  };
  const savedPaths = new Map();          // virtual saved files: path -> mtime (ms)
  const virtualFiles = new Map();        // files that exist only in the scenario: path -> mtime (ms)
  if (scenario === 'save_fails_existing' || scenario === 'save_silent_fail') {
    // a recreated_edit.aep left by an earlier run (old mtime)
    virtualFiles.set(path.join(jsxDir, 'recreated_edit.aep'), 1700000000000);
  }
  const T2P = new WeakMap();
  const P2T = new WeakMap();
  const absent = new Set(ABSENT_IN_CC2019);
  if (scenario === 'no_marker_property') absent.add('CompItem.markerProperty');
  let nextId = 1;
  let undoDepth = 0;

  // ---- helpers -------------------------------------------------------------------------------
  function err(msg) {
    rec.mock_errors.push(msg);
    return new CError('AE mock: ' + msg);
  }
  function unwrap(v) {
    if (v !== null && (typeof v === 'object' || typeof v === 'function') && P2T.has(v)) return P2T.get(v);
    return v;
  }
  function carr(list) {
    const a = new CArray();
    for (let i = 0; i < list.length; i++) a.push(Array.isArray(list[i]) ? carr(list[i]) : list[i]);
    return a;
  }
  function num(v, what) {
    if (typeof v !== 'number' || !Number.isFinite(v)) throw err(what + ': expected a finite number, got ' + String(v));
    return v;
  }
  function rng(v, lo, hi, what) {
    num(v, what);
    if (v < lo || v > hi) throw err(what + ': ' + v + ' outside [' + lo + ', ' + hi + ']');
    return v;
  }
  function int(v, what, lo, hi) {
    num(v, what);
    if (!Number.isInteger(v)) throw err(what + ': expected an integer, got ' + v);
    return rng(v, lo, hi, what);
  }
  function bool(v, what) {
    if (typeof v !== 'boolean') throw err(what + ': expected a boolean, got ' + String(v));
    return v;
  }
  function str(v, what) {
    if (typeof v !== 'string') throw err(what + ': expected a string, got ' + typeof v);
    return v;
  }
  function numArr(v, n, what) {
    if (!Array.isArray(v)) throw err(what + ': expected an Array');
    const len = v.length;
    if (typeof n === 'number' ? len !== n : (n.indexOf(len) < 0)) throw err(what + ': expected ' + n + ' elements, got ' + len);
    const out = [];
    for (let i = 0; i < len; i++) out.push(num(v[i], what + '[' + i + ']'));
    return out;
  }
  function color3(v, what) {
    const c = numArr(v, 3, what);
    for (let i = 0; i < 3; i++) if (c[i] < 0 || c[i] > 1) throw err(what + ': colour components must be in [0, 1]');
    return c;
  }
  function findDesc(o, k) {
    for (let p = o; p; p = Object.getPrototypeOf(p)) {
      const d = Object.getOwnPropertyDescriptor(p, k);
      if (d) return d;
    }
    return null;
  }
  function method(target, fn) {
    return function () { return fn.apply(target, arguments); };
  }
  function wrap(t, cls) {
    if (t === null || t === undefined) return t;
    if (T2P.has(t)) return T2P.get(t);
    cls = cls || t._cls || 'Object';
    const p = new Proxy(t, {
      get(target, k) {
        if (typeof k === 'symbol') return undefined;
        if (/^\d+$/.test(k) && typeof target._index === 'function') return target._index(Number(k));
        if (absent.has(cls + '.' + k)) return undefined;
        if (k.charAt(0) === '_' || !(k in target)) {
          throw err(cls + ': unknown member "' + k + '" (After Effects returns undefined; the script must not rely on it)');
        }
        const d = findDesc(target, k);
        if (d && d.get) return d.get.call(target);
        const v = target[k];
        if (typeof v === 'function') return method(target, v);
        return v;
      },
      set(target, k, v) {
        if (typeof k === 'symbol' || k.charAt(0) === '_') throw err(cls + ': cannot set "' + String(k) + '"');
        const d = findDesc(target, k);
        if (!d) throw err(cls + ': cannot set unknown member "' + k + '" (After Effects would silently ignore it)');
        if (d.set) { d.set.call(target, v); return true; }
        throw err(cls + '.' + k + ' is read-only');
      },
      has(target, k) { return typeof k === 'string' && k.charAt(0) !== '_' && k in target; },
      deleteProperty(target, k) { throw err('cannot delete ' + cls + '.' + String(k)); },
      defineProperty() { throw err('cannot define properties on ' + cls); },
    });
    T2P.set(t, p);
    P2T.set(p, t);
    return p;
  }
  const quant = {
    startTime: (v) => (scenario === 'quantize_time' ? Math.round(v * 100) / 100 : v),
    stretch: (v) => (scenario === 'quantize_time' ? Math.round(v * 1000) / 1000 : v),
  };

  // ---- enums ---------------------------------------------------------------------------------
  class EnumValue {
    constructor(e, n) { this._cls = 'EnumValue'; this._enum = e; this._name = n; }
    toString() { return this._enum + '.' + this._name; }
  }
  const E = {};
  const enumGlobals = {};
  for (const en of Object.keys(ENUMS)) {
    E[en] = {};
    const holder = { _cls: 'Enum ' + en };
    for (const mn of ENUMS[en]) {
      E[en][mn] = new EnumValue(en, mn);
      Object.defineProperty(holder, mn, { value: wrap(E[en][mn], 'EnumValue'), enumerable: true });
    }
    enumGlobals[en] = wrap(holder, 'Enum ' + en);
  }
  function enumIn(v, en, what) {
    const t = unwrap(v);
    if (!(t instanceof EnumValue) || t._enum !== en) throw err(what + ': expected a ' + en + ' value, got ' + String(v));
    return t;
  }
  const ev = (t) => wrap(t, 'EnumValue');

  // ---- files ---------------------------------------------------------------------------------
  function isMedia(p) { return Object.prototype.hasOwnProperty.call(meta, path.basename(p)); }
  function underDir(p, dir) { const r = path.relative(dir, p); return r !== '' && !r.startsWith('..') && !path.isAbsolute(r); }
  function existsPath(p) {
    if (savedPaths.has(p) || virtualFiles.has(p)) return true;
    if (p === jsxPath) return true;
    if (isMedia(p)) {
      if (scenario === 'media_missing') return false;
      if (scenario === 'rel_missing_abs_present' && underDir(p, jsxDir)) return false;
    }
    try { return fs.existsSync(p) && fs.statSync(p).isFile(); } catch (e) { return false; }
  }
  function mtimePath(p) {
    if (savedPaths.has(p)) return savedPaths.get(p);
    if (virtualFiles.has(p)) return virtualFiles.get(p);
    if (!existsPath(p)) return null;
    try { return fs.statSync(p).mtimeMs; } catch (e) { return null; }
  }
  class FsEntry {
    constructor(p, cls) { this._cls = cls; this._p = path.resolve(str(p, cls + '(path)')); }
    get fsName() { return this._p; }
    get fullName() { return this._p; }
    get absoluteURI() { return this._p; }
    get path() { return path.dirname(this._p); }
    get name() { return path.basename(this._p); }
    get displayName() { return path.basename(this._p); }
    get exists() { return existsPath(this._p); }
    get modified() { const m = mtimePath(this._p); return m === null ? null : new CDate(Math.floor(m)); }
    get parent() { return wrap(new FolderObj(path.dirname(this._p))); }
    toString() { return this._p; }
  }
  // Text files the script writes (ae_time_check.txt): mode "w" only, kept in rec.files_written (path -> text).
  class FileObj extends FsEntry {
    constructor(p) { super(p, 'File'); this._mode = null; this._buf = ''; this._enc = 'ASCII'; this._lf = 'Unix'; }
    get encoding() { return this._enc; }
    set encoding(v) { this._enc = str(v, 'File.encoding'); }
    get lineFeed() { return this._lf; }
    set lineFeed(v) { this._lf = str(v, 'File.lineFeed'); }
    open(mode) {
      str(mode, 'File.open(mode)');
      if (mode !== 'w') throw err('File.open("' + mode + '"): the mock only writes text files (mode "w")');
      this._mode = 'w';
      this._buf = '';
      return true;
    }
    _needOpen(what) { if (this._mode === null) throw err('File.' + what + ': the file is not open'); }
    write() { this._needOpen('write'); this._buf += Array.prototype.join.call(arguments, ''); return true; }
    writeln() { this._needOpen('writeln'); this._buf += Array.prototype.join.call(arguments, '') + '\n'; return true; }
    close() {
      if (this._mode === null) return false;
      rec.files_written[this._p] = this._buf;
      savedPaths.set(this._p, Date.now());
      this._mode = null;
      return true;
    }
  }
  class FolderObj extends FsEntry {
    constructor(p) { super(p, 'Folder'); }
    get exists() { try { return fs.existsSync(this._p) || this._p === path.dirname(jsxPath); } catch (e) { return false; } }
  }
  function ctor(name, make, statics) {
    const f = function () {};
    return new Proxy(f, {
      construct(target, args) { return make.apply(null, args); },
      apply(target, self, args) {
        if (name === 'File' || name === 'Folder') return make.apply(null, args);
        throw err(name + ' must be called with new');
      },
      get(target, k) {
        if (typeof k === 'symbol') return undefined;
        if (statics && Object.prototype.hasOwnProperty.call(statics, k)) return statics[k];
        if (k === 'prototype') return target.prototype;
        throw err(name + ': unknown static member "' + k + '"');
      },
      set(target, k) { throw err('cannot set ' + name + '.' + String(k)); },
    });
  }
  const FileCtor = ctor('File', (p) => wrap(new FileObj(p)), {
    openDialog: function (prompt) {
      rec.calls.openDialog++;
      rec.dialogs.push(String(prompt));
      return null;          // the user cancelled (the mock cannot pick a file)
    },
  });
  const FolderCtor = ctor('Folder', (p) => wrap(new FolderObj(p)), {});

  // ---- properties ------------------------------------------------------------------------------
  const PV = E.PropertyValueType;
  const KI = E.KeyframeInterpolationType;
  // spec: [displayName, pvt, dims, default, {range, spatial, remap, shape, marker}]
  function propSpec(mn, owner) {
    const L = owner;
    switch (mn) {
      case 'ADBE Anchor Point': return ['Anchor Point', PV.ThreeD_SPATIAL, 3, [L._srcW() / 2, L._srcH() / 2, 0], { spatial: true }];
      case 'ADBE Position': return ['Position', PV.ThreeD_SPATIAL, 3, [L._comp._w / 2, L._comp._h / 2, 0], { spatial: true }];
      case 'ADBE Scale': return ['Scale', PV.ThreeD, 3, [100, 100, 100], {}];
      case 'ADBE Rotate Z': return ['Rotation', PV.OneD, 1, 0, {}];
      case 'ADBE Opacity': return ['Opacity', PV.OneD, 1, 100, { range: [0, 100] }];
      // remap values are source times: bounded by the source duration (may exceed 3 h), not by layer time
      case 'ADBE Time Remapping': return ['Time Remap', PV.OneD, 1, 0, { remap: true, range: [-T_LIMIT, Math.max(T_LIMIT, L._srcDur + 1)] }];
      case 'ADBE Audio Levels': return ['Audio Levels', PV.TwoD, 2, [0, 0], { range: [-1000, 24] }];
      case 'ADBE Mask Shape': return ['Mask Path', PV.SHAPE, 0, null, { shape: true }];
      case 'ADBE Mask Feather': return ['Mask Feather', PV.TwoD, 2, [0, 0], { range: [0, 1000] }];
      case 'ADBE Mask Opacity': return ['Mask Opacity', PV.OneD, 1, 100, { range: [0, 100] }];
      case 'ADBE Mask Offset': return ['Mask Expansion', PV.OneD, 1, 0, {}];
      case 'ADBE Gaussian Blur 2-0001': return ['Blurriness', PV.OneD, 1, 0, { range: [0, 3000] }];
      case 'ADBE Gaussian Blur 2-0002': return ['Blur Dimensions', PV.OneD, 1, 1, { range: [1, 3], integer: true }];
      case 'ADBE Gaussian Blur 2-0003': return ['Repeat Edge Pixels', PV.OneD, 1, 0, { range: [0, 1], integer: true }];
      case 'ADBE Slider Control-0001': return ['Slider', PV.OneD, 1, 0, { range: [-1e6, 1e6] }];
      case 'ADBE Marker': return ['Marker', PV.MARKER, 0, null, { marker: true }];
      default: return null;
    }
  }
  class Property {
    constructor(owner, mn, index, parent, compTime) {
      const sp = propSpec(mn, owner);
      if (!sp) throw err('internal: no property spec for ' + mn);
      this._cls = 'Property';
      this._owner = owner;               // AVLayer, or CompItem for comp markers
      this._mn = mn;
      this._display = sp[0];
      this._pvt = sp[1];
      this._dims = sp[2];
      this._value = Array.isArray(sp[3]) ? sp[3].slice() : sp[3];
      this._opt = sp[4];
      this._keys = [];
      this._index = index;
      this._parent = parent;
      this._compTime = !!compTime;
      this._touched = false;
      this._expr = '';
      this._exprEnabled = false;
      this._exprError = '';
    }
    _what() { return 'Property "' + this._mn + '"'; }
    _lt(t) {
      if (this._compTime) return t;
      const L = this._owner;
      return (t - L._startTime) * 100 / L._stretch;
    }
    _ct(lt) {
      if (this._compTime) return lt;
      const L = this._owner;
      return L._startTime + lt * L._stretch / 100;
    }
    _editable() {
      if (this._opt.remap && !this._owner._remap) throw err(this._what() + ': time remapping is not enabled on layer "' + this._owner._name + '"');
    }
    _validate(v) {
      const what = this._what();
      if (this._opt.shape) {
        const s = unwrap(v);
        if (!(s instanceof ShapeObj)) throw err(what + ': expected a Shape');
        return s._snapshot(what);
      }
      if (this._opt.marker) {
        const m = unwrap(v);
        if (!(m instanceof MarkerValueObj)) throw err(what + ': expected a MarkerValue');
        return { comment: m._comment, duration: m._duration };
      }
      let out;
      if (this._dims === 1) {
        out = num(v, what);
        if (this._opt.integer && !Number.isInteger(out)) throw err(what + ': expected an integer');
        if (this._opt.range) rng(out, this._opt.range[0], this._opt.range[1], what);
        return out;
      }
      if (this._dims === 2) {
        out = numArr(v, 2, what);
      } else {
        out = numArr(v, [2, 3], what);
        if (out.length === 2) out.push(this._mn === 'ADBE Scale' ? 100 : 0);
      }
      if (this._opt.range) for (let i = 0; i < out.length; i++) rng(out[i], this._opt.range[0], this._opt.range[1], what);
      return out;
    }
    _ki(i, what) { return int(i, this._what() + '.' + what + '(keyIndex)', 1, Math.max(1, this._keys.length)) - 1; }
    _needKeys(i, what) { if (this._keys.length === 0) throw err(this._what() + '.' + what + ': the property has no keys'); return this._ki(i, what); }
    _addKey(t, v) {
      num(t, this._what() + ' key time');
      rng(t, -T_LIMIT, T_LIMIT, this._what() + ' key time');
      const lt = this._lt(t);
      const val = this._validate(v);
      this._touched = true;
      for (const k of this._keys) {
        if (Math.abs(k.lt - lt) < 1e-9) { k.value = val; return; }
      }
      const key = { lt: lt, value: val, inInterp: 'DEFAULT', outInterp: 'DEFAULT' };
      if (this._opt.spatial) { key.autoBezier = true; key.continuous = true; key.inTangent = null; key.outTangent = null; }
      this._keys.push(key);
      this._keys.sort((a, b) => a.lt - b.lt);
    }
    get value() {
      if (this._opt.marker) throw err(this._what() + '.value: not supported for markers in the mock');
      if (this._opt.shape) return wrap(new ShapeObj(this._value));
      const v = this._keys.length ? this._keys[0].value : this._value;
      return Array.isArray(v) ? carr(v) : v;
    }
    setValue(v) {
      this._editable();
      if (this._keys.length > 0) throw err(this._what() + '.setValue: the property has keyframes (use setValueAtTime)');
      this._value = this._validate(v);
      this._touched = true;
    }
    setValueAtTime(t, v) { this._editable(); this._addKey(t, v); }
    setValuesAtTimes(ts, vs) {
      this._editable();
      if (!Array.isArray(ts) || !Array.isArray(vs)) throw err(this._what() + '.setValuesAtTimes: expected two Arrays');
      if (ts.length !== vs.length) throw err(this._what() + '.setValuesAtTimes: ' + ts.length + ' times but ' + vs.length + ' values');
      for (let i = 0; i < ts.length; i++) this._addKey(ts[i], vs[i]);
    }
    // valueAtTime(t, false) evaluates the property's expression; the mock knows exactly one,
    // thisLayer.sourceTime(time) (the JSX's AE time check); anything else sets expressionError like AE does
    valueAtTime(t, preExpression) {
      num(t, this._what() + '.valueAtTime');
      if (preExpression !== undefined) bool(preExpression, this._what() + '.valueAtTime(preExpression)');
      if (this._expr !== '' && this._exprEnabled && preExpression === false) {
        const L = this._owner;
        if (this._expr.replace(/\s+/g, '') === 'thisLayer.sourceTime(time)' && L instanceof AVLayer) {
          this._exprError = '';
          return L._sourceTime(t);
        }
        this._exprError = 'AE mock: unsupported expression "' + this._expr + '"';
      }
      return this.value;
    }
    get expression() { return this._expr; }
    set expression(v) { this._expr = str(v, this._what() + '.expression'); this._exprEnabled = this._expr !== ''; this._exprError = ''; }
    get expressionEnabled() { return this._exprEnabled; }
    set expressionEnabled(v) { this._exprEnabled = bool(v, this._what() + '.expressionEnabled'); }
    get expressionError() { return this._exprError; }
    get canSetExpression() { return !this._opt.marker; }
    // the keyed value at LAYER time lt (LINEAR / HOLD out-interpolation; held outside the keys)
    _valueAtLT(lt) {
      const ks = this._keys;
      if (!ks.length) return this._value;
      let i = -1;
      for (let q = 0; q < ks.length; q++) if (ks[q].lt <= lt + 1e-9) i = q;
      if (i < 0) return ks[0].value;
      if (i >= ks.length - 1) return ks[ks.length - 1].value;
      if (ks[i].outInterp === 'HOLD') return ks[i].value;
      const t0 = ks[i].lt, t1 = ks[i + 1].lt;
      if (t1 <= t0) return ks[i + 1].value;
      return ks[i].value + (lt - t0) / (t1 - t0) * (ks[i + 1].value - ks[i].value);
    }
    get numKeys() { return this._keys.length; }
    keyTime(i) { return this._ct(this._keys[this._needKeys(i, 'keyTime')].lt); }
    keyValue(i) { const v = this._keys[this._needKeys(i, 'keyValue')].value; return Array.isArray(v) ? carr(v) : v; }
    removeKey(i) { this._keys.splice(this._needKeys(i, 'removeKey'), 1); }
    setInterpolationTypeAtKey(i, inT, outT) {
      const k = this._keys[this._needKeys(i, 'setInterpolationTypeAtKey')];
      const a = enumIn(inT, 'KeyframeInterpolationType', this._what() + '.setInterpolationTypeAtKey(inType)');
      const b = outT === undefined ? a : enumIn(outT, 'KeyframeInterpolationType', this._what() + '.setInterpolationTypeAtKey(outType)');
      k.inInterp = a._name;
      k.outInterp = b._name;
    }
    keyInInterpolationType(i) { const k = this._keys[this._needKeys(i, 'keyInInterpolationType')]; return ev(E.KeyframeInterpolationType[k.inInterp === 'DEFAULT' ? 'LINEAR' : k.inInterp]); }
    keyOutInterpolationType(i) { const k = this._keys[this._needKeys(i, 'keyOutInterpolationType')]; return ev(E.KeyframeInterpolationType[k.outInterp === 'DEFAULT' ? 'LINEAR' : k.outInterp]); }
    _spatialOnly(what) { if (!this._opt.spatial) throw err(this._what() + '.' + what + ': not a spatial property'); }
    setSpatialAutoBezierAtKey(i, b) { this._spatialOnly('setSpatialAutoBezierAtKey'); this._keys[this._needKeys(i, 'setSpatialAutoBezierAtKey')].autoBezier = bool(b, 'setSpatialAutoBezierAtKey'); }
    setSpatialContinuousAtKey(i, b) { this._spatialOnly('setSpatialContinuousAtKey'); this._keys[this._needKeys(i, 'setSpatialContinuousAtKey')].continuous = bool(b, 'setSpatialContinuousAtKey'); }
    setSpatialTangentsAtKey(i, inT, outT) {
      this._spatialOnly('setSpatialTangentsAtKey');
      const k = this._keys[this._needKeys(i, 'setSpatialTangentsAtKey')];
      const n = this._pvt === PV.ThreeD_SPATIAL ? 3 : 2;
      k.inTangent = numArr(inT, n, this._what() + '.setSpatialTangentsAtKey(inTangent)');
      k.outTangent = numArr(outT === undefined ? inT : outT, n, this._what() + '.setSpatialTangentsAtKey(outTangent)');
    }
    get propertyValueType() { return ev(this._pvt); }
    get matchName() { return this._mn; }
    get name() { return this._display; }
    get isSpatial() { return !!this._opt.spatial; }
    get canVaryOverTime() { return true; }
    get isTimeVarying() { return this._keys.length > 0; }
    get propertyIndex() { return this._index; }
    get parentProperty() { return this._parent ? wrap(this._parent) : null; }
    _serialize() {
      const self = this;
      return {
        matchName: this._mn,
        value: this._opt.shape || this._opt.marker ? this._value : (Array.isArray(this._value) ? this._value.slice() : this._value),
        keys: this._keys.map(function (k) {
          const o = { layerTime: k.lt, time: self._ct(k.lt), value: k.value, inInterp: k.inInterp, outInterp: k.outInterp };
          if (self._opt.spatial) { o.autoBezier = k.autoBezier; o.continuous = k.continuous; o.inTangent = k.inTangent; o.outTangent = k.outTangent; }
          return o;
        }),
      };
    }
  }
  class PropertyGroup {
    constructor(owner, mn, display, children, parent) {
      this._cls = 'PropertyGroup';
      this._owner = owner;
      this._mn = mn;
      this._display = display;
      this._childNames = children;      // matchNames, 1-based by position
      this._children = {};
      this._parent = parent || null;
    }
    _child(mn) {
      if (!this._children[mn]) this._children[mn] = new Property(this._owner, mn, this._childNames.indexOf(mn) + 1, this);
      return this._children[mn];
    }
    property(n) {
      if (typeof n === 'number') {
        const i = int(n, this._mn + '.property(index)', 1, this._childNames.length);
        return wrap(this._child(this._childNames[i - 1]));
      }
      str(n, this._mn + '.property(name)');
      if (this._childNames.indexOf(n) >= 0) return wrap(this._child(n));
      if (DISPLAY_NAMES.has(n)) throw err(this._mn + '.property("' + n + '"): display names are localized in After Effects; use the matchName');
      throw err(this._mn + '.property("' + n + '"): unknown matchName');
    }
    get numProperties() { return this._childNames.length; }
    get matchName() { return this._mn; }
    get name() { return this._display; }
    get parentProperty() { return this._parent ? wrap(this._parent) : null; }
    _serialize(out) { for (const k of Object.keys(this._children)) out[k] = this._children[k]._serialize(); }
  }
  class MaskGroup extends PropertyGroup {
    constructor(owner, index, parent) {
      super(owner, 'ADBE Mask Atom', 'Mask ' + index, ['ADBE Mask Shape', 'ADBE Mask Feather', 'ADBE Mask Opacity', 'ADBE Mask Offset'], parent);
      this._cls = 'MaskPropertyGroup';
      this._mode = E.MaskMode.ADD;
      this._inverted = false;
      this._modeSet = false;
    }
    get maskMode() { return ev(this._mode); }
    set maskMode(v) { this._mode = enumIn(v, 'MaskMode', 'Mask.maskMode'); this._modeSet = true; }
    get inverted() { return this._inverted; }
    set inverted(v) { this._inverted = bool(v, 'Mask.inverted'); }
  }
  // effect matchName -> [display name, parameter matchNames (1-based by position)]
  const EFFECT_PARAMS = {
    'ADBE Gaussian Blur 2': ['Gaussian Blur', ['ADBE Gaussian Blur 2-0001', 'ADBE Gaussian Blur 2-0002', 'ADBE Gaussian Blur 2-0003']],
    'ADBE Slider Control': ['Slider Control', ['ADBE Slider Control-0001']],
  };
  class EffectGroup extends PropertyGroup {
    constructor(owner, mn, parent) {
      super(owner, mn, EFFECT_PARAMS[mn][0], EFFECT_PARAMS[mn][1], parent);
      this._cls = 'Effect';
      this._enabled = true;
    }
    get enabled() { return this._enabled; }
    set enabled(v) { this._enabled = bool(v, 'Effect.enabled'); }
    remove() {
      const a = this._parent._items;
      if (a.indexOf(this) < 0) throw err('Effect.remove(): the effect was already removed');
      a.splice(a.indexOf(this), 1);
    }
  }
  const EFFECTS = new Set(Object.keys(EFFECT_PARAMS));
  class IndexedGroup {
    constructor(owner, mn, display, kind) {
      this._cls = 'PropertyGroup';
      this._owner = owner;
      this._mn = mn;
      this._display = display;
      this._kind = kind;               // 'mask' | 'effect'
      this._items = [];
    }
    canAddProperty(mn) {
      str(mn, this._mn + '.canAddProperty');
      return this._kind === 'mask' ? mn === 'ADBE Mask Atom' : EFFECTS.has(mn);
    }
    addProperty(mn) {
      str(mn, this._mn + '.addProperty');
      let g;
      if (this._kind === 'mask') {
        if (mn !== 'ADBE Mask Atom') throw err('ADBE Mask Parade.addProperty("' + mn + '"): only "ADBE Mask Atom" can be added');
        g = new MaskGroup(this._owner, this._items.length + 1, this);
      } else {
        if (!EFFECTS.has(mn)) throw err('ADBE Effect Parade.addProperty("' + mn + '"): unknown effect matchName');
        g = new EffectGroup(this._owner, mn, this);
      }
      this._items.push(g);
      return wrap(g);
    }
    property(n) {
      if (typeof n === 'number') return wrap(this._items[int(n, this._mn + '.property(index)', 1, this._items.length) - 1]);
      str(n, this._mn + '.property(name)');
      for (const g of this._items) if (g._mn === n) return wrap(g);
      throw err(this._mn + '.property("' + n + '"): not found (matchNames only)');
    }
    get numProperties() { return this._items.length; }
    get matchName() { return this._mn; }
    get name() { return this._display; }
  }

  // ---- shapes and markers --------------------------------------------------------------------
  class ShapeObj {
    constructor(v) {
      this._cls = 'Shape';
      v = v || {};
      this._vertices = v.vertices || [];
      this._inTangents = v.inTangents || [];
      this._outTangents = v.outTangents || [];
      this._closed = v.closed === undefined ? true : v.closed;
      this._feather = {};
    }
    _pts(v, what) {
      if (!Array.isArray(v)) throw err('Shape.' + what + ': expected an Array of [x, y]');
      const out = [];
      for (let i = 0; i < v.length; i++) out.push(numArr(v[i], 2, 'Shape.' + what + '[' + i + ']'));
      return out;
    }
    get vertices() { return carr(this._vertices); }
    set vertices(v) { this._vertices = this._pts(v, 'vertices'); }
    get inTangents() { return carr(this._inTangents); }
    set inTangents(v) { this._inTangents = this._pts(v, 'inTangents'); }
    get outTangents() { return carr(this._outTangents); }
    set outTangents(v) { this._outTangents = this._pts(v, 'outTangents'); }
    get closed() { return this._closed; }
    set closed(v) { this._closed = bool(v, 'Shape.closed'); }
    _snapshot(what) {
      const n = this._vertices.length;
      if (n < 2) throw err(what + ': a mask shape needs at least 2 vertices');
      if (this._inTangents.length !== n || this._outTangents.length !== n) {
        throw err(what + ': vertices, inTangents and outTangents must have the same length');
      }
      return { vertices: this._vertices.map((p) => p.slice()), inTangents: this._inTangents.map((p) => p.slice()),
        outTangents: this._outTangents.map((p) => p.slice()), closed: this._closed };
    }
  }
  for (const f of ['featherSegLocs', 'featherRelSegLocs', 'featherRadii', 'featherInterps', 'featherTensions',
    'featherTypes', 'featherRelCornerAngles']) {
    Object.defineProperty(ShapeObj.prototype, f, {
      get() { return carr(this._feather[f] || []); },
      set(v) { if (!Array.isArray(v)) throw err('Shape.' + f + ': expected an Array'); this._feather[f] = Array.prototype.slice.call(v); },
    });
  }
  class MarkerValueObj {
    constructor(comment, chapter, url, frameTarget, cuePointName) {
      this._cls = 'MarkerValue';
      this._comment = str(comment, 'MarkerValue(comment)');
      this._chapter = chapter === undefined ? '' : str(chapter, 'MarkerValue(chapter)');
      this._url = url === undefined ? '' : str(url, 'MarkerValue(url)');
      this._frameTarget = frameTarget === undefined ? '' : str(frameTarget, 'MarkerValue(frameTarget)');
      this._cue = cuePointName === undefined ? '' : str(cuePointName, 'MarkerValue(cuePointName)');
      this._duration = 0;
      this._label = 0;
    }
    get comment() { return this._comment; }
    set comment(v) { this._comment = str(v, 'MarkerValue.comment'); }
    get duration() { return this._duration; }
    set duration(v) { this._duration = rng(v, 0, T_LIMIT, 'MarkerValue.duration'); }
    get chapter() { return this._chapter; }
    set chapter(v) { this._chapter = str(v, 'MarkerValue.chapter'); }
    get url() { return this._url; }
    set url(v) { this._url = str(v, 'MarkerValue.url'); }
    get label() { return this._label; }
    set label(v) { this._label = int(v, 'MarkerValue.label', 0, 16); }
  }

  // ---- items -----------------------------------------------------------------------------------
  class Item {
    constructor(proj, name, cls) {
      this._cls = cls;
      this._id = nextId++;
      this._proj = proj;
      this._name = str(name, cls + '.name');
      this._comment = '';
      this._parent = null;
      if (proj) proj._items.push(this);
    }
    get id() { return this._id; }
    get name() { return this._name; }
    set name(v) { this._name = str(v, this._cls + '.name'); }
    get comment() { return this._comment; }
    set comment(v) {
      // AE Scripting Guide: Item.comment is 'a string ... up to 15,999 bytes in length after any encoding
      // conversion'; a longer value is rejected (the item keeps its previous comment)
      const s = str(v, this._cls + '.comment');
      const nb = Buffer.byteLength(s, 'utf8');
      if (nb > ITEM_COMMENT_MAX_BYTES) {
        throw err(this._cls + '.comment: ' + nb + ' bytes (After Effects stores at most ' + ITEM_COMMENT_MAX_BYTES + ')');
      }
      this._comment = s;
    }
    get parentFolder() { return wrap(this._parent || this._proj._root); }
    set parentFolder(v) {
      const f = unwrap(v);
      if (!(f instanceof FolderItem)) throw err(this._cls + '.parentFolder: expected a FolderItem');
      this._parent = f;
    }
    get typeName() { return this._typeName(); }
  }
  class FolderItem extends Item {
    constructor(proj, name) { super(proj, name, 'FolderItem'); }
    _typeName() { return 'Folder'; }
    get numItems() { const self = this; return this._proj._items.filter((i) => i._parent === self).length; }
  }
  class FileSource {
    constructor(item) {
      this._cls = 'FileSource';
      this._item = item;
      this._conform = 0;
      this._fields = E.FieldSeparationType.UPPER_FIELD_FIRST;   // hostile default: AE may guess fields
      this._pulldown = E.PulldownPhase.OFF;
    }
    get conformFrameRate() { return this._conform; }
    set conformFrameRate(v) {
      num(v, 'FileSource.conformFrameRate');
      if (v !== 0) rng(v, 1, 999, 'FileSource.conformFrameRate');
      this._conform = v;
    }
    get fieldSeparationType() { return ev(this._fields); }
    set fieldSeparationType(v) { this._fields = enumIn(v, 'FieldSeparationType', 'FileSource.fieldSeparationType'); }
    get removePulldown() { return ev(this._pulldown); }
    set removePulldown(v) {
      const p = enumIn(v, 'PulldownPhase', 'FileSource.removePulldown');
      if (p._name !== 'OFF' && this._fields._name === 'OFF') throw err('FileSource.removePulldown: needs field separation');
      this._pulldown = p;
    }
    get isStill() { return false; }
    get hasAlpha() { return false; }
    get nativeFrameRate() { return Math.fround(this._item._fpsNum / this._item._fpsDen); }
    get displayFrameRate() { return this._item.frameRate; }
    get file() { return wrap(this._item._file); }
  }
  class SolidSource {
    constructor(item, color) { this._cls = 'SolidSource'; this._item = item; this._color = color; }
    get color() { return carr(this._color); }
    set color(v) { this._color = color3(v, 'SolidSource.color'); }
    get isStill() { return true; }
    get hasAlpha() { return false; }
  }
  class FootageItem extends Item {
    constructor(proj, name, spec) {
      super(proj, name, 'FootageItem');
      this._solid = !!spec.solid;
      this._w = spec.width;
      this._h = spec.height;
      this._pa = spec.pixelAspect || 1;
      this._hasAudio = !!spec.has_audio && !spec.solid;
      if (this._solid) {
        this._dur = spec.duration;
        this._src = new SolidSource(this, spec.color);
      } else {
        this._file = spec.file;
        this._fpsNum = spec.fps_num;             // the rate AE READ (misread in the fps_misread_* scenarios)
        this._fpsDen = spec.fps_den;
        this._frames = spec.frames;               // the frames AE SEES (one more in frame_count_off)
        this._src = new FileSource(this);
      }
    }
    _typeName() { return 'Footage'; }
    _rate() { return this._src._conform > 0 ? this._src._conform : this._fpsNum / this._fpsDen; }
    _durationValue() {
      if (this._solid) return this._dur;
      return this._src._conform > 0 ? this._frames / this._src._conform : this._frames * this._fpsDen / this._fpsNum;
    }
    get width() { return this._w; }
    set width(v) { if (!this._solid) throw err('FootageItem.width is read-only for file footage'); this._w = int(v, 'FootageItem.width', 4, 30000); }
    get height() { return this._h; }
    set height(v) { if (!this._solid) throw err('FootageItem.height is read-only for file footage'); this._h = int(v, 'FootageItem.height', 4, 30000); }
    get pixelAspect() { return this._pa; }
    set pixelAspect(v) { this._pa = rng(v, 0.01, 100, 'FootageItem.pixelAspect'); }
    get duration() { return this._durationValue(); }
    get frameRate() { return this._solid ? 0 : Math.fround(this._rate()); }
    get frameDuration() { return this._solid ? 0 : 1 / Math.fround(this._rate()); }
    get hasAudio() { return this._hasAudio; }
    get hasVideo() { return true; }
    get mainSource() { return wrap(this._src); }
    get file() { return this._solid ? null : wrap(this._file); }
    get footageMissing() { return false; }
  }
  class LayerCollection {
    constructor(comp) { this._cls = 'LayerCollection'; this._comp = comp; }
    _index(i) {
      if (i < 1 || i > this._comp._layers.length) throw err('LayerCollection[' + i + ']: index out of range (1-based)');
      return wrap(this._comp._layers[i - 1]);
    }
    get length() { return this._comp._layers.length; }
    add(item, duration) {
      const it = unwrap(item);
      if (!(it instanceof FootageItem) && !(it instanceof CompItem)) throw err('LayerCollection.add(): expected a FootageItem or CompItem');
      if (it === this._comp) throw err('LayerCollection.add(): cannot add a comp to itself');
      if (duration !== undefined) rng(duration, 1e-6, T_LIMIT, 'LayerCollection.add(duration)');
      const kind = it instanceof CompItem ? 'comp' : (it._solid ? 'solid' : 'footage');
      const L = new AVLayer(this._comp, it, kind);
      this._comp._layers.unshift(L);
      return wrap(L);
    }
    addSolid(color, name, w, h, pa, duration) {
      const c = color3(color, 'addSolid(color)');
      str(name, 'addSolid(name)');
      int(w, 'addSolid(width)', 4, 30000);
      int(h, 'addSolid(height)', 4, 30000);
      rng(pa, 0.01, 100, 'addSolid(pixelAspect)');
      let d = this._comp._duration;
      if (duration !== undefined) { d = num(duration, 'addSolid(duration)'); if (!(d > 0 && d <= T_LIMIT)) throw err('addSolid(duration): ' + d + ' outside (0, 10800]'); }
      const f = new FootageItem(this._comp._proj, name, { solid: true, color: c, width: w, height: h, pixelAspect: pa, duration: d });
      const L = new AVLayer(this._comp, f, 'solid');
      this._comp._layers.unshift(L);
      return wrap(L);
    }
  }
  class CompItem extends Item {
    constructor(proj, name, w, h, pa, dur, fps) {
      super(proj, name, 'CompItem');
      this._w = w; this._h = h; this._pa = pa; this._duration = dur; this._fps = fps;
      this._layers = [];
      this._layerColl = new LayerCollection(this);
      this._bg = [0.2, 0.2, 0.2];
      this._frameBlending = true;              // hostile defaults: the JSX must switch these off
      this._motionBlur = true;
      this._was = 0;
      this._wad = dur;
      this._time = 7 / fps;                    // CTI not at 0: layers.add places new layers here
      this._markers = new Property(this, 'ADBE Marker', 0, null, true);
      this._opened = false;
    }
    _typeName() { return 'Composition'; }
    get layers() { return wrap(this._layerColl); }
    get numLayers() { return this._layers.length; }
    layer(i) {
      if (typeof i === 'number') {
        int(i, 'CompItem.layer(index)', 1, Math.max(1, this._layers.length));
        if (i > this._layers.length) throw err('CompItem.layer(' + i + '): out of range');
        return wrap(this._layers[i - 1]);
      }
      str(i, 'CompItem.layer(name)');
      for (const L of this._layers) if (L._name === i) return wrap(L);
      return null;
    }
    get width() { return this._w; }
    set width(v) { this._w = int(v, 'CompItem.width', 4, 30000); }
    get height() { return this._h; }
    set height(v) { this._h = int(v, 'CompItem.height', 4, 30000); }
    get pixelAspect() { return this._pa; }
    set pixelAspect(v) { this._pa = rng(v, 0.01, 100, 'CompItem.pixelAspect'); }
    get duration() { return this._duration; }
    set duration(v) { num(v, 'CompItem.duration'); if (!(v > 0 && v <= T_LIMIT)) throw err('CompItem.duration ' + v + ' outside (0, 10800]'); this._duration = v; }
    get frameRate() { return Math.fround(this._fps); }
    set frameRate(v) { this._fps = rng(v, 1, 999, 'CompItem.frameRate'); }
    get frameDuration() { return 1 / Math.fround(this._fps); }
    set frameDuration(v) { rng(v, 1 / 999, 1, 'CompItem.frameDuration'); this._fps = 1 / v; }
    get bgColor() { return carr(this._bg); }
    set bgColor(v) { this._bg = color3(v, 'CompItem.bgColor'); }
    get markerProperty() { return wrap(this._markers); }
    get workAreaStart() { return this._was; }
    set workAreaStart(v) { rng(v, 0, this._duration, 'CompItem.workAreaStart'); this._was = v; if (this._was + this._wad > this._duration + 1e-9) this._wad = this._duration - this._was; }
    get workAreaDuration() { return this._wad; }
    set workAreaDuration(v) { num(v, 'CompItem.workAreaDuration'); if (!(v > 0) || this._was + v > this._duration + 1e-9) throw err('CompItem.workAreaDuration ' + v + ' does not fit the comp'); this._wad = v; }
    get frameBlending() { return this._frameBlending; }
    set frameBlending(v) { this._frameBlending = bool(v, 'CompItem.frameBlending'); }
    get motionBlur() { return this._motionBlur; }
    set motionBlur(v) { this._motionBlur = bool(v, 'CompItem.motionBlur'); }
    get time() { return this._time; }
    set time(v) { this._time = rng(v, 0, this._duration, 'CompItem.time'); }
    get hasAudio() { return this._layers.some((L) => L._hasAudio()); }
    get hasVideo() { return true; }
    openInViewer() { rec.calls.openInViewer++; this._opened = true; return null; }
  }

  // ---- layers ----------------------------------------------------------------------------------
  class AVLayer {
    constructor(comp, source, kind) {
      this._cls = 'AVLayer';
      this._comp = comp;
      this._source = source;
      this._kind = kind;                       // 'footage' | 'solid' | 'comp'
      this._name = source._name;
      this._comment = '';
      this._startTime = comp._time;
      this._stretch = 100;
      this._srcDur = kind === 'comp' ? source._duration : source._durationValue();
      this._in = this._startTime;
      this._out = this._startTime + this._srcDur;
      this._enabled = true;
      this._audioEnabled = this._hasAudio();
      this._guide = false;
      this._blend = E.BlendingMode.NORMAL;
      this._quality = E.LayerQuality.DRAFT;               // hostile defaults (see file header)
      this._sampling = E.LayerSamplingQuality.BICUBIC;
      this._fb = E.FrameBlendingType.FRAME_MIX;
      this._motionBlur = true;
      this._remap = false;
      this._label = 0;
      this._groups = {};
      this._remapProp = new Property(this, 'ADBE Time Remapping', 2, null);
    }
    _hasAudio() {
      if (this._kind === 'footage') return this._source._hasAudio;
      if (this._kind === 'comp') return this._source._layers.some((L) => L._hasAudio());
      return false;
    }
    _srcW() { return this._kind === 'comp' ? this._source._w : this._source._w; }
    _srcH() { return this._kind === 'comp' ? this._source._h : this._source._h; }
    _extent() {
      if (this._remap || this._kind === 'solid') return null;
      const a = this._startTime, b = this._startTime + this._srcDur * this._stretch / 100;
      return a <= b ? [a, b] : [b, a];
    }
    _clamp(v, what) {
      const ext = this._extent();
      if (!ext) return v;
      const c = Math.min(Math.max(v, ext[0]), ext[1]);
      if (c !== v) rec.clamps.push({ layer: this._name, member: what, requested: v, clamped: c });
      return c;
    }
    _group(key) {
      if (!this._groups[key]) {
        if (key === 'transform') this._groups[key] = new PropertyGroup(this, 'ADBE Transform Group', 'Transform', ['ADBE Anchor Point', 'ADBE Position', 'ADBE Scale', 'ADBE Rotate Z', 'ADBE Opacity']);
        else if (key === 'audio') this._groups[key] = new PropertyGroup(this, 'ADBE Audio Group', 'Audio', ['ADBE Audio Levels']);
        else if (key === 'masks') this._groups[key] = new IndexedGroup(this, 'ADBE Mask Parade', 'Masks', 'mask');
        else if (key === 'effects') this._groups[key] = new IndexedGroup(this, 'ADBE Effect Parade', 'Effects', 'effect');
      }
      return this._groups[key];
    }
    get name() { return this._name; }
    set name(v) { this._name = str(v, 'AVLayer.name'); }
    get comment() { return this._comment; }
    set comment(v) { this._comment = str(v, 'AVLayer.comment'); }
    get index() { return this._comp._layers.indexOf(this) + 1; }
    get source() { return wrap(this._source); }
    get containingComp() { return wrap(this._comp); }
    get width() { return this._srcW(); }
    get height() { return this._srcH(); }
    get hasAudio() { return this._hasAudio(); }
    get hasVideo() { return true; }
    get enabled() { return this._enabled; }
    set enabled(v) { this._enabled = bool(v, 'AVLayer.enabled'); }
    get audioEnabled() { return this._audioEnabled; }
    set audioEnabled(v) {
      bool(v, 'AVLayer.audioEnabled');
      if (!this._hasAudio()) throw err('AVLayer.audioEnabled: layer "' + this._name + '" has no audio (check hasAudio first)');
      this._audioEnabled = v;
    }
    get guideLayer() { return this._guide; }
    set guideLayer(v) { this._guide = bool(v, 'AVLayer.guideLayer'); }
    get blendingMode() { return ev(this._blend); }
    set blendingMode(v) { this._blend = enumIn(v, 'BlendingMode', 'AVLayer.blendingMode'); }
    get quality() { return ev(this._quality); }
    set quality(v) { this._quality = enumIn(v, 'LayerQuality', 'AVLayer.quality'); }
    get samplingQuality() { return ev(this._sampling); }
    set samplingQuality(v) { this._sampling = enumIn(v, 'LayerSamplingQuality', 'AVLayer.samplingQuality'); }
    get frameBlendingType() { return ev(this._fb); }
    set frameBlendingType(v) { this._fb = enumIn(v, 'FrameBlendingType', 'AVLayer.frameBlendingType'); }
    get motionBlur() { return this._motionBlur; }
    set motionBlur(v) { this._motionBlur = bool(v, 'AVLayer.motionBlur'); }
    get label() { return this._label; }
    set label(v) { this._label = int(v, 'AVLayer.label', 0, 16); }
    get threeDLayer() { return false; }
    set threeDLayer(v) { if (bool(v, 'AVLayer.threeDLayer')) throw err('AVLayer.threeDLayer: 3D layers are not supported by the mock'); }
    get startTime() { return this._startTime; }
    set startTime(v) {
      rng(v, -T_LIMIT, T_LIMIT, 'AVLayer.startTime');
      v = quant.startTime(v);
      // moving the layer moves its in/out points (AE's +-10800 s limit applies to values set directly;
      // a layer of a > 3 h source already ends past it)
      const d = v - this._startTime;
      this._startTime = v; this._in += d; this._out += d;
    }
    get stretch() { return this._stretch; }
    set stretch(v) {
      num(v, 'AVLayer.stretch');
      if (v === 0 || Math.abs(v) > 9900) throw err('AVLayer.stretch ' + v + ' outside [-9900, 9900] \\ {0}');
      v = quant.stretch(v);
      const f = v / this._stretch, s0 = this._startTime;
      let a = s0 + (this._in - s0) * f, b = s0 + (this._out - s0) * f;
      if (a > b) { const t = a; a = b; b = t; }
      this._stretch = v; this._in = a; this._out = b;
    }
    get inPoint() { return this._in; }
    set inPoint(v) {
      rng(v, -T_LIMIT, T_LIMIT, 'AVLayer.inPoint');
      const c = this._clamp(v, 'inPoint');
      if (c >= this._out) throw err('AVLayer.inPoint ' + c + ' must be < outPoint ' + this._out + ' (layer "' + this._name + '")');
      this._in = c;
    }
    get outPoint() { return this._out; }
    set outPoint(v) {
      rng(v, -T_LIMIT, T_LIMIT, 'AVLayer.outPoint');
      const c = this._clamp(v, 'outPoint');
      if (c <= this._in) throw err('AVLayer.outPoint ' + c + ' must be > inPoint ' + this._in + ' (layer "' + this._name + '")');
      this._out = c;
    }
    // the expression sourceTime(t): layer time ((t - startTime) * 100 / stretch), through Time Remap if enabled
    _sourceTime(t) {
      const lt = (t - this._startTime) * 100 / this._stretch;
      return this._remap ? this._remapProp._valueAtLT(lt) : lt;
    }
    get canSetTimeRemapEnabled() { return this._kind !== 'solid'; }
    get timeRemapEnabled() { return this._remap; }
    set timeRemapEnabled(v) {
      bool(v, 'AVLayer.timeRemapEnabled');
      if (v && this._kind === 'solid') throw err('AVLayer.timeRemapEnabled: cannot time-remap a solid');
      if (v && !this._remap) {
        this._remap = true;
        // AE creates two keys (layer start -> source 0, layer end -> source end); scripts must remove them
        const P = this._remapProp;
        P._keys = [];
        const lt0 = (this._in - this._startTime) * 100 / this._stretch;
        const lt1 = (this._out - this._startTime) * 100 / this._stretch;
        P._keys.push({ lt: lt0, value: lt0, inInterp: 'LINEAR', outInterp: 'LINEAR' });
        P._keys.push({ lt: lt1, value: Math.min(lt1, this._srcDur), inInterp: 'LINEAR', outInterp: 'LINEAR' });
      } else if (!v && this._remap) {
        this._remap = false;
        this._remapProp._keys = [];
        const ext = this._extent();
        if (ext) { this._in = Math.max(this._in, ext[0]); this._out = Math.min(this._out, ext[1]); }
      }
    }
    property(n) {
      if (typeof n !== 'string') throw err('AVLayer.property(' + String(n) + '): use a matchName string');
      switch (n) {
        case 'ADBE Transform Group': return wrap(this._group('transform'));
        case 'ADBE Time Remapping':
          if (!this.canSetTimeRemapEnabled) return null;
          return wrap(this._remapProp);
        case 'ADBE Audio Group': return this._hasAudio() ? wrap(this._group('audio')) : null;
        case 'ADBE Mask Parade': return wrap(this._group('masks'));
        case 'ADBE Effect Parade': return wrap(this._group('effects'));
        default: break;
      }
      if (DISPLAY_NAMES.has(n)) throw err('AVLayer.property("' + n + '"): display names are localized in After Effects; use the matchName');
      throw err('AVLayer.property("' + n + '"): unknown matchName');
    }
    moveToEnd() { const a = this._comp._layers; a.splice(a.indexOf(this), 1); a.push(this); }
    moveToBeginning() { const a = this._comp._layers; a.splice(a.indexOf(this), 1); a.unshift(this); }
    moveBefore(other) {
      const o = unwrap(other);
      if (!(o instanceof AVLayer) || o._comp !== this._comp || o === this) throw err('AVLayer.moveBefore(): expected another layer of the same comp');
      const a = this._comp._layers; a.splice(a.indexOf(this), 1); a.splice(a.indexOf(o), 0, this);
    }
    moveAfter(other) {
      const o = unwrap(other);
      if (!(o instanceof AVLayer) || o._comp !== this._comp || o === this) throw err('AVLayer.moveAfter(): expected another layer of the same comp');
      const a = this._comp._layers; a.splice(a.indexOf(this), 1); a.splice(a.indexOf(o) + 1, 0, this);
    }
    remove() { const a = this._comp._layers; a.splice(a.indexOf(this), 1); }
    _serialize(index) {
      const props = {};
      if (this._groups.transform) this._groups.transform._serialize(props);
      if (this._groups.audio) this._groups.audio._serialize(props);
      if (this._remap || this._remapProp._touched) props['ADBE Time Remapping'] = this._remapProp._serialize();
      const masks = this._groups.masks ? this._groups.masks._items.map((m) => {
        const sp = m._children['ADBE Mask Shape'];
        return {
          mode: m._mode._name, modeSet: m._modeSet, inverted: m._inverted,
          shape: sp ? sp._value : null,
          shapeKeys: sp ? sp._keys.map((k) => ({ time: sp._ct(k.lt), layerTime: k.lt, value: k.value,
            inInterp: k.inInterp, outInterp: k.outInterp })) : [],
          feather: m._children['ADBE Mask Feather'] ? m._children['ADBE Mask Feather']._value : null,
        };
      }) : [];
      const effects = this._groups.effects ? this._groups.effects._items.map((g) => {
        const params = {};
        for (const k of Object.keys(g._children)) params[k] = g._children[k]._value;
        return { matchName: g._mn, enabled: g._enabled, params: params };
      }) : [];
      return {
        index: index, name: this._name, comment: this._comment, sourceId: this._source._id,
        sourceType: this._kind, sourceName: this._source._name,
        enabled: this._enabled, audioEnabled: this._audioEnabled, hasAudio: this._hasAudio(),
        guideLayer: this._guide, blendingMode: this._blend._name, quality: this._quality._name,
        samplingQuality: this._sampling._name, frameBlendingType: this._fb._name, motionBlur: this._motionBlur,
        startTime: this._startTime, stretch: this._stretch, inPoint: this._in, outPoint: this._out,
        timeRemapEnabled: this._remap, props: props, masks: masks, effects: effects,
      };
    }
  }

  // ---- project / app -----------------------------------------------------------------------------
  class ItemCollection {
    constructor(proj) { this._cls = 'ItemCollection'; this._proj = proj; }
    _index(i) {
      if (i < 1 || i > this._proj._items.length) throw err('ItemCollection[' + i + ']: index out of range (1-based)');
      return wrap(this._proj._items[i - 1]);
    }
    get length() { return this._proj._items.length; }
    addComp(name, w, h, pa, dur, fps) {
      str(name, 'addComp(name)');
      int(w, 'addComp(width)', 4, 30000);
      int(h, 'addComp(height)', 4, 30000);
      rng(pa, 0.01, 100, 'addComp(pixelAspect)');
      num(dur, 'addComp(duration)');
      if (!(dur > 0 && dur <= T_LIMIT)) throw err('addComp(duration): ' + dur + ' outside (0, 10800]');
      rng(fps, 1, 999, 'addComp(frameRate)');
      return wrap(new CompItem(this._proj, name, w, h, pa, dur, fps));
    }
    addFolder(name) { return wrap(new FolderItem(this._proj, str(name, 'addFolder(name)'))); }
  }
  class ImportOptionsObj {
    constructor(file) {
      this._cls = 'ImportOptions';
      this._file = null;
      if (file !== undefined) this.file = file;
      this._importAs = E.ImportAsType.FOOTAGE;
      this._sequence = false;
      this._forceAlpha = false;
    }
    get file() { return wrap(this._file); }
    set file(v) { const f = unwrap(v); if (!(f instanceof FileObj)) throw err('ImportOptions.file: expected a File'); this._file = f; }
    get importAs() { return ev(this._importAs); }
    set importAs(v) { this._importAs = enumIn(v, 'ImportAsType', 'ImportOptions.importAs'); }
    canImportAs(t) {
      const e = enumIn(t, 'ImportAsType', 'ImportOptions.canImportAs');
      if (!this._file) throw err('ImportOptions.canImportAs: no file');
      return e._name === 'FOOTAGE' && this._file.exists && Object.prototype.hasOwnProperty.call(meta, path.basename(this._file._p));
    }
    get sequence() { return this._sequence; }
    set sequence(v) { this._sequence = bool(v, 'ImportOptions.sequence'); }
    get forceAlphabetical() { return this._forceAlpha; }
    set forceAlphabetical(v) { this._forceAlpha = bool(v, 'ImportOptions.forceAlphabetical'); }
  }
  class Project {
    constructor() {
      this._cls = 'Project';
      this._items = [];
      this._root = new FolderItem(null, 'Root');
      this._root._proj = this;
      this._itemsColl = new ItemCollection(this);
      this._file = null;
    }
    get items() { return wrap(this._itemsColl); }
    get numItems() { return this._items.length; }
    item(i) {
      int(i, 'Project.item(index)', 1, Math.max(1, this._items.length));
      if (i > this._items.length) throw err('Project.item(' + i + '): out of range');
      return wrap(this._items[i - 1]);
    }
    get rootFolder() { return wrap(this._root); }
    get file() { return this._file ? wrap(this._file) : null; }
    get activeItem() { return null; }
    importFile(io) {
      const o = unwrap(io);
      if (!(o instanceof ImportOptionsObj)) throw err('Project.importFile(): expected ImportOptions');
      if (!o._file) throw err('Project.importFile(): ImportOptions has no file');
      if (o._importAs._name !== 'FOOTAGE') throw err('Project.importFile(): the mock imports FOOTAGE only');
      if (!o._file.exists) throw err('Project.importFile(): file not found: ' + o._file._p);
      const base = path.basename(o._file._p);
      const m = meta[base];
      if (!m) throw err('Project.importFile(): no footage metadata for ' + base);
      rec.calls.importFile++;
      let fn = m.fps_num, fd = m.fps_den, frames = m.frames;
      if (scenario === 'fps_misread_down') { fn = m.fps_num * 1000; fd = m.fps_den * 1001; }
      if (scenario === 'fps_misread_up') { fn = m.fps_num * 1001; fd = m.fps_den * 1000; }
      if (scenario === 'fps_display_rounded') { fn = Math.round(m.fps_num / m.fps_den * 100); fd = 100; }
      if (scenario === 'frame_count_off') frames = m.frames + 1;
      return wrap(new FootageItem(this, base, {
        file: o._file, width: m.width, height: m.height, fps_num: fn, fps_den: fd,
        frames: frames, has_audio: !!m.has_audio,
      }));
    }
    save(file) {
      const f = unwrap(file);
      if (!(f instanceof FileObj)) throw err('Project.save(): expected a File');
      rec.calls.save++;
      if (scenario === 'save_fails_existing') {
        throw new CError('After Effects error: Unable to save "' + path.basename(f._p) + '" (permission denied)');
      }
      if (scenario === 'save_silent_fail') return false;       // AE showed an error dialog, nothing written
      rec.saved.push(f._p);
      const old = mtimePath(f._p);
      savedPaths.set(f._p, Math.max(Date.now(), old === null ? 0 : old + 1000));
      this._file = f;
      return true;
    }
    _serialize() {
      const self = this;
      const items = this._items.map((it) => ({ id: it._id, typeName: it._typeName(), name: it._name, comment: it._comment,
        parentFolder: it._parent ? it._parent._id : null }));
      const footage = this._items.filter((it) => it instanceof FootageItem).map((f) => ({
        id: f._id, name: f._name, comment: f._comment, solid: f._solid, width: f._w, height: f._h,
        file: f._solid ? null : f._file._p, fsName: f._solid ? null : f._file._p,
        fps_num: f._solid ? null : f._fpsNum, fps_den: f._solid ? null : f._fpsDen,
        frames: f._solid ? null : f._frames, duration: f._durationValue(), hasAudio: f._hasAudio,
        conformFrameRate: f._solid ? 0 : f._src._conform,
        fieldSeparationType: f._solid ? null : f._src._fields._name,
        removePulldown: f._solid ? null : f._src._pulldown._name,
        color: f._solid ? f._src._color : null,
        parentFolder: f._parent ? f._parent._id : null,
      }));
      const comps = this._items.filter((it) => it instanceof CompItem).map((c) => ({
        id: c._id, name: c._name, comment: c._comment, width: c._w, height: c._h, pixelAspect: c._pa,
        duration: c._duration, frameRate: c._fps, frameRateF32: Math.fround(c._fps), frameDuration: 1 / c._fps,
        bgColor: c._bg, frameBlending: c._frameBlending, motionBlur: c._motionBlur,
        workAreaStart: c._was, workAreaDuration: c._wad, opened: c._opened,
        parentFolder: c._parent ? c._parent._id : null,
        markers: c._markers._keys.map((k) => ({ time: k.lt, comment: k.value.comment })),
        layers: c._layers.map((L, i) => L._serialize(i + 1)),
      }));
      return { items: items, footage: footage, comps: comps, file: self._file ? self._file._p : null };
    }
  }
  let project = new Project();
  class App {
    constructor() { this._cls = 'Application'; }
    get project() { return wrap(project); }
    newProject() {
      rec.calls.newProject++;
      if (scenario === 'new_project_null') return null;
      project = new Project();
      return wrap(project);
    }
    beginUndoGroup(name) { str(name, 'app.beginUndoGroup(name)'); rec.calls.beginUndoGroup++; undoDepth++; }
    endUndoGroup() {
      rec.calls.endUndoGroup++;
      if (undoDepth <= 0) throw err('app.endUndoGroup() without beginUndoGroup()');
      undoDepth--;
    }
    get version() { return '16.1.3x7'; }
    get buildName() { return 'mock'; }
  }
  class Dollar {
    constructor() { this._cls = '$'; }
    get fileName() { return jsxPath; }
    get os() { return 'mock'; }
    get version() { return '4.5.5'; }
    writeln() { rec.logs.push(Array.prototype.join.call(arguments, '')); }
    write() { rec.logs.push(Array.prototype.join.call(arguments, '')); }
  }

  function install() {
    ctx.app = wrap(new App());
    ctx.$ = wrap(new Dollar());
    ctx.alert = function (msg) { rec.calls.alert++; rec.alerts.push(String(msg)); };
    ctx.File = FileCtor;
    ctx.Folder = FolderCtor;
    ctx.ImportOptions = ctor('ImportOptions', (f) => wrap(new ImportOptionsObj(f)), {});
    ctx.MarkerValue = ctor('MarkerValue', (c, ch, u, ft, cp) => wrap(new MarkerValueObj(c, ch, u, ft, cp)), {});
    ctx.Shape = ctor('Shape', () => wrap(new ShapeObj()), {});
    for (const en of Object.keys(enumGlobals)) ctx[en] = enumGlobals[en];
  }

  function finish() {
    const p = project._serialize();
    rec.project = { items: p.items, file: p.file };
    rec.footage = p.footage;
    rec.comps = p.comps;
    rec.undo_depth = undoDepth;
    rec.warnings = rec.logs.filter((l) => l.indexOf('match_cuts warning: ') === 0).map((l) => l.slice(20));
    return rec;
  }

  return { rec: rec, install: install, finish: finish };
}

module.exports = { createMock: createMock, POISON_ES5: POISON_ES5, ENUMS: ENUMS };
