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
    python scripts/detect_anomalies.py --zdroj tydenni      # 11 diagnóz týdenního souhrnu ÚZIS
                                                            # do posledního uzavřeného měsíce
                                                            # → anomaly_signals_weekly.json

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart_meta
import labels_en
import last_checked
import weekly_months

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
NAMES_FILE = ROOT / "curated" / "diagnosis_names.yaml"

# Týdenní zdroj (--zdroj tydenni): první měsíc, ve kterém vznikaly záznamy z hlášení
# EWS, a podíl EWS (v procentech za posledních 12 měsíců, celá ČR), od kterého se
# měsíce s EWS nepočítají do baseline. Deset procent je stejná hranice, od které
# compute_isin.py přestává srovnávat s předchozími roky (EWS_COMPARABLE_MAX).
EWS_START = "2025-07"
EWS_BASELINE_MAX = 10.0

# Jak EWS na diagnózu působí (`classify_weekly`). Celková úroveň od EWS_START se
# poměřuje s mediánem stejných kalendářních měsíců před ním:
#   presun      úroveň do LEVEL_RATIO_MAX — hlášení se do EWS jen přesunula, celkový
#               počet je s historií srovnatelný a hodnotí se jako dřív;
#   novy_kanal  úroveň nad LEVEL_RATIO_MAX a z EWS aspoň NEW_CHANNEL_EWS_MIN % —
#               EWS zachytil případy, které se dřív nehlásily; řada měří něco
#               jiného než dřív a s historií se nesrovnává (nehodnotí se);
#   nejiste     úroveň skočila, ale EWS tvoří menšinu — nárůst může být epidemie
#               i kanál; platí opatrný verdikt `channel_verdict`;
#   bez_zmeny   EWS pod EWS_BASELINE_MAX — jako u otevřeného CSV.
# Změřeno na listopadu 2026: přesun 0,90–1,29, nový kanál 1,66–4,54.
LEVEL_RATIO_MAX = 1.5
NEW_CHANNEL_EWS_MIN = 50.0
# Epidemický měsíc (celá ČR nad EPIDEMIC_FACTOR × medián téhož kalendářního měsíce
# a aspoň EPIDEMIC_MIN případů) se vynechá z baseline všech řad diagnózy. Převážení
# odlehlých bodů ve farrington_score nestačí, když epidemie zabere třetinu krátké
# historie: černý kašel 2024 (12 580 případů v týdnech 27–45 proti 34–184) zvedl
# mez normy na 7 277 měsíčně.
EPIDEMIC_FACTOR = 4.0
EPIDEMIC_MIN = 10

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

    if "EWS" not in df.columns:          # starší snapshoty sloupec nemají
        df["EWS"] = 0
    df = df.rename(columns={"EWS": "ews"})

    grouped = (df.groupby(["diagnoza", "diagnoza_nazev", "kraj_kod", "kraj_nazev",
                           "rok", "mesic"])[["pocet_pripadu", "ews"]].sum().reset_index())

    cz = (df.groupby(["diagnoza", "diagnoza_nazev", "rok", "mesic"])[["pocet_pripadu", "ews"]]
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


REGION_NAMES = {
    "CZ": "Česká republika", "CZ010": "Hl. m. Praha", "CZ020": "Středočeský kraj",
    "CZ031": "Jihočeský kraj", "CZ032": "Plzeňský kraj", "CZ041": "Karlovarský kraj",
    "CZ042": "Ústecký kraj", "CZ051": "Liberecký kraj", "CZ052": "Královéhradecký kraj",
    "CZ053": "Pardubický kraj", "CZ063": "Kraj Vysočina", "CZ064": "Jihomoravský kraj",
    "CZ071": "Olomoucký kraj", "CZ072": "Zlínský kraj", "CZ080": "Moravskoslezský kraj",
}


def load_weekly_series(csv_path: Path | None = None,
                       state_path: Path | None = None) -> tuple[pd.DataFrame, list[str], int, dict]:
    """
    Měsíční řady z týdenního souhrnu ÚZIS — stejný tvar jako `load_series`, navíc
    popis vstupu (stav dat u zdroje, názvy diagnóz).

    Proč vlastní řada a ne prodloužení otevřeného CSV: měsíc přepočtený z týdnů je
    odhad (weekly_months.py), u tří diagnóz má souhrn užší kód než CSV (A04.5,
    A37.0, A69.2) a součet krajů v něm od března 2026 nedává celou ČR. Historie
    i hodnocený měsíc proto vznikají stejně a z jednoho zdroje — za cenu kratší
    historie (souhrn nese posledních pět let).

    Řada „CZ“ je list ČR ze zdroje, ne součet krajů: část letošních záznamů kraj
    nemá (methodology_changes.yaml, `isin-tydenni-kraje-bez-kraje`).
    """
    csv_path = csv_path or (DATA_DIR / "isin" / "isin_tydenni.csv")
    state_path = state_path or (DATA_DIR / "isin" / "isin_tydenni_stav.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    weekly = pd.read_csv(csv_path)

    # „Stav k datu 15. 11. 05:34“ = hlášení načtená do předchozího dne.
    data_until = date.fromisoformat(state["stav_k"][:10]) - timedelta(days=1)
    long, months = weekly_months.to_months(weekly, data_until)

    unknown = set(long["kraj_kod"]) - set(REGION_NAMES)
    if unknown:
        raise ValueError(f"týdenní souhrn má neznámé území: {sorted(unknown)}")
    curated = yaml.safe_load(NAMES_FILE.read_text(encoding="utf-8")) if NAMES_FILE.exists() else {}
    names = {}
    for code, official in state["diagnozy"].items():
        short = curated.get(code) or {}
        names[code] = {"cs": short.get("cs") or official["cs"].capitalize(),
                       "en": short.get("en") or official["en"],
                       "official": official}
    long["diagnoza_nazev"] = long["diagnoza"].map(lambda c: names[c]["cs"])
    long["kraj_nazev"] = long["kraj_kod"].map(REGION_NAMES)

    periods = [f"{y:04d}-{m:02d}" for y, m in months]
    index = {p: i for i, p in enumerate(periods)}
    long["t"] = (long["rok"].map("{:04d}".format) + "-" + long["mesic"].map("{:02d}".format)).map(index)
    info = {"slozka": state.get("slozka"), "stav_k": state["stav_k"], "diagnozy": names,
            "data_until": data_until.isoformat()}
    return long, periods, len(periods), info


def build_grid(long: pd.DataFrame, n_months: int):
    """Generátor (meta, counts) — kompletní mřížka s nulami tam, kde chybí řádek.
    Chybějící měsíc v agregovaném hlášení znamená nula případů, ne díru v datech."""
    for (dg, dg_name, kraj, kraj_name), g in long.groupby(
            ["diagnoza", "diagnoza_nazev", "kraj_kod", "kraj_nazev"]):
        counts = np.zeros(n_months)
        counts[g.t.to_numpy()] = g.pocet_pripadu.to_numpy()
        yield {"diagnoza": dg, "diagnoza_nazev": dg_name,
               "kraj_kod": kraj, "kraj_nazev": kraj_name}, counts


# ── nový kanál hlášení (EWS) ─────────────────────────────────────────────────
#
# ÚZIS od července 2025 přijímá případy i přes hlášení EWS; sloupec `EWS` říká,
# kolik případů řádku tudy přišlo (do 6/2025 všude nula, za 2. pololetí 2025
# přes 25 tisíc). U pásového oparu tak přišlo 62 % případů, u boreliózy 59 %,
# u mononukleózy 51 %. Řada tím skokově vzroste, aniž by nemocných přibylo,
# a detektor to bez téhle znalosti ohlásí jako ohnisko.
#
# Prosté odečtení EWS nestačí: část případů se do nového kanálu PŘELILA ze
# starého (pásový opar bez EWS klesl z ~340 na ~175 měsíčně). Počet srovnatelný
# s historií proto neznáme — víme jen, že leží mezi (nahlášeno − EWS) a nahlášeno.
# Z toho plyne poctivé rozhodnutí o každém signálu:
#
#   (nahlášeno − EWS) nad prahem → signál platí, ať se přelilo cokoli („robust“)
#   jinak                        → nelze rozhodnout; nárůst může být jen nový kanál
#
# Předpoklad: baseline je z doby před EWS. Dnes platí (posledních 6 měsíců se do
# baseline nepočítá). Až do ní měsíce s EWS vstoupí, zvednou očekávanou hladinu
# i práh — verdikt „robust“ tím zůstane konzervativní, jen jich ubude.

def ews_grid(long: pd.DataFrame, n_months: int) -> dict:
    """(diagnóza, kraj) → počty případů z kanálu EWS po měsících; stejná mřížka jako build_grid."""
    grid = {}
    for (dg, kraj), g in long.groupby(["diagnoza", "kraj_kod"]):
        ews = np.zeros(n_months)
        ews[g.t.to_numpy()] = g.ews.to_numpy()
        grid[(dg, kraj)] = ews
    return grid


def channel_verdict(res: dict, ews: float) -> dict | None:
    """Verdikt o signálu s ohledem na kanál EWS; None = signálu se kanál netýká."""
    if not res["signal"] or ews <= 0:
        return None
    lower = res["observed"] - ews                       # dolní mez srovnatelného počtu
    robust = lower >= RARE_ALERT if res["type"] == "rare" else lower > res["threshold"]
    return {"ews": float(ews), "share": round(float(ews / res["observed"]), 2),
            "lower_bound": float(lower), "robust": bool(robust)}


def _national(long: pd.DataFrame, dg: str, n: int) -> tuple[np.ndarray, np.ndarray]:
    g = long[(long["kraj_kod"] == "CZ") & (long["diagnoza"] == dg)]
    counts, ews = np.zeros(n), np.zeros(n)
    counts[g["t"].to_numpy()] = g["pocet_pripadu"].to_numpy()
    ews[g["t"].to_numpy()] = g["ews"].to_numpy()
    return counts, ews


def classify_weekly(long: pd.DataFrame, periods: list[str], t0: int) -> dict:
    """
    Diagnóza → jak na ni působí EWS a co z toho plyne pro hodnocení měsíce t0:
    {"group", "level_ratio", "ews_share", "epidemic", "exclude", "range"}.

    exclude  měsíce mimo baseline: epidemické měsíce před EWS_START a u skupin
             „nejiste“ a „novy_kanal“ i všechny měsíce od EWS_START
    range    kalendářní měsíc → (min, max) celostátního počtu v letech před EWS
             bez epidemických měsíců — „obvyklé rozpětí“ pro graf na portálu
    Skupiny viz LEVEL_RATIO_MAX výše.
    """
    n = len(periods)
    start = next((i for i, p in enumerate(periods) if p >= EWS_START), n)
    out = {}
    for dg in sorted(long["diagnoza"].unique()):
        counts, ews = _national(long, dg, n)
        by_month: dict[int, list[float]] = {}
        for t in range(start):
            by_month.setdefault(t % 12, []).append(counts[t])
        med = {m: float(np.median(v)) for m, v in by_month.items()}
        epidemic = [t for t in range(start)
                    if counts[t] >= EPIDEMIC_MIN and counts[t] > EPIDEMIC_FACTOR * med[t % 12]]
        rng_: dict[int, list[float]] = {}
        for t in range(start):
            if t not in epidemic:
                rng_.setdefault(t % 12, []).append(counts[t])

        lo12 = max(0, t0 - 11)
        total12 = counts[lo12:t0 + 1].sum()
        share = 100.0 * ews[lo12:t0 + 1].sum() / total12 if total12 else 0.0
        if t0 >= start:
            base = sum(med.get(t % 12, 0.0) for t in range(start, t0 + 1))
            ratio = float(counts[start:t0 + 1].sum() / base) if base > 0 else None
        else:
            ratio = None

        if t0 < start or share < EWS_BASELINE_MAX:
            group = "bez_zmeny"
        elif ratio is not None and ratio <= LEVEL_RATIO_MAX:
            group = "presun"
        elif share >= NEW_CHANNEL_EWS_MIN:
            group = "novy_kanal"
        else:
            group = "nejiste"

        # U přesunu jsou měsíce s EWS srovnatelné a v krátké historii jsou potřeba
        # (bez nich černému kašli po vynechání epidemie 2024 zbudou dva roky).
        exclude = set(epidemic)
        if group in ("nejiste", "novy_kanal"):
            exclude.update(range(start, n))
        out[dg] = {"group": group,
                   "level_ratio": round(ratio, 2) if ratio is not None else None,
                   "ews_share": round(share, 1),
                   "epidemic": [periods[t] for t in epidemic],
                   "exclude": frozenset(exclude),
                   "range": {m: (min(v), max(v)) for m, v in rng_.items()}}
    return out


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
                model="plny", phase=0, alpha=FDR_ALPHA, provenance=None, *,
                name="anomaly_signals", rules=None, series_exclude=None,
                annotate=labels_en.annotate_signal, extra_fields=None,
                no_channel=frozenset()) -> int:
    """
    Oskóruje poslední měsíc všech řad a zapíše `<name>.json`.

    Pojmenované parametry slouží týdennímu zdroji (viz `main`): vlastní jméno
    výstupu, vlastní pravidla registru, měsíce mimo baseline po diagnózách
    (`series_exclude`), anglické názvy, pole přidaná do výstupu
    (`extra_fields(records, signals)` → dict) a diagnózy, u kterých se signál
    posuzuje na celkovém počtu bez verdiktu o kanálu EWS (`no_channel`).
    """
    t0 = n_months - 1
    rng = np.random.default_rng(PI_SEED)
    rules = load_registry() if rules is None else rules
    series_exclude = series_exclude or {}
    records, skipped, prebaselining = [], 0, []
    ews = ews_grid(long, n_months)

    for meta, counts in build_grid(long, n_months):
        static_ex, breaks, active = registry_masks(meta, periods, rules)
        extra = masks_for_t0(static_ex, breaks, t0) | series_exclude.get(meta["diagnoza"], frozenset())
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
        ews_t0 = float(ews[(meta["diagnoza"], meta["kraj_kod"])][t0])
        channel = None if meta["diagnoza"] in no_channel else channel_verdict(res, ews_t0)
        if channel:
            res["reporting_channel"] = channel
        records.append({**meta, **res, "ews_observed": ews_t0})

    k_fdr, k_bh = apply_fdr(records, alpha)
    # `ews_observed` slouží jen přehledu týdenního zdroje; signál nese EWS v `reporting_channel`.
    signals = [
        {k: v for k, v in r.items() if k not in ("signal", "ews_observed")}
        for r in records if r["signal"]
    ]
    # Nahoře signály, které platí bez ohledu na nový kanál hlášení; nerozhodnutelné
    # pod nimi — jinak by tabulce vévodily nárůsty, za kterými epidemie být nemusí.
    def undecided(s):
        return "reporting_channel" in s and not s["reporting_channel"]["robust"]
    signals.sort(key=lambda s: (undecided(s), s["score"] is None, -(s["score"] or 0), -s["observed"]))

    out = {
        "generated_at": (provenance or {}).get("as_of")
                        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target_period": periods[t0],
        "method": "Farrington–Noufaily, kvazi-Poisson GLM, 99. percentil, "
                  "π parametrickým bootstrapem, bayesovská FDR",
        "method_en": "Farrington–Noufaily, quasi-Poisson GLM, 99th percentile, "
                     "π by parametric bootstrap, Bayesian FDR",
        "n_series_scored": len(records),
        "n_series_skipped": skipped,
        "n_signals": len(signals),
        "n_signals_undecided": sum(1 for s in signals if undecided(s)),
        "fdr": {"alpha": alpha, "n_tested": len(records),
                "n_pass": k_fdr, "n_pass_bh": k_bh},
        "prebaselining": [annotate(r) for r in prebaselining],
        "signals": [annotate(s) for s in signals],
    }
    if extra_fields:
        out.update(extra_fields(records, signals))
    if provenance:
        out["provenance"] = provenance
    CHARTS_OUT.mkdir(parents=True, exist_ok=True)
    path = CHARTS_OUT / f"{name}.json"
    # `meta` nese mimo jiné caveaty ze stejného registru metodických změn, se
    # kterým pracuje detektor. U signálů je to nejpotřebnější: kdo je čte bez
    # vědomí o zlomu v hlášení (EWS od 7/2025), vydá změnu kanálu za epidemii.
    path.write_text(json.dumps(chart_meta.attach(name, out), ensure_ascii=False),
                    encoding="utf-8")
    # Zpětný přepočet nad starým snapshotem (--as-of) neříká nic o dnešním zdroji.
    if not (provenance or {}).get("as_of"):
        last_checked.mark_chart(name)
    n_sig_fdr = sum(1 for r in signals if r.get("fdr_pass"))
    print(f"[{periods[t0]}] oskórováno {len(records)} řad ({skipped} přeskočeno), "
          f"signálů {len(signals)} (po FDR α={alpha}: {n_sig_fdr}; BH: {k_bh}; "
          f"nerozhodnutelných kvůli kanálu EWS: {out['n_signals_undecided']}) → {path}")
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
                 alpha=FDR_ALPHA, *, out_name="anomaly_backtest", rules=None,
                 series_exclude_at=None) -> int:
    if diagnoza:
        long = long[long.diagnoza_nazev == diagnoza]
        if long.empty:
            print(f"Diagnóza „{diagnoza}“ v datech není.", file=sys.stderr)
            return 1
    rng = np.random.default_rng(PI_SEED)
    rules = load_registry() if rules is None else rules
    rows = []
    start = 12 * MIN_YEARS  # skórovat lze až s MIN_YEARS lety historie
    per_t0 = {t0: series_exclude_at(t0) for t0 in range(start, n_months)} if series_exclude_at else {}
    ews = ews_grid(long, n_months)
    for meta, counts in build_grid(long, n_months):
        static_ex, breaks, active = registry_masks(meta, periods, rules)
        for t0 in range(start, n_months):
            own = per_t0.get(t0, {}).get(meta["diagnoza"], frozenset())
            merged = frozenset(exclude_t | masks_for_t0(static_ex, breaks, t0) | own)
            res = farrington_score(counts, t0, merged, model, phase, rng=rng)
            if res is None:
                continue
            note = registry_annotation(active, t0)
            if note:
                res["metodicka_zmena"] = note
            channel = channel_verdict(res, ews[(meta["diagnoza"], meta["kraj_kod"])][t0])
            res["ews"] = float(ews[(meta["diagnoza"], meta["kraj_kod"])][t0])
            res["robust"] = channel["robust"] if channel else None
            rows.append({**meta, "period": periods[t0], **res})

    # FDR se aplikuje průřezově v rámci každého období — tak, jak by běžela naživo.
    by_period: dict = {}
    for r in rows:
        by_period.setdefault(r["period"], []).append(r)
    for period_rows in by_period.values():
        apply_fdr(period_rows, alpha)

    out_dir = DATA_DIR / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{out_name}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    n_sig = sum(r["signal"] for r in rows)
    n_fdr = sum(1 for r in rows if r.get("fdr_pass"))
    print(f"Backtest: {len(rows)} skóre, {n_sig} signálů, {n_fdr} po FDR (α={alpha}) → {path}")
    return 0


