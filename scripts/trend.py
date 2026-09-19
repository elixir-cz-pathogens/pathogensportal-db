"""
Trend epidemie — roste, nebo klesá, a s jakou jistotou.

Vzor: CDC „Current Epidemic Trends“, které místo čísla Rt ukazuje kategorii
(roste / pravděpodobně roste / beze změny / pravděpodobně klesá / klesá)
odvozenou z pravděpodobnosti růstu. Číslo 1,08 čtenáři nic neřekne; „s 90%
pravděpodobností roste“ ano.

Proč tempo růstu, a ne Rt: reprodukční číslo patří jednomu patogenu se známým
generačním intervalem. ILI a ARI jsou klinické syndromy — směs chřipky, RSV,
covidu a rhinovirů s různými intervaly — a převod na Rt by byl přesný jen
naoko. Tempo růstu (o kolik procent týdně) žádný takový předpoklad nepotřebuje
a říká totéž: nad nulou epidemie roste, pod nulou klesá.

Metoda: vážená lineární regrese log(míry) na čase v klouzavém okně 3 týdnů.
Váhou je počet případů (rozptyl log-míry ≈ φ / případy — delta metoda pro
přerozptýlený Poisson), φ se odhaduje z reziduí s podlahou 1 jako v detektoru
anomálií. Pravděpodobnost růstu je P(sklon > 0) ze Studentova rozdělení
s n − 2 stupni volnosti — při třech bodech velmi těžké chvosty, které drží
jistotu na uzdě.

Délku okna rozhodl zpětný test (compute_mem.trend_blocks, sezóny bez pandemických
a bez svátečních týdnů): se třemi týdny jsou kategorie správně seřazené — po
„roste“ míra ILI další týden vzrostla v 69 % případů, po „pravděpodobně roste“
v 65 %, „beze změny“ 55 %, „pravděpodobně klesá“ 14 %, „klesá“ 11 %. Delší okna
(4, 5) to pořadí ztrácejí a kolem vrcholu hlásí „roste“ ještě dva týdny po
obratu. Aktuální čísla zpětného testu jsou vždy ve výstupním JSON.

Přes léto se ILI/ARI nehlásí; dokud po pauze nejsou tři souvislé týdny, trend
není (None) — sklon přes díru v datech by byl sklon odjinud.
Jen výpočet, žádné I/O.
"""

import numpy as np

WINDOW = 3                     # týdnů; zvoleno zpětným testem, viz výše
CATEGORIES = (                 # dolní mez P(růst) → kategorie (hranice CDC)
    (0.90, "growing"),
    (0.75, "likely_growing"),
    (0.25, "stable"),
    (0.10, "likely_declining"),
    (0.00, "declining"),
)
# Kolem Vánoc mají ordinace zavřeno: hlášených ILI/ARI ubude bez ohledu na
# epidemii a po Novém roce zase skokově přibude. Trend přes tyhle týdny je
# artefakt hlášení — počítá se, ale nese upozornění.
HOLIDAY_WEEKS = (52, 53, 1)


def _t_cdf(x: float, df: int) -> float:
    """Distribuční funkce Studentova t numerickou integrací hustoty (bez scipy)."""
    from math import gamma, pi, sqrt
    c = gamma((df + 1) / 2) / (sqrt(df * pi) * gamma(df / 2))
    grid = np.linspace(0.0, abs(x), 4001)
    area = np.trapezoid(c * (1 + grid ** 2 / df) ** (-(df + 1) / 2), grid)
    return float(0.5 + np.sign(x) * area)


def growth(cases, population) -> dict | None:
    """
    Tempo růstu z posledních týdnů (nejstarší první). Vrací None, když okno
    není úplné nebo v něm nejsou žádné případy.
    """
    cases = np.asarray(cases, dtype=float)
    population = np.asarray(population, dtype=float)
    n = len(cases)
    if n < 3 or np.isnan(cases).any() or np.isnan(population).any() or cases.sum() == 0:
        return None

    y = np.log((cases + 0.5) / population)             # +0,5: nulový týden nesmí dát log(0)
    w = cases + 0.5
    t = np.arange(n, dtype=float)
    X = np.column_stack([np.ones(n), t])
    xtwx_inv = np.linalg.inv(X.T @ (w[:, None] * X))
    beta = xtwx_inv @ X.T @ (w * y)
    df = n - 2
    phi = max(1.0, float((w * (y - X @ beta) ** 2).sum() / df))
    slope, se = float(beta[1]), float(np.sqrt(phi * xtwx_inv[1, 1]))

    p_growth = _t_cdf(slope / se, df)
    return {
        "weekly_change": float(np.expm1(slope)),          # +0,25 = o čtvrtinu víc než minulý týden
        "doubling_weeks": float(np.log(2) / abs(slope)) if abs(slope) > 1e-9 else None,
        "p_growth": p_growth,
        "category": next(name for bound, name in CATEGORIES if p_growth >= bound),
    }
