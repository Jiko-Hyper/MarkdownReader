# -*- coding: utf-8 -*-
"""MDReader — read Markdown comfortably, keep documents organised in projects."""

from .core import APP_NAME, APP_VERSION, Workspace, serve, default_workspace  # noqa: F401

__all__ = ["APP_NAME", "APP_VERSION", "Workspace", "serve", "default_workspace"]