GROUP_TEXT = {
    "presun":     ("hlášení se do EWS přesunula, celkový počet je s minulými roky srovnatelný",
                   "reports moved to EWS; the total is comparable with previous years"),
    "novy_kanal": ("EWS zachytil případy, které se dřív nehlásily; s minulými roky se nesrovnává",
                   "EWS captured cases that used to go unreported; not compared with previous years"),
    "nejiste":    ("úroveň vzrostla, ale EWS tvoří menšinu; signál platí jen bez případů z EWS",
                   "the level rose but EWS is a minority; a signal holds only without EWS cases"),
    "bez_zmeny":  ("EWS se diagnózy skoro netýká", "EWS barely concerns this diagnosis"),
}


def national_overview(records: list[dict], names: dict, long: pd.DataFrame, periods: list[str],
                      classes: dict, months_shown: int = 12) -> list[dict]:
    """
    Stav celostátní řady každé diagnózy v hodnoceném měsíci — i těch bez signálu —
    a posledních `months_shown` měsíců s obvyklým rozpětím pro graf.

    status
      v_norme           pod mezí normy
      signal            nad mezí normy (u skupiny „nejiste“ i po odečtení EWS)
      nelze_rozhodnout  nad mezí jen díky případům z EWS (skupina „nejiste“)
      zmena_hlaseni     nehodnotí se — skupina „novy_kanal“
      nehodnoceno       řadu nešlo oskórovat (málo historie)
    """
    n = len(periods)
    t0 = n - 1
    by_code = {r["diagnoza"]: r for r in records if r["kraj_kod"] == "CZ"}
    out = []
    for code, name in names.items():
        cls = classes.get(code, {"group": "bez_zmeny", "range": {}})
        counts, ews = _national(long, code, n)
        months = []
        for t in range(max(0, n - months_shown), n):
            lo, hi = cls["range"].get(t % 12, (None, None))
            months.append({"period": periods[t], "n": int(counts[t]), "ews": int(ews[t]),
                           "lo": None if lo is None else int(lo), "hi": None if hi is None else int(hi)})
        row = {"diagnoza": code, "diagnoza_nazev": name["cs"], "diagnoza_nazev_en": name["en"],
               "group": cls["group"], "group_text": GROUP_TEXT[cls["group"]][0],
               "group_text_en": GROUP_TEXT[cls["group"]][1],
               "level_ratio": cls.get("level_ratio"), "ews_share_12m": cls.get("ews_share"),
               "epidemic_months": cls.get("epidemic", []),
               "observed": float(counts[t0]), "ews": float(ews[t0]),
               "ews_share": round(float(ews[t0] / counts[t0]), 2) if counts[t0] else None,
               "months": months}
        r = by_code.get(code)
        if cls["group"] == "novy_kanal":
            out.append({**row, "status": "zmena_hlaseni"})
            continue
        if r is None:
            out.append({**row, "status": "nehodnoceno"})
            continue
        channel = r.get("reporting_channel")
        if not r["signal"]:
            status = "v_norme"
        elif channel and not channel["robust"]:
            status = "nelze_rozhodnout"
        else:
            status = "signal"
        out.append({**row, "status": status, "type": r["type"], "expected": r["expected"],
                    "threshold": r["threshold"], "score": r["score"], "n_baseline": r["n_baseline"]})
    return out


