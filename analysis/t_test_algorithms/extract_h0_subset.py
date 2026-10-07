"""Extracts subsets of between-class tests by H0 decision (rejected / not rejected)."""
from pathlib import Path
import glob

import pandas as pd

RESULTS_DIR = Path(__file__).parent / "results"
SENSORS = ["nisar", "sentinel_1"]
COLS = ["class_a_code", "class_b_code", "class_a_samples", "class_b_samples",
        "gamma", "T_observed", "p_value", "alpha", "reject_h0"]


def load(sensor: str) -> pd.DataFrame:
    files = glob.glob(str(RESULTS_DIR / sensor / "algorithm1_between_class_results_*gamma*.csv"))
    df = pd.concat(pd.read_csv(f) for f in files)
    df["reject_h0"] = df["reject_h0"].astype(str).eq("True")
    return df


def main() -> None:
    for sensor in SENSORS:
        df = load(sensor)
        for label, mask in [("rejected", df["reject_h0"]), ("not_rejected", ~df["reject_h0"])]:
            subset = df[mask][COLS].sort_values(["class_a_samples", "class_b_samples", "gamma"])
            out = RESULTS_DIR / sensor / f"h0_subset_{label}.csv"
            subset.to_csv(out, index=False)
            print(f"{sensor} {label}: {len(subset)}/{len(df)} -> {out.name}")
        print(df.groupby(["class_a_code", "class_b_code"])["reject_h0"].agg(["sum", "count"]))


if __name__ == "__main__":
    main()
