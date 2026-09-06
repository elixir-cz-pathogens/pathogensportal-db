"""
Detekce anomálií nad ISIN — systém včasného varování (Farrington/Noufaily).

ISIN dává přes 1 500 měsíčních časových řad (114 diagnóz × 14 krajů + celá ČR),
které nikdo nesleduje očima. Tenhle skript pro každou řadu spočítá očekávanou
endemickou hladinu a označí měsíce, kdy hlášený počet překročil prahovou mez.

Metoda je Farringtonův algoritmus (Farrington et al. 1996) s vylepšeními podle
Noufaily et al. 2012 — stejný postup, který týdně běží nad tisíci řadami v UKHSA
a jehož referenční implementací je R balík `surveillance`. Zde reimplementace
v čistém numpy (malé GLM, IRLS je pár řádků — pipeline nepotřebuje těžkou
závislost typu statsmodels). Baseline má dvě varianty, viz `farrington_score`:
plná historie se sezónními faktory (default, Noufaily) a klasická okna ±1 měsíc
(`--model okna`); v simulační studii má plná varianta vyšší záchyt při
srovnatelném podílu planých poplachů — viz scripts/simulate_detection.py.

Jak se skóruje jeden měsíc jedné řady:

1.  Baseline tvoří stejná část roku v minulých letech (±1 měsíc kolem stejného
    kalendářního měsíce) — sezónnost tak řeší výběr dat, ne složitý model.
2.  Na baseline se nafituje kvazi-Poissonův GLM s lineárním trendem. „Kvazi“
    proto, že skutečné počty mají větší rozptyl než Poisson; podcenit rozptyl
    znamená falešné poplachy. Trend zůstává jen tehdy, když je průkazný
    a nepřestřeluje (Farringtonovo pravidlo).
3.  Minulé epidemie v baseline se převáží dolů (podle Anscombeho reziduí) —
    jinak by se systém naučil, že loňská epidemie je normál, a letos by mlčel.
4.  Práh je horní mez predikčního intervalu na mocninné škále 2/3 (stabilnější
    u malých počtů). Signál = pozorování nad prahem; síla se hlásí jako
    exceedance skóre (pozorované − očekávané) / (práh − očekávané).

Řady, kde je nemoc tak vzácná, že GLM nedává smysl (spalničky, záškrt…), mají
vlastní pravidlo: baseline prakticky nulová → hlásí se každý shluk případů.

Použití:
    python scripts/detect_anomalies.py                  # oskóruje poslední měsíc, zapíše JSON
    python scripts/detect_anomalies.py --backtest       # oskóruje celou historii do CSV
    python scripts/detect_anomalies.py --backtest --diagnoza "Dávivý kašel [pertussis]"
    python scripts/detect_anomalies.py --as-of 2026-09-01   # nad archivovaným snapshotem,
                                                            # bit-identický výstup
    python scripts/detect_anomalies.py --alpha 0.05         # hladina bayesovské FDR

Proměnné prostředí: DATA_DIR (vstupní CSV), OUTPUT_DIR (kam psát JSON) —
stejný kontrakt jako generate_json.py.
"""

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
CHARTS_OUT = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))

# ── parametry metody ─────────────────────────────────────────────────────────
Z_QUANTILE = 2.326      # 99. percentil — práh „1 falešný poplach na 100 klidných měsíců“
REWEIGHT_LIMIT = 2.58   # Anscombeho reziduum, od kterého se bod v baseline převažuje (Noufaily)
HALF_WINDOW = 1         # ±1 měsíc kolem stejného kalendářního měsíce
MIN_YEARS = 3           # méně let historie → řada se nehodnotí
MIN_CASES_GLM = 3       # signál z GLM se nehlásí pod 3 případy (šum u malých čísel)
RARE_TOTAL = 5          # suma celé historie ≤ 5 → „vzácná“ řada, GLM nedává smysl
RARE_ALERT = 2          # u vzácné řady se hlásí ≥ 2 případy v měsíci (1 import je běžný)
RECENT_SKIP = 6         # plný model: posledních 6 měsíců mimo baseline (rozjíždějící se
                        # epidemie nesmí zvednout vlastní práh — Noufaily vynechává 26 týdnů)
