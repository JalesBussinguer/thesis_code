"""Model-free Tsallis entropy estimators for continuous pixel samples.

The spacing estimator and bootstrap correction follow the authors' R code:
https://github.com/rjaneth/Tsallis_entropy_2025/tree/main/Code

The functions in this module operate on one-dimensional samples. For a
multichannel pixel vector, estimate each channel separately or provide a
scalar derived intensity; this module does not silently reduce vectors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np


def _as_sample(values: Iterable[float]) -> np.ndarray:
    sample = np.asarray(list(values), dtype=float)
    if sample.ndim != 1:
        raise ValueError("A amostra deve ser unidimensional.")
    if sample.size < 2:
        raise ValueError("A amostra deve conter pelo menos duas observações.")
    if not np.all(np.isfinite(sample)):
        raise ValueError("A amostra contém valores não finitos.")
    return sample


def _default_m(n: int) -> int:
    # This reproduces round(sqrt(n) + 0.5) from the reference R code.
    return max(1, math.floor(math.sqrt(n) + 1.0))


def _validate_m(m: int | None, n: int) -> int:
    if m is None:
        candidate = _default_m(n) if n >= 10 else max(1, math.floor(math.sqrt(n)))
    else:
        if isinstance(m, bool) or int(m) != m:
            raise ValueError("m deve ser um inteiro positivo.")
        candidate = int(m)
    # Keep the historical two-observation API usable as well.  For n=2 the
    # nearest-neighbour spacing is the only valid spacing, even though the
    # usual ``floor((n - 1) / 2)`` upper bound is zero.
    return max(1, min(candidate, max(1, (n - 1) // 2)))


def m_spacing(sample: Iterable[float], m: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Return sorted m-spacings and their boundary correction coefficients."""
    values = _as_sample(sample)
    spacing_m = _validate_m(m, values.size)
    ordered = np.sort(values, kind="quicksort")
    indexes = np.arange(values.size)
    left = np.maximum(indexes - spacing_m, 0)
    right = np.minimum(indexes + spacing_m, values.size - 1)
    differences = ordered[right] - ordered[left]

    coefficients = np.where(
        indexes < spacing_m,
        (spacing_m + indexes) / spacing_m,
        np.where(
            indexes >= values.size - spacing_m,
            (values.size + spacing_m - 1 - indexes) / spacing_m,
            2.0,
        ),
    )
    return differences, coefficients


def _validate_order(lambda_: float) -> float:
    order = float(lambda_)
    if not math.isfinite(order) or order <= 0:
        raise ValueError("lambda deve ser finito e maior que zero.")
    if order == 1.0:
        raise ValueError("lambda=1 corresponde ao limite de Shannon, não a Tsallis.")
    return order


def tsallis_entropy(
    sample: Iterable[float],
    lambda_: float,
    m: int | None = None,
) -> float:
    """Estimate continuous Tsallis entropy with the spacing estimator.

    This is the Python translation of ``tsallis_estimator_optimized``. If all
    spacings vanish, ``nan`` is returned because the continuous-density
    estimate is undefined for a degenerate sample.
    """
    order = _validate_order(lambda_)
    differences, coefficients = m_spacing(sample, m)
    valid = differences > np.finfo(float).eps
    if not np.any(valid):
        return float("nan")

    n = differences.size
    normalized_spacing = n * differences[valid] / (coefficients[valid] * _validate_m(m, n))
    with np.errstate(over="raise", invalid="raise"):
        try:
            power_sum = float(np.sum(normalized_spacing ** (1.0 - order)))
        except FloatingPointError as error:
            raise ValueError("Não foi possível calcular a potência dos espaçamentos.") from error
    return float((1.0 - power_sum / n) / (order - 1.0))


