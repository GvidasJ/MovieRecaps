"""Stage 5.1 (coarse audio alignment) and Stage 5.6 (audio per segment) -- DESIGN.md §5 audio_align.py.

Plain numpy/scipy DSP (no librosa):

``features``
    Own STFT (Hann, n_fft = 64 ms) -> HTK log-mel filterbank, six octave-wide log-frequency bands and a
    spectral-flux onset envelope, all at ``cfg.audio_feat_rate`` Hz; frame ``i`` is centred on
    ``t = i / rate`` (same convention for competitor and RAW, so lags are unbiased).

``coarse_align``  (prompt 5.1)
    Every ~1 s competitor window (hop 0.25 s) is cross-correlated against the WHOLE RAW with an
    FFT-based, exactly normalised NCC (overlap-save blocks; the sliding RAW energy comes from
    cumulative sums) on a cheap *coarse* feature (onset envelope + octave-band energies -- both survive
    the pitch shift of a tape-style speed change). The top peaks (non-max suppression ±0.3 s) are
    verified on the *full* log-mel feature under two hypotheses: tape (the competitor's mel filters
    are warped by ``v`` so its band ``b`` sees the RAW band ``b`` content shifted up by ``v``) and
    pitch-preserving (unwarped). The winner is refined on the 16 kHz waveform within ±50 ms
    (normalised xcorr, parabolic sub-sample peak); for tape/1.0 windows the two window halves give a
    drift that refines the speed. Windows that are weak at speed 1.00 are re-searched with
    time-scaled windows (v = 0.90 .. 1.30, step 0.01): a decimated coarse scan shortlists speeds,
    then the full search runs at the shortlisted speeds; found speeds are propagated to neighbouring
    windows. ``conf`` = peak / second peak, where the second peak is the best verified candidate
    outside ±0.3 s or -- if larger -- the expected maximum of the full-feature NCC over the whole
    RAW estimated from random lags (mu + 4.5 sigma), so windows with no genuine match never look
    confident. ``psr`` = peak-to-sidelobe ratio of the coarse NCC curve.

``xcorr_lag``
    Normalised cross-correlation lag of two equally long signals (b delayed by lag vs a).

``analyze_segments_audio``  (prompt 5.6)
    Per segment: the RAW-rebuilt audio (tape-style resampling for v != 1, windowed-sinc) is compared
    with the competitor: J/L offsets at hard cuts (audio switch point from a two-model local NCC
    segmentation), lag/corr over the segment's audio range, pitch preservation for speed != 1
    (whitened log-frequency spectrum of the competitor vs the RAW source at shift log(v) vs 0),
    added audio (music bed / SFX / voice-over) from the residual energy after subtracting the
    lag-compensated, gain-fitted rebuilt track, and the run status ok / no_audio / audio_replaced.
"""
from __future__ import annotations

import math
from collections import OrderedDict
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
_NULL_FLOOR_MIN = 0.12
_WAVE_MIN_PEAK = 0.3          # waveform NCC needed to trust the sample-precise refinement
_SILENT_RMS = 10 ** (-70 / 20)
_KAISER_BETA = 8.0
_SINC_HALF = 16               # windowed-sinc half width (input samples at full bandwidth)
_SINC_PHASES = 1024
_TAPE, _TEMPO = "tape", "tempo"
_ADDED_FRAME_S = 0.02         # residual-energy frame for added-audio detection
_MAX_LAG_SEG_S = 0.1          # per-segment lag search (same as verify s9_5)
_MIN_SEG_S = 0.5              # shorter audio ranges -> 'too_short' when they do not line up


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
    ypad = np.zeros(len(y) + n_fft + 2, np.float32)
    ypad[half:half + len(y)] = y
    win = _hann(n_fft)
    norm = (2.0 / float(win.sum())) ** 2
    ar = np.arange(n_fft)
    for i0 in range(0, T, chunk):
        i1 = min(T, i0 + chunk)
        starts = np.round(np.arange(i0, i1) * (sr / rate)).astype(np.int64)
        fr = ypad[starts[:, None] + ar[None, :]] * win[None, :]
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
    y = np.asarray(y, np.float32).reshape(-1)
    return _spectral(y, int(sr), cfg, keep_power=False)


