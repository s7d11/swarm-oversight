"""DeLong paired AUROC test (analyze.delong_paired) against a direct implementation of DeLong et al. (1988)."""
import math
import os
import sys

import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analyze import _midrank, delong_paired  # noqa: E402


def slow_delong(y, s1, s2):
    """Structural components computed pairwise (O(m n)), straight from DeLong et al. (1988)."""
    y = np.asarray(y)
    psi = lambda a, b: 1.0 if a > b else 0.5 if a == b else 0.0
    v10s, v01s, aucs = [], [], []
    for s in (np.asarray(s1, float), np.asarray(s2, float)):
        X, Y = s[y == 1], s[y == 0]
        v10 = np.array([np.mean([psi(x, yy) for yy in Y]) for x in X])
        v01 = np.array([np.mean([psi(x, yy) for x in X]) for yy in Y])
        v10s.append(v10); v01s.append(v01); aucs.append(v10.mean())
    m, n = (y == 1).sum(), (y == 0).sum()
    S = np.cov(np.array(v10s)) / m + np.cov(np.array(v01s)) / n
    var = S[0, 0] + S[1, 1] - 2 * S[0, 1]
    return aucs[0], aucs[1], var


def test_midrank_ties():
    assert list(_midrank(np.array([3.0, 1.0, 3.0, 2.0]))) == [3.5, 1.0, 3.5, 2.0]


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_matches_direct_definition_and_sklearn(seed):
    rng = np.random.default_rng(seed)
    y = np.array([1] * 23 + [0] * 40)
    base = rng.normal(size=len(y)) + y * 1.0
    s1 = np.round(base + rng.normal(scale=0.8, size=len(y)), 1)        # rounded: plenty of ties
    s2 = np.round(base + rng.normal(scale=0.4, size=len(y)), 1)
    d = delong_paired(y, s1, s2)
    a1, a2, var = slow_delong(y, s1, s2)
    assert d["auc1"] == pytest.approx(roc_auc_score(y, s1)) == pytest.approx(a1)
    assert d["auc2"] == pytest.approx(roc_auc_score(y, s2)) == pytest.approx(a2)
    assert d["se"] == pytest.approx(math.sqrt(var), rel=1e-9)
    assert 0 <= d["p"] <= 1


def test_identical_views_and_degenerate_cases():
    y = [1, 1, 1, 0, 0, 0, 0]
    s = [90, 70, 40, 50, 20, 10, 0]
    d = delong_paired(y, s, s)
    assert d["diff"] == 0 and d["p"] == 1.0
    perfect = [100, 90, 80, 10, 5, 3, 0]
    d = delong_paired(y, s, perfect)
    assert d["auc2"] == 1.0 and d["diff"] > 0 and d["p"] < 1
    assert math.isnan(delong_paired([1, 0, 0], [1, 0, 0], [0, 1, 0])["p"])   # too few positives