FULL_MIN_POINTS = 42    # plný model má 13 parametrů; pod ~3 body na parametr → okna

PI_DRAWS = 500          # vzorků parametrů pro π (pravděpodobnost překročení prahu)
PI_SEED = 20260906      # pevný seed → deterministický výstup (reprodukovatelnost)
FDR_ALPHA = 0.10        # default hladiny bayesovské FDR (viz --alpha)

REGISTRY_FILE = ROOT / "methodology_changes.yaml"

REGION_UNKNOWN = "CZ999"  # „neuvedeno“ — patří do součtu ČR, samostatně se neskóruje


# ── kvazi-Poissonův GLM (IRLS) ───────────────────────────────────────────────

def fit_quasipoisson(X: np.ndarray, y: np.ndarray, prior_w: np.ndarray | None = None):
    """
    Kvazi-Poissonův GLM s log-linkem přes IRLS.

    prior_w jsou váhy pozorování (Var(y_i) = φ·μ_i / w_i) — přes ně se dělá
    Noufailyové převážení minulých epidemií. Vrací (mu, beta, cov, phi, hat):
    cov už je škálovaná disperzí φ, hat je diagonála projekční matice
    (potřebná ke standardizaci reziduí).
    """
    n, p = X.shape
    w = np.ones(n) if prior_w is None else prior_w
    beta = np.zeros(p)
    beta[0] = np.log(max(y.mean(), 0.1))

    for _ in range(100):
        eta = np.clip(X @ beta, -30, 30)
        mu = np.exp(eta)
        W = w * mu                       # IRLS váha pro log-link
        z = eta + (y - mu) / mu          # pracovní odezva
        XtW = X.T * W
        A = XtW @ X
        try:
            beta_new = np.linalg.solve(A, XtW @ z)
        except np.linalg.LinAlgError:
            # Degenerovaná baseline (skoro samé nuly) — pseudoinverze místo
            # pádu; výsledek stejně projde prahovou podlahou.
            beta_new = np.linalg.pinv(A) @ (XtW @ z)
        if np.max(np.abs(beta_new - beta)) < 1e-8:
            beta = beta_new
            break
        beta = beta_new

    eta = np.clip(X @ beta, -30, 30)
    mu = np.exp(eta)
    W = w * mu
    A = (X.T * W) @ X
    try:
        A_inv = np.linalg.inv(A)
    except np.linalg.LinAlgError:
        A_inv = np.linalg.pinv(A)

    pearson = (y - mu) * np.sqrt(w / mu)
    phi = max(1.0, float(pearson @ pearson) / max(n - p, 1))  # disperze, min. 1 (Poisson)
    cov = A_inv * phi
    hat = np.clip(np.einsum("ij,jk,ik->i", X, A_inv, X) * W, 0, 0.999)
    return mu, beta, cov, phi, hat


def _anscombe(y: np.ndarray, mu: np.ndarray, phi: float, hat: np.ndarray) -> np.ndarray:
    """Standardizovaná Anscombeho rezidua — na nich stojí převažování epidemií.
    μ má podlahu, jinak bod s μ≈0 dá nekonečné reziduum (a dělení nulou níž)."""
    mu = np.maximum(mu, 1e-3)
    r = 1.5 * (np.power(y, 2 / 3) - np.power(mu, 2 / 3)) / np.power(mu, 1 / 6)
    return r / np.sqrt(phi * (1 - hat))


