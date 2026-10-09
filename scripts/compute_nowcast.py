"""
Nowcast týdenních laboratorních detekcí (graf flu_weekly) → flu_nowcast.json.

STÍNOVÝ REŽIM (od 10/2026): odhad se počítá a ukládá, ale portál ho nezobrazuje
(`"display": false`). Každý odhad se zapíše do logu $DATA_DIR/nowcast/flu_nowcast_log.csv
a jakmile je týden starý D_FINAL týdnů, porovná se s doplněným číslem. Po 8–10
týdnech sezóny se podle `shadow_evaluation` rozhodne, jestli a jak odhad zobrazit
(docs/analytics/nowcasting.md).

Data:
  - faktory doplnění: snapshoty ERVISS ($DATA_DIR/ecdc/erviss_snapshots/, scraper
    ecdc_erviss_snapshots.py) — non-sentinel detekce, táž čísla jako SZÚ
  - aktuální nahlášená čísla: szu/szu_weekly_viry.csv — přesně to, co ukazuje graf

Řady: chřipka A, chřipka B, RSV. SARS-CoV-2 ne: je kompletní hned při prvním
zveřejnění (98 % konečného), není co odhadovat.

Selhání tohohle kroku nesmí shodit obnovu dat — v Dockerfile je za `||`.

Použití:
    python scripts/compute_nowcast.py
    python scripts/compute_nowcast.py --as-of 2026-11-20   # jako by byl ten den

Proměnné prostředí: DATA_DIR, OUTPUT_DIR — stejný kontrakt jako generate_json.py.
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import chart_meta
import nowcast

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "site" / "static" / "data" / "charts")))

# Názvy řad = názvy datasetů v flu_weekly.json, ať se dají spárovat na frontendu.
# ERVISS od 2024 nedává součet za typ, jen subtypy — sčítá se všechno kromě "total".
SERIES = {
    "Influenza A (celkem)": {
        "erviss": {"pathogentype": "Influenza A", "sum_subtypes": True},
        "szu": ["Influenza A", "Influenza A/H1N1pdm", "Influenza A/H3N2", "Influenza A/H1"],
    },
    "Influenza B": {
        "erviss": {"pathogentype": "Influenza B", "sum_subtypes": True},
        "szu": ["Influenza B"],
    },
    "RSV": {
        "erviss": {"pathogentype": "RSV", "sum_subtypes": False},
        "szu": ["RSV"],
    },
}

LOG_COLUMNS = ["series", "week", "d", "as_of", "reported"] + [f"q{lv}" for lv in nowcast.LEVELS]
SEED = 20261009   # pevné: hodinové běhy nad stejnými daty dají stejný JSON (jinak commit každou hodinu)


def iso_week_start(year: int, week: int) -> date:
    return date.fromisocalendar(int(year), int(week), 1)


def week_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"KT {w}/{str(y)[2:]}"


def load_erviss_triangles(snap_dir: Path) -> dict[str, pd.DataFrame]:
    """Trojúhelník week × snapshot pro každou řadu ze zrcadla snapshotů ERVISS."""
    frames = []
    for p in sorted(snap_dir.glob("*_nonSentinelTestsDetections.csv.gz")):
        df = pd.read_csv(p)
        if df.empty:
            continue
        df = df[(df["age"] == "total") & (df["indicator"] == "detections")]
        df["snapshot"] = date.fromisoformat(p.name[:10])
        frames.append(df)
    if not frames:
        return {}
    allrows = pd.concat(frames, ignore_index=True)
    allrows["week"] = [iso_week_start(y[:4], y[6:]) for y in allrows["yearweek"]]

    out = {}
    for name, spec in SERIES.items():
        e = spec["erviss"]
        sel = allrows["pathogentype"] == e["pathogentype"]
        sel &= (allrows["pathogensubtype"] != "total") if e["sum_subtypes"] else \
            (allrows["pathogensubtype"] == e["pathogentype"])
        obs = allrows[sel].groupby(["week", "snapshot"], as_index=False)["value"].sum()
        out[name] = nowcast.triangle(obs)
    return out


def load_szu(path: Path) -> pd.DataFrame:
    """Nahlášené týdenní počty SZÚ po řadách; týden, který v matici je, má u chybějící řady 0."""
    s = pd.read_csv(path)
    s["week"] = [iso_week_start(r, t) for r, t in zip(s["rok"], s["tyden"])]
    weeks = sorted(s["week"].unique())
    rows = []
    for name, spec in SERIES.items():
        per_week = s[s["virus"].isin(spec["szu"])].groupby("week")["pocet"].sum()
        rows += [{"series": name, "week": w, "reported": float(per_week.get(w, 0))} for w in weeks]
    return pd.DataFrame(rows)


def update_log(log_path: Path, new_rows: list[dict]) -> pd.DataFrame:
    """Jeden záznam na (řada, týden, zpoždění) — platí poslední odhad, který graf ukázal."""
    parts = [pd.read_csv(log_path)] if log_path.exists() else []
    if new_rows:
        parts.append(pd.DataFrame(new_rows, columns=LOG_COLUMNS))
    log = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=LOG_COLUMNS)
    log = log.drop_duplicates(subset=["series", "week", "d"], keep="last")
    log = log.sort_values(["series", "week", "d"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log.to_csv(log_path, index=False)
    return log


def shadow_evaluation(log: pd.DataFrame, szu: pd.DataFrame, as_of: date) -> dict:
    """Odhady ze stínového režimu proti číslům, která se mezitím doplnila."""
    if log.empty:
        return {}
    final = szu.assign(week=szu["week"].astype(str)).set_index(["series", "week"])["reported"]
    out = {}
    for name, g in log.groupby("series"):
        # .loc s maskou: prázdný seznam v g[[...]] by pandas vzal jako výběr sloupců
        ripe = g.loc[np.array([nowcast.delay_weeks(date.fromisoformat(w), as_of) >= nowcast.D_FINAL
                               and (name, w) in final.index for w in g["week"]], dtype=bool)]
        res = ripe.assign(final=[float(final[(name, w)]) for w in ripe["week"]])
        if not res.empty:
            levels = list(nowcast.LEVELS)
            res["wis_nowcast"] = [nowcast.weighted_interval_score(
                levels, [r[f"q{lv}"] for lv in levels], r["final"]) for _, r in res.iterrows()]
            res["wis_reported"] = (res["final"] - res["reported"]).abs()
        out[name] = {**nowcast.summarize(res), "pending": int(len(g) - len(ripe))}
    return out


def compute(as_of: date) -> dict | None:
    snap_dir = DATA_DIR / "ecdc" / "erviss_snapshots"
    szu_path = DATA_DIR / "szu" / "szu_weekly_viry.csv"
    if not szu_path.exists():
        print("  [flu_nowcast] chybí szu/szu_weekly_viry.csv — přeskakuji")
        return None
    triangles = load_erviss_triangles(snap_dir)
    if not triangles:
        print(f"  [flu_nowcast] žádné snapshoty ERVISS v {snap_dir} — přeskakuji")
        return None

    szu = load_szu(szu_path)
    rng = np.random.default_rng(SEED)
    series_out, log_rows = {}, []
    for name, tri in triangles.items():
        model = nowcast.fit(tri, as_of)
        weeks = []
        cur = szu[szu["series"] == name]
        for _, r in cur.sort_values("week").iterrows():
            d = nowcast.delay_weeks(r["week"], as_of)
            if d not in nowcast.NOWCAST_DELAYS or not nowcast.in_season(r["week"]):
                continue
            q = nowcast.quantiles(r["reported"], model, d, rng)
            weeks.append({
                "week": r["week"].isoformat(), "label": week_label(r["week"]), "delay": d,
                "reported": int(r["reported"]),
                "quantiles": {str(lv): round(float(v), 1) for lv, v in zip(nowcast.LEVELS, q)},
            })
            log_rows.append({"series": name, "week": r["week"].isoformat(), "d": d,
                             "as_of": as_of.isoformat(), "reported": r["reported"],
                             **{f"q{lv}": float(v) for lv, v in zip(nowcast.LEVELS, q)}})
        bt = nowcast.backtest(tri)
        series_out[name] = {
            "weeks": weeks,
            "model": {
                "factors": {str(d): round(model.factors[d], 3) for d in nowcast.NOWCAST_DELAYS},
                "dispersion": {str(d): round(model.dispersion[d], 2) for d in nowcast.NOWCAST_DELAYS},
                "training_weeks": {str(d): model.n_weeks[d] for d in nowcast.NOWCAST_DELAYS},
            },
            "backtest": {
                "all": nowcast.summarize(bt),
                "by_delay": {str(d): nowcast.summarize(bt[bt["d"] == d]) for d in nowcast.NOWCAST_DELAYS}
                if not bt.empty else {},
            },
        }

    log = update_log(DATA_DIR / "nowcast" / "flu_nowcast_log.csv", log_rows)
    snaps = sorted(p.name[:10] for p in snap_dir.glob("*_nonSentinelTestsDetections.csv.gz"))
    return {
        "status": "shadow",
        "display": False,
        "method": "chain-ladder doplnění hlášení, negativně binomická nejistota",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": as_of.isoformat(),
        "in_season": nowcast.in_season(as_of),
        "levels": list(nowcast.LEVELS),
        "params": {"d_final": nowcast.D_FINAL, "window_weeks": nowcast.WINDOW_WEEKS,
                   "delays": list(nowcast.NOWCAST_DELAYS)},
        "training_snapshots": {"n": len(snaps), "first": snaps[0], "last": snaps[-1]},
        "series": series_out,
        "shadow_evaluation": shadow_evaluation(log, szu, as_of),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: dnes)")
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    out = compute(as_of)
    if out is None:
        return 0
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "flu_nowcast.json"
    path.write_text(json.dumps(chart_meta.attach("flu_nowcast", out), ensure_ascii=False),
                    encoding="utf-8")
    for name, s in out["series"].items():
        now = ", ".join(f"{w['label']}: {w['reported']} → {w['quantiles']['0.5']:.0f}" for w in s["weeks"])
        b = s["backtest"]["all"]
        print(f"  [flu_nowcast] {name}: {now or 'nic k odhadu'}; backtest n={b.get('n')}, "
              f"zlepšení {b.get('improvement')}, pokrytí 95 % {b.get('coverage_95')}")
    print(f"  [flu_nowcast] → {path} (stínový režim, nezobrazuje se)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
