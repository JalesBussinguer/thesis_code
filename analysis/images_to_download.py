

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.validation import make_valid

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT_DIR / "datasets" / "triple_satellite_intersection"

# ======================= #
# CONFIGURACAO DO USUARIO #
# ======================= #
SENTINEL1_SCENES_PATH = ROOT_DIR / "datasets" / "asf_scenes_sentinel1" / "sentinel-1_scene_geometries.geojson"
NISAR_SCENES_PATH = ROOT_DIR / "datasets" / "asf_scenes_nisar" / "nisar_scene_geometries.geojson"
BIOMASS_SCENES_PATH = ROOT_DIR / "datasets" / "biomass_scenes" / "biomass_scene_geometries.geojson"
OUTPUT_DIR = DEFAULT_OUTPUT_DIR

# Se definido (em horas), remove combinacoes com janela temporal maior que esse valor.
MAX_TEMPORAL_SPAN_HOURS: float | None = None


def load_scenes(path: Path, id_col: str, time_col: str, prefix: str) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {path}")

    gdf = gpd.read_file(path)
    if gdf.empty:
        raise ValueError(f"Arquivo sem feicoes: {path}")

    required = {id_col, time_col, "geometry"}
    missing = [col for col in required if col not in gdf.columns]
    if missing:
        raise ValueError(f"Colunas ausentes em {path}: {missing}")

    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")

    gdf = gdf[[id_col, time_col, "geometry"]].copy()
    gdf = gdf.rename(columns={id_col: f"{prefix}_id", time_col: f"{prefix}_time"})
    gdf[f"{prefix}_id"] = gdf[f"{prefix}_id"].astype(str).str.strip()
    gdf[f"{prefix}_time"] = pd.to_datetime(gdf[f"{prefix}_time"], utc=True, errors="coerce")
    gdf["geometry"] = gdf.geometry.apply(make_valid)

    gdf = gdf.loc[
        gdf.geometry.notnull()
        & ~gdf.geometry.is_empty
        & gdf[f"{prefix}_id"].ne("")
        & gdf[f"{prefix}_time"].notnull()
    ].copy()

    if gdf.empty:
        raise ValueError(f"Nenhuma cena valida apos limpeza: {path}")

    return gdf.reset_index(drop=True)


def geom_area_m2(geometry) -> float:
    # Projecao equivalente global para area em metros quadrados.
    series = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs("EPSG:6933")
    return float(series.area.iloc[0])