def _pi_bootstrap(y0: float, beta: np.ndarray, cov: np.ndarray,
                  x0: np.ndarray, phi: float, rng) -> float:
    """
    π = Pr(pozorování > práh) při zohlednění nejistoty odhadu parametrů.

    Dnešní binární pravidlo `y0 > U` se tváří, že práh známe přesně — ale U je
    spočítané z konečné historie. Vzorkujeme proto koeficienty z N(β̂, Σ̂)
    a práh počítáme pro každý vzorek zvlášť. Pozor na dvojí započtení: per-vzorkový
    práh nese už JEN šum pozorování (φμ) — člen Var(μ̂₀) z bodového vzorce tu
    nahrazuje samo vzorkování.
    """
    p = len(beta)
    jitter = 1e-10 * np.eye(p)
    try:
        L = np.linalg.cholesky(cov[:p, :p] + jitter)
    except np.linalg.LinAlgError:
        vals, vecs = np.linalg.eigh(cov[:p, :p])
        L = vecs @ np.diag(np.sqrt(np.clip(vals, 0, None)))
    draws = beta + rng.standard_normal((PI_DRAWS, p)) @ L.T
    mu0 = np.exp(np.clip(draws @ x0, -30, 30))
    V = phi * mu0
    U = mu0 * (1 + (2 / 3) * Z_QUANTILE * np.sqrt(V) / np.maximum(mu0, 1e-9)) ** 1.5
    U = np.maximum(U, 1.0)
    return float(np.mean(y0 > U))


# ── skórování jedné řady ─────────────────────────────────────────────────────

