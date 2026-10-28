"""Make the generated models importable during tests.

`up` generates them into .up/python at the repository root - a `crossplane-models`
distribution exposing the top-level `models` package - and installs them into the function's
environment at build time. Tests run outside that environment, so the generated tree goes on
sys.path directly. The GKE XR model is generated from apis/gke/definition.yaml, so regenerate
after changing the XRD.
"""

import sys
from pathlib import Path

import pytest

_MODELS = Path(__file__).resolve().parents[3] / ".up" / "python"

if not _MODELS.is_dir():
    # Raised at conftest import, before collection fails with a less useful ModuleNotFoundError.
    raise pytest.UsageError(
        f"generated models not found at {_MODELS}\n"
        "Generate them from the repository root with:\n\n"
        "    up project build\n"
    )

sys.path.insert(0, str(_MODELS))