def bootstrap_tsallis_entropy(
    sample: Iterable[float],
    lambda_: float,
    B: int = 200,
    m: int | None = None,
    seed: int | None = None,
    return_replicates: bool = False,
) -> float | tuple[float, np.ndarray]:
    """Return the bias-corrected Tsallis estimate and optionally replicates.

    Bootstrap samples contain ``n`` observations drawn with replacement.
    Replicates that are degenerate are discarded, matching the reference
    implementation's handling of invalid results. At least half of the
    requested replicates must remain valid.
    """
    values = _as_sample(sample)
    if isinstance(B, bool) or int(B) != B or B < 1:
        raise ValueError("B deve ser um inteiro positivo.")
    B = int(B)
    original = tsallis_entropy(values, lambda_, m)
    if not math.isfinite(original):
        result = float("nan")
        replicates = np.empty(0, dtype=float)
    else:
        rng = np.random.default_rng(seed)
        indices = rng.integers(0, values.size, size=(B, values.size))
        estimates = []
        for index in indices:
            try:
                estimates.append(tsallis_entropy(values[index], lambda_, m))
            except (FloatingPointError, ValueError):
                # Resamples with too many ties can make a spacing estimate
                # undefined; these are discarded as in the reference R code.
                continue
        replicates = np.asarray([value for value in estimates if math.isfinite(value)])
        if replicates.size < B / 2:
            result = float("nan")
        else:
            result = float(2.0 * original - np.mean(replicates))
    if return_replicates:
        return result, replicates
    return result


def gamma_sar_tsallis_entropy(
    mu: float,
    looks: float,
    lambda_: float,
) -> float:
    """Return theoretical Tsallis entropy for ``GammaSAR(mu, looks)``."""
    mean = float(mu)
    number_of_looks = float(looks)
    order = _validate_order(lambda_)
    if not math.isfinite(mean) or mean <= 0:
        raise ValueError("mu deve ser finito e maior que zero.")
    if not math.isfinite(number_of_looks) or number_of_looks < 1:
        raise ValueError("looks deve ser finito e maior ou igual a um.")

    exponent = order * (number_of_looks - 1.0) + 1.0
    if exponent <= 0:
        raise ValueError("lambda e looks produzem um expoente inválido.")
    log_integral = (
        (1.0 - order) * math.log(mean)
        + (order - 1.0) * math.log(number_of_looks)
        + math.lgamma(exponent)
        - order * math.lgamma(number_of_looks)
        - exponent * math.log(order)
    )
    try:
        return float(-math.expm1(log_integral) / (order - 1.0))
    except OverflowError as error:
        raise ValueError("Não foi possível calcular a entropia Gamma-SAR.") from error


def excess_tsallis_entropy(
    sample: Iterable[float],
    looks: float,
    lambda_: float = 0.85,
    B: int = 200,
    m: int | None = None,
    seed: int | None = None,
) -> float:
    """Return bootstrap Tsallis entropy minus the fitted Gamma-SAR entropy."""
    values = _as_sample(sample)
    estimate = bootstrap_tsallis_entropy(values, lambda_, B, m, seed)
    if not math.isfinite(estimate):
        return float("nan")
    reference = gamma_sar_tsallis_entropy(float(np.mean(values)), looks, lambda_)
    return float(estimate - reference)


@dataclass(frozen=True)
class AdaptiveTsallisConfig:
    """Parameters for the image estimator.

    ``window_sizes`` are odd side lengths. The adaptive rule follows the
    paper's border-CV criterion, with ``eta`` controlling smoothing.
    """

    window_sizes: tuple[int, ...] = (5, 7, 9, 11)
    eta: float = 3.0
    lambda_: float = 0.85
    looks: float = 1.0
    bootstrap_replicates: int = 200
    monte_carlo_replicates: int = 500
    seed: int | None = None
    alpha: float = 0.05

    def __post_init__(self) -> None:
        sizes = tuple(int(s) for s in self.window_sizes)
        if not sizes or any(s < 3 or s % 2 == 0 for s in sizes):
            raise ValueError("window_sizes deve conter tamanhos ímpares >= 3.")
        if tuple(sorted(set(sizes))) != sizes:
            raise ValueError("window_sizes deve estar em ordem crescente e sem repetição.")
        if not math.isfinite(self.eta) or self.eta <= 0:
            raise ValueError("eta deve ser finito e maior que zero.")
        if not math.isfinite(self.looks) or self.looks < 1:
            raise ValueError("looks deve ser finito e maior ou igual a um.")
        if isinstance(self.bootstrap_replicates, bool) or int(self.bootstrap_replicates) != self.bootstrap_replicates or self.bootstrap_replicates < 1:
            raise ValueError("bootstrap_replicates deve ser um inteiro positivo.")
        if isinstance(self.monte_carlo_replicates, bool) or int(self.monte_carlo_replicates) != self.monte_carlo_replicates or self.monte_carlo_replicates < 1:
            raise ValueError("monte_carlo_replicates deve ser um inteiro positivo.")
        if not 0 < self.alpha < 1:
            raise ValueError("alpha deve estar entre zero e um.")