def farrington_score(counts: np.ndarray, t0: int,
                     exclude_t: frozenset = frozenset(),
                     model: str = "plny", phase: int = 0,
                     rng=None) -> dict | None:
    """
    Oskóruje měsíc t0 řady `counts` (kompletní měsíční mřížka, index = pořadí
    měsíce od začátku dat). Vrací dict s výsledkem, nebo None, když řadu nelze
    hodnotit (málo historie).

    exclude_t jsou měsíce vynechané z baseline (typicky covidová éra 2020–21,
    kdy protiepidemická opatření stlačila hlášení většiny nemocí hluboko pod
    normál). Vynechaný rok se nepočítá ani do minima let historie.

    model volí stavbu baseline:
      "okna" — klasický Farrington 1996: stejné kalendářní měsíce ±HALF_WINDOW
               z minulých let (~21 bodů); sezónnost řeší výběr dat.
      "plny" — Noufaily 2012: celá historie kromě posledních RECENT_SKIP měsíců
               (ty může kontaminovat rozjíždějící se epidemie), sezónnost přes
               faktor kalendářního měsíce (referenční úroveň = cílový měsíc,
               takže predikce v t0 je prostě exp(intercept)). Využívá ~4× víc
               dat; když jich je na 13 parametrů málo, spadne zpět na okna.
    phase je kalendářní měsíc indexu t=0 (0 = leden) — potřebuje ho jen "plny".
    """
    # Okenní baseline se staví vždy — je zároveň fallbackem plného modelu.
    idx, years_used = [], set()
    k = 1
    while True:
        anchor = t0 - 12 * k
        if anchor + HALF_WINDOW < 0:
            break
        for d in range(-HALF_WINDOW, HALF_WINDOW + 1):
            t = anchor + d
            if 0 <= t < t0 and t not in exclude_t:
                idx.append(t)
                years_used.add(k)
        k += 1
    if len(years_used) < MIN_YEARS:
        return None

    seasonal_factors = False
    if model == "plny":
        full = [t for t in range(0, t0 - RECENT_SKIP) if t not in exclude_t]
        if len(full) >= FULL_MIN_POINTS:
            idx = full
            seasonal_factors = True

    idx = np.array(sorted(idx))
    y = counts[idx].astype(float)
    y0 = float(counts[t0])

    history_total = float(counts[:t0].sum())
    if history_total <= RARE_TOTAL:
        # GLM nad samými nulami nedává smysl — vzácná nemoc, hlásí se shluk.
        sig = y0 >= RARE_ALERT
        return {
            "type": "rare",
            "observed": y0,
            "expected": 0.0,
            "threshold": float(RARE_ALERT - 0.5),
            "score": None,
            "pi": 1.0 if sig else 0.0,  # pravidlová řada — π definičně
            "signal": sig,
            "n_baseline": len(idx),
        }

    if y.sum() == 0:
        # Historie případy má, ale v baseline oknech nejsou žádné — typicky
        # nemoc, která se začala vykazovat až v průběhu období, nebo silně
        # mimosezónní výskyt. GLM nad samými nulami je singulární; chová se to
        # tedy jako sporadická řada s prahem jednoho případu.
        sig = y0 >= MIN_CASES_GLM
        return {
            "type": "sporadic",
            "observed": y0,
            "expected": 0.0,
            "threshold": 1.0,
            "score": round(y0, 2),
            "pi": 1.0 if sig else 0.0,
            "signal": sig,
            "n_baseline": len(idx),
        }

    # Čas škálujeme na roky, ať je koeficient trendu čitelný a IRLS stabilní.
    t_scale = (idx - t0) / 12.0
    ones = np.ones(len(idx))
    if seasonal_factors:
        # Faktor kalendářního měsíce; referenční úroveň = cílový měsíc, takže
        # predikce v t0 nepotřebuje žádnou dummy a x0 má jedničku jen u interceptu.
        months = (idx + phase) % 12
        target_m = (t0 + phase) % 12
        other = np.array([m for m in range(12) if m != target_m])
        D = (months[:, None] == other[None, :]).astype(float)
        X_with = np.column_stack([ones, t_scale, D])
        X_without = np.column_stack([ones, D])
    else:
        X_with = np.column_stack([ones, t_scale])
        X_without = ones.reshape(-1, 1)

    def fit_with_trend_rule(prior_w=None):
        """Farringtonovo pravidlo: trend jen průkazný a nepřestřelující."""
        mu, beta, cov, phi, hat = fit_quasipoisson(X_with, y, prior_w)
        se_trend = np.sqrt(max(cov[1, 1], 0))
        mu0 = float(np.exp(np.clip(beta[0], -30, 30)))  # predikce v t0 (t_scale=0, dummy=0)
        significant = se_trend > 0 and abs(beta[1]) / se_trend > 1.96
        if significant and mu0 <= max(y.max(), 1.0):
            x0 = np.zeros(X_with.shape[1]); x0[0] = 1.0
            return mu, beta, cov, phi, hat, mu0, x0
        mu, beta, cov, phi, hat = fit_quasipoisson(X_without, y, prior_w)
        mu0 = float(np.exp(np.clip(beta[0], -30, 30)))
        x0 = np.zeros(X_without.shape[1]); x0[0] = 1.0
        return mu, beta, cov, phi, hat, mu0, x0

    mu, beta, cov, phi, hat, mu0, x0 = fit_with_trend_rule()

    # Převážení odlehlých bodů baseline a jeden refit. Noufaily 2012 převažuje
    # jen kladná rezidua (minulé epidemie); tady se převažuje symetricky,
    # protože covidové roky počty naopak *potlačily* — jednostranné převážení
    # nechává stlačenou baseline táhnout očekávání dolů a návrat k normálu pak
    # vypadá jako epidemie. Změřeno na datech 12/2025: 24 z 67 signálů stálo
    # na baseline, kde roky 2020–21 měly méně než polovinu normálu.
    resid = _anscombe(y, mu, phi, hat)
    outlier = np.abs(resid) > REWEIGHT_LIMIT
    if np.any(outlier):
        w = np.ones(len(resid))
        w[outlier] = resid[outlier] ** -2.0  # jen kde třeba — np.where počítá obě větve
        w = w * len(w) / w.sum()
        mu, beta, cov, phi, hat, mu0, x0 = fit_with_trend_rule(prior_w=w)

    # Práh: horní mez predikčního intervalu na škále 2/3 (Farrington 1996).
    var_mu0 = mu0 ** 2 * float(x0 @ cov @ x0)
    V = phi * mu0 + var_mu0
    if mu0 > 0 and V > 0:
        U = mu0 * (1 + (2 / 3) * Z_QUANTILE * np.sqrt(V) / mu0) ** 1.5
    else:
        U = mu0 + Z_QUANTILE * np.sqrt(max(V, 1.0))

    # Podlaha prahu: u řady, jejíž baseline okna jsou skoro samé nuly, vyjde
    # U ≈ 0 a jmenovatel skóre se blíží nule — šest případů něčeho sporadického
    # by pak mělo skóre v tisících a přeskočilo skutečné epidemie. Práh jednoho
    # případu drží skóre ve významu „kolikrát nad minimálním detekovatelným
    # shlukem“, aniž by signál zrušil.
    U = max(float(U), 1.0)

    score = (y0 - mu0) / (U - mu0) if U > mu0 else None
    signal = bool(score is not None and score > 1 and y0 >= MIN_CASES_GLM)
    pi = None
    if rng is not None:
        pi = round(_pi_bootstrap(y0, beta, cov, x0, phi, rng), 4)
        if y0 < MIN_CASES_GLM:
            pi = 0.0  # minimum případů platí i pro pravděpodobnostní cestu
    return {
        "type": "glm",
        "observed": y0,
        "pi": pi,
        "expected": round(mu0, 2),
        "threshold": round(float(U), 2),
        "score": round(float(score), 2) if score is not None else None,
        "signal": signal,
        "n_baseline": len(idx),
    }


