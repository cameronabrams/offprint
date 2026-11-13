# Sphinx configuration for the Read the Docs build.
#
# The pages are MyST Markdown, not reStructuredText, because every other
# document in this repository is Markdown and a second syntax is a reason for
# an edit not to happen.

import pathlib as _pathlib
import re as _re

# The version lives in mirror.py and is read, never restated: a second
# copy in this file would be one more thing to forget on a release.
_src = (_pathlib.Path(__file__).parent.parent / "mirror.py").read_text(encoding="utf-8")
release = version = _re.search(r'^__version__ = "([^"]+)"', _src, _re.M).group(1)

project = "offprint"
author = "Cameron F. Abrams"
copyright = "2026, Cameron F. Abrams"

extensions = [
    "myst_parser",
    "sphinx_copybutton",
]

# The docs are prose about a handful of standalone scripts, not an API
# reference: every script here declares its dependencies in a PEP 723 header
# and is run with `uv run --script`, so it is not importable in a docs build
# without installing them. Nothing here uses autodoc.

myst_enable_extensions = ["deflist", "linkify", "colon_fence"]
myst_heading_anchors = 3          # so ## and ### are linkable from other pages

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_static_path = ["_static"]
html_logo = "_static/offprint-icon.png"
html_favicon = "_static/offprint-favicon.ico"
html_title = f"offprint {release}"
