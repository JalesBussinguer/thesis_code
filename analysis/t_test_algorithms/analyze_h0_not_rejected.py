"""Analisa separadamente, por sensor, os testes entre classes que NAO rejeitaram H0.

Para cada sensor gera, em results/<sensor>/h0_not_rejected_analysis/:
  - sample_stats.csv: estatisticas descritivas das amostras envolvidas vs. as demais da mesma classe
  - bootstrap_summary.csv: T observado vs. distribuicao bootstrap nula (quantis, valor critico)
  - bootstrap_<par>_gamma<g>.png: histograma bootstrap com T observado
"""
from pathlib import Path
import glob

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).parent / "results"
SAMPLES = ROOT / "datasets" / "samples_sbsr_bsb"
SENSORS = {"nisar": "nisar_samples", "sentinel_1": "sentinel_1_samples"}


def load_results(sensor: str) -> pd.DataFrame:
    files = glob.glob(str(RESULTS / sensor / "algorithm1_between_class_results_*gamma*.csv"))
    df = pd.concat(pd.read_csv(f) for f in files)
    df["reject_h0"] = df["reject_h0"].astype(str).eq("True")
    return df


def sample_stats(path: Path) -> dict:
    d = pd.read_csv(path)
    co, cross = d.iloc[:, 1], d.iloc[:, 2]
    return {
        "n": len(d),
        "mean_co": co.mean(), "median_co": co.median(), "cv_co": co.std() / co.mean(),
        "mean_cross": cross.mean(), "median_cross": cross.median(), "cv_cross": cross.std() / cross.mean(),
        "enl_co": co.mean() ** 2 / co.var(), "enl_cross": cross.mean() ** 2 / cross.var(),
        "corr": np.corrcoef(co, cross)[0, 1],
        "ratio_cross_co": cross.mean() / co.mean(),
    }


def analyze(sensor: str) -> None:
    df = load_results(sensor)
    nr = df[~df["reject_h0"]]
    out = RESULTS / sensor / "h0_not_rejected_analysis"
    out.mkdir(exist_ok=True)

    involved = sorted(set(nr["class_a_samples"]) | set(nr["class_b_samples"]))
    all_samples = sorted(p.stem for p in (SAMPLES / SENSORS[sensor]).glob("*.csv"))
    stats = pd.DataFrame({s: sample_stats(SAMPLES / SENSORS[sensor] / f"{s}.csv") for s in all_samples}).T
    stats.insert(0, "in_non_rejected", [s in involved for s in all_samples])
    stats.index.name = "sample"
    stats.to_csv(out / "sample_stats.csv")

    rows = []
    for _, r in nr.sort_values(["class_a_samples", "class_b_samples", "gamma"]).iterrows():
        pair, g = f"{r.class_a_samples}_x_{r.class_b_samples}", r.gamma
        boot_dir = glob.glob(str(RESULTS / sensor / f"bootstrap_distributions_between_samples_gamma{g}_*"))[0]
        boot = pd.read_csv(Path(boot_dir) / f"{pair}.csv")["T_bootstrap"].to_numpy()
        crit = np.quantile(boot, 1 - r.alpha)
        rows.append({
            "pair": pair, "gamma": g, "T_observed": r.T_observed, "p_value": r.p_value,
            "T_crit_95": crit, "T_obs_percentile": (boot < r.T_observed).mean() * 100,
            "boot_median": np.median(boot), "boot_p05": np.quantile(boot, 0.05), "boot_p95": crit,
        })
        fig, ax = plt.subplots(figsize=(5, 3))
        ax.hist(boot, bins=60, color="0.7")
        ax.axvline(r.T_observed, color="C3", label=f"T obs = {r.T_observed:.3g}")
        ax.axvline(crit, color="C0", ls="--", label=f"crit 95% = {crit:.3g}")
        ax.set(title=f"{sensor} {pair} γ={g} (p={r.p_value:.3f})", xlabel="T bootstrap")
        ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(out / f"bootstrap_{pair}_gamma{g}.png", dpi=150)
        plt.close(fig)
    summary = pd.DataFrame(rows)
    summary.to_csv(out / "bootstrap_summary.csv", index=False)

    pd.set_option("display.width", 250)
    print(f"\n===== {sensor}: {len(nr)}/{len(df)} testes sem rejeicao =====")
    print(summary.round(4).to_string(index=False))
    print("\nEstatisticas das amostras (classes envolvidas):")
    classes = {s.split("_")[0] for s in involved}
    print(stats[stats.index.str.split("_").str[0].isin(classes)].round(4).to_string())


