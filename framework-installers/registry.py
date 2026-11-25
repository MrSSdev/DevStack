"""Explicit adapter registration; never import browser-supplied module names."""
from laravel.adapter import LaravelAdapter

ADAPTERS = {"laravel": LaravelAdapter}
