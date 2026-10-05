"""gpu.KnnIndex: the exact k nearest neighbours (Task 9: float16 tensor-core distances, two-stage selection) against a
brute force in integers -- with many equal distances, a last group and a last chunk cut short, k larger than a group,
and the extremes of uint8."""
import numpy as np
import pytest


def _gpu_ok():
    from match_cuts import gpu
    return gpu.available() is None


pytestmark = pytest.mark.skipif(not _gpu_ok(), reason="no GPU")


def _brute(x: np.ndarray, q: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """The k nearest of each query in int64 arithmetic: (indices, squared distances), equal distances by index."""
    X = x.astype(np.int64)
    k = min(k, len(X))
    out_i, out_d = [], []
    for row in q.astype(np.int64):
        d2 = ((X - row) ** 2).sum(1)
        order = np.lexsort((np.arange(len(d2)), d2))[:k]
        out_i.append(order)
        out_d.append(d2[order].astype(np.float32))
    return np.array(out_i), np.array(out_d)


@pytest.mark.parametrize("chunk,group,k", [(997, 8, 24), (4096, 64, 24), (300, 8, 40), (1000, 16, 1), (64, 64, 5),
                                           (8192, 8, 24), (8192, 4, 40)])     # one chunk: no other chunk helps
def test_the_search_is_the_brute_force_with_many_equal_distances(chunk, group, k):
    from match_cuts import gpu
    rng = np.random.default_rng(chunk + group + k)
    x = rng.integers(0, 3, (5003, 128)).astype(np.uint8)          # three values: equal distances everywhere
    x[::7] = rng.integers(0, 256, (len(x[::7]), 128))            # and some spread
    q = np.concatenate([x[:20], rng.integers(0, 3, (120, 128)), rng.integers(0, 256, (60, 128))]).astype(np.uint8)
    idx = gpu.KnnIndex(x, chunk=chunk, group=group)
    try:
        (ind, d2), = idx.search([q], k)
    finally:
        idx.close()
    bi, bd = _brute(x, q, k)
    assert np.array_equal(ind, bi) and np.array_equal(d2, bd)


def test_the_nearest_spread_over_as_many_groups_as_there_are_neighbours():
    """One chunk, each of the k nearest in a group of its own (every 8th row is near the query): every one of the k
    groups is needed (one group fewer fails here), with many of them at equal distance."""
    from match_cuts import gpu
    rng = np.random.default_rng(11)
    x = rng.integers(200, 256, (4000, 128)).astype(np.uint8)       # far from the queries
    x[::8] = rng.integers(0, 3, (500, 128))                        # near: one per group of 8, many at equal distance
    x[1::8][:100] = 0                                              # and ties at distance 0 in the next group too
    q = np.concatenate([np.zeros((5, 128)), rng.integers(0, 3, (40, 128))]).astype(np.uint8)
    for group in (8, 16):
        idx = gpu.KnnIndex(x, chunk=8192, group=group)
        try:
            (ind, d2), = idx.search([q], 24)
        finally:
            idx.close()
        bi, bd = _brute(x, q, 24)
        assert np.array_equal(ind, bi) and np.array_equal(d2, bd), group


def test_the_search_is_exact_at_the_extremes_of_uint8():
    """Every value 255: the largest squared norms and dot products uint8 vectors have (128 x 255^2 < 2^24)."""
    from match_cuts import gpu
    rng = np.random.default_rng(5)
    x = rng.integers(0, 256, (3000, 128)).astype(np.uint8)
    x[:50] = 255
    x[50:100] = 0
    q = np.concatenate([np.full((3, 128), 255), np.zeros((3, 128)), rng.integers(0, 256, (30, 128))]).astype(np.uint8)
    idx = gpu.KnnIndex(x, chunk=1024, group=16)
    try:
        sets = idx.search([q[:3], q[3:6], q[6:].astype(np.float32)], 24)     # query sets; float32 SIFT values too
    finally:
        idx.close()
    got_i = np.concatenate([s[0] for s in sets])
    got_d = np.concatenate([s[1] for s in sets])
    bi, bd = _brute(x, q, 24)
    assert np.array_equal(got_i, bi) and np.array_equal(got_d, bd)
    assert got_d[0, 0] == 0.0 and got_d[3, 0] == 0.0 and got_d[0].max() <= 128 * 255 ** 2


def test_the_search_refuses_values_it_could_not_search_exactly():
    from match_cuts import gpu
    x = np.zeros((100, 128), np.uint8)
    idx = gpu.KnnIndex(x)
    try:
        with pytest.raises(ValueError):
            idx.search([np.full((2, 128), 0.5, np.float32)], 3)
        with pytest.raises(ValueError):
            idx.search([np.full((2, 128), 300.0, np.float32)], 3)
        empty = idx.search([np.zeros((0, 128), np.uint8)], 3)
        assert empty[0][0].shape == (0, 3)
    finally:
        idx.close()
