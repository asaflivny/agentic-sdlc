import hashlib
import hmac
import logging

from fastapi import Header, HTTPException, Request

from config import get_settings

logger = logging.getLogger(__name__)


async def verify_webhook_signature(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> None:
    secret = get_settings().webhook_secret
    if not secret:
        logger.debug("webhook_secret not configured, skipping signature check")
        return

    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    body = await request.body()
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, x_hub_signature_256):
        logger.warning("webhook signature mismatch from %s", request.client)
        raise HTTPException(status_code=401, detail="Invalid signature")

    logger.info("HMAC signature OK from %s", request.client)
