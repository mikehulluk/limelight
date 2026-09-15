"""The page a story is laid out on: the one place its numbers live.

The writer, the runtime, the story view and the PDF exporter all reason
about the same page, so its defaults are defined once here. Anything that
sizes to millimetres imports from this module rather than restating a number.
"""

from __future__ import annotations

MM_PER_INCH = 25.4

# A4, with the margins the PDF exporter has always used. It is what a package
# built before page geometry existed is laid out as: A4 in the PDF, and
# whatever the window gave it on screen.
DEFAULT_PAGE_WIDTH_MM = 210.0
DEFAULT_PAGE_HEIGHT_MM = 297.0
DEFAULT_PAGE_MARGIN_MM = 15.0

# What a package built before spacing existed was set with: close to the
# stylesheet constants both renderers used, so its look does not change.
DEFAULT_BLOCK_GAP_MM = 3.0
DEFAULT_FIGURE_GAP_MM = 4.5
DEFAULT_HEADING_GAP_BEFORE_MM = 5.0
DEFAULT_HEADING_GAP_AFTER_MM = 2.0