@dataclass
class TsallisMapResult:
    """Arrays produced by :func:`tsallis_image` (all have image shape)."""

    entropy: np.ndarray
    reference: np.ndarray
    excess: np.ndarray
    standardized: np.ndarray
    p_values: np.ndarray
    heterogeneity: np.ndarray
    window_size: np.ndarray
    border_cv: np.ndarray
    null_distributions: dict[int, np.ndarray]
    diagnostics: dict
    profile: dict


def read_single_band_geotiff(path: str | Path, masked: bool = True) -> tuple[np.ndarray, dict]:
    """Read one band from a GeoTIFF, returning ``(pixels, raster profile)``.

    Rasterio is imported lazily so the one-dimensional estimators remain usable
    without geospatial dependencies.
    """
    try:
        import rasterio
    except ImportError as error:  # pragma: no cover - environment dependent
        raise ImportError("A leitura GeoTIFF requer o pacote rasterio.") from error
    with rasterio.open(path) as source:
        if source.count != 1:
            raise ValueError("A entrada deve conter exatamente uma banda.")
        data = source.read(1, masked=masked)
        profile = source.profile.copy()
    if np.ma.isMaskedArray(data):
        pixels = np.asarray(data.filled(np.nan), dtype=float)
    else:
        pixels = np.asarray(data, dtype=float)
    if pixels.ndim != 2:
        raise ValueError("O GeoTIFF deve ser uma imagem bidimensional.")
    return pixels, profile