# ---------------------------------------------------------------------------------------------
# Windowed-sinc fractional resampling (tape-style speed changes, fractional delays)
# ---------------------------------------------------------------------------------------------

_SINC_TABLES: dict[tuple[float, int], tuple[np.ndarray, np.ndarray]] = {}


def _sinc_table(cutoff: float) -> tuple[np.ndarray, np.ndarray]:
    fc = float(min(1.0, max(0.05, cutoff)))
    key = (round(fc, 6), _SINC_HALF)
    tab = _SINC_TABLES.get(key)
    if tab is None:
        H = int(math.ceil(_SINC_HALF / fc))
        taps = np.arange(-H + 1, H + 1)
        ph = np.arange(_SINC_PHASES + 1) / _SINC_PHASES          # fractional position 0..1
        x = ph[:, None] - taps[None, :]                          # distance position - sample
        arg = np.clip(1.0 - (x / H) ** 2, 0.0, 1.0)
        win = np.i0(_KAISER_BETA * np.sqrt(arg)) / np.i0(_KAISER_BETA)
        k = fc * np.sinc(fc * x) * win
        tab = (k.astype(np.float32), taps)
        _SINC_TABLES[key] = tab
    return tab


def resample_at(y: np.ndarray, pos: np.ndarray, cutoff: float = 1.0, chunk: int = 1 << 15) -> np.ndarray:
    """Band-limited interpolation of ``y`` at fractional sample positions ``pos`` (Kaiser-windowed sinc,
    1024 tabulated phases; ``cutoff`` = fraction of Nyquist, use min(1, 1/step) when the positions
    advance by ``step`` > 1 per output sample so nothing aliases). Samples outside ``y`` are zero."""
    y = np.asarray(y, np.float32).reshape(-1)
    pos = np.asarray(pos, np.float64).reshape(-1)
    out = np.zeros(pos.size, np.float32)
    if y.size == 0 or pos.size == 0:
        return out
    tab, taps = _sinc_table(cutoff)
    H = int(-taps[0]) + 1
    ypad = np.zeros(y.size + 2 * H + 2, np.float32)
    ypad[H:H + y.size] = y
    for c0 in range(0, pos.size, chunk):
        p = pos[c0:c0 + chunk]
        ok = (p > -H) & (p < y.size + H - 1)
        if not np.any(ok):
            continue
        pp = np.where(ok, p, 0.0)
        base = np.floor(pp).astype(np.int64)
        ph = np.round((pp - base) * _SINC_PHASES).astype(np.int64)
        idx = base[:, None] + taps[None, :] + H
        np.clip(idx, 0, ypad.size - 1, out=idx)
        v = np.einsum("ij,ij->i", tab[ph], ypad[idx])
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
    from scipy.signal import fftconvolve
    c = np.asarray(chunk, np.float64)
    r = np.asarray(region, np.float64)
    m = c.size
    if m == 0 or r.size < m:
        return np.zeros(0)
    c = c - c.mean()
    nc = float(np.sqrt(np.sum(c * c)))
    num = fftconvolve(r, c[::-1], mode="valid")
    cs1 = np.concatenate([[0.0], np.cumsum(r)])
    cs2 = np.concatenate([[0.0], np.cumsum(r * r)])
    s1 = cs1[m:] - cs1[:-m]
    e = np.maximum((cs2[m:] - cs2[:-m]) - s1 * s1 / m, 0.0)
    den = nc * np.sqrt(e)
    tiny = 1e-9 * max(nc, 1e-12) * math.sqrt(m) * 1e-3
    return np.where(den > tiny, num / np.maximum(den, 1e-300), 0.0)


