"""Model-free Tsallis entropy estimators for continuous pixel samples.

The spacing estimator and bootstrap correction follow the authors' R code:
https://github.com/rjaneth/Tsallis_entropy_2025/tree/main/Code

The functions in this module operate on one-dimensional samples. For a
multichannel pixel vector, estimate each channel separately or provide a
scalar derived intensity; this module does not silently reduce vectors.
"""

from __future__ import annotations

import math
from typing import Iterable

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
    return max(1, min(candidate, (n - 1) // 2))


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
        estimates = [
            tsallis_entropy(values[index], lambda_, m)
            for index in indices
        ]
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
