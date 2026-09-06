"""GLM jádro proti referenci (statsmodels).

Validace, která existovala jen jako jednorázová seance, není validace — proto
je tu jako test. statsmodels není v requirements (v produkci ho nechceme),
takže se test bez něj čistě přeskočí; lokálně a v prostředí s nainstalovaným
statsmodels běží naplno.
"""
import numpy as np
import pytest

from detect_anomalies import fit_quasipoisson

sm = pytest.importorskip("statsmodels.api")


def test_irls_matches_statsmodels():
    rng = np.random.default_rng(42)
    for trial in range(60):
        n = int(rng.integers(9, 30))
        X = np.column_stack([np.ones(n), rng.uniform(-3, 0, n)])
        mu_true = np.exp(rng.uniform(0, 3) + rng.uniform(-0.5, 0.5) * X[:, 1])
        y = rng.poisson(mu_true).astype(float) + rng.integers(0, 3, n)
        w = rng.uniform(0.3, 1.0, n) if trial % 2 else np.ones(n)

        mu, beta, cov, phi, hat = fit_quasipoisson(X, y, w)
        ref = sm.GLM(y, X, family=sm.families.Poisson(), var_weights=w).fit(scale="X2")
        phi_ref = max(1.0, ref.scale)
        cov_ref = ref.cov_params() / ref.scale * phi_ref  # sjednocení podlahy φ≥1

        assert np.allclose(beta, ref.params, atol=1e-6)
        assert np.allclose(np.sqrt(np.diag(cov)), np.sqrt(np.diag(cov_ref)), rtol=1e-4)
        assert abs(phi - phi_ref) < 1e-6
        assert np.allclose(hat, ref.get_hat_matrix_diag(), atol=1e-6)