# ── data ─────────────────────────────────────────────────────────────────────

def load_series(src_path: Path | None = None) -> tuple[pd.DataFrame, list[str], int]:
    """
    Vrátí (long tabulka diagnóza×kraj×měsíc, seznam period 'YYYY-MM', počet měsíců).
    Kraj 'CZ' je celostátní součet (včetně CZ999 „neuvedeno“, které se jinak
    samostatně neskóruje — případ bez kraje pořád je případ v ČR).
    """
    path = src_path or (DATA_DIR / "isin" / "isin_infekcni_nemoci.csv")
    df = pd.read_csv(path, encoding="utf-8-sig")  # pandas čte .gz transparentně
    df.columns = df.columns.str.strip()

    grouped = (df.groupby(["diagnoza", "diagnoza_nazev", "kraj_kod", "kraj_nazev",
                           "rok", "mesic"])["pocet_pripadu"].sum().reset_index())

    cz = (df.groupby(["diagnoza", "diagnoza_nazev", "rok", "mesic"])["pocet_pripadu"]
            .sum().reset_index())
    cz["kraj_kod"], cz["kraj_nazev"] = "CZ", "Česká republika"

    long = pd.concat([grouped[grouped.kraj_kod != REGION_UNKNOWN], cz], ignore_index=True)

    y_min, m_min = int(df.rok.min()), int(df[df.rok == df.rok.min()].mesic.min())
    y_max, m_max = int(df.rok.max()), int(df[df.rok == df.rok.max()].mesic.max())
    n_months = (y_max - y_min) * 12 + (m_max - m_min) + 1
    periods = []
    y, m = y_min, m_min
    for _ in range(n_months):
        periods.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    long["t"] = (long.rok - y_min) * 12 + (long.mesic - m_min)
    return long, periods, n_months


def build_grid(long: pd.DataFrame, n_months: int):
    """Generátor (meta, counts) — kompletní mřížka s nulami tam, kde chybí řádek.
    Chybějící měsíc v agregovaném hlášení znamená nula případů, ne díru v datech."""
    for (dg, dg_name, kraj, kraj_name), g in long.groupby(
            ["diagnoza", "diagnoza_nazev", "kraj_kod", "kraj_nazev"]):
        counts = np.zeros(n_months)
        counts[g.t.to_numpy()] = g.pocet_pripadu.to_numpy()
        yield {"diagnoza": dg, "diagnoza_nazev": dg_name,
               "kraj_kod": kraj, "kraj_nazev": kraj_name}, counts


# ── reprodukovatelnost ───────────────────────────────────────────────────────

