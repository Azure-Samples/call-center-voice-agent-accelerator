"""Validates Azure Event Grid AAD delivery authentication tokens."""

import logging
import time

import aiohttp
import jwt

logger = logging.getLogger(__name__)

# Microsoft identity platform JWKS endpoint
_JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
_EXPECTED_AUDIENCE = "https://eventgrid.azure.net"

# Cache JWKS keys for 1 hour
_jwks_cache: dict = {"keys": None, "fetched_at": 0}
_JWKS_CACHE_TTL = 3600


async def _get_signing_keys() -> dict:
    """Fetch and cache JWKS signing keys from Microsoft identity platform."""
    now = time.time()
    if _jwks_cache["keys"] and (now - _jwks_cache["fetched_at"]) < _JWKS_CACHE_TTL:
        return _jwks_cache["keys"]

    async with aiohttp.ClientSession() as session:
        async with session.get(_JWKS_URL) as resp:
            if resp.status != 200:
                logger.error("Failed to fetch JWKS keys: status=%s", resp.status)
                return _jwks_cache["keys"] or {}
            data = await resp.json()
            _jwks_cache["keys"] = data
            _jwks_cache["fetched_at"] = now
            return data


async def validate_eventgrid_token(auth_header: str, tenant_id: str) -> bool:
    """Validate an Event Grid AAD delivery token.

    Returns True if valid, False otherwise.
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.warning("[EventGridAuth] Missing or malformed Authorization header")
        return False

    token = auth_header[7:]  # Strip "Bearer "

    try:
        # Decode header to get kid
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            logger.warning("[EventGridAuth] Token missing kid in header")
            return False

        # Get JWKS keys
        jwks_data = await _get_signing_keys()
        if not jwks_data:
            logger.error("[EventGridAuth] No JWKS keys available")
            return False

        # Find matching key
        jwk_client = jwt.PyJWKSet.from_dict(jwks_data)
        signing_key = None
        for key in jwk_client.keys:
            if key.key_id == kid:
                signing_key = key
                break

        if not signing_key:
            logger.warning("[EventGridAuth] No matching key found for kid=%s", kid)
            return False

        # Validate token
        expected_issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_EXPECTED_AUDIENCE,
            issuer=expected_issuer,
        )
        return True

    except jwt.ExpiredSignatureError:
        logger.warning("[EventGridAuth] Token expired")
    except jwt.InvalidAudienceError:
        logger.warning("[EventGridAuth] Invalid audience")
    except jwt.InvalidIssuerError:
        logger.warning("[EventGridAuth] Invalid issuer")
    except jwt.PyJWTError as e:
        logger.warning("[EventGridAuth] Token validation failed: %s", e)
    except Exception as e:
        logger.error("[EventGridAuth] Unexpected error: %s", e)

    return False
