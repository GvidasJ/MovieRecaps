"""match_cuts -- rebuild a competitor's short-form edit frame-exactly from the RAW source."""
import os as _os

__version__ = "0.1.0"

# Never start scipy's ducc FFT thread pool (every FFT here runs single-threaded); its idle threads would be
# alive whenever a worker pool forks. Set before any scipy FFT runs -- common.limit_native_threads, DESIGN D7.
_os.environ.setdefault("DUCC0_NUM_THREADS", "1")
