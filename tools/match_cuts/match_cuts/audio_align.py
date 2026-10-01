"""Stage 5.1 (coarse audio alignment) and Stage 5.6 (audio per segment) -- DESIGN.md §5 audio_align.py.

Plain numpy/scipy DSP (no librosa):

``features``
    Own STFT (Hann, n_fft = 64 ms) -> HTK log-mel filterbank, six octave-wide log-frequency bands and a
    spectral-flux onset envelope, all at ``cfg.audio_feat_rate`` Hz; frame ``i`` is centred on
    ``t = i / rate`` (same convention for competitor and RAW, so lags are unbiased).

``coarse_align``  (prompt 5.1)
    Every ~1 s competitor window (hop 0.25 s) is cross-correlated against the WHOLE RAW with an
    FFT-based, exactly normalised NCC (overlap-save blocks; the sliding RAW energy comes from
    cumulative sums) on a cheap *coarse* feature: onset envelope + octave-band log energies (per-file
    z-scored); for a time-scaled window the octave bands are tape-warped and the onset envelope is
    pitch-invariant. The top coarse peaks (non-max suppression ±0.3 s) are verified on the *full*
    feature -- delta log-mel (measured to separate true matches from the null far better than plain
    log-mel) -- under two hypotheses: tape (competitor mel filters warped by v, i.e. pitch follows
    speed) and pitch-preserving (unwarped). ``conf`` = peak / second peak, the second peak being the
    best verified candidate outside ±0.3 s or -- if larger -- the null level (max and mean + 4.5 std of
    the full NCC at 64 random lags, never below 0.38), so windows without a genuine match stay
    below 1.3; ``psr`` = peak-to-sidelobe ratio of the coarse curve (outside ±0.3 s).
    Windows weak at speed 1.00 are re-searched with time-scaled windows (v = 0.90 .. 1.30, step
    0.01): a decimated (25 Hz) coarse scan over all speeds shortlists speeds (and must beat 1.00 by
    0.05), the full search runs at those; found speeds are propagated to neighbouring windows (also
    replacing weaker confident results) and tried on the windows between scanned ones. When no probe
    window matches at any speed and nothing matched at 1.00, the scan stops early (audio replaced).
    Confident windows are refined on the 16 kHz waveform: speed 1 -> whole-window NCC within
    ±audio_refine_ms (sub-sample peak) + a quarter-window drift check; other speeds -> robust line
    through the lags of the four window quarters (tape-style resampling of the window) at candidate
    speeds around the feature estimate, giving offset and speed to ~1e-4, then a whole-window NCC.

``xcorr_lag``
    Normalised cross-correlation lag of two equally long signals (b delayed by lag vs a), with a
    band-limited sub-sample refinement (~0.02 sample).

``analyze_segments_audio``  (prompt 5.6)
    Per segment: the RAW-rebuilt audio (tape-style resampling for v != 1 like AE's stretch,
    Kaiser-windowed sinc) is compared with the competitor: J/L offsets at hard cuts (audio switch
    point from a two-model local-NCC segmentation), lag/corr over the segment's audio range, pitch
    preservation for speed != 1 (whitened log-frequency spectrum at shift log2(v) vs 0, confirmed by a
    time-aligned tape-vs-unwarped delta-log-mel test that also gates on the audio being related to
    the RAW at all), added audio (music bed / SFX / voice-over) from the residual energy after
    subtracting the lag-compensated, gain-fitted rebuilt track, and the run status ok / no_audio /
    audio_replaced.

Both stages pin OpenBLAS to one thread while they run (``single_thread_blas``): they issue thousands
of small matrix products, which a multi-threaded BLAS on a busy machine makes ~100x slower.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Sequence

import numpy as np

from .common import DecisionLog, log, null_dlog, parse_fps
from .model import AudioHints, Segment

# ---------------------------------------------------------------------------------------------
# Tuning constants (defaults; the ones that are worth tuning are read with getattr(cfg, ...))
# ---------------------------------------------------------------------------------------------
_LOG_FLOOR = 1e-10            # power floor of the log features (-100 dB re a full-scale sine)
_N_BROAD = 6                  # octave bands 125 Hz .. 4 kHz (centres)
_BROAD_F0 = 125.0
_ONSET_WEIGHT = 2.0           # weight of the onset channel vs one octave band in the coarse NCC
_SCAN_DECIM = 4               # decimation of the coarse feature for the speed scan (100 -> 25 Hz)
_TOP_K = 10                   # coarse peaks verified on the full feature
_VERIFY_R = 3                 # +- frames searched around each coarse peak on the full feature
_EXCL_S = 0.3                 # the second peak lies outside +- this (s)
_NULL_LAGS = 64               # random lags used to estimate the full-feature null distribution
_NULL_Z = 4.5                 # null floor = mean + 4.5 std of the full NCC at random lags
_NULL_FLOOR_MIN = 0.38        # never below the typical whole-RAW null maximum of the delta-log-mel NCC
_WAVE_MIN_PEAK = 0.3          # waveform NCC needed to trust the sample-precise refinement
_SILENT_RMS = 10 ** (-70 / 20)
_KAISER_BETA = 8.0
_SINC_HALF = 16               # windowed-sinc half width (input samples at full bandwidth)
_SINC_PHASES = 1024
_TAPE, _TEMPO = "tape", "tempo"
_ADDED_FRAME_S = 0.02         # residual-energy frame for added-audio detection
_MAX_LAG_SEG_S = 0.1          # per-segment lag search (same as verify s9_5)
_MIN_SEG_S = 0.5              # shorter audio ranges -> 'too_short' when they do not line up


_BLAS_CTL: list = []


def _blas_lib_paths(d: str) -> list[str]:
    """numpy's bundled OpenBLAS in its ``numpy.libs`` folder: ``.so`` on Linux, ``.dll`` on Windows (the
    Windows wheels name it e.g. ``libscipy_openblas64_-<hash>.dll``), ``.dylib`` on macOS builds that bundle it."""
    import glob
    import os
    out: list[str] = []
    for pat in ("*openblas*.so*", "*openblas*.dll", "*openblas*.dylib"):
        out += glob.glob(os.path.join(d, pat))
    return sorted(set(out))


def _blas_ctl():
    """(set_num_threads, get_num_threads) of numpy's bundled OpenBLAS via ctypes, or None."""
    if not _BLAS_CTL:
        found = None
        try:
            import ctypes
            import os
            d = os.path.join(os.path.dirname(np.__file__), os.pardir, "numpy.libs")
            for path in _blas_lib_paths(d):
                lib = ctypes.CDLL(path)
                for sn, gn in (("scipy_openblas_set_num_threads64_", "scipy_openblas_get_num_threads64_"),
                               ("openblas_set_num_threads64_", "openblas_get_num_threads64_"),
                               ("openblas_set_num_threads", "openblas_get_num_threads")):
                    if hasattr(lib, sn) and hasattr(lib, gn):
                        found = (getattr(lib, sn), getattr(lib, gn))
                        break
                if found:
                    break
        except Exception:          # pragma: no cover - platform specific
            found = None
        _BLAS_CTL.append(found)
    return _BLAS_CTL[0]


@contextmanager
def single_thread_blas():
    """Pin OpenBLAS to one thread for the duration (restored afterwards; no-op if not controllable).

    This stage runs thousands of small matrix products; with a multi-threaded OpenBLAS on a busy
    machine each one costs ~100x more (measured: 8 ms vs 0.08 ms for 132x513 @ 513x40)."""
    ctl = _blas_ctl()
    if ctl is None:
        yield
        return
    setter, getter = ctl
    try:
        old = int(getter())
    except Exception:              # pragma: no cover
        yield
        return
    setter(1)
    try:
        yield
    finally:
        setter(max(1, old))


def _mono(y: Any) -> np.ndarray:
    """float32 mono view of an audio array: None -> empty; (N, C) -> channel mean; (N,) unchanged."""
    if y is None:
        return np.zeros(0, np.float32)
    a = np.asarray(y)
    if a.ndim == 2:
        a = a.mean(axis=1) if a.shape[1] > 1 else a[:, 0]
    return np.ascontiguousarray(a.reshape(-1), dtype=np.float32)


def _cfg(cfg: Any, name: str, default: Any) -> Any:
    return getattr(cfg, name, default) if cfg is not None else default


# =============================================================================================
# DSP helpers
# =============================================================================================

def hz_to_mel(f: np.ndarray | float) -> np.ndarray:
    """HTK mel scale."""
    return 2595.0 * np.log10(1.0 + np.asarray(f, np.float64) / 700.0)


def mel_to_hz(m: np.ndarray | float) -> np.ndarray:
    return 700.0 * (10.0 ** (np.asarray(m, np.float64) / 2595.0) - 1.0)


