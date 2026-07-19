"""Shared Redis connection."""

import redis

from .config import config

redis_client = redis.Redis.from_url(config.redis_url, decode_responses=True)
