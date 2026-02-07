"""
Image Service - Upload and manage images via Cloudinary.

Handles:
- Downloading images from source URLs
- Uploading to Cloudinary
- Returning permanent URLs that work with Twilio (JPG format)
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
    import cloudinary.utils
    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False
    logger.warning("Cloudinary not installed. Run: pip install cloudinary")


class ImageService:
    """Service for managing images via Cloudinary."""

    def __init__(self):
        self.configured = False
        self.cloud_name = ""

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
        self.cloud_name = cloud_name
        self.configured = True
        logger.info(f"Cloudinary configured for cloud: {cloud_name}")

    def _generate_public_id(self, source: str, url: str) -> str:
        """Generate a unique public ID for an image."""
        hash_input = f"{source}:{url}"
        return f"jewelclaw/{source}/{hashlib.md5(hash_input.encode()).hexdigest()[:16]}"

    def _build_jpg_url(self, public_id: str) -> str:
        """Build Cloudinary URL with JPG format transformation."""
        # Format: https://res.cloudinary.com/{cloud}/image/upload/f_jpg,w_500,h_500,c_limit,q_auto/{public_id}
        return f"https://res.cloudinary.com/{self.cloud_name}/image/upload/f_jpg,w_500,h_500,c_limit,q_auto/{public_id}"

    async def upload_from_url(self, image_url: str, source: str = "unknown") -> Optional[str]:
        """
        Upload an image from URL to Cloudinary.
        Returns the Cloudinary URL with JPG format for Twilio compatibility.
        """
        if not self.configured:
            logger.warning("Cloudinary not configured, returning original URL")
            return image_url

        if not image_url:
            return None

        try:
            public_id = self._generate_public_id(source, image_url)

            # Upload to Cloudinary (without transformation - we'll apply it in URL)
            result = cloudinary.uploader.upload(
                image_url,
                public_id=public_id,
                overwrite=False,  # Don't re-upload if exists
                resource_type="image",
            )

            # Get the public_id from result and build URL with JPG transformation
            uploaded_public_id = result.get('public_id')
            jpg_url = self._build_jpg_url(uploaded_public_id)

            logger.info(f"Uploaded to Cloudinary: {jpg_url}")
            return jpg_url

        except Exception as e:
            # Check if it's "already exists" error - still return the transformed URL
            if "already exists" in str(e).lower():
                logger.info(f"Image already exists in Cloudinary")
                return self._build_jpg_url(public_id)

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
            )

            # Build URL with JPG transformation
            uploaded_public_id = result.get('public_id')
            return self._build_jpg_url(uploaded_public_id)

        except Exception as e:
            if "already exists" in str(e).lower():
                return self._build_jpg_url(public_id)
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