def xcorr_lag(a: np.ndarray, b: np.ndarray, sr: int, max_lag_s: float) -> tuple[float, float]:
    """Normalised cross-correlation lag between two (equally long) signals.

    Returns ``(lag_s, peak)``: ``b`` is delayed by ``lag_s`` seconds relative to ``a`` (``b(t) ≈ a(t -
    lag)``; positive = b late), searched within ``±max_lag_s`` (and at most half the length), with a
    parabolic sub-sample peak. ``peak`` is the normalised correlation in [-1, 1] at the best lag
    (energies of the overlapping parts). Silent / empty input -> ``(0.0, 0.0)``."""
    a = np.asarray(a, np.float64).reshape(-1)
    b = np.asarray(b, np.float64).reshape(-1)
    n = min(a.size, b.size)
    if n < 2:
        return 0.0, 0.0
    a = a[:n] - a[:n].mean()
    b = b[:n] - b[:n].mean()
    if float(np.sum(a * a)) <= 1e-18 or float(np.sum(b * b)) <= 1e-18:
        return 0.0, 0.0
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
    i = int(np.argmax(ncc))
    off = 0.0
    if 0 < i < ncc.size - 1:
        off = _parabolic(ncc[i - 1], ncc[i], ncc[i + 1])
    return float((lags[i] + off) / sr), float(np.clip(ncc[i], -1.0, 1.0))


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
        """NCC curves of several windows (lengths may differ, each <= m_max)."""
        import scipy.fft as sfft
        out: list[np.ndarray] = [np.zeros(0) for _ in windows]
        live = [i for i, w in enumerate(windows) if 2 <= w.shape[0] <= self.m_max and w.shape[0] <= self.T]
        if not live:
            return out
        mb = max(windows[i].shape[0] for i in live)
        W = np.zeros((len(live), mb, self.C))
        norms = np.zeros(len(live))
        for r, i in enumerate(live):
            w = np.asarray(windows[i], np.float64).reshape(windows[i].shape[0], -1)
            w = w - w.mean(0, keepdims=True)
            W[r, :w.shape[0]] = w
            norms[r] = math.sqrt(float(np.sum(w * w)))
        FW = np.conj(sfft.rfft(W, n=self.nfft, axis=1)).astype(np.complex64)     # [B, F, C]
        acc = np.matmul(FW.transpose(1, 0, 2), self.spec)                          # [F, B, K]
        num = sfft.irfft(acc.transpose(1, 2, 0), n=self.nfft, axis=2)[:, :, :self.step]
        num = num.reshape(len(live), -1)
        for r, i in enumerate(live):
            m = windows[i].shape[0]
            e = self.energy(m)
            den = norms[r] * np.sqrt(e)
            nm = num[r, :e.size]
            good = (e > 1e-6 * m) & (norms[r] > 1e-9)
            out[i] = np.where(good, nm / np.where(good, den, 1.0), 0.0)
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
        self._warp_cache: OrderedDict[float, tuple[np.ndarray, np.ndarray]] = OrderedDict()
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

    def _warped(self, v: float) -> tuple[np.ndarray, np.ndarray]:
        """Competitor (log-mel, octave-band) features with every filter warped by v (tape hypothesis),
        for the whole competitor; small LRU cache (the same speed is used by many windows)."""
        key = round(float(v), 6)
        hit = self._warp_cache.get(key)
        if hit is not None:
            self._warp_cache.move_to_end(key)
            return hit
        P = self.c["power"]
        fb = mel_filterbank(self.sr, self.n_fft, self.st["n_mels"], self.st["fmin"], self.st["fmax"], warp=key)
        bb = broad_filterbank(self.sr, self.n_fft, warp=key)
        val = (_log_db(P @ fb.T), _log_db(P @ bb.T))
        self._warp_cache[key] = val
        if len(self._warp_cache) > 8:
            self._warp_cache.popitem(last=False)
        return val

    def _positions(self, i0: int, v: float, extra: int = 0) -> np.ndarray:
        """Absolute (fractional) competitor frame positions of the RAW-grid frames -extra .. m-1 of a
        window starting at comp frame i0 matched at speed v (m = round(n v))."""
        m = int(round(self.n * v))
        return i0 + np.arange(-extra, m) / v

    def _interp_rows(self, F: np.ndarray, q: np.ndarray) -> np.ndarray:
        """Rows of the [Tc, k] competitor feature F linearly interpolated at absolute positions q."""
        q = np.clip(q, 0.0, F.shape[0] - 1.0)
        j0 = np.floor(q).astype(np.int64)
        j1 = np.minimum(j0 + 1, F.shape[0] - 1)
        fr = (q - j0).astype(np.float32)[:, None]
        return F[j0] * (1.0 - fr) + F[j1] * fr

    def comp_coarse(self, i0: int, v: float) -> np.ndarray:
        """Competitor coarse window starting at comp frame i0, time-scaled onto RAW frames at speed v
        (octave bands tape-warped by v; the onset envelope is pitch-invariant)."""
        q = self._positions(i0, v)
        br = self.c["broad"] if abs(v - 1.0) < 1e-12 else self._warped(v)[1]
        on = self._interp_rows(self.c["onset"][:, None], q)[:, 0]
        return self._coarse_from(on, self._interp_rows(br, q), self.c_stats)

    def comp_full(self, i0: int, v: float, hyp: str) -> np.ndarray:
        """Competitor delta-log-mel window at speed v on the RAW frame grid (tape: mel filters warped by
        v; tempo: unwarped). Deltas are taken AFTER time-scaling, like the RAW's."""
        L = self._warped(v)[0] if (hyp == _TAPE and abs(v - 1.0) > 1e-12) else self.c["logmel"]
        return np.diff(self._interp_rows(L, self._positions(i0, v, extra=1)), axis=0).astype(np.float32)

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
        return resample_at(self.comp_y, a + np.arange(m) / v, cutoff=min(1.0, v))

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

    def _line_fit(self, c0: float, v: float, r0: float, search_s: float) -> tuple[float, float] | None:
        """Lag line lag(t) = a + b t of the window (tape-resampled at speed v) against the RAW around
        start r0: waveform lags of the 4 window quarters (±search_s, NCC >= _WAVE_MIN_PEAK) and a
        robust line through them (>= 3 within 0.6 ms). Returns (a seconds, b) or None. Captures speed
        errors up to ~0.3 % (beyond that the resampled quarters are pitch-mismatched and decorrelate)."""
        sr = self.sr
        m = int(math.floor(self.W * v * sr))
        if m < 4 * 400:
            return None
        chunk = self._chunk(c0, v, m)
        h = m // 4
        pts: list[tuple[float, float]] = []
        for q in range(4):
            al = self._align(chunk[q * h:(q + 1) * h], r0 * sr + q * h, search_s)
            if al is not None:
                pts.append(((q * h + h / 2.0) / sr, al[0] / sr))
        return _robust_line(pts, tol=0.0006, max_slope=0.03, min_inliers=3)

    def refine(self, wi: int, res: _Res) -> _Res:
        """Sample-precise RAW time (and speed) on the 16 kHz waveform.

        1. quarter-lag line fit (tape-style resampling of the window) at the found speed and, if it
           does not converge, at speeds ±0.003·k (k <= 4: covers the ±0.01 grid uncertainty): gives
           the start offset a and the speed v (1 + b) to ~1e-4;
        2. polish with a second line fit and a whole-window NCC (±3 ms, parabolic sub-sample peak).
        Without a line fit, a whole-window NCC within ±audio_refine_ms at the found speed; when the
        waveform does not follow at all (pitch-preserving stretch, heavy added audio) the
        feature-level estimate is kept. Sets res.raw_t / res.speed / res.wave_peak."""
        sr = self.sr
        c0 = self.starts_s[wi]
        v0 = res.v
        r0 = res.lag / self.rate
        v = v0
        wave_peak = float("nan")
        search = max(self.refine_s, 0.03)
        fit = None
        for k in (0, -1, 1, -2, 2, -3, 3, -4, 4):
            vk = round(v0 + 0.003 * k, 6)
            if vk <= 0.5 * self.vmin or (k and abs(vk - 1.0) < 1e-9 and abs(v0 - 1.0) > 0.02):
                continue
            f = self._line_fit(c0, vk, r0, search)
            if f is not None:
                fit = (vk, f)
                break
        if fit is not None:
            vk, (a, b) = fit
            r0 += a
            v = vk * (1.0 + b)
            f2 = self._line_fit(c0, v, r0, 0.003)
            if f2 is not None and abs(f2[1]) < 0.003:
                r0 += f2[0]
                v = v * (1.0 + f2[1])
        m = int(math.floor(self.W * v * sr))
        al = self._align(self._chunk(c0, v, m), r0 * sr, 0.003 if fit is not None else self.refine_s) if m > 64 else None
        if al is not None:
            r0 += al[0] / sr
            wave_peak = al[1]
        speed = v
        if abs(v0 - 1.0) < 1e-12 and abs(speed - 1.0) <= 0.003:
            speed = 1.0
        res.raw_t = float(r0 + v * self.W / 2.0)
        res.speed = float(speed)
        res.wave_peak = wave_peak
        return res


