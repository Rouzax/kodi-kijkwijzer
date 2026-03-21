"""Configuration loading and validation."""

import logging
import sys

import yaml

log = logging.getLogger(__name__)


def load_config(path):
    try:
        with open(path) as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: Config file not found: {path}")
        print("Copy config.example.yaml to config.yaml and edit it.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML in {path}: {e}")
        sys.exit(1)
    if not isinstance(config, dict):
        print(f"Error: Config file {path} is empty or invalid")
        sys.exit(1)
    return config


def validate_config(config):
    """Check required config keys exist. Returns list of errors."""
    errors = []
    if not config.get("kodi", {}).get("url"):
        errors.append("kodi.url is required")
    if not config.get("tmdb", {}).get("api_key"):
        errors.append("tmdb.api_key is required")
    if not config.get("omdb", {}).get("api_key"):
        errors.append("omdb.api_key is required")
    if not config.get("rating", {}).get("target_country"):
        errors.append("rating.target_country is required")
    return errors


def load_overrides(path):
    """Load manual rating overrides from YAML file. Returns dict of title -> rating."""
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
            return data.get("overrides", {})
    except FileNotFoundError:
        return {}


def load_overrides_for_type(opts, media_type_name):
    """Load overrides for a specific media type.

    Looks for overrides_movies.yaml / overrides_tvshows.yaml.
    """
    default = f"overrides_{media_type_name}s.yaml"
    key = f"overrides_{media_type_name}s_file"
    path = opts.get(key, default)
    return load_overrides(path), path