def _tri_weights(freqs: np.ndarray, lo: np.ndarray, ce: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Triangular filters (peak 1 at ``ce``) evaluated at ``freqs``; a filter narrower than the bin
    spacing keeps its nearest bin so that no band is empty."""
    f = freqs[None, :]
    up = (f - lo[:, None]) / np.maximum(ce - lo, 1e-9)[:, None]
    dn = (hi[:, None] - f) / np.maximum(hi - ce, 1e-9)[:, None]
    w = np.maximum(0.0, np.minimum(up, dn))
    empty = w.sum(1) <= 0
    if np.any(empty):
        for b in np.nonzero(empty)[0]:
            j = int(np.argmin(np.abs(freqs - ce[b])))
            w[b, j] = 1.0
    return w


def mel_filterbank(sr: int, n_fft: int, n_mels: int, fmin: float, fmax: float, warp: float = 1.0) -> np.ndarray:
    """[n_mels, n_fft//2 + 1] triangular HTK-mel filters (peak 1), every edge frequency multiplied by
    ``warp``. With peak-normalised filters the band energy of a signal played ``warp`` times faster
    (tape style: every frequency x warp, spectral density / warp) through the warped bank equals the
    original's through the unwarped bank, for tones and for noise alike."""
    pts = mel_to_hz(np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2)) * float(warp)
    freqs = np.arange(n_fft // 2 + 1) * (sr / n_fft)
    return _tri_weights(freqs, pts[:-2], pts[1:-1], pts[2:]).astype(np.float32)


def broad_filterbank(sr: int, n_fft: int, warp: float = 1.0, n_bands: int = _N_BROAD,
                     f0: float = _BROAD_F0) -> np.ndarray:
    """[n_bands, F] octave-wide triangles on a log2 frequency axis (centres f0 * 2^k * warp)."""
    freqs = np.arange(n_fft // 2 + 1) * (sr / n_fft)
    cent = f0 * (2.0 ** np.arange(n_bands)) * float(warp)
    lf = np.log2(np.maximum(freqs, 1e-3))[None, :]
    w = np.maximum(0.0, 1.0 - np.abs(lf - np.log2(cent)[:, None]))
    return w.astype(np.float32)


def _n_fft_for(sr: int) -> int:
    return int(1 << int(math.ceil(math.log2(max(16, 0.064 * sr)))))


def _hann(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n)).astype(np.float32)   # periodic


def _frame_count(n_samples: int, sr: int, rate: int) -> int:
    if n_samples <= 0:
        return 0
    return int((n_samples - 1) * rate // sr) + 1


def _power_chunks(y: np.ndarray, sr: int, rate: int, n_fft: int, chunk: int = 4096):
    """Yield (i0, P[i0:i1]) power spectra of centred frames (frame i centred at sample round(i*sr/rate)),
    normalised so a sine of amplitude A peaks at ~A^2."""
    import scipy.fft as sfft
    T = _frame_count(len(y), sr, rate)
    if T == 0:
        return
    half = n_fft // 2
    win = _hann(n_fft)
    norm = (2.0 / float(win.sum())) ** 2
    ar = np.arange(n_fft)
    for i0 in range(0, T, chunk):
        i1 = min(T, i0 + chunk)
        starts = np.round(np.arange(i0, i1) * (sr / rate)).astype(np.int64) - half   # first sample of each frame
        a, b = int(starts[0]), int(starts[-1]) + n_fft
        seg = np.zeros(b - a, np.float32)                  # this chunk's samples, zero outside the signal
        lo, hi = max(0, a), min(len(y), b)
        if hi > lo:
            seg[lo - a:hi - a] = y[lo:hi]
        fr = seg[(starts - a)[:, None] + ar[None, :]] * win[None, :]
        X = sfft.rfft(fr, axis=1)
        yield i0, ((X.real.astype(np.float32) ** 2 + X.imag.astype(np.float32) ** 2) * np.float32(norm))


def _log_db(e: np.ndarray) -> np.ndarray:
    return (10.0 * np.log10(e + _LOG_FLOOR)).astype(np.float32)


def _onset(logmel: np.ndarray) -> np.ndarray:
    """Spectral flux of the log-mel (dB): mean over bands of the positive frame-to-frame increase."""
    o = np.zeros(logmel.shape[0], np.float32)
    if logmel.shape[0] > 1:
        o[1:] = np.maximum(np.diff(logmel, axis=0), 0.0).mean(1)
    return o


def _feature_setup(sr: int, cfg: Any) -> dict:
    rate = int(_cfg(cfg, "audio_feat_rate", 100))
    n_fft = _n_fft_for(sr)
    vmax = max(1.0, float(_cfg(cfg, "audio_speed_max", 1.30)))
    fmin = float(_cfg(cfg, "audio_fmin", 80.0))
    # keep the warped (x vmax) filters below Nyquist so the tape hypothesis stays observable
    fmax = min(float(_cfg(cfg, "audio_fmax", 7600.0)), 0.98 * 0.5 * sr / vmax)
    n_mels = int(_cfg(cfg, "audio_n_mels", 40))
    return {"rate": rate, "n_fft": n_fft, "fmin": fmin, "fmax": fmax, "n_mels": n_mels}


def _spectral(y: np.ndarray, sr: int, cfg: Any, keep_power: bool = False) -> dict:
    st = _feature_setup(sr, cfg)
    rate, n_fft = st["rate"], st["n_fft"]
    fb = mel_filterbank(sr, n_fft, st["n_mels"], st["fmin"], st["fmax"])
    bb = broad_filterbank(sr, n_fft)
    T = _frame_count(len(y), sr, rate)
    logmel = np.zeros((T, st["n_mels"]), np.float32)
    broad = np.zeros((T, _N_BROAD), np.float32)
    power = np.zeros((T, n_fft // 2 + 1), np.float32) if keep_power else None
    for i0, P in _power_chunks(np.asarray(y, np.float32), sr, rate, n_fft):
        i1 = i0 + P.shape[0]
        logmel[i0:i1] = _log_db(P @ fb.T)
        broad[i0:i1] = _log_db(P @ bb.T)
        if power is not None:
            power[i0:i1] = P
    out = {"logmel": logmel, "onset": _onset(logmel), "broad": broad, "rate": rate, "n_fft": n_fft,
           "fmin": st["fmin"], "fmax": st["fmax"], "sr": int(sr)}
    if power is not None:
        out["power"] = power
    return out


def features(y: np.ndarray, sr: int, cfg: Any) -> dict:
    """Analysis features of a mono signal at ``cfg.audio_feat_rate`` Hz (frame i centred at i / rate).

    Returns ``{'logmel': float32 [T, n_mels] (dB, own numpy HTK mel filterbank), 'onset': float32 [T]
    (spectral flux of the log-mel), 'broad': float32 [T, 6] (octave-band log energies, 125 Hz..4 kHz
    centres), 'rate': int, 'n_fft': int, 'fmin', 'fmax', 'sr'}``. Empty input -> T = 0."""
    return _spectral(_mono(y), int(sr), cfg, keep_power=False)


# ---------------------------------------------------------------------------------------------
# Windowed-sinc fractional resampling (tape-style speed changes, fractional delays)
# ---------------------------------------------------------------------------------------------

_SINC_TABLES: dict[tuple[float, int], tuple[np.ndarray, np.ndarray]] = {}


def _sinc_table(cutoff: float, half: int = _SINC_HALF) -> tuple[np.ndarray, np.ndarray]:
    fc = float(min(1.0, max(0.05, cutoff)))
    key = (round(fc, 6), int(half))
    tab = _SINC_TABLES.get(key)
    if tab is None:
        H = int(math.ceil(half / fc))
        taps = np.arange(-H + 1, H + 1)
        ph = np.arange(_SINC_PHASES + 1) / _SINC_PHASES          # fractional position 0..1
        x = ph[:, None] - taps[None, :]                          # distance position - sample
        arg = np.clip(1.0 - (x / H) ** 2, 0.0, 1.0)
        win = np.i0(_KAISER_BETA * np.sqrt(arg)) / np.i0(_KAISER_BETA)
        k = fc * np.sinc(fc * x) * win
        tab = (k.astype(np.float32), taps)
        _SINC_TABLES[key] = tab
    return tab


def resample_at(y: np.ndarray, pos: np.ndarray, cutoff: float = 1.0, chunk: int = 1 << 15,
                half: int = _SINC_HALF) -> np.ndarray:
    """Band-limited interpolation of ``y`` at fractional sample positions ``pos`` (Kaiser-windowed sinc
    of ``2*half`` taps, 1024 tabulated phases; ``cutoff`` = fraction of Nyquist, use min(1, 1/step) when
    the positions advance by ``step`` > 1 per output sample so nothing aliases). Samples outside ``y``
    are zero."""
    y = np.asarray(y, np.float32).reshape(-1)
    pos = np.asarray(pos, np.float64).reshape(-1)
    out = np.zeros(pos.size, np.float32)
    if y.size == 0 or pos.size == 0:
        return out
    tab, taps = _sinc_table(cutoff, half)
    H = int(-taps[0]) + 1
    for c0 in range(0, pos.size, chunk):
        p = pos[c0:c0 + chunk]
        ok = (p > -H) & (p < y.size + H - 1)
        if not np.any(ok):
            continue
        # only the samples this chunk touches, zero-padded by the kernel half-width
        lo = max(0, int(math.floor(p[ok].min())) - H)
        hi = min(y.size, int(math.floor(p[ok].max())) + H + 2)
        seg = np.zeros(hi - lo + 2 * H + 2, np.float32)
        seg[H:H + hi - lo] = y[lo:hi]
        pp = np.where(ok, p, float(lo))
        base = np.floor(pp).astype(np.int64)
        ph = np.round((pp - base) * _SINC_PHASES).astype(np.int64)
        idx = (base - lo)[:, None] + taps[None, :] + H
        np.clip(idx, 0, seg.size - 1, out=idx)
        v = np.einsum("ij,ij->i", tab[ph], seg[idx])
        out[c0:c0 + chunk] = np.where(ok, v, 0.0)
    return out


# ---------------------------------------------------------------------------------------------
# Normalised cross-correlation helpers
# ---------------------------------------------------------------------------------------------

def _parabolic(ym: float, y0: float, yp: float) -> float:
    """Sub-sample offset of a parabola's vertex through (-1, ym), (0, y0), (1, yp), in [-0.5, 0.5]."""
    den = ym - 2.0 * y0 + yp
    if not np.isfinite(den) or den >= 0:
        return 0.0
    return float(np.clip(0.5 * (ym - yp) / den, -0.5, 0.5))


def _ncc_slide(chunk: np.ndarray, region: np.ndarray) -> np.ndarray:
    """NCC of ``chunk`` at every offset inside ``region`` (len(region) >= len(chunk)); exact
    normalisation by the sliding region energy (cumulative sums); mean-removed chunk."""
    c = np.asarray(chunk, np.float64)
    r = np.asarray(region, np.float64)
    m = c.size
    if m == 0 or r.size < m:
        return np.zeros(0)
    c = c - c.mean()
    nc = float(np.sqrt(np.sum(c * c)))
    n_lags = r.size - m + 1
    if n_lags * m <= 400_000:
        num = np.correlate(r, c, mode="valid")              # few lags: direct is cheaper
    else:
        from scipy.signal import fftconvolve
        num = fftconvolve(r, c[::-1], mode="valid")
    cs1 = np.concatenate([[0.0], np.cumsum(r)])
    cs2 = np.concatenate([[0.0], np.cumsum(r * r)])
    s1 = cs1[m:] - cs1[:-m]
    e = np.maximum((cs2[m:] - cs2[:-m]) - s1 * s1 / m, 0.0)
    den = nc * np.sqrt(e)
    return np.where(den > 1e-12 * max(nc, 1e-12), num / np.maximum(den, 1e-300), 0.0)


def xcorr_lag(a: np.ndarray, b: np.ndarray, sr: int, max_lag_s: float) -> tuple[float, float]:
    """Normalised cross-correlation lag between two (equally long) signals.

    Returns ``(lag_s, peak)``: ``b`` is delayed by ``lag_s`` seconds relative to ``a`` (``b(t) ≈ a(t -
    lag)``; positive = b late), searched within ``±max_lag_s`` (and at most half the length); the
    integer peak is refined by windowed-sinc interpolation of the correlation (0.05-sample grid +
    parabola, ~0.02 sample accuracy). ``peak`` is the normalised correlation in [-1, 1] at the best
    lag (energies of the overlapping parts). Multi-channel input is averaged to mono. Silent / empty
    input -> ``(0.0, 0.0)``."""
    lag, peak, _side = xcorr_lag_side(a, b, sr, max_lag_s)
    return lag, peak


def xcorr_lag_side(a: np.ndarray, b: np.ndarray, sr: int, max_lag_s: float,
                   inner_s: float | None = None) -> tuple[float, float, float]:
    """``xcorr_lag`` plus the best SIDELOBE: the highest normalised correlation outside the main lobe of the
    peak (the contiguous run of positive correlation around it) within the same search range (-1 when the
    main lobe fills the range). On short windows of tonal audio or under a music bed the correlation is
    nearly periodic -- peaks one period apart reach almost the same height (film24's 3-frame S07: 0.926 at
    +21 ms vs 0.922 at its true -1 ms) -- so ``peak - side`` tells whether the lag is unique.

    ``inner_s``: a HYPOTHESIS test instead -- the peak is the best lag within ±inner_s and the sidelobe the best
    correlation beyond it (up to ±max_lag_s): does the signal follow this alignment rather than another one?"""
    a = _mono(a).astype(np.float64)
    b = _mono(b).astype(np.float64)
    n = min(a.size, b.size)
    if n < 2:
        return 0.0, 0.0, -1.0
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    if float(np.sum(a * a)) <= 1e-18 or float(np.sum(b * b)) <= 1e-18:
        return 0.0, 0.0, -1.0
    import scipy.fft as sfft
    L = int(min(max(0, round(float(max_lag_s) * sr)), n // 2))
    N = sfft.next_fast_len(n + L + 1, real=True)
    r = sfft.irfft(np.conj(sfft.rfft(a, N)) * sfft.rfft(b, N), N)       # r[L] = sum_t a[t] b[t+L]
    lags = np.arange(-L, L + 1)
    num = r[lags % N]
    ca = np.concatenate([[0.0], np.cumsum(a * a)])
    cb = np.concatenate([[0.0], np.cumsum(b * b)])
    pos = lags >= 0
    # L >= 0: a[0:n-L] . b[L:n] ; L < 0: a[-L:n] . b[0:n+L]
    ea = np.where(pos, ca[n - np.abs(lags)], ca[n] - ca[np.abs(lags)])
    eb = np.where(pos, cb[n] - cb[np.abs(lags)], cb[n - np.abs(lags)])
    den = np.sqrt(np.maximum(ea * eb, 1e-300))
    ncc = num / den
    if inner_s is not None:
        Li = int(min(L, max(0, round(float(inner_s) * sr))))
        i = L - Li + int(np.argmax(ncc[L - Li:L + Li + 1]))
        lo_i, hi_i = L - Li, L + Li
    else:
        i = int(np.argmax(ncc))
        lo_i, hi_i = i, i
        while lo_i > 0 and ncc[lo_i - 1] > 0.0:
            lo_i -= 1
        while hi_i < ncc.size - 1 and ncc[hi_i + 1] > 0.0:
            hi_i += 1
    outside = np.concatenate([ncc[:lo_i], ncc[hi_i + 1:]])
    side = float(np.clip(outside.max(), -1.0, 1.0)) if outside.size else -1.0
    off = 0.0
    if 0 < i < ncc.size - 1:
        # band-limited refinement: the correlation is band-limited, so windowed-sinc interpolation of
        # its integer-lag samples gives it at fractional lags (0.05-sample grid over +-1 sample, then a
        # parabola on that fine grid)
        tau = lags[i] + np.linspace(-1.0, 1.0, 41)
        H = 24
        loc = r[(lags[i] + np.arange(-H, H + 1)) % N]              # integer-lag correlation around the peak
        rf = resample_at(loc, tau - lags[i] + H, cutoff=1.0)        # band-limited (windowed-sinc) interpolation
        dfr = np.interp(tau, lags[i - 1:i + 2].astype(np.float64), den[i - 1:i + 2])
        nf = rf / dfr
        j = int(np.argmax(nf))
        off = float(tau[j] - lags[i])
        if 0 < j < nf.size - 1:
            off += 0.05 * _parabolic(nf[j - 1], nf[j], nf[j + 1])
        return float((lags[i] + off) / sr), float(np.clip(max(ncc[i], nf[j]), -1.0, 1.0)), side
    return float((lags[i] + off) / sr), float(np.clip(ncc[i], -1.0, 1.0)), side


class _Correlator:
    """Sliding multi-channel NCC of short windows against a long feature matrix ``x`` [T, C]:
    overlap-save FFT blocks for the numerator, exact normalisation by the sliding (per-channel
    mean-removed) energy of ``x`` from cumulative sums. ``ncc(w)[l]`` = NCC of ``w`` [m, C] with
    ``x[l:l+m]`` (both mean-removed per channel), l = 0 .. T - m."""

    def __init__(self, x: np.ndarray, m_max: int, nfft: int = 8192):
        import scipy.fft as sfft
        x = np.asarray(x, np.float64)
        if x.ndim == 1:
            x = x[:, None]
        self.T, self.C = x.shape
        self.m_max = int(max(2, m_max))
        nfft = int(max(nfft, 1 << int(math.ceil(math.log2(2 * self.m_max)))))
        self.nfft = nfft
        self.step = nfft - self.m_max + 1
        self.K = max(1, -(-self.T // self.step))
        pad = np.zeros((self.K * self.step + nfft, self.C))
        pad[:self.T] = x
        spec = np.empty((nfft // 2 + 1, self.C, self.K), np.complex64)
        for k in range(self.K):
            blk = pad[k * self.step:k * self.step + nfft]
            spec[:, :, k] = sfft.rfft(blk, axis=0)
        self.spec = spec                                          # [F, C, K]
        self.cs1 = np.zeros((self.T + 1, self.C))
        self.cs2 = np.zeros((self.T + 1, self.C))
        np.cumsum(x, axis=0, out=self.cs1[1:])
        np.cumsum(x * x, axis=0, out=self.cs2[1:])
        self._energy: dict[int, np.ndarray] = {}

    def energy(self, m: int) -> np.ndarray:
        e = self._energy.get(m)
        if e is None:
            s1 = self.cs1[m:] - self.cs1[:-m]
            s2 = self.cs2[m:] - self.cs2[:-m]
            e = np.maximum((s2 - s1 * s1 / m).sum(1), 0.0)
            self._energy[m] = e
        return e

    def ncc_multi(self, windows: Sequence[np.ndarray]) -> list[np.ndarray]:
        """NCC curves of several windows (lengths may differ, each <= m_max). Processed one window at a
        time: per window one small rfft, a [F, 1, C] @ [F, C, K] product and K inverse FFTs (batching
        several windows was measured slower)."""
        import scipy.fft as sfft
        out: list[np.ndarray] = []
        for w in windows:
            m = int(w.shape[0])
            if m < 2 or m > self.m_max or m > self.T:
                out.append(np.zeros(0))
                continue
            w = np.asarray(w, np.float64).reshape(m, -1)
            w = w - w.mean(0, keepdims=True)
            nw = math.sqrt(float(np.sum(w * w)))
            e = self.energy(m)
            if nw <= 1e-9:
                out.append(np.zeros(e.size))
                continue
            FW = np.conj(sfft.rfft(w, n=self.nfft, axis=0)).astype(np.complex64)          # [F, C]
            acc = np.matmul(FW[:, None, :], self.spec)[:, 0, :]                            # [F, K]
            num = sfft.irfft(acc, n=self.nfft, axis=0)[:self.step]                          # [step, K]
            num = num.T.reshape(-1)[:e.size]
            good = e > 1e-6 * m
            out.append(np.where(good, num / (nw * np.sqrt(np.where(good, e, 1.0))), 0.0))
        return out


def _top_peaks(curve: np.ndarray, k: int, excl: int) -> list[int]:
    """Indices of up to ``k`` highest local maxima of ``curve`` separated by more than ``excl``."""
    c = np.asarray(curve)
    if c.size == 0:
        return []
    if c.size < 3:
        return [int(np.argmax(c))]
    left = np.concatenate([[-np.inf], c[:-1]])
    right = np.concatenate([c[1:], [-np.inf]])
    lm = np.nonzero((c >= left) & (c > right))[0]
    if lm.size == 0:
        lm = np.array([int(np.argmax(c))])
    n_keep = min(lm.size, max(8 * k, 64))
    if lm.size > n_keep:
        sel = np.argpartition(-c[lm], n_keep - 1)[:n_keep]
        lm = lm[sel]
    lm = lm[np.lexsort((lm, -c[lm]))]            # value desc, index asc (deterministic)
    out: list[int] = []
    for j in lm:
        if all(abs(int(j) - o) > excl for o in out):
            out.append(int(j))
            if len(out) >= k:
                break
    return out


# =============================================================================================
# Stage 5.1 -- coarse alignment
# =============================================================================================

@dataclass
class _Res:
    """Best match of one competitor window at one speed."""
    v: float
    lag: float                 # RAW feature frame (fractional) of the window start
    peak: float                # full-feature NCC
    conf: float
    psr: float
    hyp: str
    second: float
    floor: float
    coarse: float              # coarse NCC at the chosen peak
    null: tuple = ()               # (mean, std, max) of the full NCC at random lags
    raw_t: float = float("nan")    # RAW seconds matching the window centre (after refinement)
    speed: float = 1.0             # reported speed
    wave_peak: float = float("nan")

    def ok(self, min_conf: float) -> bool:
        return bool(np.isfinite(self.conf) and self.conf >= min_conf)


class _Aligner:
    """Feature store + correlators for one competitor/RAW pair."""

    def __init__(self, comp_y: np.ndarray, raw_y: np.ndarray, sr: int, cfg: Any):
        self.cfg = cfg
        self.sr = int(sr)
        self.comp_y = comp_y
        self.raw_y = raw_y
        self.min_conf = float(_cfg(cfg, "audio_min_conf", 1.3))
        self.seed = int(_cfg(cfg, "seed", 12345))
        st = _feature_setup(self.sr, cfg)
        self.rate = st["rate"]
        comp_dur = len(comp_y) / self.sr
        self.W = float(min(float(_cfg(cfg, "audio_window", 1.0)), comp_dur))
        self.hop = float(_cfg(cfg, "audio_hop", 0.25))
        self.n = max(4, int(round(self.W * self.rate)))
        vmin = float(_cfg(cfg, "audio_speed_min", 0.90))
        vmax = float(_cfg(cfg, "audio_speed_max", 1.30))
        vstep = float(_cfg(cfg, "audio_speed_step", 0.01))
        self.vstep = vstep
        grid = np.round(np.arange(vmin, vmax + vstep / 2, vstep), 6)
        self.speeds = [float(v) for v in grid if abs(v - 1.0) > 1e-9]
        self.vmin, self.vmax = min(vmin, 1.0), max(vmax, 1.0)
        self.refine_s = float(_cfg(cfg, "audio_refine_ms", 50.0)) / 1000.0
        self.excl = int(round(_EXCL_S * self.rate))
        # features
        self.c = _spectral(comp_y, self.sr, cfg, keep_power=True)
        self.r = _spectral(raw_y, self.sr, cfg, keep_power=False)
        self.n_fft = self.c["n_fft"]
        self.Tc = self.c["logmel"].shape[0]
        self.Tr = self.r["logmel"].shape[0]
        self.r_full = _delta(self.r["logmel"])          # full verification feature: delta log-mel
        self._fb_cache: dict[float, tuple[np.ndarray, np.ndarray]] = {}
        self.st = st
        # coarse matrices (per-file z-scored channels; onset weighted)
        self.c_stats = self._stats(self.c)
        self.r_stats = self._stats(self.r)
        rc = self._coarse_from(self.r["onset"], self.r["broad"], self.r_stats)
        self.m_max = int(math.ceil(self.n * self.vmax)) + 2
        self.corr = _Correlator(rc, self.m_max, nfft=8192)
        rcd = _pool(rc, _SCAN_DECIM)
        self.corr_d = _Correlator(rcd, self.m_max // _SCAN_DECIM + 2, nfft=4096)
        # windows
        self.starts_s: list[float] = []
        if self.W > 0:
            nw = int(math.floor((comp_dur - self.W) / self.hop + 1e-9)) + 1 if comp_dur >= self.W else 0
            self.starts_s = [i * self.hop for i in range(max(0, nw))]
        self.i0 = [int(round(s * self.rate)) for s in self.starts_s]
        self.energy_ok = []
        for s in self.starts_s:
            a = int(round(s * self.sr))
            seg = comp_y[a:a + int(round(self.W * self.sr))]
            self.energy_ok.append(bool(seg.size and float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))) > _SILENT_RMS))
        self.n_evals = 0

    # ---- feature plumbing ------------------------------------------------------------------
    @staticmethod
    def _stats(f: dict) -> tuple[np.ndarray, np.ndarray]:
        m = np.concatenate([f["onset"][:, None], f["broad"]], axis=1).astype(np.float64)
        if m.shape[0] == 0:
            return np.zeros(1 + _N_BROAD), np.ones(1 + _N_BROAD)
        mu = m.mean(0)
        sd = np.maximum(m.std(0), 1e-6)
        return mu, sd

    @staticmethod
    def _coarse_from(onset: np.ndarray, broad: np.ndarray, stats: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
        m = np.concatenate([np.asarray(onset, np.float64).reshape(-1, 1), np.asarray(broad, np.float64)], axis=1)
        m = (m - stats[0]) / stats[1]
        m[:, 0] *= _ONSET_WEIGHT
        return m

    def _fbs(self, v: float) -> tuple[np.ndarray, np.ndarray]:
        """(mel, octave) filterbanks warped by v (tape hypothesis), cached per speed."""
        key = round(float(v), 6)
        hit = self._fb_cache.get(key)
        if hit is None:
            hit = (mel_filterbank(self.sr, self.n_fft, self.st["n_mels"], self.st["fmin"], self.st["fmax"], warp=key),
                   broad_filterbank(self.sr, self.n_fft, warp=key))
            self._fb_cache[key] = hit
        return hit

    def _positions(self, i0: int, v: float, extra: int = 0) -> np.ndarray:
        """Absolute (fractional) competitor frame positions of the RAW-grid frames -extra .. m-1 of a
        window starting at comp frame i0 matched at speed v (m = round(n v))."""
        m = int(round(self.n * v))
        return np.clip(i0 + np.arange(-extra, m) / v, 0.0, self.Tc - 1.0)

    @staticmethod
    def _interp_rows(F: np.ndarray, q: np.ndarray, a: int = 0) -> np.ndarray:
        """Rows of F (row 0 = absolute frame a) linearly interpolated at absolute positions q."""
        rel = np.clip(q - a, 0.0, F.shape[0] - 1.0)
        j0 = np.floor(rel).astype(np.int64)
        j1 = np.minimum(j0 + 1, F.shape[0] - 1)
        fr = (rel - j0).astype(np.float32)[:, None]
        return F[j0] * (1.0 - fr) + F[j1] * fr

    def _rows(self, q: np.ndarray) -> tuple[int, int]:
        return int(math.floor(q.min())), min(self.Tc, int(math.floor(q.max())) + 2)

    def comp_coarse(self, i0: int, v: float) -> np.ndarray:
        """Competitor coarse window starting at comp frame i0, time-scaled onto RAW frames at speed v
        (octave bands tape-warped by v; the onset envelope is pitch-invariant)."""
        q = self._positions(i0, v)
        a, b = self._rows(q)
        if abs(v - 1.0) < 1e-12:
            br = self.c["broad"][a:b]
        else:
            br = _log_db(self.c["power"][a:b] @ self._fbs(v)[1].T)
        on = self._interp_rows(self.c["onset"][a:b, None], q, a)[:, 0]
        return self._coarse_from(on, self._interp_rows(br, q, a), self.c_stats)

    def comp_full(self, i0: int, v: float, hyp: str) -> np.ndarray:
        """Competitor delta-log-mel window at speed v on the RAW frame grid (tape: mel filters warped by
        v; tempo: unwarped). Deltas are taken AFTER time-scaling, like the RAW's."""
        q = self._positions(i0, v, extra=1)
        a, b = self._rows(q)
        if hyp == _TAPE and abs(v - 1.0) > 1e-12:
            L = _log_db(self.c["power"][a:b] @ self._fbs(v)[0].T)
        else:
            L = self.c["logmel"][a:b]
        return np.diff(self._interp_rows(L, q, a), axis=0).astype(np.float32)

    def full_ncc(self, wf: np.ndarray, lags: np.ndarray) -> np.ndarray:
        """Full-feature NCC (delta log-mel, per-band mean removed) of wf [m, B] at RAW start frames
        ``lags`` (-1 where the window does not fit)."""
        m = wf.shape[0]
        lags = np.asarray(lags, np.int64).reshape(-1)
        out = np.full(lags.size, -1.0)
        ok = (lags >= 0) & (lags + m <= self.Tr)
        if not np.any(ok) or m < 2:
            return out
        w = wf.astype(np.float32) - wf.mean(0, dtype=np.float64).astype(np.float32)
        nw = float(np.sqrt(np.sum(w.astype(np.float64) ** 2)))
        if nw <= 1e-9:
            return out
        X = self.r_full[lags[ok][:, None] + np.arange(m)[None, :]]                 # [L, m, B] float32
        num = np.einsum("lmb,mb->l", X, w, dtype=np.float64)
        s1 = X.sum(1, dtype=np.float64)                                             # [L, B]
        s2 = np.einsum("lmb,lmb->l", X, X, dtype=np.float64)
        e = np.maximum(s2 - np.sum(s1 * s1, 1) / m, 0.0)
        den = nw * np.sqrt(e)
        out[ok] = np.where(den > 1e-9, num / np.maximum(den, 1e-300), 0.0)
        return out

    def hyps(self, v: float) -> tuple[str, ...]:
        return (_TAPE,) if abs(v - 1.0) < 1e-12 else (_TAPE, _TEMPO)

    # ---- evaluation -------------------------------------------------------------------------
    def evaluate(self, wi: int, v: float) -> _Res | None:
        """Global search of window wi at speed v: coarse NCC over the whole RAW, full verification."""
        curve = self.corr.ncc_multi([self.comp_coarse(self.i0[wi], v)])[0]
        return self.evaluate_curve(wi, v, curve)

    def evaluate_curve(self, wi: int, v: float, curve: np.ndarray) -> _Res | None:
        self.n_evals += 1
        if curve.size == 0:
            return None
        peaks = _top_peaks(curve, _TOP_K, self.excl)
        if not peaks:
            return None
        i0 = self.i0[wi]
        offs = np.arange(-_VERIFY_R, _VERIFY_R + 1)
        P = np.asarray(peaks, np.int64)
        lags = (P[:, None] + offs[None, :]).reshape(-1)
        wfs = {h: self.comp_full(i0, v, h) for h in self.hyps(v)}
        best_s = np.full(P.size, -np.inf)
        best_l = np.zeros(P.size)
        best_h = [_TAPE] * P.size
        for h, wf in wfs.items():
            S = self.full_ncc(wf, lags).reshape(P.size, offs.size)
            j = np.argmax(S, axis=1)
            for k in range(P.size):
                s = S[k]
                if s[j[k]] > best_s[k]:
                    sub = _parabolic(s[j[k] - 1], s[j[k]], s[j[k] + 1]) if 0 < j[k] < offs.size - 1 else 0.0
                    best_s[k] = s[j[k]]
                    best_l[k] = P[k] + offs[j[k]] + sub
                    best_h[k] = h
        order = sorted(range(P.size), key=lambda k: (-best_s[k], best_l[k]))
        kb = order[0]
        bscore, blag, bhyp, pk = float(best_s[kb]), float(best_l[kb]), best_h[kb], int(P[kb])
        second = max([float(best_s[k]) for k in order[1:] if abs(best_l[k] - blag) > self.excl] or [-1.0])
        # null distribution of the full-feature NCC (deterministic pseudo-random lags)
        m = wfs[bhyp].shape[0]
        hi = self.Tr - m
        floor = _NULL_FLOOR_MIN
        null: tuple = (float("nan"), float("nan"), float("nan"))
        if hi > 2 * self.excl + 4:
            rng = np.random.default_rng(self.seed + 7919 * wi + int(round(v * 1000)))
            rl = rng.integers(0, hi + 1, _NULL_LAGS * 2)
            rl = rl[np.abs(rl - blag) > self.excl][:_NULL_LAGS]
            if rl.size >= 8:
                s = self.full_ncc(wfs[bhyp], rl)
                null = (float(s.mean()), float(s.std()), float(s.max()))
                floor = max(floor, _null_floor(*null))
        conf = bscore / max(second, floor, 1e-6) if bscore > 0 else 0.0
        side = np.ones(curve.size, bool)
        side[max(0, pk - self.excl):pk + self.excl + 1] = False
        psr = 0.0
        if side.sum() > 8:
            sv = curve[side]
            psr = float((curve[pk] - sv.mean()) / max(float(sv.std()), 1e-9))
        return _Res(v=float(v), lag=blag, peak=bscore, conf=float(conf), psr=psr, hyp=bhyp,
                    second=float(second), floor=float(floor), coarse=float(curve[pk]), null=null)

    def local_sweep(self, wi: int, res: _Res, span: float) -> tuple[float, float, dict[float, float]]:
        """Full-feature score at the candidate location (window centre kept fixed) for speeds within
        ±span of res.v (grid step). Returns (best v, best score, {v: score})."""
        i0 = self.i0[wi]
        centre = res.lag + res.v * self.n / 2.0
        scores: dict[float, float] = {}
        k = int(round(span / self.vstep))
        offs = np.arange(-_VERIFY_R, _VERIFY_R + 1)
        for d in range(-k, k + 1):
            v = round(res.v + d * self.vstep, 6)
            if v < self.vmin - 1e-9 or v > self.vmax + 1e-9:
                continue
            l0 = int(round(centre - v * self.n / 2.0))
            scores[v] = max(float(self.full_ncc(self.comp_full(i0, v, h), l0 + offs).max()) for h in self.hyps(v))
        vb = max(scores, key=lambda x: (scores[x], -abs(x - res.v)))
        return vb, scores[vb], scores

    def scan_speeds(self, wi: int) -> tuple[list[float], float, float]:
        """Decimated coarse scan over the speed grid (incl. 1.00 as reference). Returns (shortlisted
        speeds, best score, score at 1.00)."""
        i0 = self.i0[wi]
        grid = [1.0] + self.speeds
        curves = self.corr_d.ncc_multi([_pool(self.comp_coarse(i0, v), _SCAN_DECIM) for v in grid])
        best = np.array([float(c.max()) if c.size else -1.0 for c in curves])
        ref = float(best[0])
        best = best[1:]
        if best.size == 0 or best.max() <= 0:
            return [], -1.0, ref
        order = list(np.argsort(-best, kind="stable"))
        top = float(best[order[0]])
        picks: list[int] = []
        for j in order:
            if best[j] < top - 0.1 or len(picks) >= 3:
                break
            if all(abs(j - p) > 3 for p in picks):
                picks.append(int(j))
        return [self.speeds[j] for j in picks], top, ref

    # ---- sample-precise refinement ------------------------------------------------------------
    def _chunk(self, c0: float, v: float, m: int) -> np.ndarray:
        """The competitor window starting at comp second c0, resampled onto the RAW sample grid for a
        tape-style speed v (m samples)."""
        a = c0 * self.sr
        if abs(v - 1.0) < 1e-12 and abs(a - round(a)) < 1e-9:
            s = self.comp_y[int(round(a)):int(round(a)) + m].astype(np.float32)
            return np.pad(s, (0, m - s.size)) if s.size < m else s
        return resample_at(self.comp_y, a + np.arange(m) / v, cutoff=min(1.0, v), half=8)

    def _align(self, chunk: np.ndarray, s0: float, search_s: float) -> tuple[float, float] | None:
        """Sub-sample offset (samples, relative to the expected RAW position s0) of ``chunk`` in the RAW
        waveform within ±search_s, and its NCC; None if not an interior peak >= _WAVE_MIN_PEAK."""
        R = int(round(search_s * self.sr))
        base = int(round(s0)) - R
        lo, hi = max(0, base), min(len(self.raw_y), base + chunk.size + 2 * R)
        if hi - lo < chunk.size + 2:
            return None
        s = _ncc_slide(chunk, self.raw_y[lo:hi])
        if s.size < 3:
            return None
        j = int(np.argmax(s))
        if j == 0 or j == s.size - 1 or s[j] < _WAVE_MIN_PEAK:
            return None
        return lo + j + _parabolic(s[j - 1], s[j], s[j + 1]) - s0, float(s[j])

    def _line_fit(self, c0: float, v: float, r0: float, search_s: float) -> tuple[float, float, float] | None:
        """Lag line lag(t) = a + b t of the window (tape-resampled at speed v) against the RAW around
        start r0: waveform lags of the 4 window quarters (±search_s, NCC >= _WAVE_MIN_PEAK) and a
        robust line through them (>= 3 within 0.6 ms). Returns (a seconds, b, quality) with quality =
        inliers + mean inlier NCC - rms residual / 0.6 ms, or None. Captures speed errors up to ~0.3 %; beyond that voiced
        speech still correlates at pitch-period-shifted lags, which the quality score exposes."""
        sr = self.sr
        m = int(math.floor(self.W * v * sr))
        if m < 4 * 400:
            return None
        chunk = self._chunk(c0, v, m)
        h = m // 4
        pts: list[tuple[float, float]] = []
        pks: list[float] = []
        for q in range(4):
            al = self._align(chunk[q * h:(q + 1) * h], r0 * sr + q * h, search_s)
            if al is not None:
                pts.append(((q * h + h / 2.0) / sr, al[0] / sr))
                pks.append(al[1])
        fit = _robust_line(pts, tol=0.0006, max_slope=0.03, min_inliers=3)
        if fit is None:
            return None
        a, b, inl = fit
        x = np.array([p[0] for p in pts])[inl]
        y = np.array([p[1] for p in pts])[inl]
        rms = float(np.sqrt(np.mean((y - (a + b * x)) ** 2)))
        # quality: inliers, mean inlier NCC, minus the residual in units of the tolerance
        return a, b, float(inl.sum() + np.mean(np.asarray(pks)[inl]) - rms / 0.0006)

    def refine(self, wi: int, res: _Res, hints: Sequence[float] = ()) -> _Res:
        """Sample-precise RAW time (and speed) on the 16 kHz waveform.

        Speed 1: whole-window NCC within ±audio_refine_ms (parabolic sub-sample peak) and a quarter
        line fit that only checks for a drift. Other speeds: quarter-lag line fits (tape-style
        resampling) at ``hints`` (refined speeds of neighbouring windows), the found speed and speeds
        ±0.003·k (k <= 6, covers the feature-level uncertainty); the best fit by (inliers + mean
        quarter NCC - residual/tolerance) wins (early exit at >= 4.5: four quarters on one line
        within ~0.1 ms), giving start offset a and speed v (1 + b); then a polishing fit and a
        whole-window NCC (±3 ms) that must reach 0.5 or the fit is discarded. When the waveform does
        not follow (pitch-preserving stretch, heavy added audio) the feature-level estimate is kept.
        Sets res.raw_t / res.speed / res.wave_peak."""
        sr = self.sr
        c0 = self.starts_s[wi]
        v0 = res.v
        r0 = res.lag / self.rate
        v = v0
        wave_peak = float("nan")
        search = max(self.refine_s, 0.03)
        if abs(v0 - 1.0) < 1e-12:
            # speed 1: plain slices -- whole-window NCC, then a quarter fit only to detect a drift
            m = int(math.floor(self.W * sr))
            al = self._align(self._chunk(c0, 1.0, m), r0 * sr, self.refine_s)
            if al is not None:
                r0 += al[0] / sr
                f = self._line_fit(c0, 1.0, r0, 0.003)
                if f is None or abs(f[1]) <= 0.003:
                    res.raw_t = float(r0 + self.W / 2.0)
                    res.speed = 1.0
                    res.wave_peak = al[1]
                    return res
                r0 += f[0]
                v0 = v = 1.0 + f[1]
        best = None
        cands = [float(h) for h in hints if abs(float(h) - v0) <= 0.02]
        cands += [round(v0 + 0.003 * k, 6) for k in (0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6)]
        for vk in cands:
            if vk <= 0.5 * self.vmin:
                continue
            f = self._line_fit(c0, vk, r0, search)
            if f is not None and (best is None or f[2] > best[1][2]):
                best = (vk, f)
                if f[2] >= 4.5:          # 4 quarters on one line within ~0.1 ms at a good NCC
                    break
        if best is not None:
            vk, (a, b, _q) = best
            r1, v1 = r0 + a, vk * (1.0 + b)
            f2 = self._line_fit(c0, v1, r1, 0.003)
            if f2 is not None and abs(f2[1]) < 0.003:
                r1 += f2[0]
                v1 = v1 * (1.0 + f2[1])
            m = int(math.floor(self.W * v1 * sr))
            al = self._align(self._chunk(c0, v1, m), r1 * sr, 0.003) if m > 64 else None
            if al is not None and al[1] >= 0.5:
                r0, v, wave_peak = r1 + al[0] / sr, v1, al[1]
        if not np.isfinite(wave_peak):
            m = int(math.floor(self.W * v0 * sr))
            al = self._align(self._chunk(c0, v0, m), r0 * sr, self.refine_s) if m > 64 else None
            if al is not None:
                r0, v, wave_peak = r0 + al[0] / sr, v0, al[1]
        speed = v
        if abs(speed - 1.0) < 5e-4 or (abs(res.v - 1.0) < 1e-12 and abs(speed - 1.0) <= 0.003):
            speed = 1.0
        res.raw_t = float(r0 + v * self.W / 2.0)
        res.speed = float(speed)
        res.wave_peak = wave_peak
        return res


def _robust_line(pts: list[tuple[float, float]], tol: float, max_slope: float,
                 min_inliers: int = 3) -> tuple[float, float, np.ndarray] | None:
    """Least-squares line y = a + b x through the largest consistent subset (>= min_inliers points
    within tol) of a handful of points (all pairs tried). Returns (a, b, inlier mask) or None."""
    if len(pts) < min_inliers:
        return None
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    best = None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if abs(x[j] - x[i]) < 1e-9:
                continue
            b = (y[j] - y[i]) / (x[j] - x[i])
            if abs(b) > max_slope:
                continue
            a = y[i] - b * x[i]
            r = np.abs(y - (a + b * x))
            inl = r <= tol
            key = (int(inl.sum()), -float(r[inl].sum()))
            if best is None or key > best[0]:
                best = (key, inl)
    if best is None or best[0][0] < min_inliers:
        return None
    inl = best[1]
    b, a = np.polyfit(x[inl], y[inl], 1)
    if abs(b) > max_slope or np.max(np.abs(y[inl] - (a + b * x[inl]))) > tol:
        return None
    return float(a), float(b), inl


def _null_floor(mu: float, sd: float, mx: float) -> float:
    """Null level of the full-feature NCC for a window: the maximum over the random lags or mean +
    4.5 std, whichever is larger (measured on speech and tonal material: ~95 % of correct windows
    reach conf >= 1.3, ~2 % of windows without a genuine match do)."""
    return max(mx, mu + _NULL_Z * sd)


def _delta(L: np.ndarray) -> np.ndarray:
    """First temporal difference of a [T, B] feature (row 0 = 0)."""
    d = np.zeros_like(L)
    if L.shape[0] > 1:
        d[1:] = np.diff(L, axis=0)
    return d


def _pool(x: np.ndarray, d: int) -> np.ndarray:
    x = np.asarray(x)
    n = x.shape[0] // d
    if n == 0:
        return x[:0]
    return x[:n * d].reshape(n, d, *x.shape[1:]).mean(1)


def _better(new: _Res | None, old: _Res | None, min_conf: float) -> bool:
    if new is None or not new.ok(min_conf):
        return False
    if old is None or not old.ok(min_conf):
        return True
    return new.peak > old.peak + 0.01


def coarse_align(comp_y: np.ndarray, raw_y: np.ndarray, sr: int, cfg: Any, dlog: DecisionLog | None) -> AudioHints:
    """Stage 5.1: per ~1 s competitor window (hop 0.25 s) the matching RAW time, speed and confidence.

    See the module docstring for the algorithm. ``raw_t`` is the RAW time matching ``comp_t`` (the
    window centre), sample-precise when the waveform follows (tape-style or speed 1), NaN when the
    window is silent or has no candidate above the null level. ``speed`` = 1.0 exactly unless a
    time-scaled window matched better (then the measured speed). ``conf`` = peak / second peak (>= 1
    means the best candidate beats both the runner-up outside ±0.3 s and the null-level maximum);
    confident windows are ``conf >= cfg.audio_min_conf``. Empty/silent audio -> ``AudioHints.empty()``."""
    with single_thread_blas():
        return _coarse_align(comp_y, raw_y, sr, cfg, dlog)


def _coarse_align(comp_y: np.ndarray, raw_y: np.ndarray, sr: int, cfg: Any, dlog: DecisionLog | None) -> AudioHints:
    """Implementation of ``coarse_align`` (runs with single-threaded BLAS)."""
    import time
    dlog = dlog or null_dlog()
    comp_y, raw_y = _mono(comp_y), _mono(raw_y)
    t0 = time.perf_counter()
    if comp_y.size == 0 or raw_y.size == 0 or _rms(comp_y) < _SILENT_RMS or _rms(raw_y) < _SILENT_RMS:
        dlog.record("audio_align", "no_audio", comp_samples=int(comp_y.size), raw_samples=int(raw_y.size))
        return AudioHints.empty()
    A = _Aligner(comp_y, raw_y, int(sr), cfg)
    N = len(A.starts_s)
    if N == 0 or A.Tr < A.n + 2:
        dlog.record("audio_align", "too_short", comp_s=len(comp_y) / sr, raw_s=len(raw_y) / sr)
        return AudioHints.empty()
    mc = A.min_conf
    res: list[_Res | None] = [None] * N
    live = [i for i in range(N) if A.energy_ok[i]]
    # ---- pass 1: speed 1.00 on every audible window -----------------------------------------------
    for b0 in range(0, len(live), 32):
        idx = live[b0:b0 + 32]
        curves = A.corr.ncc_multi([A.comp_coarse(A.i0[i], 1.0) for i in idx])
        for i, c in zip(idx, curves):
            res[i] = A.evaluate_curve(i, 1.0, c)
    n_conf1 = sum(1 for r in res if r is not None and r.ok(mc))
    # ---- pass 1b: slight speed changes on windows that matched at 1.00 --------------------------
    for i in live:
        r = res[i]
        if r is None or not r.ok(mc) or r.peak >= 0.9:
            continue           # an excellent 1.00 match leaves no room for a speed change
        vb, sb, _ = A.local_sweep(i, r, 0.05)
        # time-scaled (interpolated) features are slightly smoother, so leaving 1.00 needs a clear gain
        if abs(vb - 1.0) > 1e-9 and sb > r.peak + 0.03:
            r2 = A.evaluate(i, vb)
            if _better(r2, r, mc):
                res[i] = r2
    # ---- pass 2: time-scaled windows where 1.00 is weak -----------------------------------------
    tried: dict[int, set[float]] = {i: {1.0} for i in live}
    scan_stats = {"probes": 0, "scanned": 0, "gated": 0, "propagated": 0, "neighbour_speeds": 0,
                  "stopped_early": False}

    def weak(i: int) -> bool:
        return res[i] is None or not res[i].ok(mc)

    def try_speed(i: int, v: float) -> bool:
        """Global search of window i at speed v (+ local ±0.02 sweep when it matches); keeps it if
        better than the current result."""
        v = round(float(v), 6)
        if v in tried[i] or not (A.vmin - 1e-9 <= v <= A.vmax + 1e-9):
            return False
        tried[i].add(v)
        r = A.evaluate(i, v)
        if r is not None and r.ok(mc):
            vb, sb, _ = A.local_sweep(i, r, 0.02)
            if abs(vb - v) > 1e-9 and vb not in tried[i] and sb > r.peak + 0.005:
                tried[i].add(vb)
                r2 = A.evaluate(i, vb)
                if _better(r2, r, mc):
                    r = r2
        if _better(r, res[i], mc):
            res[i] = r
            return True
        return False

    def propagate(i: int) -> None:
        """Extend a speed found on window i to its neighbours (also over weaker confident results)."""
        v = res[i].v
        for d in (-1, 1):
            j = i + d
            while 0 <= j < N and j in tried:
                rj = res[j]
                if rj is not None and rj.ok(mc) and abs(rj.v - v) <= A.vstep + 1e-9:
                    j += d                     # already consistent
                    continue
                if not try_speed(j, v):
                    break
                scan_stats["propagated"] += 1
                j += d

    def scan(i: int) -> bool:
        scan_stats["scanned"] += 1
        speeds, top, ref = A.scan_speeds(i)
        # a speed change must explain the window clearly better than 1.00 on the coarse feature
        if not speeds or top < ref + 0.05:
            scan_stats["gated"] += 1
            return False
        found = False
        for v in speeds:
            found |= try_speed(i, v)
            if not weak(i):
                break
        if found:
            propagate(i)
        return found

    if A.speeds:
        weak_live = [i for i in live if weak(i)]
        if weak_live:
            n_probe = min(len(weak_live), 12)
            probes = sorted({weak_live[int(round(x))] for x in np.linspace(0, len(weak_live) - 1, n_probe)})
            any_found = False
            for i in probes:
                if weak(i):
                    scan_stats["probes"] += 1
                    any_found |= scan(i)
            if not any_found and n_conf1 == 0:
                scan_stats["stopped_early"] = True       # nothing matches anywhere: audio replaced
            else:
                for i in weak_live[::2] if any_found else weak_live[::4]:
                    if weak(i) and i not in probes:
                        scan(i)
                # windows between scanned ones: speeds found nearby
                for i in weak_live:
                    if not weak(i):
                        continue
                    near = sorted({round(res[j].v, 6) for j in range(max(0, i - 4), min(N, i + 5))
                                   if res[j] is not None and res[j].ok(mc) and abs(res[j].v - 1.0) > 1e-9})
                    for v in near:
                        scan_stats["neighbour_speeds"] += 1
                        if try_speed(i, v):
                            propagate(i)
                            break
    # ---- pass 3: sample-precise refinement ---------------------------------------------------
    comp_t = np.array([s + A.W / 2.0 for s in A.starts_s], np.float64)
    raw_t = np.full(N, np.nan)
    speed = np.ones(N)
    conf = np.ones(N, np.float32)
    psr = np.zeros(N, np.float32)
    peak = np.zeros(N, np.float32)
    wave = np.full(N, np.nan, np.float32)
    evid = []
    last_refined: float | None = None
    for i in range(N):
        r = res[i]
        if r is None:
            evid.append({"i": i, "comp_t": round(float(comp_t[i]), 3), "silent": not A.energy_ok[i]})
            continue
        conf[i] = max(0.0, r.conf)
        psr[i] = r.psr
        peak[i] = r.peak
        if r.conf > 1.0:
            if r.ok(mc):
                A.refine(i, r, hints=[s for s in (last_refined,) if s is not None])
                if np.isfinite(r.wave_peak) and abs(r.speed - 1.0) > 1e-9:
                    last_refined = r.speed
            else:
                r.raw_t = float(r.lag / A.rate + r.v * A.W / 2.0)
                r.speed = r.v
            raw_t[i] = r.raw_t
            speed[i] = r.speed
            wave[i] = r.wave_peak
        else:
            speed[i] = r.v
        evid.append({"i": i, "comp_t": round(float(comp_t[i]), 3), "raw_t": None if not np.isfinite(raw_t[i]) else round(float(raw_t[i]), 6),
                     "speed": round(float(speed[i]), 5), "hyp": r.hyp, "peak": round(r.peak, 4),
                     "second": round(r.second, 4), "null_floor": round(r.floor, 4), "coarse": round(r.coarse, 4),
                     "conf": round(float(r.conf), 3),
                     "psr": round(r.psr, 2), "wave_peak": None if not np.isfinite(r.wave_peak) else round(r.wave_peak, 4)})
    hints = AudioHints(comp_t=comp_t, raw_t=raw_t, speed=speed, conf=conf.astype(np.float32),
                       psr=psr.astype(np.float32), peak=peak.astype(np.float32), window=float(A.W), hop=float(A.hop),
                       wave_peak=wave.astype(np.float32))
    n_conf = int(hints.confident(mc).sum())
    speeds_found = sorted({round(float(s), 3) for s, c in zip(speed, hints.confident(mc)) if c and abs(s - 1.0) > 1e-9})
    dt = time.perf_counter() - t0
    dlog.record("audio_align", "coarse_align", windows=N, silent=int(N - len(live)), confident=n_conf,
                confident_at_1=n_conf1, speeds_found=speeds_found, evaluations=A.n_evals, scan=scan_stats,
                comp_s=round(len(comp_y) / sr, 3), raw_s=round(len(raw_y) / sr, 3), seconds=round(dt, 2),
                evidence={"windows": evid})
    log.info("audio: %d/%d windows confident (%d at 1.00), speeds %s, %.1fs", n_conf, N, n_conf1, speeds_found or "-", dt)
    return hints


def _rms(y: np.ndarray, chunk: int = 1 << 20) -> float:
    """RMS without a full-size float64 temporary (RAWs can be hours long)."""
    y = np.asarray(y).reshape(-1)
    if y.size == 0:
        return 0.0
    acc = 0.0
    for i in range(0, y.size, chunk):
        c = y[i:i + chunk].astype(np.float64)
        acc += float(np.dot(c, c))
    return math.sqrt(acc / y.size)


# =============================================================================================
# Stage 5.6 -- audio per segment
# =============================================================================================

@dataclass
class _Model:
    """RAW time map of one segment (comp seconds -> RAW seconds), extrapolated beyond the segment."""
    seg: Segment
    kind: str                         # 'stretch' | 'remap'
    raw_in: float
    v: float
    t_in: float
    key_t: np.ndarray | None = None
    key_r: np.ndarray | None = None

    def raw_seconds(self, t: np.ndarray) -> np.ndarray:
        if self.kind == "stretch":
            return self.raw_in + self.v * (t - self.t_in)
        return np.interp(t, self.key_t, self.key_r)

    def slope_max(self) -> float:
        if self.kind == "stretch":
            return abs(self.v)
        if self.key_t is None or self.key_t.size < 2:
            return 1.0
        d = np.abs(np.diff(self.key_r) / np.maximum(np.diff(self.key_t), 1e-9))
        return float(d.max()) if d.size else 1.0

    def render(self, raw_y: np.ndarray, sr: int, n0: int, n1: int, lag_s: float = 0.0) -> np.ndarray:
        """Rebuilt audio for comp samples [n0, n1) (tape-style resample; ``lag_s`` = measured delay of
        the rebuilt vs the competitor, compensated by evaluating the map at t + lag)."""
        if n1 <= n0:
            return np.zeros(0, np.float32)
        t = (np.arange(n0, n1, dtype=np.float64) + lag_s * sr) / sr
        pos = self.raw_seconds(t) * sr
        out = resample_at(raw_y, pos, cutoff=min(1.0, 1.0 / max(self.slope_max(), 1e-6)))
        if self.kind == "remap" and self.key_t is not None and self.key_t.size >= 2:
            # frozen time (slope ~ 0) plays no audio in AE
            sl = np.abs(np.gradient(pos))
            out[sl < 1e-3] = 0.0
        return out


def _build_model(s: Segment, fps: Fraction, sr: int) -> _Model | None:
    if s.type != "raw" or s.raw_in_seconds is None:
        return None
    t_in = float(Fraction(int(s.comp_in)) / fps)
    keys = list(s.time_remap_keys or [])
    if s.time_mode == "remap" and len(keys) >= 2:
        kt = np.array([float(k["comp_frame"]) / float(fps) for k in keys], np.float64)
        kr = np.array([float(k["raw_seconds"]) for k in keys], np.float64)
        order = np.argsort(kt, kind="stable")
        return _Model(s, "remap", float(s.raw_in_seconds), float(s.speed), t_in, kt[order], kr[order])
    if s.speed is None or not np.isfinite(float(s.speed)):
        return None
    return _Model(s, "stretch", float(s.raw_in_seconds), float(s.speed), t_in)


def _crossfade_frames(s: Segment) -> tuple[int, int]:
    """(frames of incoming overlap at comp_in, frames of outgoing overlap at comp_out)."""
    def d(tr: dict | None) -> int:
        if tr and str(tr.get("type", "")) == "crossfade":
            return int(tr.get("duration_frames") or 0)
        return 0
    return d(s.transition_in), d(s.transition_out)


def _local_ncc(c: np.ndarray, m: np.ndarray, win: int, hop: int) -> tuple[np.ndarray, np.ndarray]:
    """Short-time NCC of two aligned signals (frames of ``win`` samples every ``hop``) and a
    'both audible' mask."""
    n = min(c.size, m.size)
    if n < win:
        return np.zeros(0), np.zeros(0, bool)
    from numpy.lib.stride_tricks import sliding_window_view
    C = sliding_window_view(c[:n].astype(np.float64), win)[::hop]
    M = sliding_window_view(m[:n].astype(np.float64), win)[::hop]
    C = C - C.mean(1, keepdims=True)
    M = M - M.mean(1, keepdims=True)
    ec = np.sum(C * C, 1)
    em = np.sum(M * M, 1)
    thr = win * _SILENT_RMS ** 2
    ok = (ec > thr) & (em > thr)
    ncc = np.where(ok, np.sum(C * M, 1) / np.sqrt(np.maximum(ec * em, 1e-300)), 0.0)
    return ncc, ok


def _logfreq_spectrum(y: np.ndarray, sr: int, n_fft: int = 4096) -> tuple[np.ndarray, np.ndarray]:
    """Welch-averaged log power spectrum (dB) on the linear FFT grid: (freqs, dB)."""
    y = np.asarray(y, np.float64)
    freqs = np.arange(n_fft // 2 + 1) * sr / n_fft
    if y.size < n_fft:
        y = np.pad(y, (0, n_fft - y.size))
    from numpy.lib.stride_tricks import sliding_window_view
    fr = sliding_window_view(y, n_fft)[::n_fft // 4]
    win = np.hanning(n_fft)
    P = np.mean(np.abs(np.fft.rfft(fr * win, axis=1)) ** 2, axis=0)
    return freqs, 10.0 * np.log10(P + 1e-12)


def _whiten(s: np.ndarray, width: int) -> np.ndarray:
    k = np.ones(2 * width + 1) / (2 * width + 1)
    sm = np.convolve(np.pad(s, width, mode="edge"), k, mode="valid")
    w = s - sm
    sd = w.std()
    return (w - w.mean()) / sd if sd > 1e-9 else np.zeros_like(w)


def pitch_shift_test(comp_part: np.ndarray, raw_part: np.ndarray, v: float, sr: int, bpo: int = 48) -> dict:
    """Is the pitch of ``comp_part`` (competitor audio of a speed-v segment) shifted by v relative to
    ``raw_part`` (the RAW audio it plays)? Whitened log-frequency spectra are correlated at shift
    log2(v) (tape: pitch follows speed) and at shift 0 (pitch preserved), ±1 bin (1/48 octave) for
    speed uncertainty. Returns {'corr_shift', 'corr_zero', 'pitch_preserved': bool|None}."""
    out = {"corr_shift": None, "corr_zero": None, "pitch_preserved": None}
    if v <= 0 or abs(math.log2(v)) * bpo < 2.0 or comp_part.size < sr // 4 or raw_part.size < sr // 4:
        return out
    f, Sc = _logfreq_spectrum(comp_part, sr)
    _, Sr = _logfreq_spectrum(raw_part, sr)
    f_hi = 0.45 * sr / max(1.0, v)
    f_lo = 80.0 / min(1.0, v)
    if f_hi <= f_lo * 2:
        return out
    g = f_lo * 2.0 ** (np.arange(int(math.floor(math.log2(f_hi / f_lo) * bpo))) / bpo)
    R = _whiten(np.interp(g, f, Sr), bpo // 3)

    def corr_at(scale: float) -> float:
        best = -1.0
        for d in (-1, 0, 1):
            C = _whiten(np.interp(g * scale * 2.0 ** (d / bpo), f, Sc), bpo // 3)
            if C.std() > 0 and R.std() > 0:
                best = max(best, float(np.mean(C * R)))
        return best
    cs, cz = corr_at(v), corr_at(1.0)
    out["corr_shift"], out["corr_zero"] = round(cs, 4), round(cz, 4)
    if max(cs, cz) >= 0.15:
        if cs - cz > 0.1:
            out["pitch_preserved"] = False
        elif cz - cs > 0.1:
            out["pitch_preserved"] = True
    return out


def feature_pitch_test(comp_part: np.ndarray, raw_part: np.ndarray, v: float, sr: int, cfg: Any) -> dict:
    """Time-aligned evidence for a speed-v segment: delta-log-mel NCC between the competitor audio
    (time-scaled onto the RAW frame grid) and the RAW audio it plays, with the competitor's mel filters
    warped by v (tape: pitch follows speed) or not (pitch preserved). Also tells whether the segment's
    audio is related to the RAW at all. Returns {'ncc_tape', 'ncc_tempo', 'related', 'pitch_preserved'}."""
    out: dict[str, Any] = {"ncc_tape": None, "ncc_tempo": None, "related": False, "pitch_preserved": None}
    if v <= 0 or comp_part.size < sr // 4 or raw_part.size < sr // 4:
        return out
    fc = _spectral(np.asarray(comp_part, np.float32), sr, cfg, keep_power=True)
    fr = _spectral(np.asarray(raw_part, np.float32), sr, cfg)
    Tr, Tc = fr["logmel"].shape[0], fc["logmel"].shape[0]
    if Tr < 8 or Tc < 8:
        return out
    q = np.clip(np.arange(Tr) / v, 0.0, Tc - 1.0)
    j0 = np.floor(q).astype(np.int64)
    j1 = np.minimum(j0 + 1, Tc - 1)
    w = (q - j0)[:, None]
    st = _feature_setup(sr, cfg)
    R = _delta(fr["logmel"]).astype(np.float64)
    R -= R.mean(0)
    res = {}
    for hyp, L in ((_TAPE, _log_db(fc["power"] @ mel_filterbank(sr, fc["n_fft"], st["n_mels"], st["fmin"], st["fmax"], warp=v).T)),
                   (_TEMPO, fc["logmel"])):
        Ci = L[j0] * (1.0 - w) + L[j1] * w
        C = _delta(Ci).astype(np.float64)
        C -= C.mean(0)
        den = math.sqrt(float(np.sum(C * C)) * float(np.sum(R * R)))
        res[hyp] = float(np.sum(C * R) / den) if den > 0 else 0.0
    out["ncc_tape"], out["ncc_tempo"] = round(res[_TAPE], 4), round(res[_TEMPO], 4)
    best = max(res.values())
    out["related"] = bool(best >= 0.4)
    if out["related"] and abs(math.log2(v)) * 48 >= 2.0:
        if res[_TAPE] - res[_TEMPO] > 0.05:
            out["pitch_preserved"] = False
        elif res[_TEMPO] - res[_TAPE] > 0.05:
            out["pitch_preserved"] = True
    return out


def _pitch_decision(spectral: dict, feat: dict) -> bool | None:
    """Combine the spectral-shift and time-aligned feature tests: only for audio related to the RAW;
    agreement (or one undecided) decides, disagreement -> None."""
    if not feat.get("related"):
        return None
    a, b = spectral.get("pitch_preserved"), feat.get("pitch_preserved")
    if a is None:
        return b
    if b is None or a == b:
        return a
    return None


def _classify_added(res: np.ndarray, sr: int, frame: int) -> str:
    """music | voice_over | sfx from the residual signal of one added-audio run."""
    dur = res.size / sr
    if dur < 1.5:
        return "sfx"
    n = res.size // frame
    e = np.sum(res[:n * frame].astype(np.float64).reshape(n, frame) ** 2, 1) / frame
    db = 10 * np.log10(e + 1e-12)
    med = float(np.median(db))
    pause = float(np.mean(db < med - 15.0))
    # syllabic modulation: share of envelope modulation energy in 2.5..8 Hz
    env = db - db.mean()
    fr = 1.0 / (frame / sr)
    spec = np.abs(np.fft.rfft(env * np.hanning(env.size))) ** 2
    fq = np.fft.rfftfreq(env.size, 1.0 / fr)
    band = spec[(fq >= 2.5) & (fq <= 8.0)].sum()
    total = spec[(fq >= 0.3)].sum() + 1e-12
    syll = band / total
    if pause >= 0.12 and syll >= 0.35:
        return "voice_over"
    if dur < 3.0 and pause >= 0.3:
        return "sfx"
    return "music"


def analyze_segments_audio(segments: Sequence[Segment], comp_y: np.ndarray, raw_y: np.ndarray, sr: int,
                           comp_fps: Any, cfg: Any, dlog: DecisionLog | None, *, av_offset_s: float = 0.0,
                           pass_name: str | None = None) -> dict:
    """Stage 5.6: per-segment audio analysis against the RAW-rebuilt track.

    Returns ``{'segments': {id: {in_offset_frames, out_offset_frames, pitch_preserved, lag_ms, corr,
    exception}}, 'added_audio': [{type, comp_in, comp_out, level_db, level_dbfs}], 'status':
    'ok'|'no_audio'|'audio_replaced', 'notes': [...], 'cuts': [J/L decisions], '_av_offset_s': the offset
    used, '_switch_baseline': {...}, '_measured': {id: {lag_total_ms, dur_s, corr}}}`` (keys starting with
    '_' are run internals, not cutlist fields).

    * ``av_offset_s`` = the run's competitor A/V offset g (xcorr convention, DESIGN §7 D9; 0 = in sync):
      every lag search renders the RAW pre-shifted by g and searches only the residual within
      ``±min(cfg.audio_residual_search_s, half the range)``; ``lag_ms`` is that residual (measured lag - g).
    * J/L (DESIGN §3, §7 D9): audio range = [comp_in + in_offset, comp_out + out_offset). The audio switch
      time of every hard cut is measured (sub-hop); the run's switch baseline b = weighted median over
      decisive cuts between long, well-correlated segments (any value: an offset applied after the edit
      moves every switch). A cut is a J/L cut only when its switch differs from b by >= max(0.5 frame,
      3 sigma): A.out_offset = B.in_offset = round((switch - b) * fps) (negative = J-cut: B's audio leads;
      positive = L-cut: A's audio trails). Ranges always stay ordered (a0 < a1).
    * lag_ms / corr: ``xcorr_lag(competitor, rebuilt)`` over the comp samples where the competitor plays the
      segment's audio range (shifted by b; crossfade overlaps excluded): positive lag = rebuilt late.
    * pitch_preserved: stretch segments with speed != 1 only, else None: ``pitch_shift_test``
      (spectral shift) combined with ``feature_pitch_test`` (time-aligned tape vs unwarped features,
      which also requires the audio to be related to the RAW); disagreement or unrelated -> None.
    * added_audio: residual (competitor - gain * lag-compensated rebuilt) energy frames above
      ``cfg.audio_added_thresh_db`` (default -20 dB) relative to the rebuilt track, median-smoothed,
      merged across unobservable ranges (NOT-IN-RAW, dips, crossfades); level_db = residual level
      relative to the original (rebuilt) audio in the run, level_dbfs = absolute.
    * exception (closed list): not_in_raw, no_audio, audio_replaced, pitch_preserved,
      music_dominated, too_short -- set when the segment's audio does not line up (judged on the residual)
      and why."""
    with single_thread_blas():
        return _analyze_segments_audio(segments, comp_y, raw_y, sr, comp_fps, cfg, dlog, float(av_offset_s or 0.0),
                                       pass_name)


def _round_half_away(x: float) -> int:
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def _weighted_median(x: np.ndarray, w: np.ndarray) -> float:
    o = np.argsort(x, kind="stable")
    x, w = np.asarray(x, np.float64)[o], np.asarray(w, np.float64)[o]
    c = np.cumsum(w)
    return float(x[int(np.searchsorted(c, 0.5 * c[-1]))])


def _switch_time(sA: np.ndarray, sB: np.ndarray, jb: int, centres: np.ndarray, hop: int) -> float:
    """Audio switch position (samples) before local-NCC frame ``jb``: the zero crossing of sA - sB between
    the frame centres jb-1 and jb (linear, sub-hop), else half-way between them."""
    nfr = sA.size
    if jb <= 0:
        return float(centres[0] - hop / 2.0)
    if jb >= nfr:
        return float(centres[-1] + hop / 2.0)
    d0, d1 = float(sA[jb - 1] - sB[jb - 1]), float(sA[jb] - sB[jb])
    if d0 > 0.0 > d1:
        return float(centres[jb - 1] + hop * d0 / (d0 - d1))
    return float(centres[jb] - hop / 2.0)


def _analyze_segments_audio(segments: Sequence[Segment], comp_y: np.ndarray, raw_y: np.ndarray, sr: int,
                            comp_fps: Any, cfg: Any, dlog: DecisionLog | None, g: float = 0.0,
                            pass_name: str | None = None) -> dict:
    """Implementation of ``analyze_segments_audio`` (runs with single-threaded BLAS)."""
    dlog = dlog or null_dlog()
    fps = parse_fps(comp_fps)
    sr = int(sr)
    comp, raw = _mono(comp_y), _mono(raw_y)
    segs = sorted(segments, key=lambda s: (s.comp_in, s.id))
    min_corr = float(_cfg(cfg, "audio_replaced_corr", 0.3))
    tol_ms = float(_cfg(cfg, "audio_lag_tol_ms", 10.0))
    jl_max_s = float(_cfg(cfg, "audio_jl_max_s", 1.0))
    thr_db = float(_cfg(cfg, "audio_added_thresh_db", -20.0))
    res_s = float(_cfg(cfg, "audio_residual_search_s", _MAX_LAG_SEG_S))
    unique = float(_cfg(cfg, "audio_peak_unique_margin", 0.1))
    tag = {"pass": pass_name} if pass_name else {}

    def rec(decision: str, **ev: Any) -> None:
        dlog.record("audio_segments", decision, **tag, **ev)

    out: dict[int, dict] = {}
    for s in segs:
        out[s.id] = {"in_offset_frames": 0, "out_offset_frames": 0, "pitch_preserved": None, "lag_ms": None,
                     "corr": None, "exception": "not_in_raw" if s.type == "not_in_raw" else
                     ("uncertain" if s.type == "uncertain" else None), "line": None}
    notes: list[str] = []
    n_frames = max((int(s.comp_out) for s in segs), default=0)

    def f2s(k: int) -> int:
        """Comp frame index -> audio sample index (exact rational, rounded)."""
        return int(round(Fraction(int(k)) * sr / fps))

    if comp.size == 0 or raw.size == 0 or _rms(comp) < _SILENT_RMS or _rms(raw) < _SILENT_RMS:
        which = "competitor" if (comp.size == 0 or _rms(comp) < _SILENT_RMS) else "RAW"
        for s in segs:
            if s.type == "raw":
                out[s.id]["exception"] = "no_audio"
        notes.append(f"no usable audio in the {which}: per-segment audio analysis skipped")
        rec("no_audio", which=which)
        return {"segments": out, "added_audio": [], "status": "no_audio", "notes": notes, "cuts": [],
                "_av_offset_s": g, "_switch_baseline": {"ms": None, "n": 0}, "_measured": {}}

    models: dict[int, _Model] = {}
    for s in segs:
        m = _build_model(s, fps, sr)
        if m is not None:
            models[s.id] = m
    n_total = min(comp.size, f2s(n_frames)) if n_frames else comp.size
    # Where the competitor may switch to a segment's audio relative to its picture cut while the switch
    # baseline is not known yet: anywhere between 0 (offset inside the source) and -g (offset applied to
    # the finished mix). Comp windows that must hold only the segment's own audio leave that band out.
    gl, gh = int(round(min(0.0, -g) * sr)), int(round(max(0.0, -g) * sr))

    # ---- core ranges (samples) and first lag estimate ------------------------------------------
    core: dict[int, tuple[int, int]] = {}
    lag0: dict[int, float] = {}
    pk0: dict[int, float] = {}
    for s in segs:
        if s.id not in models:
            continue
        din, dout = _crossfade_frames(s)
        a, b = f2s(s.comp_in + din), min(f2s(s.comp_out - dout), comp.size)
        core[s.id] = (a, b)
        lag0[s.id] = g                 # too short / weak: the run's offset (never 0 when the run is offset)
        w0, w1 = a + gh, min(b + gl, comp.size)
        if w1 - w0 >= int(0.1 * sr):
            lag, pk = xcorr_lag(comp[w0:w1], models[s.id].render(raw, sr, w0, w1, g), sr, res_s)
            pk0[s.id] = pk
            if pk >= min_corr:
                lag0[s.id] = g + lag

    # ---- J/L cuts: switch time per hard cut ------------------------------------------------------
    win, hop = int(round(0.03 * sr)), int(round(0.01 * sr))
    side = 5                                                  # local-NCC frames per side for the decisiveness

    def measure(A: Segment, B: Segment) -> dict | None:
        """The audio switch at the hard cut A|B: local NCC of both models (rendered at their lag0) across the
        cut, the best single switch, its sub-hop time and how decisive it is on both sides."""
        mA, mB = models.get(A.id), models.get(B.id)
        if mA is None and mB is None:
            return None
        cut = int(B.comp_in)
        jmax = min(int(round(jl_max_s * float(fps))), max(0, A.length - 2))
        lmax = min(int(round(jl_max_s * float(fps))), max(0, B.length - 2))
        if mA is None:
            lmax = min(lmax, B.length // 2)
            jmax = min(jmax, A.length // 2)
        if jmax + lmax < 1:
            return None
        n0, n1 = max(0, f2s(cut - jmax) + gl), min(f2s(cut + lmax) + gh, comp.size)
        if n1 - n0 < win + hop:
            return None
        c = comp[n0:n1]
        sA = _local_ncc(c, mA.render(raw, sr, n0, n1, lag0.get(A.id, g)), win, hop)[0] if mA else None
        sB = _local_ncc(c, mB.render(raw, sr, n0, n1, lag0.get(B.id, g)), win, hop)[0] if mB else None
        nfr = (sA if sA is not None else sB).size
        if nfr < 2:
            return None
        # boundary between frame j-1 and j sits half-way between their centres
        centres = n0 + np.arange(nfr) * hop + win // 2
        j_cut = int(np.clip(np.searchsorted(centres, f2s(cut)), 0, nfr))
        if sA is None or sB is None:
            # one side has no RAW model (placeholder): the other model must keep explaining the audio
            # clearly better than half its typical local NCC on its own side of the cut
            own = sA[:j_cut] if sA is not None else sB[j_cut:]
            level = float(np.median(own)) if own.size else 0.0
            if level < 0.4:
                return None
            sA = sA if sA is not None else np.full(nfr, 0.5 * level)
            sB = sB if sB is not None else np.full(nfr, 0.5 * level)
        csA = np.concatenate([[0.0], np.cumsum(sA)])
        csB = np.concatenate([[0.0], np.cumsum(sB)])
        score = csA + (csB[-1] - csB)                         # switch before frame j, j = 0..nfr
        jb = int(np.argmax(score))
        t_sw = _switch_time(sA, sB, jb, centres, hop)
        pre = (sA - sB)[max(0, jb - 1 - side):max(0, jb - 1)]
        post = (sB - sA)[min(nfr, jb + 1):min(nfr, jb + 1 + side)]
        decisive = float(min(pre.mean(), post.mean())) if pre.size and post.size else 0.0
        return {"A": A, "B": B, "mA": mA, "mB": mB, "cut": cut, "jmax": jmax, "lmax": lmax, "sA": sA, "sB": sB,
                "centres": centres, "jb": jb, "j_cut": j_cut, "score": score, "t_sw": t_sw,
                "switch_s": t_sw / sr - float(Fraction(cut) / fps), "decisive": decisive,
                "sides": (int(pre.size), int(post.size))}

    pairs = [(A, B) for A, B in zip(segs[:-1], segs[1:])
             if A.comp_out == B.comp_in and not _crossfade_frames(A)[1] and not _crossfade_frames(B)[0]]
    meas: list[dict] = [m for m in (measure(A, B) for A, B in pairs) if m is not None]

    # ---- the run's switch baseline (DESIGN §7 D9) ------------------------------------------------
    k_strong = int(_cfg(cfg, "audio_jl_strong_frames", 10))
    c_strong = float(_cfg(cfg, "audio_jl_strong_corr", 0.8))
    m_strong = float(_cfg(cfg, "audio_jl_strong_margin", 0.5))
    n_min = int(_cfg(cfg, "audio_jl_baseline_min_cuts", 3))
    # decisive cuts between two long segments with both models: the switch is clearly measured there. The
    # 'strong' tier also wants both models to correlate over their whole core; with fewer than n_min such
    # cuts the decisive cuts alone decide (a genuine J/L at a segment's OTHER end lowers that core's
    # correlation -- film24's S02 after the 6-frame L-cut -- without making the switch at this cut less clear)
    decisive_cuts = [m for m in meas if m["mA"] is not None and m["mB"] is not None and m["A"].length >= k_strong
                     and m["B"].length >= k_strong and m["decisive"] >= m_strong]
    strong = [m for m in decisive_cuts if pk0.get(m["A"].id, 0.0) >= c_strong and pk0.get(m["B"].id, 0.0) >= c_strong]
    tier = "strong"
    if len(strong) < n_min <= len(decisive_cuts):
        strong, tier = decisive_cuts, "decisive"
    sigma = hop / math.sqrt(12.0) / sr                      # sub-hop switch estimate (window/hop geometry)
    base: float | None = None
    spread = None
    if len(strong) >= n_min:
        xs = np.array([m["switch_s"] for m in strong])
        ws = np.array([m["decisive"] for m in strong])
        base = _weighted_median(xs, ws)
        spread = 1.4826 * _weighted_median(np.abs(xs - base), ws)
        sigma = max(sigma, spread)
    thr = max(float(_cfg(cfg, "audio_jl_min_frames", 0.5)) / float(fps), 3.0 * sigma)
    if base is not None and abs(base) < 0.5 * thr:
        base = 0.0                     # below half the J/L threshold: indistinguishable from switching at the cut
    b_lo, b_hi = (base, base) if base is not None else (min(0.0, -g), max(0.0, -g))
    switch_info = {"ms": None if base is None else round(base * 1000.0, 3), "n": len(strong),
                   "spread_ms": None if spread is None else round(spread * 1000.0, 3),
                   "threshold_ms": round(thr * 1000.0, 3), "range_ms": [round(b_lo * 1000.0, 3), round(b_hi * 1000.0, 3)],
                   "tier": tier if base is not None else None}
    rec("switch_baseline", **switch_info, measured_cuts=len(meas), av_offset_ms=round(g * 1000.0, 3),
        strong=[{"cut": m["cut"], "switch_ms": round(m["switch_s"] * 1000.0, 3), "decisive": round(m["decisive"], 4)}
                for m in strong],
        candidates=[{"cut": m["cut"], "switch_ms": round(m["switch_s"] * 1000.0, 3), "decisive": round(m["decisive"], 4),
                     "pk0": [None if m["A"].id not in pk0 else round(pk0[m["A"].id], 4),
                             None if m["B"].id not in pk0 else round(pk0[m["B"].id], 4)],
                     "frames": [m["A"].length, m["B"].length]} for m in meas if m["decisive"] > 0.0])

    # ---- a known baseline says where the competitor plays each segment: models whose band-excluded core was
    # too short to measure (3-5 frame segments next to an offset of the finished mix) are aligned on that
    # window now, and the switches next to them measured again (a model left at the run's offset can be a
    # fraction of a ms off: on tonal audio the local NCC then flips sign and the switch lands anywhere) ----
    if base is not None:
        sh = int(round(base * sr))
        again: set[int] = set()
        for s in segs:
            if s.id not in models or s.id in pk0:
                continue
            a, b = core[s.id]
            w0, w1 = max(0, a + sh), min(b + sh, comp.size)
            if w1 - w0 >= int(0.1 * sr):
                lag, pk, sl = xcorr_lag_side(comp[w0:w1], models[s.id].render(raw, sr, w0, w1, g), sr, res_s)
                pk0[s.id] = pk
                use = pk >= min_corr and pk - sl >= unique       # a short window's lag only when its peak is unique
                if use:
                    lag0[s.id] = g + lag
                    again.add(s.id)
                rec("lag0_at_baseline", seg=s.id, window_samples=[w0, w1], lag_ms=round((g + lag) * 1000.0, 3),
                    corr=round(pk, 4), sidelobe=round(sl, 4), used=bool(use))
        if again:
            by_cut = {m["cut"]: m for m in meas}
            for A, B in pairs:
                if A.id in again or B.id in again:
                    m = measure(A, B)
                    if m is None:
                        by_cut.pop(int(B.comp_in), None)
                    else:
                        by_cut[m["cut"]] = m
            meas = [by_cut[k] for k in sorted(by_cut)]

    # ---- J/L decisions: switch vs baseline, ranges kept ordered --------------------------------
    cuts: list[dict] = []
    big = int(_cfg(cfg, "audio_jl_large_frames", 4))
    min_dec = float(_cfg(cfg, "audio_jl_min_decisive", 0.3))
    for m in meas:
        A, B, mA, mB, cut = m["A"], m["B"], m["mA"], m["mB"], m["cut"]
        sA, sB, centres, jb = m["sA"], m["sB"], m["centres"], m["jb"]
        sw = m["switch_s"]
        near = min(max(sw, b_lo), b_hi)
        dsw = sw - near                                    # how far the switch lies outside the baseline
        offset = _round_half_away(dsw * float(fps))
        # A's audio range must stay ordered: comp_out + offset > comp_in + in_offset(A)
        off_lo = max(-m["jmax"], int(out[A.id]["in_offset_frames"]) - A.length + 1)
        offset = int(min(max(offset, off_lo), m["lmax"]))
        nfr = sA.size
        j_ref = int(np.clip(np.searchsorted(centres, f2s(cut) + near * sr), 0, nfr))
        gain = float(m["score"][jb] - m["score"][j_ref])
        lo_j, hi_j = sorted((jb, j_ref))
        diff = (sB - sA)[lo_j:hi_j] if jb < j_ref else (sA - sB)[lo_j:hi_j]
        mdiff = float(diff.mean()) if diff.size else 0.0
        # the switch must lie clearly outside the baseline, the frames in between must clearly follow the other
        # model (mean local-NCC margin >= 0.3) and both models must explain their own side of the switch
        # (evidence on both sides: a switch at the edge of the search window, where one side was never
        # observed, is not a J/L cut -- film24's 'J-cut' at 189 after a 3-frame segment)
        both_sides = min(m["sides"]) >= 2 and m["decisive"] >= min_dec
        accept = abs(dsw) >= thr and offset != 0 and diff.size >= 2 and mdiff >= 0.3 and both_sides
        reason = None
        if accept and abs(offset) >= big:
            # a large J/L next to a retimed segment, or where B's time line meets A's extended line, is
            # evidence about the picture model (continuous audio over a freeze / slow motion, one shot
            # split in two), not an editorial J/L cut: logged, not exported
            retimed = [s.id for s in (A, B) if s.type == "raw" and (s.time_mode == "remap" or s.speed is None
                                                                      or abs(float(s.speed) - 1.0) > 1e-6)]
            t = np.array([m["t_sw"] / sr])
            meets = (mA is not None and mB is not None
                     and abs(float(mA.raw_seconds(t)[0]) - float(mB.raw_seconds(t)[0])) <= 1.0 / float(fps))
            if retimed or meets:
                accept = False
                reason = (f"next to retimed segment(s) {', '.join(f'S{i:02d}' for i in retimed)}" if retimed
                          else "the switch is where the next segment's time line meets the previous one's")
        ev = {"cut": cut, "a": A.id, "b": B.id, "switch_ms": round(sw * 1000.0, 3),
              "baseline_ms": None if base is None else round(base * 1000.0, 3),
              "switch_frame": int(cut + offset), "offset_frames": offset if accept else 0, "measured_offset": offset,
              "mean_margin": round(mdiff, 4), "decisive": round(m["decisive"], 4), "sides": list(m["sides"]),
              "gain": round(gain, 3),
              "a_model": mA is not None, "b_model": mB is not None}
        if reason:
            ev["not_exported"] = reason
        cuts.append(ev)
        rec("jl_cut" if accept else ("jl_not_exported" if reason else "audio_cut_matches_video"), **ev)
        if reason:
            notes.append(f"audio switch {offset:+d} frames from the picture cut at comp frame {cut} "
                         f"(S{A.id:02d}|S{B.id:02d}) not exported as a J/L cut: {reason}")
        if accept:
            if mA is not None:
                out[A.id]["out_offset_frames"] = offset
            if mB is not None:
                out[B.id]["in_offset_frames"] = offset
            notes.append(f"{'J' if offset < 0 else 'L'}-cut at comp frame {cut} (S{A.id:02d}|S{B.id:02d}): "
                         f"audio {'leads' if offset < 0 else 'trails'} by {abs(offset)} frames")

    # ---- per-segment lag / corr over the final audio range + pitch -----------------------------
    # the competitor plays a segment's audio range shifted by the switch baseline (or somewhere in the
    # band while it is unknown): measure only where it surely plays this segment
    sh_lo, sh_hi = int(round(b_lo * sr)), int(round(b_hi * sr))
    ranges: dict[int, tuple[int, int]] = {}
    played: dict[int, tuple[int, int]] = {}
    silent: dict[int, str] = {}
    measured: dict[int, dict] = {}
    for s in segs:
        if s.id not in models:
            continue
        din, dout = _crossfade_frames(s)
        a = f2s(s.comp_in + (din if din else out[s.id]["in_offset_frames"]))
        b = min(f2s(s.comp_out - (dout if dout else -out[s.id]["out_offset_frames"])), comp.size)
        a = max(0, a)
        ranges[s.id] = (a, b)
        w0, w1 = max(0, a + sh_hi), min(b + sh_lo, comp.size)
        played[s.id] = (w0, w1)
        if w1 - w0 < int(0.1 * sr):
            continue
        rb = models[s.id].render(raw, sr, w0, w1, g)
        if _rms(comp[w0:w1]) < _SILENT_RMS:
            silent[s.id] = "competitor"
            continue
        if _rms(rb) < _SILENT_RMS:
            silent[s.id] = "rebuilt"          # e.g. a freeze: AE plays no audio, the competitor does
            continue
        lag, pk, sl = xcorr_lag_side(comp[w0:w1], rb, sr, res_s)
        out[s.id]["lag_ms"] = round(lag * 1000.0, 3)
        out[s.id]["corr"] = round(pk, 4)
        measured[s.id] = {"lag_total_ms": round((g + lag) * 1000.0, 3), "dur_s": round((w1 - w0) / sr, 6),
                          "corr": round(pk, 4)}
        m = models[s.id]
        if m.kind == "stretch" and m.v > 0 and abs(m.v - 1.0) > 1e-6:
            r0 = m.raw_seconds(np.array([w0 / sr + g]))[0]
            r1 = m.raw_seconds(np.array([w1 / sr + g]))[0]
            ra, rb_ = int(round(r0 * sr)), int(round(r1 * sr))
            cpart, rpart = comp[w0:w1], raw[max(0, ra):max(0, rb_)]
            pt = pitch_shift_test(cpart, rpart, m.v, sr)
            fm = feature_pitch_test(cpart, rpart, m.v, sr, cfg)
            out[s.id]["pitch_preserved"] = _pitch_decision(pt, fm)
            rec("pitch", seg=s.id, speed=m.v, spectral=pt, features=fm, pitch_preserved=out[s.id]["pitch_preserved"])
        # a short window's lag only when its peak is unique (periodic tonal audio / a music bed: S07 above)
        ok_lag = pk >= min_corr and (w1 - w0 >= int(_MIN_SEG_S * sr) or pk - sl >= unique)
        lag0[s.id] = (g + lag) if ok_lag else lag0.get(s.id, g)

    # ---- run status -----------------------------------------------------------------------------
    measured_ids = [sid for sid, (a, b) in ranges.items() if b - a >= int(_MIN_SEG_S * sr) and out[sid]["corr"] is not None]
    status = "ok"
    if measured_ids and all(out[sid]["corr"] < min_corr for sid in measured_ids):
        status = "audio_replaced"
        notes.append("competitor audio does not correlate with the RAW-rebuilt audio in any segment (audio replaced)")

    # ---- audio lines over video-only retimes / uncertain / placeholder regions (FX-14) -------------
    line_models: dict[int, _Model] = {}
    if status == "ok":
        line_models = _audio_lines(segs, out, models, comp, raw, sr, fps, g, (sh_lo, sh_hi), cfg, rec, notes, cuts)
        for sid, lm in line_models.items():
            measured.pop(sid, None)              # the picture map's measurement says nothing about the offset here
            lag0[sid] = g + float(out[sid]["line"]["lag_ms"]) / 1000.0
            if sid not in played:                # a placeholder carrying a line
                s = next(x for x in segs if x.id == sid)
                played[sid] = (max(0, f2s(s.comp_in) + sh_hi), min(f2s(s.comp_out) + sh_lo, comp.size))
                ranges[sid] = (max(0, f2s(s.comp_in)), min(f2s(s.comp_out), comp.size))

    # ---- rebuilt track + residual -> added audio -------------------------------------------------
    rebuilt = np.zeros(n_total, np.float32)
    observable = np.zeros(n_total, bool)
    for s in segs:
        mdl = line_models.get(s.id, models.get(s.id))
        if mdl is None or s.id not in played:
            continue
        a, b = played[s.id]
        b = min(b, n_total)
        if b <= a or out[s.id]["pitch_preserved"]:
            continue       # a pitch-preserving stretch is RAW audio the tape rebuild cannot explain
        gain = 0.0
        rb = mdl.render(raw, sr, a, b, lag0.get(s.id, g))
        if status != "audio_replaced":
            den = float(np.dot(rb.astype(np.float64), rb))
            gain = float(np.clip(np.dot(comp[a:b].astype(np.float64), rb) / den, 0.0, 4.0)) if den > 0 else 0.0
        rebuilt[a:b] += gain * rb
        observable[a:b] = True
    if status == "audio_replaced":
        observable[:] = True
        rebuilt[:] = 0.0
    added = _added_audio(comp[:n_total], rebuilt, observable, sr, fps, n_frames, thr_db, dlog)
    for ad in added:
        rel = f"{ad['level_db']:+.1f} dB re original, " if ad["level_db"] is not None else ""
        notes.append(f"added {ad['type']} comp frames {ad['comp_in']}-{ad['comp_out'] - 1} "
                     f"({rel}{ad['level_dbfs']:.1f} dBFS)")

    # ---- exceptions ------------------------------------------------------------------------------
    for s in segs:
        o = out[s.id]
        if o["line"] is not None:
            o["exception"] = None            # its audio follows a verified audio line (FX-14)
            rec("segment", seg=s.id, range_samples=list(ranges.get(s.id, (0, 0))), lag_ms=o["lag_ms"], corr=o["corr"],
                pitch_preserved=o["pitch_preserved"], in_offset=o["in_offset_frames"],
                out_offset=o["out_offset_frames"], exception=None, line=o["line"])
            continue
        if s.type != "raw":
            continue
        if s.id not in models:
            o["exception"] = None
            continue
        a, b = ranges.get(s.id, (0, 0))
        dur = (b - a) / sr
        corr, lag = o["corr"], o["lag_ms"]
        good = corr is not None and corr >= min_corr and lag is not None and abs(lag) <= tol_ms
        exc = None
        if s.id in silent:
            s0, s1 = s.comp_in, s.comp_out
            over = any(ad["comp_in"] < s1 and ad["comp_out"] > s0 for ad in added)
            exc = "no_audio" if silent[s.id] == "competitor" else ("music_dominated" if over else "audio_replaced")
        elif status == "audio_replaced":
            exc = "audio_replaced"
        elif o["pitch_preserved"]:
            exc = "pitch_preserved"
        elif not good:
            if dur < _MIN_SEG_S:
                exc = "too_short"
            elif corr is not None and corr < min_corr:
                s0, s1 = s.comp_in, s.comp_out
                over = any(ad["comp_in"] < s1 and ad["comp_out"] > s0 for ad in added)
                exc = "music_dominated" if over else "audio_replaced"
        o["exception"] = exc
        rec("segment", seg=s.id, range_samples=[a, b], lag_ms=lag, corr=corr,
            pitch_preserved=o["pitch_preserved"], in_offset=o["in_offset_frames"],
            out_offset=o["out_offset_frames"], exception=exc)
        if exc in ("audio_replaced", "music_dominated") and status != "audio_replaced":
            notes.append(f"S{s.id:02d}: competitor audio does not follow RAW ({exc}, corr {corr})")
        if o["pitch_preserved"]:
            notes.append(f"S{s.id:02d}: pitch preserved at speed {s.speed:.3f} (AE's stretch changes pitch)")
    jl = [c for c in cuts if c["offset_frames"]]
    rec("summary", status=status, added_audio=added, cuts=len(cuts), jl=jl, av_offset_ms=round(g * 1000.0, 3),
        switch_baseline=switch_info, lines=sorted({o["line"]["id"] for o in out.values() if o["line"]}))
    return {"segments": out, "added_audio": added, "status": status, "notes": notes, "cuts": jl,
            "_av_offset_s": g, "_switch_baseline": switch_info, "_measured": measured}


def _audio_lines(segs: Sequence[Segment], out: dict, models: dict, comp: np.ndarray, raw: np.ndarray, sr: int,
                 fps: Fraction, g: float, shifts: tuple[int, int], cfg: Any, rec: Callable, notes: list[str],
                 cuts: list[dict]) -> dict[int, _Model]:
    """FX-14: continuous audio across video-only retimes, uncertain segments and placeholders.

    A REGION is a maximal run of adjacent pieces whose own picture map does not explain their audio (corr <
    verify_audio_strong_corr or a residual beyond audio_lag_tol_ms) and that are a placeholder, a retimed segment
    (remap / freeze / speed != 1 / frame blend), an uncertain segment or a piece shorter than _MIN_SEG_S. Candidate
    lines (picture-synced RAW time r(t) = r0 + v (t - t0), played at the run's offset g like any segment):
      * the confidently explained segment just before the region, its map extended forward;
      * the one just after it, extended backward;
      * a retimed piece's own picture in-point at speed 1 (a video-only slow motion / hold over audio that keeps
        playing), its in-point corrected by the measured residual.
    Each piece is verified on its own surely-played window with a CONFIDENT peak (>= verify_audio_strong_corr,
    never audio_replaced_corr): a neighbour's line, which must continue without a jump, is a hypothesis test -- its
    best alignment within ±audio_lag_tol_ms must reach strong and beat every other alignment up to the residual
    search; an own in-point (no anchor) must be the unique peak of the search (by audio_peak_unique_margin over the
    best sidelobe). A line runs from its anchor over consecutive verified pieces; a piece too short to measure is
    bridged only between two verified pieces of the same line. A real NOT-IN-RAW insert (foreign audio) verifies
    no line and stays silent. Sets
    out[id]['line'] = {id (comp frame where the line is anchored), raw_in_seconds (the line's picture-synced RAW
    time at the piece's comp_in), speed, source, lag_ms, corr} and the piece's lag_ms / corr; a J/L offset at a cut
    the line makes seamless is removed. Returns {segment id: line model}."""
    strong = float(_cfg(cfg, "verify_audio_strong_corr", 0.8))
    tol_ms = float(_cfg(cfg, "audio_lag_tol_ms", 10.0))
    res_s = float(_cfg(cfg, "audio_residual_search_s", _MAX_LAG_SEG_S))
    unique = float(_cfg(cfg, "audio_peak_unique_margin", 0.1))
    sh_lo, sh_hi = shifts

    def f2s(k: int) -> int:
        return int(round(Fraction(int(k)) * sr / fps))

    def window(s: Segment) -> tuple[int, int]:
        return max(0, f2s(s.comp_in) + sh_hi), min(f2s(s.comp_out) + sh_lo, comp.size)

    def explained(s: Segment) -> bool:
        o, m = out[s.id], models.get(s.id)
        return (m is not None and m.kind == "stretch" and m.v > 0 and o["corr"] is not None and o["corr"] >= strong
                and o["lag_ms"] is not None and abs(float(o["lag_ms"])) <= tol_ms and not o["pitch_preserved"])

    def retimed(s: Segment) -> bool:
        return s.type == "raw" and (s.time_mode == "remap" or bool(s.time_remap_keys) or s.speed is None
                                    or abs(float(s.speed) - 1.0) > 1e-6 or (s.retime or "none") != "none")

    def candidate(s: Segment) -> bool:
        if s.type in ("not_in_raw", "uncertain"):      # no verified RAW picture: the audio may still follow a line
            return True
        if s.type != "raw" or explained(s) or out[s.id]["pitch_preserved"] or _crossfade_frames(s) != (0, 0):
            return False
        return retimed(s) or bool(s.uncertain) or f2s(s.comp_out) - f2s(s.comp_in) < int(_MIN_SEG_S * sr)

    def verify(s: Segment, lm: _Model, lim_ms: float) -> tuple[bool | None, float, float, float]:
        """(ok | None when too short to measure, residual lag s, peak, sidelobe) of piece s against line lm."""
        w0, w1 = window(s)
        if w1 - w0 < int(0.1 * sr):
            return None, 0.0, 0.0, -1.0
        # a neighbour's line (lim = the lag tolerance) is a HYPOTHESIS test with continuity evidence: its alignment
        # (within ±tol) must correlate >= strong and be the best alignment up to ±res_s -- periodic content keeps
        # sidelobes close on short windows (film24's 0.37 s freeze: 0.913 at 0.0 ms vs 0.869 at +67 ms), so no
        # margin is asked of it; an own in-point (lim = the whole search, no anchor) must be the UNIQUE peak of the
        # search by audio_peak_unique_margin
        if lim_ms < res_s * 1000.0:
            lag, pk, sl = xcorr_lag_side(comp[w0:w1], lm.render(raw, sr, w0, w1, g), sr, res_s, inner_s=lim_ms / 1000.0)
            return bool(pk >= strong and pk > sl and abs(lag) * 1000.0 <= lim_ms), lag, pk, sl
        lag, pk, sl = xcorr_lag_side(comp[w0:w1], lm.render(raw, sr, w0, w1, g), sr, res_s)
        return bool(pk >= strong and pk - sl >= unique), lag, pk, sl

    def line_of(s: Segment) -> _Model:
        m = models[s.id]
        return _Model(s, "stretch", m.raw_in, m.v, m.t_in)

    assigned: dict[int, tuple[_Model, int, str, float, float]] = {}   # id -> (line, line id, source, lag, peak)
    trials: list[dict] = []

    def run(pieces: list[Segment], lm: _Model, lid: int, src: str, lim_ms: float) -> None:
        """Assign line lm to consecutive pieces while they verify; short pieces only as bridges."""
        pending: list[Segment] = []
        for s in pieces:
            if s.id in assigned:
                break
            ok, lag, pk, sl = verify(s, lm, lim_ms)
            trials.append({"seg": s.id, "line": lid, "source": src, "ok": ok, "lag_ms": round(lag * 1000.0, 3),
                           "corr": round(pk, 4), "sidelobe": round(sl, 4)})
            if ok is None:
                pending.append(s)
                continue
            if not ok:
                break
            for q in pending:                    # bridged: a verified piece of the same line on both sides
                assigned[q.id] = (lm, lid, src + " (bridged)", lag, pk)
            pending = []
            assigned[s.id] = (lm, lid, src, lag, pk)

    i = 0
    n = len(segs)
    while i < n:
        if not candidate(segs[i]):
            i += 1
            continue
        j = i
        while j + 1 < n and candidate(segs[j + 1]) and segs[j + 1].comp_in == segs[j].comp_out:
            j += 1
        region = list(segs[i:j + 1])
        prev = segs[i - 1] if i > 0 and segs[i - 1].comp_out == region[0].comp_in and explained(segs[i - 1]) else None
        nxt = segs[j + 1] if j + 1 < n and segs[j + 1].comp_in == region[-1].comp_out and explained(segs[j + 1]) else None
        if prev is not None:
            run(region, line_of(prev), int(prev.comp_in), f"S{prev.id:02d} continued", tol_ms)
        if nxt is not None:
            run(list(reversed(region)), line_of(nxt), int(nxt.comp_in), f"S{nxt.id:02d} continued back", tol_ms)
        for k, s in enumerate(region):
            if s.id in assigned or not retimed(s) or s.id not in models:
                continue
            t_in = float(Fraction(int(s.comp_in)) / fps)
            own = _Model(s, "stretch", float(models[s.id].raw_seconds(np.array([t_in]))[0]), 1.0, t_in)
            ok, lag, pk, sl = verify(s, own, res_s * 1000.0)
            trials.append({"seg": s.id, "line": int(s.comp_in), "source": "own in-point at speed 1", "ok": ok,
                           "lag_ms": round(lag * 1000.0, 3), "corr": round(pk, 4), "sidelobe": round(sl, 4)})
            if not ok:
                continue
            # the audio's own in-point: the picture's corrected by the measured residual (rendered at g + lag)
            own = _Model(s, "stretch", own.raw_in + lag, 1.0, t_in)
            run(region[k:], own, int(s.comp_in), "own in-point at speed 1", tol_ms)
        i = j + 1

    lines: dict[int, _Model] = {}
    seg_by_id = {s.id: s for s in segs}
    for sid, (lm, lid, src, lag, pk) in sorted(assigned.items()):
        s = seg_by_id[sid]
        r_in = float(lm.raw_seconds(np.array([float(Fraction(int(s.comp_in)) / fps)]))[0])
        out[sid]["line"] = {"id": int(lid), "raw_in_seconds": round(r_in, 9), "speed": float(lm.v), "source": src,
                            "lag_ms": round(lag * 1000.0, 3), "corr": round(pk, 4)}
        out[sid]["lag_ms"], out[sid]["corr"] = round(lag * 1000.0, 3), round(pk, 4)
        out[sid]["in_offset_frames"] = out[sid]["out_offset_frames"] = 0
        lines[sid] = lm
    # a J/L offset at a cut next to a line piece is removed: inside one line the anchor's audio simply
    # continues; at another cut the switch was measured with the piece's PICTURE model, which does not carry its
    # audio (both sides keep their ranges, never two audio layers over the same frames)
    for c in cuts:
        a_line, b_line = out[c["a"]]["line"], out[c["b"]]["line"]
        if c["offset_frames"] and (a_line is not None or b_line is not None):
            same = (a_line or {}).get("id", int(seg_by_id[c["a"]].comp_in)) == (b_line or {}).get(
                "id", int(seg_by_id[c["b"]].comp_in))
            out[c["a"]]["out_offset_frames"] = out[c["b"]]["in_offset_frames"] = 0
            c["offset_frames"] = 0
            c["not_exported"] = ("inside one audio line" if same else
                                 "next to a piece whose audio follows an audio line, not its picture map")
            head = f"comp frame {c['cut']} (S{c['a']:02d}|S{c['b']:02d})"
            notes[:] = [x for x in notes if not (x[1:].startswith(f"-cut at {head}:") and x[0] in "JL")]
            notes.append(f"audio switch at {head} not exported as a J/L cut: {c['not_exported']}")
            rec("jl_not_exported", **c)
    runs: dict[int, list[int]] = {}
    for sid in sorted(lines, key=lambda x: seg_by_id[x].comp_in):
        runs.setdefault(out[sid]["line"]["id"], []).append(sid)
    for lid, ids in runs.items():
        s0, s1 = seg_by_id[ids[0]], seg_by_id[ids[-1]]
        src = out[ids[0]]["line"]["source"]
        notes.append(f"continuous audio line ({src}) under comp frames {s0.comp_in}-{s1.comp_out - 1} "
                     f"({', '.join(f'S{i:02d}' for i in ids)}): one audio layer on that line instead of silence")
    rec("audio_lines", lines={str(k): v for k, v in runs.items()}, trials=trials)
    return lines


def _added_audio(comp: np.ndarray, rebuilt: np.ndarray, observable: np.ndarray, sr: int, fps: Fraction,
                 n_frames: int, thr_db: float, dlog: DecisionLog) -> list[dict]:
    """Runs where the competitor carries audio the RAW-rebuilt track does not explain."""
    from scipy.ndimage import median_filter
    fr = int(round(_ADDED_FRAME_S * sr))
    n = min(comp.size, rebuilt.size) // fr
    if n < 3:
        return []
    c = comp[:n * fr].astype(np.float64)
    m = rebuilt[:n * fr].astype(np.float64)
    r = c - m
    E = lambda x: np.sum(x.reshape(n, fr) ** 2, 1) / fr   # noqa: E731
    ec, em, er = E(c), E(m), E(r)
    obs = observable[:n * fr].reshape(n, fr).all(1)
    abs_floor = 10 ** (-60 / 10)
    flag = (er > np.maximum(em * 10 ** (thr_db / 10), abs_floor)) & obs
    k = max(3, int(round(0.5 / _ADDED_FRAME_S)) | 1)
    sm = median_filter(flag.astype(np.uint8), size=k, mode="nearest").astype(bool) & obs
    runs: list[list[int]] = []
    j = 0
    while j < n:
        if sm[j]:
            e = j
            while e < n and sm[e]:
                e += 1
            runs.append([j, e])
            j = e
        else:
            j += 1
    runs = [ru for ru in runs if (ru[1] - ru[0]) * _ADDED_FRAME_S >= 0.2]
    # merge runs separated by unobservable frames or short gaps (< 0.5 s) FIRST, then classify each merged run on
    # the residual of its observable frames: a music bed under a dynamic original only crosses the threshold where
    # the original is quiet, so its pieces are short and each one alone looks like an effect (film24's -12 dB bed
    # was 'sfx' 0-532: many < 1.5 s pieces merged after being typed one by one)
    merged: list[list] = []
    for a, b in runs:
        if merged:
            ga, gb = merged[-1][1], a
            gap_obs = obs[ga:gb]
            if (gb - ga) * _ADDED_FRAME_S < 0.5 or not gap_obs.any() or gap_obs.mean() < 0.2:
                merged[-1][1] = b
                continue
        merged.append([a, b])
    for ru in merged:
        a, b = ru
        keep = np.repeat(obs[a:b], fr)
        ru.append(_classify_added(r[a * fr:b * fr][keep], sr, fr))
    out = []
    frames_per = sr / float(fps)
    edge = int(round(0.5 * float(fps)))
    for a, b, t in merged:
        k0 = int(math.floor(a * fr / frames_per))
        k1 = int(math.ceil(b * fr / frames_per))
        if n_frames:
            if k0 <= edge:
                k0 = 0
            if k1 >= n_frames - edge:
                k1 = n_frames
            k1 = min(k1, n_frames)
        sel = obs[a:b] & ~np.isnan(er[a:b])
        e_res = float(er[a:b][sel].mean()) if sel.any() else float(er[a:b].mean())
        e_org = float(em[a:b][sel].mean()) if sel.any() else 0.0
        level_rel = 10 * math.log10(e_res / e_org) if e_org > 1e-12 else float("inf")
        level_fs = 10 * math.log10(max(e_res * 2.0, 1e-12))    # dBFS re a full-scale sine
        out.append({"type": t, "comp_in": int(k0), "comp_out": int(k1),
                    "level_db": round(level_rel, 1) if math.isfinite(level_rel) else None,
                    "level_dbfs": round(level_fs, 1)})
        dlog.record("audio_segments", "added_audio", type=t, comp_in=int(k0), comp_out=int(k1),
                    level_db=out[-1]["level_db"], level_dbfs=out[-1]["level_dbfs"],
                    frames_flagged=int(b - a), threshold_db=thr_db)
    return out


# =============================================================================================
# Global A/V offset of the competitor (DESIGN §7 D9)
# =============================================================================================
#
# One sign convention everywhere: the offset g is a LAG in xcorr_lag's convention between the
# competitor's audio and the recreation that keeps RAW's own A/V sync (picture-synced RAW audio):
# rebuilt(t) ~ competitor(t - g). g < 0 = the competitor's audio plays LATER than its picture (relative to
# RAW's own sync); per-segment lags are residuals (measured lag - g). A speed-1 segment whose picture fixes
# its raw_in to the floor interval [lo, hi] and whose audio implies the in-point x_a = raw_in + lag
# constrains g to [x_a - hi, x_a - lo]; the run's offset is the point covered by the most segment weight.

def stab_intervals(lo: Sequence[float], hi: Sequence[float], w: Sequence[float]) -> dict:
    """Weighted maximum coverage ('interval stabbing') of closed intervals [lo_i, hi_i] with weights w_i.

    Returns {'best': the largest covered weight, 'total': sum of weights, 'set': [a, b] (the widest
    maximal run of points covered by 'best'; the lowest one on ties) or None when there is no interval}."""
    lo, hi, w = (np.asarray(x, np.float64).reshape(-1) for x in (lo, hi, w))
    keep = np.isfinite(lo) & np.isfinite(hi) & np.isfinite(w) & (hi >= lo) & (w > 0)
    lo, hi, w = lo[keep], hi[keep], w[keep]
    total = float(w.sum())
    if lo.size == 0:
        return {"best": 0.0, "total": total, "set": None}
    # events: an interval opens before one closes at the same coordinate (closed intervals)
    ev = sorted([(float(a), 0, float(x)) for a, x in zip(lo, w)] + [(float(b), 1, float(x)) for b, x in zip(hi, w)])
    tol = 1e-9 * max(1.0, total)
    cov, best, start, runs = 0.0, 0.0, None, []
    for x, kind, wt in ev:
        if kind == 0:
            cov += wt
            if cov > best + tol:
                best, runs, start = cov, [], x
            elif abs(cov - best) <= tol:
                start = x
        else:
            if start is not None and abs(cov - best) <= tol:
                runs.append((start, x))
            start = None
            cov -= wt
    a, b = max(runs, key=lambda r: (r[1] - r[0], -r[0]))
    return {"best": best, "total": total, "set": [a, b]}


def coverage_at(x: float, lo: Sequence[float], hi: Sequence[float], w: Sequence[float]) -> float:
    """Weight of the intervals [lo_i, hi_i] that contain x."""
    lo, hi, w = (np.asarray(v, np.float64).reshape(-1) for v in (lo, hi, w))
    return float(w[(lo <= x) & (x <= hi)].sum())


def _stab_centre(st: dict, lo: np.ndarray, hi: np.ndarray, w: np.ndarray, zero_frac: float) -> float:
    """Centre of the max-coverage set, or 0 exactly when 0 is in it or covers >= zero_frac of the best."""
    a, b = st["set"]
    if a <= 0.0 <= b or coverage_at(0.0, lo, hi, w) >= zero_frac * st["best"]:
        return 0.0
    return 0.5 * (a + b)


def solve_av_offset(lo: Sequence[float], hi: Sequence[float], w: Sequence[float], cfg: Any,
                    audio_s: float | None = None) -> dict:
    """The run's A/V offset (seconds, xcorr convention) from per-segment offset intervals [lo_i, hi_i].

    g = 0 EXACTLY when 0 is in the max-coverage set or covers >= cfg.av_offset_zero_frac of the best
    coverage (a zero-offset input behaves exactly as without the offset model). Otherwise g = the centre
    of the max-coverage set, accepted when there are >= av_offset_min_segments segments and >=
    av_offset_min_audio_s of audio, the offset explains >= av_offset_min_coverage of the weight, no single
    segment decides where it is (every leave-one-out max-coverage set lies within av_offset_max_spread_ms of
    the published set; a segment may only narrow it) and av_offset_min_ms <= |g| <= av_offset_max_s; else
    0 and today's per-segment behaviour. Returns {'status': 'measured' | 'zero' | 'not_measured', 'lag_s',
    'interval_s' (the max-coverage set), 'centre_s', 'n', 'coverage', 'coverage_zero', 'spread_ms' (spread
    of the leave-one-out centres), 'loo_distance_ms', 'audio_s', 'reason'}."""
    lo, hi, w = (np.asarray(v, np.float64).reshape(-1) for v in (lo, hi, w))
    n = int(lo.size)
    zf = float(_cfg(cfg, "av_offset_zero_frac", 0.9))
    out: dict[str, Any] = {"status": "not_measured", "lag_s": 0.0, "interval_s": None, "centre_s": None, "n": n,
                           "coverage": None, "coverage_zero": None, "spread_ms": None, "loo_distance_ms": None,
                           "audio_s": None if audio_s is None else round(float(audio_s), 3), "reason": ""}
    st = stab_intervals(lo, hi, w)
    if st["set"] is None or st["best"] <= 0:
        out["reason"] = "no segment constrains the offset"
        return out
    a, b = st["set"]
    c = _stab_centre(st, lo, hi, w, zf)
    out.update(interval_s=[a, b], centre_s=0.5 * (a + b), coverage=st["best"] / st["total"],
               coverage_zero=coverage_at(0.0, lo, hi, w) / st["total"])
    if c == 0.0:
        out.update(status="zero", reason="an offset of 0 explains the segments")
        return out
    loo, dist = [], 0.0
    for i in range(n):
        m = np.ones(n, bool)
        m[i] = False
        s_i = stab_intervals(lo[m], hi[m], w[m])
        if s_i["set"] is None:
            continue
        c_i = _stab_centre(s_i, lo[m], hi[m], w[m], zf)
        loo.append(c_i)
        a_i, b_i = (0.0, 0.0) if c_i == 0.0 else s_i["set"]
        dist = max(dist, a_i - b, a - b_i, 0.0)            # gap between that set and the published one
    spread = (max(loo) - min(loo)) * 1000.0 if loo else 0.0
    out.update(spread_ms=spread, loo_distance_ms=dist * 1000.0)
    why = []
    if n < int(_cfg(cfg, "av_offset_min_segments", 3)):
        why.append(f"{n} segment(s)")
    if audio_s is not None and audio_s < float(_cfg(cfg, "av_offset_min_audio_s", 2.0)):
        why.append(f"{audio_s:.2f} s of audio")
    if out["coverage"] < float(_cfg(cfg, "av_offset_min_coverage", 0.7)):
        why.append(f"coverage {out['coverage']:.0%}")
    if dist * 1000.0 > float(_cfg(cfg, "av_offset_max_spread_ms", 2.0)):
        why.append(f"one segment moves the offset by {dist * 1000.0:.2f} ms")
    if abs(c) * 1000.0 < float(_cfg(cfg, "av_offset_min_ms", 2.0)):
        why.append(f"|offset| {abs(c) * 1000.0:.2f} ms below the minimum")
    if abs(c) > float(_cfg(cfg, "av_offset_max_s", 1.0)):
        why.append(f"|offset| {abs(c) * 1000.0:.1f} ms above the maximum")
    if why:
        out["reason"] = "not accepted: " + ", ".join(why)
        return out
    out.update(status="measured", lag_s=c, reason="accepted")
    return out


def av_offset_text(lag_ms: float | None) -> str:
    """The offset in plain words (one sign convention: lag < 0 = the competitor's audio is LATE)."""
    if lag_ms is None or not math.isfinite(float(lag_ms)) or abs(float(lag_ms)) < 1e-9:
        return "competitor audio is in sync with its picture, relative to RAW's own A/V sync"
    rel = "later" if float(lag_ms) < 0 else "earlier"
    return f"competitor audio is {abs(float(lag_ms)):.1f} ms {rel} than its picture, relative to RAW's own A/V sync"


def _stretch_seg(s: Segment) -> bool:
    """A forward stretch segment with a feasible raw_in interval (can constrain the offset)."""
    return (s.type == "raw" and s.time_mode != "remap" and not s.time_remap_keys and s.speed is not None
            and float(s.speed) > 0 and s.raw_in_seconds is not None and bool(s.raw_in_interval))


def _stretch_v1(s: Segment) -> bool:
    return _stretch_seg(s) and abs(float(s.speed) - 1.0) <= 1e-6


def av_offset_prior(hints: Any, segments: Sequence[Segment], comp_fps: Any, cfg: Any,
                    dlog: DecisionLog | None = None) -> dict:
    """Search centre of the first per-segment audio pass, from the S5.1 coarse windows (DESIGN §7 D9).

    Every confident speed-1 window with a waveform NCC >= cfg.av_offset_prior_wave_peak lying inside one
    speed-1 stretch segment gives the raw_in its audio implies, d = audio RAW time - picture RAW time, and
    an offset interval against the segment's floor interval. Accepted with >= av_offset_prior_min_windows
    windows whose d agree within av_offset_prior_max_mad_ms (MAD; the median ignores repeated-music
    outliers); the prior is then the stabbing solution of the window intervals (0 exactly when 0 explains
    them, as for the precise estimate; their median when no point covers av_offset_min_coverage of them),
    else 0. Returns {'lag_s', 'accepted', 'n_windows', 'median_ms', 'mad_ms', 'interval_ms', 'reason'}."""
    dlog = dlog or null_dlog()
    fps = parse_fps(comp_fps)
    out: dict[str, Any] = {"lag_s": 0.0, "accepted": False, "n_windows": 0, "median_ms": None, "mad_ms": None,
                           "interval_ms": None, "reason": ""}
    n_h = len(getattr(hints, "comp_t", [])) if hints is not None else 0
    wp = getattr(hints, "wave_peak", None) if n_h else None
    d, lo, hi = [], [], []
    if n_h and wp is not None:
        thr = float(_cfg(cfg, "av_offset_prior_wave_peak", 0.8))
        eps = float(_cfg(cfg, "av_offset_eps_ms", 0.5)) / 1000.0
        half = 0.5 * float(getattr(hints, "window", 1.0))
        spans = [(float(Fraction(int(s.comp_in)) / fps), float(Fraction(int(s.comp_out)) / fps), s)
                 for s in sorted(segments, key=lambda s: (s.comp_in, s.id)) if _stretch_v1(s)]
        for t, r, v, pk in zip(np.asarray(hints.comp_t, np.float64), np.asarray(hints.raw_t, np.float64),
                               np.asarray(hints.speed, np.float64), np.asarray(wp, np.float64)):
            if not (np.isfinite(r) and np.isfinite(pk) and pk >= thr and abs(v - 1.0) <= 1e-6):
                continue
            for t0, t1, s in spans:
                if t0 <= t - half and t + half <= t1:
                    x_a = float(r - (t - t0))                       # the raw_in this window's audio implies
                    a, b = (float(x) for x in s.raw_in_interval)
                    d.append(x_a - float(s.raw_in_seconds))
                    lo.append(x_a - b - eps)
                    hi.append(x_a - a + eps)
                    break
    n = len(d)
    out["n_windows"] = n
    if n:
        med = float(np.median(d))
        out.update(median_ms=round(med * 1000.0, 3),
                   mad_ms=round(float(np.median(np.abs(np.asarray(d) - med))) * 1000.0, 3))
    if not n_h:
        out["reason"] = "no audio hints"
    elif n < int(_cfg(cfg, "av_offset_prior_min_windows", 8)):
        out["reason"] = f"{n} usable window(s)"
    elif out["mad_ms"] > float(_cfg(cfg, "av_offset_prior_max_mad_ms", 10.0)):
        out["reason"] = f"windows disagree (MAD {out['mad_ms']:.2f} ms)"
    else:
        ones = np.ones(n)
        st = stab_intervals(lo, hi, ones)
        g0 = _stab_centre(st, np.asarray(lo), np.asarray(hi), ones, float(_cfg(cfg, "av_offset_zero_frac", 0.9)))
        if g0 != 0.0 and st["best"] < float(_cfg(cfg, "av_offset_min_coverage", 0.7)) * n:
            g0 = float(np.median(d))
        if abs(g0) > float(_cfg(cfg, "av_offset_max_s", 1.0)):
            out["reason"] = f"|prior| {abs(g0) * 1000.0:.1f} ms above the maximum"
        else:
            out.update(lag_s=g0, accepted=True, reason="0 explains the windows" if g0 == 0.0 else "accepted",
                       interval_ms=[round(st["set"][0] * 1000.0, 3), round(st["set"][1] * 1000.0, 3)])
    dlog.record("audio_align", "av_offset_prior", lag_ms=round(out["lag_s"] * 1000.0, 3),
                **{k: v for k, v in out.items() if k != "lag_s"})
    return out


def av_offset_probe(segments: Sequence[Segment], comp_y: np.ndarray, raw_y: np.ndarray, sr: int, comp_fps: Any,
                    cfg: Any, dlog: DecisionLog | None = None) -> dict:
    """Fallback search centre when the S5.1 windows give no prior (too few long windows, DESIGN §7 D9): one
    wide lag search (±cfg.av_offset_max_s, at most half the range) per speed-1 stretch segment with >=
    av_offset_seg_min_s of core audio; the segments correlating >= verify_audio_strong_corr constrain the
    offset exactly like the precise estimate (``solve_av_offset``: 0 exactly when 0 explains them).
    Returns {'lag_s', 'accepted', 'source': 'probe', 'n_segments', 'interval_ms', 'reason'}."""
    with single_thread_blas():
        dlog = dlog or null_dlog()
        fps = parse_fps(comp_fps)
        sr = int(sr)
        comp, raw = _mono(comp_y), _mono(raw_y)
        out: dict[str, Any] = {"lag_s": 0.0, "accepted": False, "source": "probe", "n_segments": 0, "interval_ms": None,
                               "reason": ""}
        if comp.size == 0 or raw.size == 0:
            out["reason"] = "no audio"
            return out
        max_s = float(_cfg(cfg, "av_offset_max_s", 1.0))
        strong = float(_cfg(cfg, "verify_audio_strong_corr", 0.8))
        min_s = float(_cfg(cfg, "av_offset_seg_min_s", 0.5))
        eps = float(_cfg(cfg, "av_offset_eps_ms", 0.5)) / 1000.0
        lo, hi, w = [], [], []
        dur = 0.0
        for s in sorted(segments, key=lambda s: (s.comp_in, s.id)):
            m = _build_model(s, fps, sr) if _stretch_v1(s) else None
            if m is None:
                continue
            din, dout = _crossfade_frames(s)
            a = int(round(Fraction(int(s.comp_in + din)) * sr / fps))
            b = min(int(round(Fraction(int(s.comp_out - dout)) * sr / fps)), comp.size)
            if b - a < int(min_s * sr):
                continue
            lag, pk = xcorr_lag(comp[a:b], m.render(raw, sr, a, b), sr, max_s)
            if pk < strong:
                continue
            x_a = float(s.raw_in_seconds) + lag
            ia, ib = (float(x) for x in s.raw_in_interval)
            lo.append(x_a - ib - eps)
            hi.append(x_a - ia + eps)
            w.append((b - a) / sr * pk * pk)
            dur += (b - a) / sr
        sol = solve_av_offset(lo, hi, w, cfg, audio_s=dur)
        out.update(n_segments=sol["n"], reason=sol["reason"], accepted=sol["status"] in ("measured", "zero"),
                   lag_s=float(sol["lag_s"]),
                   interval_ms=None if sol["interval_s"] is None else [round(sol["interval_s"][0] * 1000.0, 3),
                                                                       round(sol["interval_s"][1] * 1000.0, 3)])
        dlog.record("audio_align", "av_offset_probe", lag_ms=round(out["lag_s"] * 1000.0, 3),
                    **{k: v for k, v in out.items() if k != "lag_s"})
        return out


def av_offset_estimate(segments: Sequence[Segment], audio_result: dict, cfg: Any, dlog: DecisionLog | None = None,
                       *, prior: dict | None = None) -> dict:
    """The run's A/V offset from a per-segment audio pass (DESIGN §7 D9): every forward stretch segment with
    corr >= cfg.verify_audio_strong_corr over >= av_offset_seg_min_s of audio (or >= av_offset_seg_short_s at
    corr >= av_offset_seg_short_corr) gives the offset interval [(x_a - hi) / v, (x_a - lo) / v] (x_a = raw_in
    + v x measured total lag, [lo, hi] = its floor raw_in interval; an offset in competitor time, exact for
    an offset of the finished mix -- one inside the source scales by 1/v, which max coverage tolerates while
    most weight is speed 1), widened by av_offset_eps_ms, weight = audio seconds x corr^2; ``solve_av_offset``
    decides. The lags are audio_result['_measured'] totals, so the
    pass's own search centre does not matter. Returns the published cutlist.audio.av_offset block:
    {'status', 'lag_ms', 'lag_ms_interval', 'centre_ms', 'n_segments', 'coverage', 'coverage_zero',
    'spread_ms', 'audio_s', 'reason', 'text', 'prior', 'segments'} plus 'lag_s' (seconds, not published)."""
    dlog = dlog or null_dlog()
    strong = float(_cfg(cfg, "verify_audio_strong_corr", 0.8))
    min_s = float(_cfg(cfg, "av_offset_seg_min_s", 0.5))
    short_s = float(_cfg(cfg, "av_offset_seg_short_s", 0.25))
    short_c = float(_cfg(cfg, "av_offset_seg_short_corr", 0.9))
    eps = float(_cfg(cfg, "av_offset_eps_ms", 0.5)) / 1000.0
    meas = (audio_result or {}).get("_measured") or {}
    lo, hi, w, ids, items = [], [], [], [], []
    dur = 0.0
    for s in sorted(segments, key=lambda s: (s.comp_in, s.id)):
        m = meas.get(s.id, meas.get(str(s.id)))
        if m is None or not _stretch_seg(s) or (s.audio or {}).get("line") or (s.audio or {}).get("exception") in (
                "not_in_raw", "no_audio", "pitch_preserved", "audio_replaced", "music_dominated"):
            continue                     # (a segment whose audio follows an audio line says nothing about it)
        corr, d_s = float(m["corr"]), float(m["dur_s"])
        if corr < strong or not (d_s >= min_s or (d_s >= short_s and corr >= short_c)):
            continue
        v = float(s.speed)
        x_a = float(s.raw_in_seconds) + v * float(m["lag_total_ms"]) / 1000.0
        a, b = (float(x) for x in s.raw_in_interval)
        lo.append((x_a - b) / v - eps)
        hi.append((x_a - a) / v + eps)
        w.append(d_s * corr * corr)
        ids.append(int(s.id))
        dur += d_s
        items.append({"seg": int(s.id), "speed": v, "lag_total_ms": float(m["lag_total_ms"]), "corr": corr,
                      "dur_s": round(d_s, 4),
                      "interval_ms": [round((x_a - b) / v * 1000.0, 3), round((x_a - a) / v * 1000.0, 3)]})
    sol = solve_av_offset(lo, hi, w, cfg, audio_s=dur)
    lag_ms = round(sol["lag_s"] * 1000.0, 3)
    iv = sol["interval_s"]
    pr = dict(prior or {})
    if "lag_s" in pr:
        pr["lag_ms"] = round(float(pr.pop("lag_s")) * 1000.0, 3)
    pub = {"status": sol["status"], "lag_ms": lag_ms,
           "lag_ms_interval": None if iv is None else [round(iv[0] * 1000.0, 3), round(iv[1] * 1000.0, 3)],
           "centre_ms": None if sol["centre_s"] is None else round(sol["centre_s"] * 1000.0, 3),
           "n_segments": sol["n"], "coverage": None if sol["coverage"] is None else round(sol["coverage"], 4),
           "coverage_zero": None if sol["coverage_zero"] is None else round(sol["coverage_zero"], 4),
           "spread_ms": None if sol["spread_ms"] is None else round(sol["spread_ms"], 3),
           "loo_distance_ms": None if sol["loo_distance_ms"] is None else round(sol["loo_distance_ms"], 3),
           "audio_s": sol["audio_s"], "reason": sol["reason"], "text": av_offset_text(lag_ms), "prior": pr,
           "segments": ids}
    dlog.record("audio_align", "av_offset", **pub, evidence={"segments": items})
    pub["lag_s"] = float(sol["lag_s"])
    return pub
