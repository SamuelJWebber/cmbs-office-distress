"""cmbs_distress: SEC Form ABS-EE (CMBS) loan-tape parsing, distress analytics, static dashboard.

Evidence vocabulary used throughout this repo (see CLAUDE.md):

* fixture-proven  - asserted by a test against the synthetic fixture in tests/fixtures/
* seen on the data - observed in a real ABS-EE filing that was downloaded and inspected
* seen live        - observed on the deployed GitHub Pages site / a live Actions run
* unverified       - written from the spec or from memory, not yet checked against anything

At v0.2 everything in this package (including longitudinal multi-filing migration analytics)
is fixture-proven at most. Nothing is "seen on the data" yet.
"""

__version__ = "0.2.0"