def export_geojson(gdf: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gdf.empty:
        path.write_text(
            json.dumps({"type": "FeatureCollection", "features": []}, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        return
    gdf.to_file(path, driver="GeoJSON")


def main() -> None:
    output_dir = Path(OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    s1 = load_scenes(SENTINEL1_SCENES_PATH, id_col="product_id", time_col="start_time", prefix="s1")
    nisar = load_scenes(NISAR_SCENES_PATH, id_col="product_id", time_col="start_time", prefix="nisar")
    biomass = load_scenes(BIOMASS_SCENES_PATH, id_col="scene_id", time_col="start_datetime", prefix="biomass")

    # 1) Interseccao espacial par-a-par entre Sentinel-1 e NISAR.
    pairs_sn = gpd.sjoin(
        s1[["s1_id", "s1_time", "geometry"]],
        nisar[["nisar_id", "nisar_time", "geometry"]],
        how="inner",
        predicate="intersects",
    )

    if pairs_sn.empty:
        print("Nenhuma interseccao espacial entre Sentinel-1 e NISAR.")
        return

    # 2) Geometria de interseccao S1 x NISAR (precisa ser area > 0 depois do triple-intersection).
    sn_rows: list[dict] = []
    for s1_idx, row in pairs_sn.iterrows():
        nisar_idx = int(row["index_right"])
        geom_sn = make_valid(s1.geometry.iloc[s1_idx].intersection(nisar.geometry.iloc[nisar_idx]))
        if geom_sn.is_empty:
            continue
        sn_rows.append(
            {
                "s1_id": row["s1_id"],
                "s1_time": row["s1_time"],
                "nisar_id": row["nisar_id"],
                "nisar_time": row["nisar_time"],
                "geometry": geom_sn,
            }
        )

    sn_intersections = gpd.GeoDataFrame(sn_rows, geometry="geometry", crs="EPSG:4326")
    sn_intersections = sn_intersections.loc[sn_intersections.geometry.notnull() & ~sn_intersections.geometry.is_empty].copy()

    if sn_intersections.empty:
        print("Interseccoes S1 x NISAR vazias apos validacao geometrica.")
        return

    # 3) Candidatas BIOMASS que intersectam a interseccao S1 x NISAR.
    candidates = gpd.sjoin(
        sn_intersections,
        biomass[["biomass_id", "biomass_time", "geometry"]],
        how="inner",
        predicate="intersects",
    ).rename(columns={"index_right": "biomass_index"})

    if candidates.empty:
        print("Nenhuma cena BIOMASS intersecta as interseccoes S1 x NISAR.")
        return

    # 4) Interseccao espacial tripla obrigatoria e metricas temporais.
    result_rows: list[dict] = []
    for _, row in candidates.iterrows():
        biomass_idx = int(row["biomass_index"])
        bio_geom = biomass.geometry.iloc[biomass_idx]

        triple_geom = make_valid(row.geometry.intersection(bio_geom))
        if triple_geom.is_empty:
            continue

        area_m2 = geom_area_m2(triple_geom)
        if area_m2 <= 0:
            continue

        s1_time = pd.Timestamp(row["s1_time"])
        nisar_time = pd.Timestamp(row["nisar_time"])
        biomass_time = pd.Timestamp(row["biomass_time"])

        dif_s1_nisar = abs((s1_time - nisar_time).total_seconds())
        dif_s1_bio = abs((s1_time - biomass_time).total_seconds())
        dif_nisar_bio = abs((nisar_time - biomass_time).total_seconds())

        t_min = min(s1_time, nisar_time, biomass_time)
        t_max = max(s1_time, nisar_time, biomass_time)
        temporal_span_sec = (t_max - t_min).total_seconds()

        result_rows.append(
            {
                "s1_id": row["s1_id"],
                "nisar_id": row["nisar_id"],
                "biomass_id": row["biomass_id"],
                "s1_time": s1_time.isoformat(),
                "nisar_time": nisar_time.isoformat(),
                "biomass_time": biomass_time.isoformat(),
                "delta_s1_nisar_seconds": float(dif_s1_nisar),
                "delta_s1_biomass_seconds": float(dif_s1_bio),
                "delta_nisar_biomass_seconds": float(dif_nisar_bio),
                "temporal_span_seconds": float(temporal_span_sec),
                "triple_intersection_area_m2": area_m2,
                "geometry": triple_geom,
            }
        )

    results = gpd.GeoDataFrame(result_rows, geometry="geometry", crs="EPSG:4326")
    if results.empty:
        print("Nao houve interseccao espacial tripla com area positiva.")
        return

    # Remove duplicatas de tripla de cenas, mantendo a maior area.
    results = results.sort_values(
        by=["s1_id", "nisar_id", "biomass_id", "triple_intersection_area_m2"],
        ascending=[True, True, True, False],
    ).drop_duplicates(subset=["s1_id", "nisar_id", "biomass_id"], keep="first")

    if MAX_TEMPORAL_SPAN_HOURS is not None:
        threshold = float(MAX_TEMPORAL_SPAN_HOURS) * 3600.0
        results = results.loc[results["temporal_span_seconds"] <= threshold].copy()

    if results.empty:
        print("Resultados filtrados ficaram vazios.")
        return

    # Ordena por melhor coincidencia temporal, depois maior area espacial.
    results = results.sort_values(
        by=["temporal_span_seconds", "triple_intersection_area_m2"],
        ascending=[True, False],
    ).reset_index(drop=True)

    # Melhor combinacao temporal global (pode ter empate).
    best_span = results["temporal_span_seconds"].min()
    best = results.loc[results["temporal_span_seconds"] == best_span].copy()
    best = best.sort_values(by=["triple_intersection_area_m2"], ascending=[False]).reset_index(drop=True)

    # Exportacoes
    all_geojson = output_dir / "triple_intersections_all.geojson"
    all_csv = output_dir / "triple_intersections_all.csv"
    best_geojson = output_dir / "triple_intersections_best.geojson"
    best_csv = output_dir / "triple_intersections_best.csv"
    summary_json = output_dir / "triple_intersections_summary.json"

    export_geojson(results, all_geojson)
    export_geojson(best, best_geojson)
    results.drop(columns="geometry").to_csv(all_csv, index=False)
    best.drop(columns="geometry").to_csv(best_csv, index=False)

    summary = {
        "sentinel1_scenes_input": str(SENTINEL1_SCENES_PATH),
        "nisar_scenes_input": str(NISAR_SCENES_PATH),
        "biomass_scenes_input": str(BIOMASS_SCENES_PATH),
        "total_results_all": int(len(results)),
        "total_results_best": int(len(best)),
        "best_temporal_span_seconds": float(best_span),
        "outputs": {
            "all_geojson": str(all_geojson),
            "all_csv": str(all_csv),
            "best_geojson": str(best_geojson),
            "best_csv": str(best_csv),
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()