SENSOR_LABELS = {"nisar": "NISAR", "sentinel_1": "Sentinel-1"}
POLS = {"nisar": ("HH", "HV"), "sentinel_1": ("VV", "VH")}


def _num(x: float, decimals: int = 2) -> str:
    if x != 0 and (abs(x) >= 1e5 or abs(x) < 1e-2):
        mantissa, exp = f"{x:.2e}".split("e")
        return f"${mantissa}\\times 10^{{{int(exp)}}}$"
    return f"{x:.{decimals}f}"


def _tex_table(caption: str, label: str, colspec: str, header: str, rows: list[str]) -> str:
    body = "\n".join(rows)
    return (
        "\\begin{table}[htbp]\n"
        f"\\caption{{{caption}}}\n\\label{{{label}}}\n\\scriptsize\n"
        "\\setlength{\\tabcolsep}{3pt}\n"
        "\\noindent\\resizebox{\\columnwidth}{!}{%\n"
        f"\\begin{{tabular}}{{{colspec}}}\n\\toprule\n{header} \\\\\n\\midrule\n{body}\n"
        "\\bottomrule\n\\end{tabular}%\n}\n\\end{table}\n"
    )


def write_latex_tables(sensor: str) -> None:
    out = RESULTS / sensor / "h0_not_rejected_analysis"
    name = SENSOR_LABELS[sensor]
    co, cross = POLS[sensor]
    tag = sensor.replace("_", "")

    summary = pd.read_csv(out / "bootstrap_summary.csv")
    rows = []
    for pair, grp in summary.groupby("pair", sort=True):
        a, b = pair.replace("_x_", " ").split()
        for i, r in enumerate(grp.itertuples()):
            first = f"\\multirow{{{len(grp)}}}{{*}}{{{a.replace('_', '\\_')} $\\times$ {b.replace('_', '\\_')}}}" if i == 0 else ""
            rows.append(
                f"{first} & {_num(r.gamma, 1)} & {_num(r.T_observed)} & {_num(r.T_crit_95)} "
                f"& {_num(r.T_obs_percentile, 1)} & {_num(r.p_value, 4)} \\\\"
            )
        rows.append("\\midrule")
    rows.pop()
    (out / "table_not_rejected_tests.tex").write_text(
        _tex_table(
            f"{name} between-class tests that did not reject $H_0$ ($\\alpha=0.05$).",
            f"tab:{tag}-not-rejected-tests",
            "@{}l r r r r r@{}",
            "Sample pair & $\\gamma$ & Observed $T$ & Critical $T$ (95\\%) & Percentile of $T$ & $p$-value",
            rows,
        ),
        encoding="utf-8",
    )

    stats = pd.read_csv(out / "sample_stats.csv")
    classes = {s.split("_")[0] for s in stats.loc[stats["in_non_rejected"], "sample"]}
    stats = stats[stats["sample"].str.split("_").str[0].isin(classes)]
    rows = []
    prev = None
    for r in stats.itertuples():
        cls = r.sample.split("_")[0]
        if prev is not None and cls != prev:
            rows.append("\\midrule")
        prev = cls
        mark = "$^{*}$" if r.in_non_rejected else ""
        md = 3 if sensor == "nisar" else 2
        rows.append(
            f"{r.sample.replace('_', '\\_')}{mark} & {_num(r.mean_co, md)} & {_num(r.cv_co)} & {_num(r.enl_co)} "
            f"& {_num(r.mean_cross, md)} & {_num(r.cv_cross)} & {_num(r.enl_cross)} & {_num(r.ratio_cross_co)} \\\\"
        )
    (out / "table_sample_stats.tex").write_text(
        _tex_table(
            f"Statistics of the {name} samples in the classes with tests that did not reject $H_0$. "
            "Samples marked with $^{*}$ took part in at least one non-rejected test.",
            f"tab:{tag}-not-rejected-samples",
            "@{}l r r r r r r r@{}",
            f"Sample & Mean {co} & CV {co} & ENL {co} & Mean {cross} & CV {cross} & ENL {cross} & Ratio {cross}/{co}",
            rows,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    for s in SENSORS:
        analyze(s)
        write_latex_tables(s)