def resolve_snapshot(as_of: str) -> Path:
    """Najde nejnovější archivovanou verzi ISIN ≤ danému datu. Archiv ukládá
    soubor jen v den, kdy se změnil — hledá se tedy poslední starší den."""
    rel = Path("isin") / "isin_infekcni_nemoci.csv.gz"
    days = sorted(d.name for d in (DATA_DIR / "raw").iterdir()
                  if d.is_dir() and d.name <= as_of and (d / rel).exists())
    if not days:
        raise FileNotFoundError(f"V archivu není žádný snapshot ISIN ≤ {as_of}")
    return DATA_DIR / "raw" / days[-1] / rel


def _sha256_file(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)  # hash obsahu, ne komprese — srovnatelný s manifestem
    return hashlib.sha256(data).hexdigest()


def _code_version() -> str:
    try:
        return subprocess.run(["git", "describe", "--tags", "--always", "--dirty"],
                              capture_output=True, text=True, cwd=ROOT,
                              timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"  # v Docker image .git není (.dockerignore)


def build_provenance(input_path: Path, as_of: str | None, alpha: float) -> dict:
    return {
        "input": str(input_path),
        "input_sha256": _sha256_file(input_path),
        "as_of": as_of,
        "code_version": _code_version(),
        "params": {
            "z_quantile": Z_QUANTILE, "reweight_limit": REWEIGHT_LIMIT,
            "recent_skip": RECENT_SKIP, "full_min_points": FULL_MIN_POINTS,
            "min_years": MIN_YEARS, "min_cases_glm": MIN_CASES_GLM,
            "rare_total": RARE_TOTAL, "rare_alert": RARE_ALERT,
            "pi_draws": PI_DRAWS, "pi_seed": PI_SEED, "fdr_alpha": alpha,
        },
    }


# ── registr metodických změn ─────────────────────────────────────────────────

def load_registry() -> list[dict]:
    if not REGISTRY_FILE.exists():
        return []
    return yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8")) or []


def registry_masks(meta: dict, periods: list[str], rules: list[dict]):
    """
    Přeloží záznamy registru na (extra_exclude: set[int], aktivní pravidla).

    break   → z baseline vypadne VŠE před datem změny (úroveň řady se změnila,
              stará historie o nové realitě lže)
    exclude → z baseline vypadne období od–do
    flag / poznamka → baseline se nemění; flag jen označuje výsledky
    """
    def period_t(p, default):
        if p is None:
            return default
        if p < periods[0]:
            return 0
        for i, q in enumerate(periods):
            if q >= p:
                return i
        return len(periods)

    static_exclude: set[int] = set()
    breaks: list[int] = []   # od_t break-pravidel — aplikují se až od data změny
    active: list[dict] = []
    for r in rules:
        if r.get("akce") == "poznamka":
            continue
        scope = r.get("rozsah") or {}
        dgs = scope.get("diagnozy")
        if dgs is not None and meta["diagnoza_nazev"] not in dgs:
            continue
        kraje = scope.get("kraje")
        if kraje is not None and meta["kraj_kod"] not in kraje and meta["kraj_kod"] != "CZ":
            continue
        od_t = period_t(str(r["od"]), 0)
        do_t = period_t(str(r["do"]), len(periods)) if r.get("do") else len(periods)
        if r["akce"] == "break":
            # Neaplikuje se staticky: pro t0 PŘED změnou byla stará baseline
            # platná — jinak by break smazal řadu i z retrospektivy.
            breaks.append(od_t)
        elif r["akce"] == "exclude":
            static_exclude.update(range(od_t, do_t))  # vadná data jsou vadná pro každé t0
        active.append({**r, "_od_t": od_t, "_do_t": do_t})
    return static_exclude, breaks, active


def masks_for_t0(static_exclude: set, breaks: list[int], t0: int) -> set:
    out = set(static_exclude)
    for od_t in breaks:
        if t0 >= od_t:
            out.update(range(0, od_t))
    return out


def registry_annotation(active: list[dict], t0: int) -> str | None:
    hits = [r["id"] for r in active if r["_od_t"] <= t0 < r["_do_t"]]
    return "; ".join(hits) if hits else None


# ── FDR napříč řadami ────────────────────────────────────────────────────────

def apply_fdr(records: list[dict], alpha: float) -> tuple[int, int]:
    """
    Bayesovská FDR: seřaď π sestupně, vezmi největší k, pro které průměr
    (1−π) horních k nepřekročí α — tedy očekávaný podíl falešných mezi
    ohlášenými ≤ α. Vedle toho Benjamini–Hochberg (p ≈ 1−π) pro srovnání.
    Označí records in-place polem fdr_pass; vrací (k_bayes, k_bh).
    """
    scored = [r for r in records if r.get("pi") is not None]
    order = sorted(scored, key=lambda r: -r["pi"])
    cum, k = 0.0, 0
    for j, r in enumerate(order, 1):
        cum += 1.0 - r["pi"]
        if cum / j <= alpha:
            k = j
    for j, r in enumerate(order, 1):
        r["fdr_pass"] = j <= k

    n = len(order)
    pvals = sorted(1.0 - r["pi"] for r in order)
    k_bh = max((j for j in range(1, n + 1) if pvals[j - 1] <= alpha * j / n), default=0)
    return k, k_bh


# ── běhy ─────────────────────────────────────────────────────────────────────

def run_current(long, periods, n_months, exclude_t=frozenset(),
                model="plny", phase=0, alpha=FDR_ALPHA, provenance=None) -> int:
    t0 = n_months - 1
    rng = np.random.default_rng(PI_SEED)
    rules = load_registry()
    records, skipped, prebaselining = [], 0, []

    for meta, counts in build_grid(long, n_months):
        static_ex, breaks, active = registry_masks(meta, periods, rules)
        extra = masks_for_t0(static_ex, breaks, t0)
        res = farrington_score(counts, t0, frozenset(exclude_t | extra),
                               model, phase, rng=rng)
        if res is None:
            skipped += 1
            # break-pravidlo mohlo řadu připravit o historii — to není chyba,
            # ale stav „přebaselinovává se“, a uživatel o něm musí vědět.
            brk = [r for r in active if r["akce"] == "break"]
            if brk and counts[t0] > 0:
                prebaselining.append({**meta, "observed": float(counts[t0]),
                                      "zmena": brk[0]["id"], "od": str(brk[0]["od"])})
            continue
        note = registry_annotation(active, t0)
        if note:
            res["metodicka_zmena"] = note
        records.append({**meta, **res})

    k_fdr, k_bh = apply_fdr(records, alpha)
    signals = [
        {k: v for k, v in r.items() if k != "signal"}
        for r in records if r["signal"]
    ]
    signals.sort(key=lambda s: (s["score"] is None, -(s["score"] or 0), -s["observed"]))

    out = {
        "generated_at": (provenance or {}).get("as_of")
                        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_period": periods[t0],
        "method": "Farrington–Noufaily, kvazi-Poisson GLM, 99. percentil, "
                  "π parametrickým bootstrapem, bayesovská FDR",
        "n_series_scored": len(records),
        "n_series_skipped": skipped,
        "n_signals": len(signals),
        "fdr": {"alpha": alpha, "n_tested": len(records),
                "n_pass": k_fdr, "n_pass_bh": k_bh},
        "prebaselining": prebaselining,
        "signals": signals,
    }
    if provenance:
        out["provenance"] = provenance
    CHARTS_OUT.mkdir(parents=True, exist_ok=True)
    path = CHARTS_OUT / "anomaly_signals.json"
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    n_sig_fdr = sum(1 for r in signals if r.get("fdr_pass"))
    print(f"[{periods[t0]}] oskórováno {len(records)} řad ({skipped} přeskočeno), "
          f"signálů {len(signals)} (po FDR α={alpha}: {n_sig_fdr}; BH: {k_bh}) → {path}")
    for r in prebaselining:
        print(f"  ⚠ přebaselinovává se po metodické změně: {r['diagnoza_nazev']} "
              f"/ {r['kraj_nazev']} (od {r['od']}, {r['zmena']})")
    for sg in signals[:15]:
        print(f"  {sg['diagnoza_nazev'][:42]:<42} {sg['kraj_nazev']:<20} "
              f"{sg['observed']:>6.0f} (oček. {sg['expected']:>7.1f}, práh {sg['threshold']:>7.1f}, "
              f"π={sg.get('pi')}, FDR={'✓' if sg.get('fdr_pass') else '✗'})")
    return 0


def run_backtest(long, periods, n_months, diagnoza: str | None,
                 exclude_t=frozenset(), model="plny", phase=0,
                 alpha=FDR_ALPHA) -> int:
    if diagnoza:
        long = long[long.diagnoza_nazev == diagnoza]
        if long.empty:
            print(f"Diagnóza „{diagnoza}“ v datech není.", file=sys.stderr)
            return 1
    rng = np.random.default_rng(PI_SEED)
    rules = load_registry()
    rows = []
    start = 12 * MIN_YEARS  # skórovat lze až s MIN_YEARS lety historie
    for meta, counts in build_grid(long, n_months):
        static_ex, breaks, active = registry_masks(meta, periods, rules)
        for t0 in range(start, n_months):
            merged = frozenset(exclude_t | masks_for_t0(static_ex, breaks, t0))
            res = farrington_score(counts, t0, merged, model, phase, rng=rng)
            if res is None:
                continue
            note = registry_annotation(active, t0)
            if note:
                res["metodicka_zmena"] = note
            rows.append({**meta, "period": periods[t0], **res})

    # FDR se aplikuje průřezově v rámci každého období — tak, jak by běžela naživo.
    by_period: dict = {}
    for r in rows:
        by_period.setdefault(r["period"], []).append(r)
    for period_rows in by_period.values():
        apply_fdr(period_rows, alpha)

    out_dir = DATA_DIR / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "anomaly_backtest.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    n_sig = sum(r["signal"] for r in rows)
    n_fdr = sum(1 for r in rows if r.get("fdr_pass"))
    print(f"Backtest: {len(rows)} skóre, {n_sig} signálů, {n_fdr} po FDR (α={alpha}) → {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true",
                    help="oskórovat celou historii (výstup CSV), ne jen poslední měsíc")
    ap.add_argument("--diagnoza", default=None,
                    help="omezit backtest na jednu diagnózu (přesný název)")
    ap.add_argument("--covid-baseline", choices=["vynechat", "ponechat"], default="ponechat",
                    help="zda covidovou éru 2020–21 zahrnout do baseline (default: ponechat)")
    ap.add_argument("--model", choices=["okna", "plny"], default="plny",
                    help="plny = Noufaily 2012, celá historie (default; v simulaci vyšší záchyt při srovnatelných planých poplaších), okna = Farrington 1996 (±1 měsíc)")
    ap.add_argument("--as-of", default=None, metavar="YYYY-MM-DD",
                    help="běh nad archivovaným snapshotem místo živého CSV — "
                         "reprodukovatelný, bit-identický výstup")
    ap.add_argument("--alpha", type=float, default=FDR_ALPHA,
                    help=f"hladina bayesovské FDR (default {FDR_ALPHA})")
    args = ap.parse_args()

    src = None
    if args.as_of:
        src = resolve_snapshot(args.as_of)
        print(f"Vstup ze snapshotu: {src}")
    long, periods, n_months = load_series(src)
    provenance = build_provenance(
        src or (DATA_DIR / "isin" / "isin_infekcni_nemoci.csv"),
        args.as_of, args.alpha)

    exclude_t = frozenset()
    if args.covid_baseline == "vynechat":
        exclude_t = frozenset(i for i, p in enumerate(periods) if "2020-01" <= p <= "2021-12")
    phase = int(periods[0][5:7]) - 1  # kalendářní měsíc indexu t=0 (0 = leden)
    if args.backtest:
        return run_backtest(long, periods, n_months, args.diagnoza, exclude_t,
                            args.model, phase, args.alpha)
    return run_current(long, periods, n_months, exclude_t, args.model, phase,
                       args.alpha, provenance)


if __name__ == "__main__":
    sys.exit(main())
