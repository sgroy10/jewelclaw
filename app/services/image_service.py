"""
Image Service - Upload and manage images via Cloudinary.

Handles:
- Downloading images from source URLs
- Uploading to Cloudinary
- Returning permanent URLs that work with Twilio
"""

import logging
import hashlib
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

# Try to import cloudinary
try:
    import cloudinary
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False
    logger.warning("Cloudinary not installed. Run: pip install cloudinary")


class ImageService:
    """Service for managing images via Cloudinary."""

    def __init__(self):
        self.configured = False

    def configure(self, cloud_name: str, api_key: str, api_secret: str):
        """Configure Cloudinary credentials."""
        if not CLOUDINARY_AVAILABLE:
            logger.error("Cloudinary not available")
            return

        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret
        )
        self.configured = True
        logger.info(f"Cloudinary configured for cloud: {cloud_name}")

    def _generate_public_id(self, source: str, url: str) -> str:
        """Generate a unique public ID for an image."""
        hash_input = f"{source}:{url}"
        return f"jewelclaw/{source}/{hashlib.md5(hash_input.encode()).hexdigest()[:16]}"

    async def upload_from_url(self, image_url: str, source: str = "unknown") -> Optional[str]:
        """
        Upload an image from URL to Cloudinary.
        Returns the Cloudinary URL or None if failed.
        """
        if not self.configured:
            logger.warning("Cloudinary not configured, returning original URL")
            return image_url

        if not image_url:
            return None

        try:
            public_id = self._generate_public_id(source, image_url)

            # Upload to Cloudinary
            result = cloudinary.uploader.upload(
                image_url,
                public_id=public_id,
                overwrite=False,  # Don't re-upload if exists
                resource_type="image",
                folder="jewelclaw",
                transformation=[
                    {'width': 500, 'height': 500, 'crop': 'limit'},  # Resize for WhatsApp
                    {'quality': 'auto:good'},
                    {'format': 'jpg'}  # Convert to JPG for better compatibility
                ]
            )

            cloudinary_url = result.get('secure_url')
            logger.info(f"Uploaded to Cloudinary: {cloudinary_url}")
            return cloudinary_url

        except Exception as e:
            logger.error(f"Cloudinary upload failed: {e}")
            return image_url  # Return original URL as fallback

    async def download_and_upload(self, image_url: str, source: str = "unknown") -> Optional[str]:
        """
        Download image first, then upload to Cloudinary.
        Use this for sites that block direct access.
        """
        if not self.configured:
            return image_url

        if not image_url:
            return None

        try:
            # Download image
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "image/*",
            }

            async with httpx.AsyncClient() as client:
                response = await client.get(image_url, headers=headers, timeout=30)
                if response.status_code != 200:
                    logger.warning(f"Failed to download image: {response.status_code}")
                    return image_url

                image_data = response.content

            # Upload to Cloudinary
            public_id = self._generate_public_id(source, image_url)

            result = cloudinary.uploader.upload(
                image_data,
                public_id=public_id,
                overwrite=False,
                resource_type="image",
                folder="jewelclaw",
                transformation=[
                    {'width': 500, 'height': 500, 'crop': 'limit'},
                    {'quality': 'auto:good'},
                    {'format': 'jpg'}
                ]
            )

            return result.get('secure_url')

        except Exception as e:
            logger.error(f"Download and upload failed: {e}")
            return image_url

    async def batch_upload(self, images: list, source: str = "unknown") -> list:
        """
        Upload multiple images.
        Returns list of Cloudinary URLs.
        """
        results = []
        for image_url in images:
            url = await self.upload_from_url(image_url, source)
            results.append(url)
        return results


# Global instance
image_service = ImageService()
