"""
Hardware integration adapters for Study Runner.

Each adapter is optional. If the required library is not installed the adapter
logs a clear message and does nothing — no error is raised and the study runs normally.

Available adapters:
  lsl_adapter   — sends event markers via Lab Streaming Layer (requires pylsl)
  osc_adapter   — sends OSC messages to TouchDesigner or similar (requires python-osc)

To add a new integration: create a new file in this folder following the same pattern.
Call its initialize() function from app/__init__.py and its action functions from
app/trial_service.py. See lsl_adapter.py and osc_adapter.py for the exact structure.
"""
