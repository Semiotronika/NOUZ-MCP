"""Vector math helpers used by semantic NOUZ modes."""

from typing import Dict, List


def cosine(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = sum(a * a for a in v1) ** 0.5
    n2 = sum(b * b for b in v2) ** 0.5
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def mean_center(vecs: Dict[str, List[float]]) -> Dict[str, List[float]]:
    """Subtract the mean vector from all vectors (anisotropy correction).

    Transformer embeddings cluster in a narrow cone, inflating all pairwise
    cosine similarities. Subtracting the centroid removes this shared component
    and reveals true semantic distances (Su et al. 2021, WhitenedCSE 2023).
    """
    if len(vecs) < 2:
        return vecs
    dim = len(next(iter(vecs.values())))
    mean = [0.0] * dim
    for v in vecs.values():
        for i in range(dim):
            mean[i] += v[i]
    n = len(vecs)
    mean = [m / n for m in mean]
    return {k: [v[i] - mean[i] for i in range(dim)] for k, v in vecs.items()}