def _robust_line(pts: list[tuple[float, float]], tol: float, max_slope: float,
                 min_inliers: int = 3) -> tuple[float, float] | None:
    """Least-squares line y = a + b x through the largest consistent subset (>= min_inliers points
    within tol) of a handful of points (all pairs tried); None if no such subset."""
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
            inl = np.abs(y - (a + b * x)) <= tol
            key = (int(inl.sum()), -float(np.abs(y - (a + b * x))[inl].sum()))
            if best is None or key > best[0]:
                best = (key, inl)
    if best is None or best[0][0] < min_inliers:
        return None
    inl = best[1]
    b, a = np.polyfit(x[inl], y[inl], 1)
    if abs(b) > max_slope:
        return None
    return float(a), float(b)


def _null_floor(mu: float, sd: float, mx: float) -> float:
    """Expected maximum of the full-feature NCC over the whole RAW for a window WITHOUT a genuine
    match, from its mean/std at random lags (measured on speech and tonal material: mu + 4.5 sigma
    keeps ~95 % of correct windows at conf >= 1.3 and ~2 % of wrong ones)."""
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
    import time
    dlog = dlog or null_dlog()
    comp_y = np.asarray(comp_y if comp_y is not None else np.zeros(0), np.float32).reshape(-1)
    raw_y = np.asarray(raw_y if raw_y is not None else np.zeros(0), np.float32).reshape(-1)
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
    # ---- pass 1: speed 1.00, batched coarse curves --------------------------------------------
    for b0 in range(0, len(live), 32):
        idx = live[b0:b0 + 32]
        curves = A.corr.ncc_multi([A.comp_coarse(A.i0[i], 1.0) for i in idx])
        for i, c in zip(idx, curves):
            res[i] = A.evaluate_curve(i, 1.0, c)
    n_conf1 = sum(1 for r in res if r is not None and r.ok(mc))
    # ---- pass 1b: slight speed changes on windows that matched at 1.00 --------------------------
    for i in live:
        r = res[i]
        if r is None or not r.ok(mc):
            continue
        vb, sb, _ = A.local_sweep(i, r, 0.05)
        if abs(vb - 1.0) > 1e-9 and sb > r.peak + 0.01:
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
    evid = []
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
                A.refine(i, r)
            else:
                r.raw_t = float(r.lag / A.rate + r.v * A.W / 2.0)
                r.speed = r.v
            raw_t[i] = r.raw_t
            speed[i] = r.speed
        else:
            speed[i] = r.v
        evid.append({"i": i, "comp_t": round(float(comp_t[i]), 3), "raw_t": None if not np.isfinite(raw_t[i]) else round(float(raw_t[i]), 6),
                     "speed": round(float(speed[i]), 5), "hyp": r.hyp, "peak": round(r.peak, 4),
                     "second": round(r.second, 4), "null_floor": round(r.floor, 4), "conf": round(float(r.conf), 3),
                     "psr": round(r.psr, 2), "wave_peak": None if not np.isfinite(r.wave_peak) else round(r.wave_peak, 4)})
    hints = AudioHints(comp_t=comp_t, raw_t=raw_t, speed=speed, conf=conf.astype(np.float32),
                       psr=psr.astype(np.float32), peak=peak.astype(np.float32), window=float(A.W), hop=float(A.hop))
    n_conf = int(hints.confident(mc).sum())
    speeds_found = sorted({round(float(s), 3) for s, c in zip(speed, hints.confident(mc)) if c and abs(s - 1.0) > 1e-9})
    dt = time.perf_counter() - t0
    dlog.record("audio_align", "coarse_align", windows=N, silent=int(N - len(live)), confident=n_conf,
                confident_at_1=n_conf1, speeds_found=speeds_found, evaluations=A.n_evals, scan=scan_stats,
                comp_s=round(len(comp_y) / sr, 3), raw_s=round(len(raw_y) / sr, 3), seconds=round(dt, 2),
                evidence={"windows": evid})
    log.info("audio: %d/%d windows confident (%d at 1.00), speeds %s, %.1fs", n_conf, N, n_conf1, speeds_found or "-", dt)
    return hints


