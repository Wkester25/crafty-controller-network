import logging
import os
from pathlib import Path

from app.classes.web.routes.api.crafty.upload.index import ApiFilesUploadHandler

logger = logging.getLogger(__name__)

IMAGE_MIME_TYPES = [
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/pipeg",
    "image/tiff",
    "image/x-icon",
    "image/png",
    "image/webp",
]

CUSTOM_GRAPHICS = "app/frontend/static/assets/images/auth/custom"
EMBED_GRAPHICS = "app/frontend/static/assets/images/embed"
LOGO_FULL_GRAPHICS = "app/frontend/static/assets/images/logos/full"
LOGO_SQUARE_GRAPHICS = "app/frontend/static/assets/images/logos/square"


class APICraftyCustomizeUpload(ApiFilesUploadHandler):
    # Destination for this handler's uploads, relative to the project root.
    # Subclasses point the same superuser-only image upload flow at their own
    # directory by overriding this.
    upload_subdir = CUSTOM_GRAPHICS
    image_upload = True
    invalid_type_key = "invalidImageType"

    async def post(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        try:
            # 1. Authorize user and resolve types
            accepted_types = self._authorize_upload(auth_data)
        except PermissionError:
            return self._finish_unauthorized(auth_data)

        # 2. Extract and validate headers/paths
        if not self._parse_and_validate_request(accepted_types, auth_data[4]["lang"]):
            return
        try:
            self._check_traversal()
        except ValueError:
            return self._finish_unauthorized(auth_data)
        # 3. Check disk space
        file_size = int(self.request.headers.get("fileSize", 0))
        total_chunks = int(self.request.headers.get("totalChunks", 0))
        # Enforce a configurable size cap on panel image uploads. The client
        # declares the total size up front via the ``fileSize`` header, so an
        # oversized image is rejected here before any bytes are written to disk.
        max_image_mb = self.helper.get_setting("max_image_upload_size_mb", 5)
        if file_size > max_image_mb * 1024 * 1024:
            logger.error(
                f"File upload failed. Filename: {self.filename}"
                f" Error: IMAGE TOO LARGE ({file_size} bytes,"
                f" limit {max_image_mb} MB)"
            )
            return self.finish_json(
                413,
                {
                    "status": "error",
                    "error": "IMAGE TOO LARGE",
                    "data": {
                        "message": self.helper.translation.translate(
                            "validators",
                            "imageTooLarge",
                            auth_data[4]["lang"],
                        ).format(max_image_mb)
                    },
                },
            )
        if not self._has_enough_space(self.upload_dir, file_size):
            return self.finish_json(
                507,
                {
                    "status": "error",
                    "error": "NO STORAGE SPACE",
                    "data": {"message": "Out Of Space!"},
                },
            )

        # 4. Handle early chunked initiation or structure prep
        if self.chunked and not self.chunk_index:
            return self.finish_json(
                200, {"status": "ok", "data": {"file-id": self.file_id}}
            )

        os.makedirs(self.upload_dir, exist_ok=True)

        # 5. Route to specific processor
        if not self.chunked:
            return await self._process_non_chunked(self.upload_dir)

        return await self._process_chunked(self.upload_dir, total_chunks, auth_data)

    def _authorize_upload(self, auth_data: tuple) -> list[str]:
        if self.is_superuser(auth_data):
            return IMAGE_MIME_TYPES
        raise PermissionError()

    def _check_traversal(self):
        self.upload_dir = Path(self.controller.project_root, self.upload_subdir)
        self.helper.validate_traversal(
            Path(self.controller.project_root, self.upload_subdir).resolve(),
            Path(self.upload_dir, self.filename).resolve(),
        )


class APICraftyEmbedUpload(APICraftyCustomizeUpload):
    """Uploads images used as the Open Graph preview image for link embeds.

    Shares the superuser-only authorization and image mime type restrictions
    of the login customization upload, but writes to its own directory.
    """

    upload_subdir = EMBED_GRAPHICS


class APICraftyLogoFullUpload(APICraftyCustomizeUpload):
    """Uploads images used as the site's full (wide) brand logo."""

    upload_subdir = LOGO_FULL_GRAPHICS


class APICraftyLogoSquareUpload(APICraftyCustomizeUpload):
    """Uploads images used as the site's square brand logo."""

    upload_subdir = LOGO_SQUARE_GRAPHICS
