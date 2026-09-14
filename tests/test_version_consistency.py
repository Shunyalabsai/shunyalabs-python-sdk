"""The version string must match the distribution version.

Through 1.0.1 the pyprojects said 1.0.1 while every ``__version__`` still said
1.0.0. That string is sent to the gateway as an ``sm-sdk`` query parameter, so
the drift made our own telemetry attribute traffic to the wrong SDK version --
silently, and in the one place you would go looking to confirm a client had
upgraded.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# (pyproject, file declaring __version__)
PACKAGES = [
    ("pyproject.toml", "src/shunyalabs/_version.py"),
    ("plugins/pipecat/pyproject.toml", "plugins/pipecat/pipecat_shunyalabs/__init__.py"),
    ("plugins/livekit/pyproject.toml", "plugins/livekit/livekit/plugins/shunyalabs/_version.py"),
]


def _dist_version(pyproject: pathlib.Path) -> str:
    for line in pyproject.read_text().splitlines():
        if line.startswith("version = "):
            return line.split("=", 1)[1].strip().strip('"')
    pytest.fail(f"no version in {pyproject}")


def _module_version(path: pathlib.Path) -> str:
    match = re.search(r'^__version__ = "([^"]+)"', path.read_text(), re.M)
    if not match:
        pytest.fail(f"no __version__ in {path}")
    return match.group(1)


@pytest.mark.parametrize("pyproject,version_file", PACKAGES)
def test_module_version_matches_distribution(pyproject, version_file):
    dist = _dist_version(REPO / pyproject)
    module = _module_version(REPO / version_file)
    assert module == dist, (
        f"{version_file} says {module} but {pyproject} says {dist}. "
        "This string is reported to the gateway; keep them in step."
    )


def test_packages_share_a_minor_series():
    # The three move together at minor level, because the plugins pin the core by
    # a >=MAJOR.MINOR.0 lower bound and a feature added to one is usually visible
    # in the others. Patch levels are deliberately allowed to diverge: a bug in
    # one plugin should not force a no-op republish of the other two.
    series = {p: _dist_version(REPO / p).rsplit(".", 1)[0] for p, _ in PACKAGES}
    assert len(set(series.values())) == 1, f"minor-series skew across packages: {series}"


@pytest.mark.parametrize(
    "plugin_pyproject",
    ["plugins/pipecat/pyproject.toml", "plugins/livekit/pyproject.toml"],
)
def test_plugins_require_the_matching_core(plugin_pyproject):
    # Both plugins now use StreamingConfig fields and a message type that older
    # cores do not define, so an under-constrained lower bound would install a
    # core that cannot serve them.
    major, minor, _ = _dist_version(REPO / "pyproject.toml").split(".")
    floor = f"{major}.{minor}.0"
    text = (REPO / plugin_pyproject).read_text()
    import re as _re
    m = _re.search(r'"shunyalabsai\[all\]>=([0-9.]+)"', text)
    assert m, f"{plugin_pyproject} has no shunyalabsai[all] lower bound"
    got = tuple(int(x) for x in m.group(1).split("."))
    assert got >= tuple(int(x) for x in floor.split(".")), (
        f"{plugin_pyproject} requires shunyalabsai[all]>={m.group(1)}, "
        f"but it uses features from {floor}"
    )
