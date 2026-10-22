"""r2g agent: wraps the r2g skills (eda-install, rtl-acquire, signoff-loop,
def-graph) as tools, drives them with a user-chosen LLM, and serves a local UI.

Rule that runs through every module: the model DECIDES (what to run, which
repair to try), deterministic code JUDGES (DRC/LVS verdicts, dataset
verification). A tool result is the only source of a pass.
"""
__version__ = "0.1.0"