def _rms(y: np.ndarray) -> float:
    if y.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(y, dtype=np.float64))))


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
                           comp_fps: Any, cfg: Any, dlog: DecisionLog | None) -> dict:
    """Stage 5.6: per-segment audio analysis against the RAW-rebuilt track.

    Returns ``{'segments': {id: {in_offset_frames, out_offset_frames, pitch_preserved, lag_ms, corr,
    exception}}, 'added_audio': [{type, comp_in, comp_out, level_db, level_dbfs}], 'status':
    'ok'|'no_audio'|'audio_replaced', 'notes': [...], 'cuts': [J/L decisions]}``.

    * J/L (DESIGN §3): audio range = [comp_in + in_offset, comp_out + out_offset). At a hard cut A|B
      where the competitor's audio switches at comp frame p != cut: A.out_offset = B.in_offset =
      p - cut (negative = J-cut: B's audio leads; positive = L-cut: A's audio trails).
    * lag_ms / corr: ``xcorr_lag(competitor, rebuilt)`` over the segment's audio range (crossfade
      overlaps excluded): positive lag = rebuilt late.
    * pitch_preserved: speed != 1 only (``pitch_shift_test``), else None.
    * added_audio: residual (competitor - gain * lag-compensated rebuilt) energy frames above
      ``cfg.audio_added_thresh_db`` (default -20 dB) relative to the rebuilt track, median-smoothed,
      merged across unobservable ranges (NOT-IN-RAW, dips, crossfades); level_db = residual level
      relative to the original (rebuilt) audio in the run, level_dbfs = absolute.
    * exception (closed list): not_in_raw, no_audio, audio_replaced, pitch_preserved,
      music_dominated, too_short -- set when the segment's audio does not line up and why."""
    dlog = dlog or null_dlog()
    fps = parse_fps(comp_fps)
    sr = int(sr)
    comp = np.asarray(comp_y if comp_y is not None else np.zeros(0), np.float32).reshape(-1)
    raw = np.asarray(raw_y if raw_y is not None else np.zeros(0), np.float32).reshape(-1)
    segs = sorted(segments, key=lambda s: (s.comp_in, s.id))
    min_corr = float(_cfg(cfg, "audio_replaced_corr", 0.3))
    tol_ms = float(_cfg(cfg, "audio_lag_tol_ms", 10.0))
    jl_max_s = float(_cfg(cfg, "audio_jl_max_s", 1.0))
    thr_db = float(_cfg(cfg, "audio_added_thresh_db", -20.0))
    out: dict[int, dict] = {}
    for s in segs:
        out[s.id] = {"in_offset_frames": 0, "out_offset_frames": 0, "pitch_preserved": None, "lag_ms": None,
                     "corr": None, "exception": "not_in_raw" if s.type == "not_in_raw" else None}
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
        dlog.record("audio_segments", "no_audio", which=which)
        return {"segments": out, "added_audio": [], "status": "no_audio", "notes": notes, "cuts": []}

    models: dict[int, _Model] = {}
    for s in segs:
        m = _build_model(s, fps, sr)
        if m is not None:
            models[s.id] = m
    n_total = min(comp.size, f2s(n_frames)) if n_frames else comp.size

    # ---- core ranges (samples) and first lag estimate ------------------------------------------
    core: dict[int, tuple[int, int]] = {}
    lag0: dict[int, float] = {}
    for s in segs:
        if s.id not in models:
            continue
        din, dout = _crossfade_frames(s)
        a, b = f2s(s.comp_in + din), min(f2s(s.comp_out - dout), comp.size)
        core[s.id] = (a, b)
        if b - a >= int(0.1 * sr):
            lag, pk = xcorr_lag(comp[a:b], models[s.id].render(raw, sr, a, b), sr, _MAX_LAG_SEG_S)
            lag0[s.id] = lag if pk >= min_corr else 0.0
        else:
            lag0[s.id] = 0.0

    # ---- J/L cuts --------------------------------------------------------------------------------
    cuts: list[dict] = []
    win, hop = int(round(0.03 * sr)), int(round(0.01 * sr))
    for A, B in zip(segs[:-1], segs[1:]):
        if A.comp_out != B.comp_in or _crossfade_frames(A)[1] or _crossfade_frames(B)[0]:
            continue
        mA, mB = models.get(A.id), models.get(B.id)
        if mA is None and mB is None:
            continue
        cut = int(B.comp_in)
        jmax = min(int(round(jl_max_s * float(fps))), max(0, A.length - 2))
        lmax = min(int(round(jl_max_s * float(fps))), max(0, B.length - 2))
        if mA is None:
            lmax = min(lmax, B.length // 2)
            jmax = min(jmax, A.length // 2)
        if jmax + lmax < 1:
            continue
        n0, n1 = f2s(cut - jmax), min(f2s(cut + lmax), comp.size)
        if n1 - n0 < win + hop:
            continue
        c = comp[n0:n1]
        sA = _local_ncc(c, mA.render(raw, sr, n0, n1, lag0.get(A.id, 0.0)), win, hop)[0] if mA else None
        sB = _local_ncc(c, mB.render(raw, sr, n0, n1, lag0.get(B.id, 0.0)), win, hop)[0] if mB else None
        nfr = (sA if sA is not None else sB).size
        if nfr < 2:
            continue
        # boundary between frame j-1 and j sits half-way between their centres
        centres = n0 + np.arange(nfr) * hop + win // 2
        j_cut = int(np.clip(np.searchsorted(centres, f2s(cut)), 0, nfr))
        if sA is None or sB is None:
            # one side has no RAW model (placeholder): the other model must keep explaining the audio
            # clearly better than half its typical local NCC on its own side of the cut
            own = sA[:j_cut] if sA is not None else sB[j_cut:]
            level = float(np.median(own)) if own.size else 0.0
            if level < 0.4:
                continue
            sA = sA if sA is not None else np.full(nfr, 0.5 * level)
            sB = sB if sB is not None else np.full(nfr, 0.5 * level)
        csA = np.concatenate([[0.0], np.cumsum(sA)])
        csB = np.concatenate([[0.0], np.cumsum(sB)])
        score = csA + (csB[-1] - csB)                         # switch before frame j, j = 0..nfr
        jb = int(np.argmax(score))
        t_switch = (centres[jb] - hop / 2.0) if jb < nfr else (centres[-1] + hop / 2.0)
        p = int(round(t_switch / sr * float(fps)))
        p = int(np.clip(p, cut - jmax, cut + lmax))
        gain = float(score[jb] - score[j_cut])
        lo_j, hi_j = sorted((jb, j_cut))
        diff = (sB - sA)[lo_j:hi_j] if jb < j_cut else (sA - sB)[lo_j:hi_j]
        mdiff = float(diff.mean()) if diff.size else 0.0
        offset = p - cut
        # the switch must be at least one comp frame away and the frames in between must clearly
        # follow the other model (mean local-NCC margin >= 0.3)
        accept = offset != 0 and diff.size >= 2 and mdiff >= 0.3
        ev = {"cut": cut, "a": A.id, "b": B.id, "switch_frame": p, "offset_frames": offset if accept else 0,
              "measured_offset": offset, "mean_margin": round(mdiff, 4), "gain": round(gain, 3),
              "a_model": mA is not None, "b_model": mB is not None}
        cuts.append(ev)
        dlog.record("audio_segments", "jl_cut" if accept else "audio_cut_matches_video", **ev)
        if accept:
            if mA is not None:
                out[A.id]["out_offset_frames"] = offset
            if mB is not None:
                out[B.id]["in_offset_frames"] = offset
            notes.append(f"{'J' if offset < 0 else 'L'}-cut at comp frame {cut} (S{A.id:02d}|S{B.id:02d}): "
                         f"audio {'leads' if offset < 0 else 'trails'} by {abs(offset)} frames")

    # ---- per-segment lag / corr over the final audio range + pitch -----------------------------
    ranges: dict[int, tuple[int, int]] = {}
    for s in segs:
        if s.id not in models:
            continue
        din, dout = _crossfade_frames(s)
        a = f2s(s.comp_in + (din if din else out[s.id]["in_offset_frames"]))
        b = min(f2s(s.comp_out - (dout if dout else -out[s.id]["out_offset_frames"])), comp.size)
        a = max(0, a)
        ranges[s.id] = (a, b)
        if b - a < int(0.1 * sr):
            continue
        rb = models[s.id].render(raw, sr, a, b)
        lag, pk = xcorr_lag(comp[a:b], rb, sr, _MAX_LAG_SEG_S)
        out[s.id]["lag_ms"] = round(lag * 1000.0, 3)
        out[s.id]["corr"] = round(pk, 4)
        m = models[s.id]
        if m.kind == "stretch" and m.v > 0 and abs(m.v - 1.0) > 1e-6:
            r0 = m.raw_seconds(np.array([a / sr]))[0]
            r1 = m.raw_seconds(np.array([b / sr]))[0]
            ra, rb_ = int(round(r0 * sr)), int(round(r1 * sr))
            pt = pitch_shift_test(comp[a:b], raw[max(0, ra):max(0, rb_)], m.v, sr)
            out[s.id]["pitch_preserved"] = pt["pitch_preserved"]
            dlog.record("audio_segments", "pitch", seg=s.id, speed=m.v, **pt)
        lag0[s.id] = lag if pk >= min_corr else lag0.get(s.id, 0.0)

    # ---- run status -----------------------------------------------------------------------------
    measured = [sid for sid, (a, b) in ranges.items() if b - a >= int(_MIN_SEG_S * sr) and out[sid]["corr"] is not None]
    status = "ok"
    if measured and all(out[sid]["corr"] < min_corr for sid in measured):
        status = "audio_replaced"
        notes.append("competitor audio does not correlate with the RAW-rebuilt audio in any segment (audio replaced)")

    # ---- rebuilt track + residual -> added audio -------------------------------------------------
    rebuilt = np.zeros(n_total, np.float32)
    observable = np.zeros(n_total, bool)
    for s in segs:
        if s.id not in models or s.id not in ranges:
            continue
        a, b = ranges[s.id]
        b = min(b, n_total)
        if b <= a:
            continue
        g = 0.0
        rb = models[s.id].render(raw, sr, a, b, lag0.get(s.id, 0.0))
        if status != "audio_replaced":
            den = float(np.dot(rb.astype(np.float64), rb))
            g = float(np.clip(np.dot(comp[a:b].astype(np.float64), rb) / den, 0.0, 4.0)) if den > 0 else 0.0
        rebuilt[a:b] += g * rb
        observable[a:b] = True
    if status == "audio_replaced":
        observable[:] = True
        rebuilt[:] = 0.0
    added = _added_audio(comp[:n_total], rebuilt, observable, sr, fps, n_frames, thr_db, dlog)
    for ad in added:
        notes.append(f"added {ad['type']} comp frames {ad['comp_in']}-{ad['comp_out'] - 1} "
                     f"({ad['level_db']:+.1f} dB re original, {ad['level_dbfs']:.1f} dBFS)")

    # ---- exceptions ------------------------------------------------------------------------------
    for s in segs:
        o = out[s.id]
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
        if status == "audio_replaced":
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
        dlog.record("audio_segments", "segment", seg=s.id, range_samples=[a, b], lag_ms=lag, corr=corr,
                    pitch_preserved=o["pitch_preserved"], in_offset=o["in_offset_frames"],
                    out_offset=o["out_offset_frames"], exception=exc)
        if exc in ("audio_replaced", "music_dominated") and status != "audio_replaced":
            notes.append(f"S{s.id:02d}: competitor audio does not follow RAW ({exc}, corr {corr})")
        if o["pitch_preserved"]:
            notes.append(f"S{s.id:02d}: pitch preserved at speed {s.speed:.3f} (AE's stretch changes pitch)")
    dlog.record("audio_segments", "summary", status=status, added_audio=added, cuts=len(cuts),
                jl=[c for c in cuts if c["offset_frames"]])
    return {"segments": out, "added_audio": added, "status": status, "notes": notes,
            "cuts": [c for c in cuts if c["offset_frames"]]}


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
    typed = []
    for a, b in runs:
        typ = _classify_added(r[a * fr:b * fr], sr, fr)
        typed.append([a, b, typ])
    # merge same-type runs separated by unobservable frames or short gaps (< 0.5 s)
    merged: list[list] = []
    for a, b, t in typed:
        if merged and merged[-1][2] == t:
            ga, gb = merged[-1][1], a
            gap_obs = obs[ga:gb]
            if (gb - ga) * _ADDED_FRAME_S < 0.5 or not gap_obs.any() or gap_obs.mean() < 0.2:
                merged[-1][1] = b
                continue
        merged.append([a, b, t])
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
