"""Verifying and applying a signed Study Runner update.

- ``signatures``   what a release signature covers and how it is checked
- ``trusted_keys`` the Ed25519 public keys a release is allowed to be signed with
- ``installer``    swaps a staged update into place, run as its own process so it
                   outlives the server it is replacing

The request-facing half is ``backend/services/settings/update_service.py``: it
downloads, stages, and asks ``signatures`` whether to trust what it got.
"""
