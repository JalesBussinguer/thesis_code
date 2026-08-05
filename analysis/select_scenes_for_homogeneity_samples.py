from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.validation import make_valid


ROOT_DIR = Path(__file__).resolve().parent.parent
SAMPLES_PATH = ROOT_DIR / "datasets" / "homogeneity_samples_paper_01.geojson"
NISAR_PATH = ROOT_DIR / "datasets" / "asf_scenes_nisar" / "nisar_scene_geometries.geojson"
S1_PATH = ROOT_DIR / "datasets" / "asf_scenes_sentinel1" / "sentinel-1_scene_geometries.geojson"
OUTPUT_DIR = ROOT_DIR / "datasets" / "selected_scenes_homogeneity"


@dataclass(frozen=True)
class SceneCandidate:
    scene_id: str
    time: pd.Timestamp


def load_samples(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")

    gdf["geometry"] = gdf.geometry.apply(make_valid)
    gdf = gdf.loc[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
    gdf = gdf.reset_index(drop=True)

    gdf["poly_id"] = gdf.apply(
        lambda r: f"{r.get('class', 'NA')}_{r.get('domain', 'NA')}_{r.get('sample', 'NA')}_{r.name}",
        axis=1,
    )
    return gdf[["poly_id", "class", "domain", "sample", "geometry"]].copy()


def load_scenes(path: Path, id_col: str, time_col: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")

    required = [id_col, time_col, "geometry"]
    missing = [c for c in required if c not in gdf.columns]
    if missing:
        raise ValueError(f"Colunas ausentes em {path}: {missing}")

    gdf = gdf[[id_col, time_col, "geometry"]].copy()
    gdf = gdf.rename(columns={id_col: "scene_id", time_col: "start_time"})
    gdf["scene_id"] = gdf["scene_id"].astype(str).str.strip()
    gdf["start_time"] = pd.to_datetime(gdf["start_time"], utc=True, errors="coerce")
    gdf["geometry"] = gdf.geometry.apply(make_valid)

    gdf = gdf.loc[
        gdf.geometry.notnull()
        & ~gdf.geometry.is_empty
        & gdf["scene_id"].ne("")
        & gdf["start_time"].notnull()
    ].copy()
    return gdf.reset_index(drop=True)


def build_candidates(samples: gpd.GeoDataFrame, scenes: gpd.GeoDataFrame) -> dict[str, list[SceneCandidate]]:
    candidates: dict[str, list[SceneCandidate]] = {}

    for _, row in samples.iterrows():
        poly = row.geometry
        poly_id = row["poly_id"]

        # Cenario exige poligono integralmente contido na cena.
        mask = scenes.geometry.covers(poly)
        sub = scenes.loc[mask, ["scene_id", "start_time"]].drop_duplicates()

        cand_list = [
            SceneCandidate(scene_id=sid, time=ts)
            for sid, ts in zip(sub["scene_id"].tolist(), sub["start_time"].tolist())
        ]
        candidates[poly_id] = cand_list

    return candidates


def choose_min_temporal_combination(candidates: dict[str, list[SceneCandidate]]):
    missing = [poly_id for poly_id, c in candidates.items() if not c]
    if missing:
        raise RuntimeError(
            "Ha poligonos sem cena cobrindo integralmente: "
            + ", ".join(missing[:20])
            + (" ..." if len(missing) > 20 else "")
        )

    all_times = sorted({cand.time for lst in candidates.values() for cand in lst})

    best = None

    for anchor in all_times:
        selected = {}
        selected_times = []

        for poly_id, lst in candidates.items():
            pick = min(
                lst,
                key=lambda c: (
                    abs((c.time - anchor).total_seconds()),
                    c.time,
                    c.scene_id,
                ),
            )
            selected[poly_id] = pick
            selected_times.append(pick.time)

        min_t = min(selected_times)
        max_t = max(selected_times)
        span_sec = float((max_t - min_t).total_seconds())
        total_dev_sec = float(sum(abs((t - anchor).total_seconds()) for t in selected_times))
        unique_scenes = len({v.scene_id for v in selected.values()})

        score = (span_sec, total_dev_sec, unique_scenes)
        if best is None or score < best["score"]:
            best = {
                "anchor": anchor,
                "score": score,
                "selected": selected,
            }

    return best


def write_outputs(
    sensor_name: str,
    samples: gpd.GeoDataFrame,
    chosen: dict[str, SceneCandidate],
    out_dir: Path,
    txt_field_name: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for _, s in samples.iterrows():
        poly_id = s["poly_id"]
        pick = chosen[poly_id]
        rows.append(
            {
                "poly_id": poly_id,
                "class": s["class"],
                "domain": s["domain"],
                "sample": s["sample"],
                "scene_time": pick.time.isoformat(),
                txt_field_name: pick.scene_id,
            }
        )

    detail = pd.DataFrame(rows).sort_values(by=["scene_time", "poly_id"]).reset_index(drop=True)

    unique_scene_df = (
        detail[[txt_field_name, "scene_time"]]
        .drop_duplicates()
        .sort_values(by=["scene_time", txt_field_name])
        .reset_index(drop=True)
    )

    txt_path = out_dir / f"{sensor_name.lower()}_{txt_field_name}_to_download.txt"
    csv_detail_path = out_dir / f"{sensor_name.lower()}_polygon_assignment.csv"

    txt_path.write_text("\n".join(unique_scene_df[txt_field_name].tolist()) + "\n", encoding="utf-8")
    detail.to_csv(csv_detail_path, index=False)


def run_for_sensor(
    sensor_name: str,
    scene_path: Path,
    id_col: str,
    time_col: str,
    txt_field_name: str,
    samples: gpd.GeoDataFrame,
    out_dir: Path,
):
    scenes = load_scenes(scene_path, id_col=id_col, time_col=time_col)
    candidates = build_candidates(samples, scenes)
    best = choose_min_temporal_combination(candidates)

    write_outputs(
        sensor_name=sensor_name,
        samples=samples,
        chosen=best["selected"],
        out_dir=out_dir,
        txt_field_name=txt_field_name,
    )

    span_sec, total_dev_sec, unique_scenes = best["score"]
    return {
        "sensor": sensor_name,
        "anchor_time": best["anchor"].isoformat(),
        "temporal_span_days": round(span_sec / 86400.0, 6),
        "total_deviation_days": round(total_dev_sec / 86400.0, 6),
        "unique_scenes": int(unique_scenes),
    }


def main() -> None:
    samples = load_samples(SAMPLES_PATH)

    summaries = []
    summaries.append(
        run_for_sensor(
            sensor_name="NISAR",
            scene_path=NISAR_PATH,
            id_col="scene_name",
            time_col="start_time",
            txt_field_name="scene_name",
            samples=samples,
            out_dir=OUTPUT_DIR,
        )
    )

    summaries.append(
        run_for_sensor(
            sensor_name="SENTINEL1",
            scene_path=S1_PATH,
            id_col="product_id",
            time_col="start_time",
            txt_field_name="product_id",
            samples=samples,
            out_dir=OUTPUT_DIR,
        )
    )

    summary_df = pd.DataFrame(summaries)
    summary_path = OUTPUT_DIR / "selection_summary.csv"
    summary_df.to_csv(summary_path, index=False)

    print(summary_df.to_string(index=False))
    print(f"\nArquivos gerados em: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