def run_weekly(args) -> int:
    """
    --zdroj tydenni: 11 diagnóz týdenního souhrnu ÚZIS do posledního uzavřeného měsíce.

    Vedlejší výstup: když souhrn není stažený, nic se nezapíše a běh končí nulou —
    hlavní signály (anomaly_signals.json) na něm nezávisí.
    """
    csv_path = DATA_DIR / "isin" / "isin_tydenni.csv"
    state_path = DATA_DIR / "isin" / "isin_tydenni_stav.json"
    if args.as_of:
        print("--as-of týdenní zdroj nepodporuje (běží jen nad posledním staženým souhrnem).", file=sys.stderr)
        return 2
    if not csv_path.exists() or not state_path.exists():
        print("  [anomaly_signals_weekly] týdenní souhrn ÚZIS není stažený — přeskakuji")
        return 0
    long, periods, n_months, info = load_weekly_series(csv_path, state_path)
    names = info["diagnozy"]
    phase = int(periods[0][5:7]) - 1
    # Registr metodických změn má pravidla psaná pro řady otevřeného CSV (podle
    # úředních názvů ISIN). Zlom EWS od 7/2025 tu řeší `classify_weekly`.
    rules: list[dict] = []

    if args.backtest:
        if args.diagnoza:
            long = long[long.diagnoza_nazev == args.diagnoza]
            if long.empty:
                print(f"Diagnóza „{args.diagnoza}“ v týdenním souhrnu není.", file=sys.stderr)
                return 1
        return run_backtest(long, periods, n_months, None, frozenset(), args.model, phase, args.alpha,
                            out_name="anomaly_backtest_weekly", rules=rules,
                            series_exclude_at=lambda t0: {dg: c["exclude"] for dg, c in
                                                          classify_weekly(long, periods, t0).items()})

    t0 = n_months - 1
    classes = classify_weekly(long, periods, t0)
    groups = {dg: c["group"] for dg, c in classes.items()}
    # Diagnózy s novým kanálem se neskórují vůbec: jejich signál by byl jen ozvěna změny hlášení.
    scored = long[long["diagnoza"].map(groups) != "novy_kanal"]
    no_channel = frozenset(dg for dg, g in groups.items() if g == "presun")

    def annotate(rec: dict) -> dict:
        rec["diagnoza_nazev_en"] = names[rec["diagnoza"]]["en"]
        kraj = labels_en.region(rec.get("kraj_kod", ""), full=True)
        if kraj:
            rec["kraj_nazev_en"] = kraj
        return rec

    def extra_fields(records: list[dict], signals: list[dict]) -> dict:
        return {
            "input": {
                "source": "uzis-isin-weekly",
                "folder": info["slozka"],
                "data_as_of": info["stav_k"],
                "history_from": periods[0],
                "months": "odhad z týdnů: týden na přelomu měsíce se dělí podle pracovních dnů",
                "months_en": "estimated from weeks: a week across two months is split by working days",
                "ews_start": EWS_START,
                "level_ratio_max": LEVEL_RATIO_MAX,
                "new_channel_ews_min_percent": NEW_CHANNEL_EWS_MIN,
                "epidemic_factor": EPIDEMIC_FACTOR,
            },
            "diagnoses": [{"code": code, "name": {"cs": n["cs"], "en": n["en"]}, "official": n["official"]}
                          for code, n in sorted(names.items())],
            "national": national_overview(records, names, long, periods, classes),
        }

    provenance = build_provenance(csv_path, None, args.alpha)
    provenance["params"].update({"ews_start": EWS_START, "ews_baseline_max": EWS_BASELINE_MAX,
                                 "level_ratio_max": LEVEL_RATIO_MAX,
                                 "new_channel_ews_min": NEW_CHANNEL_EWS_MIN,
                                 "epidemic_factor": EPIDEMIC_FACTOR, "epidemic_min": EPIDEMIC_MIN})
    return run_current(scored, periods, n_months, frozenset(), args.model, phase, args.alpha, provenance,
                       name="anomaly_signals_weekly", rules=rules,
                       series_exclude={dg: c["exclude"] for dg, c in classes.items()},
                       annotate=annotate, extra_fields=extra_fields, no_channel=no_channel)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zdroj", choices=["mesicni", "tydenni"], default="mesicni",
                    help="mesicni = otevřené CSV ISIN, všechny diagnózy (default, anomaly_signals.json); "
                         "tydenni = týdenní souhrn ÚZIS, 11 diagnóz do posledního uzavřeného měsíce "
                         "(anomaly_signals_weekly.json)")
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

    if args.zdroj == "tydenni":
        return run_weekly(args)

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
