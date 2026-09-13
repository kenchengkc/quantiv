"""Repository-local import bridge for research CLI modules.

Production research entrypoints bootstrap the repository root themselves. Expose
``scripts/research`` as the ``research`` package from that root so those CLIs do
not depend on pytest or workflow-specific ``PYTHONPATH`` configuration.
"""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[1] / "scripts" / "research")]