def _window_bounds(row: int, col: int, size: int, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    radius = size // 2
    return (max(0, row - radius), min(shape[0], row + radius + 1),
            max(0, col - radius), min(shape[1], col + radius + 1))


def _border_values(window: np.ndarray) -> np.ndarray:
    if window.shape[0] < 2 or window.shape[1] < 2:
        return window.ravel()
    return np.concatenate((window[0, :], window[-1, :],
                           window[1:-1, 0], window[1:-1, -1]))


def border_coefficient_of_variation(
    image: np.ndarray, row: int, col: int, size: int
) -> float:
    """Coefficient of variation of the finite pixels on a window's border."""
    values = np.asarray(image, dtype=float)
    if values.ndim != 2:
        raise ValueError("image deve ser bidimensional.")
    if size < 1 or int(size) != size:
        raise ValueError("size deve ser um inteiro positivo.")
    if not (0 <= row < values.shape[0] and 0 <= col < values.shape[1]):
        raise IndexError("row e col devem estar dentro da imagem.")
    r0, r1, c0, c1 = _window_bounds(row, col, size, values.shape)
    border = _border_values(values[r0:r1, c0:c1])
    border = border[np.isfinite(border)]
    if border.size < 2:
        return float("nan")
    mean = float(np.mean(border))
    return float(np.std(border, ddof=1) / abs(mean)) if mean else float("inf")


def select_adaptive_window(
    image: np.ndarray, row: int, col: int,
    window_sizes: Sequence[int] = (5, 7, 9, 11),
    threshold: float | None = None,
    *,
    looks: float | None = None,
    eta: float = 3.0,
) -> tuple[int, float]:
    """Select an adaptive size using the paper's border-CV criterion.

    ``threshold`` is retained as a compatibility option for the simple
    fixed-threshold rule. For the article's rule, provide ``looks`` and
    ``eta``; the largest admissible window is selected while its border
    satisfies ``C_ij <= U_ij``.
    """
    sizes = tuple(int(s) for s in window_sizes)
    if tuple(sorted(set(sizes))) != sizes or any(s < 3 or s % 2 == 0 for s in sizes):
        raise ValueError("window_sizes deve estar em ordem crescente e conter ímpares >= 3.")
    if looks is not None:
        if not math.isfinite(float(looks)) or float(looks) < 1:
            raise ValueError("looks deve ser finito e maior ou igual a um.")
        if not math.isfinite(float(eta)) or float(eta) <= 0:
            raise ValueError("eta deve ser finito e maior que zero.")
        sigma_n = 1.0 / math.sqrt(float(looks))
    elif threshold is None or not math.isfinite(float(threshold)) or threshold < 0:
        raise ValueError("Forneça looks/eta ou um threshold válido.")
    selected = sizes[0]
    selected_cv = float("nan")
    for size in sizes:
        current_cv = border_coefficient_of_variation(image, row, col, size)
        if not math.isfinite(current_cv):
            break
        if looks is None:
            limit = float(threshold)
        else:
            limit = float(eta) * (
                1.0 + math.sqrt((1.0 + 2.0 * sigma_n**2) / (8.0 * (size - 1)))
            ) * sigma_n
        if current_cv > limit:
            break
        selected, selected_cv = size, current_cv
    if not math.isfinite(selected_cv):
        selected_cv = border_coefficient_of_variation(image, row, col, selected)
    return selected, selected_cv


def _window_samples(image: np.ndarray, row: int, col: int, size: int) -> np.ndarray:
    r0, r1, c0, c1 = _window_bounds(row, col, size, image.shape)
    sample = np.asarray(image[r0:r1, c0:c1], dtype=float).ravel()
    return sample[np.isfinite(sample)]


def monte_carlo_null_calibration(
    window_sizes: Sequence[int], looks: float, lambda_: float = 0.85,
    B: int = 500, bootstrap_B: int = 200, seed: int | None = None,
) -> dict[int, np.ndarray]:
    """Simulate Gamma-SAR windows and return null *excess* distributions.

    Distributions are keyed by side length, making calibration reusable for all
    pixels selected with the same adaptive window.
    """
    sizes = tuple(int(s) for s in window_sizes)
    if not sizes or any(s < 3 or s % 2 == 0 for s in sizes):
        raise ValueError("window_sizes deve conter tamanhos ímpares >= 3.")
    if not math.isfinite(float(looks)) or float(looks) < 1:
        raise ValueError("looks deve ser finito e maior ou igual a um.")
    if isinstance(B, bool) or int(B) != B or B < 1:
        raise ValueError("B deve ser um inteiro positivo.")
    if isinstance(bootstrap_B, bool) or int(bootstrap_B) != bootstrap_B or bootstrap_B < 1:
        raise ValueError("B e bootstrap_B devem ser positivos.")
    B, bootstrap_B = int(B), int(bootstrap_B)
    rng = np.random.default_rng(seed)
    result: dict[int, np.ndarray] = {}
    for size in sizes:
        n = size**2
        samples = []
        for _ in range(int(B)):
            sample = rng.gamma(shape=looks, scale=1.0 / looks, size=n)
            value = excess_tsallis_entropy(
                sample, looks, lambda_, B=bootstrap_B,
                seed=int(rng.integers(0, np.iinfo(np.int64).max)),
            )
            if math.isfinite(value):
                samples.append(value)
        result[int(size)] = np.asarray(samples, dtype=float)
    return result


def standardize_observed_scale(
    observed: np.ndarray | float, null_mean: float, observed_scale: float
) -> np.ndarray | float:
    """Standardize using the window-specific null mean and observed scale."""
    observed_array = np.asarray(observed, dtype=float)
    if not math.isfinite(null_mean) or not math.isfinite(observed_scale) or observed_scale <= 0:
        standardized = np.full_like(observed_array, np.nan, dtype=float)
    else:
        standardized = (observed_array - null_mean) / observed_scale
    return float(standardized) if standardized.ndim == 0 else standardized


def pixelwise_p_value(observed: float, null_mean: float, observed_scale: float) -> float:
    """Two-sided normal p-value from the article's adaptive standardization."""
    if not math.isfinite(float(observed)) or not math.isfinite(null_mean):
        return float("nan")
    if not math.isfinite(observed_scale) or observed_scale <= 0:
        return float("nan")
    z = abs((float(observed) - null_mean) / observed_scale)
    return float(math.erfc(z / math.sqrt(2.0)))


def tsallis_image(
    input_tif: str | Path, config: AdaptiveTsallisConfig | None = None,
) -> TsallisMapResult:
    """Run the complete adaptive Tsallis workflow on a single-band GeoTIFF."""
    cfg = config or AdaptiveTsallisConfig()
    image, profile = read_single_band_geotiff(input_tif)
    nulls = monte_carlo_null_calibration(
        cfg.window_sizes, cfg.looks, cfg.lambda_, cfg.monte_carlo_replicates,
        cfg.bootstrap_replicates, cfg.seed,
    )
    shape = image.shape
    entropy = np.full(shape, np.nan); reference = np.full(shape, np.nan)
    excess = np.full(shape, np.nan); standardized = np.full(shape, np.nan)
    p_values = np.full(shape, np.nan); windows = np.zeros(shape, dtype=np.int16)
    cvs = np.full(shape, np.nan)
    for row in range(shape[0]):
        for col in range(shape[1]):
            if not math.isfinite(float(image[row, col])):
                continue
            size, cv = select_adaptive_window(
                image, row, col, cfg.window_sizes, looks=cfg.looks, eta=cfg.eta
            )
            sample = _window_samples(image, row, col, size)
            windows[row, col], cvs[row, col] = size, cv
            if sample.size < 2:
                continue
            value = bootstrap_tsallis_entropy(sample, cfg.lambda_, cfg.bootstrap_replicates,
                                              seed=None if cfg.seed is None else cfg.seed + row * shape[1] + col)
            ref = gamma_sar_tsallis_entropy(float(np.mean(sample)), cfg.looks, cfg.lambda_)
            excess[row, col], entropy[row, col], reference[row, col] = value - ref, value, ref
    valid_excess = excess[np.isfinite(excess)]
    observed_scale = float(np.std(valid_excess, ddof=1)) if valid_excess.size > 1 else float("nan")
    null_means = {
        size: float(np.mean(values)) if values.size else float("nan")
        for size, values in nulls.items()
    }
    for row in range(shape[0]):
        for col in range(shape[1]):
            if windows[row, col] == 0 or not math.isfinite(float(excess[row, col])):
                continue
            null_mean = null_means[int(windows[row, col])]
            standardized[row, col] = standardize_observed_scale(
                excess[row, col], null_mean, observed_scale
            )
            p_values[row, col] = pixelwise_p_value(
                excess[row, col], null_mean, observed_scale
            )
    heterogeneity = np.where(np.isfinite(p_values), p_values < cfg.alpha, False)
    return TsallisMapResult(entropy, reference, excess, standardized, p_values,
                            heterogeneity.astype(bool), windows, cvs, nulls,
                            {"observed_scale": observed_scale, "null_means": null_means,
                             "eta": cfg.eta, "looks": cfg.looks}, profile)


def binary_map_metrics(predicted: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    """Return confusion counts and common binary-map quality metrics."""
    pred = np.asarray(predicted, dtype=bool); actual = np.asarray(truth, dtype=bool)
    if pred.shape != actual.shape:
        raise ValueError("predicted e truth devem ter a mesma forma.")
    tp = int(np.count_nonzero(pred & actual)); tn = int(np.count_nonzero(~pred & ~actual))
    fp = int(np.count_nonzero(pred & ~actual)); fn = int(np.count_nonzero(~pred & actual))
    div = lambda n, d: float(n / d) if d else float("nan")
    return {"tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "accuracy": div(tp + tn, pred.size), "precision": div(tp, tp + fp),
            "recall": div(tp, tp + fn), "specificity": div(tn, tn + fp),
            "f1": div(2 * tp, 2 * tp + fp + fn)}


def write_tsallis_maps(result: TsallisMapResult, output_directory: str | Path) -> dict[str, Path]:
    """Write the numeric layers and binary heterogeneity map as GeoTIFFs."""
    try:
        import rasterio
    except ImportError as error:  # pragma: no cover
        raise ImportError("A escrita GeoTIFF requer o pacote rasterio.") from error
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    profile = dict(result.profile)
    profile.update(count=1, dtype="float32", nodata=np.nan)
    outputs: dict[str, Path] = {}
    layers = {
        "entropy": result.entropy, "reference": result.reference,
        "excess": result.excess, "standardized": result.standardized,
        "p_values": result.p_values, "window_size": result.window_size,
        "border_cv": result.border_cv,
        "heterogeneity": result.heterogeneity.astype(np.float32),
    }
    for name, layer in layers.items():
        path = directory / f"{name}.tif"
        layer_profile = dict(profile)
        layer_profile["dtype"] = "uint8" if name == "heterogeneity" else "float32"
        if name == "heterogeneity":
            layer_profile["nodata"] = 0
        with rasterio.open(path, "w", **layer_profile) as destination:
            destination.write(np.asarray(layer, dtype=layer_profile["dtype"]), 1)
        outputs[name] = path
    return outputs


# Descriptive aliases used by notebooks and earlier experiment scripts.
compute_metrics = binary_map_metrics
run_tsallis_image = tsallis_image
