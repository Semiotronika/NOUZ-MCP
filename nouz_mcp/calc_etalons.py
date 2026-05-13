"""Diagnose whether configured semantic etalons are distinct enough."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from .vectors import cosine, mean_center


DEFAULT_EMBED_MODEL = "text-embedding-granite-embedding-278m-multilingual"

DEFAULT_ETALONS = {
    "S": (
        "Methodology for analysing complex objects: feedback loops, "
        "emergent properties, self-regulation, bifurcation points. "
        "Cybernetics, synergetics, dissipative structures, catastrophe "
        "theory, autopoiesis - tools for understanding how the whole "
        "exceeds the sum of its parts. Not data and not code - a way "
        "of thinking about how parts form a whole and why systems "
        "behave non-linearly."
    ),
    "D": (
        "Physics and cosmology: from subatomic particles to the large-scale "
        "structure of the Universe. Lagrangians, curvature tensors, scattering "
        "cross-sections, quarks, bosons, fermions, plasma, vacuum fluctuations, "
        "cosmic microwave background, cosmological constant, decoherence. "
        "Pure science about the nature of matter, energy and spacetime."
    ),
    "E": (
        "Software engineering, machine learning and infrastructure: writing "
        "and debugging code, deployment, containerisation, neural networks, "
        "inference, tokenisation, data serialisation, microservices, CI/CD, "
        "automated testing, refactoring, Git, Docker, Kubernetes, APIs. "
        "The practical discipline of building computational systems from "
        "architecture to production."
    ),
}


def normalize_api_url(api_url: str) -> str:
    normalized = api_url.rstrip("/")
    if not normalized.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


def _default_config_path() -> Path | None:
    env_path = os.getenv("NOUZ_CONFIG")
    if env_path:
        return Path(env_path)
    cwd_config = Path("config.yaml")
    if cwd_config.exists():
        return cwd_config
    return None


def load_etalon_texts(config_path: Path | str | None) -> dict[str, str]:
    if config_path is None:
        return dict(DEFAULT_ETALONS)

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    etalons = data.get("etalons") or []
    result: dict[str, str] = {}
    for item in etalons:
        if not isinstance(item, dict):
            continue
        sign = str(item.get("sign", "")).strip()
        text = str(item.get("text", "")).strip()
        if sign and text:
            result[sign] = text

    if not result:
        raise ValueError(f"No etalons found in config: {path}")
    return result


def get_embedding(text: str, api_url: str, model: str, api_key: str) -> list[float]:
    payload: dict[str, Any] = {"input": text}
    if model:
        payload["model"] = model

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(
        f"{normalize_api_url(api_url)}/embeddings",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["data"][0]["embedding"]


def spread_percentages(vec: list[float], centered: dict[str, list[float]]) -> tuple[dict[str, float], float]:
    scores = {sign: cosine(vec, etalon_vec) for sign, etalon_vec in centered.items()}
    min_val = min(scores.values())
    max_val = max(scores.values())
    spread = max_val - min_val
    if spread <= 0.05:
        return {key: round(100.0 / len(scores), 1) for key in scores}, spread
    adjusted = {key: value - min_val for key, value in scores.items()}
    total = sum(adjusted.values())
    return {key: round(value / total * 100, 1) for key, value in adjusted.items()}, spread


def print_pairwise(title: str, vecs: dict[str, list[float]]) -> None:
    print(f"\n=== {title} ===")
    signs = sorted(vecs.keys())
    for i, s1 in enumerate(signs):
        for j, s2 in enumerate(signs):
            if i <= j:
                print(f"{s1}<->{s2}: {cosine(vecs[s1], vecs[s2]): .4f}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check raw and mean-centered cosine distances between NOUZ semantic etalons."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_default_config_path(),
        help="Path to config.yaml. Defaults to NOUZ_CONFIG or ./config.yaml. Uses public S/D/E defaults if absent.",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("EMBED_API_URL", "http://127.0.0.1:1234/v1"),
        help="OpenAI-compatible embeddings endpoint. Defaults to EMBED_API_URL.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("EMBED_API_KEY", ""),
        help="Embedding API key. Defaults to EMBED_API_KEY.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("EMBED_MODEL", DEFAULT_EMBED_MODEL),
        help="Embedding model name. Defaults to EMBED_MODEL or the public NOUZ example model.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    api_url = normalize_api_url(args.api_url)
    try:
        etalons = load_etalon_texts(args.config)
        print(f"Config: {args.config or '(public defaults)'}")
        print(f"Endpoint: {api_url}")
        print(f"Model: {args.model or '(server default)'}")
        print("\n=== Embeddings ===")

        etalon_vecs = {}
        for sign, text in etalons.items():
            vector = get_embedding(text, api_url, args.model, args.api_key)
            etalon_vecs[sign] = vector
            print(f"{sign}: dim={len(vector)}")

        centered = mean_center(etalon_vecs)
        print_pairwise("Pairwise Cosine (raw)", etalon_vecs)
        print_pairwise("Pairwise Cosine (mean-centered)", centered)

        print("\n=== Spread-normalized self-classification ===")
        for sign in sorted(etalon_vecs.keys()):
            percentages, spread = spread_percentages(etalon_vecs[sign], centered)
            dominant = max(percentages, key=percentages.get)
            print(f"{sign}: {percentages} dominant={dominant} spread={spread:.4f}")
    except (FileNotFoundError, ValueError, KeyError, HTTPError, URLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
