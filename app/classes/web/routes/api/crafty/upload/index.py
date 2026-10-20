import os
import logging
import shutil
import asyncio
from pathlib import Path
import anyio
from PIL import Image, UnidentifiedImageError
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.web.websocket_handler import WebSocketManager

logger = logging.getLogger(__name__)

FAILED_MSG = "Failed to upload files with error: %s"


class ApiFilesUploadHandler(BaseApiHandler):
    upload_locks = {}

    # Set by subclasses that only accept raster images. Those uploads are
    # rejected outright when the bytes turn out not to be a decodable image;
    # for everything else a failed EXIF strip is merely logged.
    image_upload = False

    # Translation key (in the "validators" section) explaining a rejected mime
    # type. Subclasses accepting a narrow, describable set of files set this so
    # the user reads prose instead of a raw mime type list.
    invalid_type_key = None

    def get_lock(self, key: str) -> asyncio.Lock:
        """Get or create a lock for the given key."""
        if key not in self.upload_locks:
            self.upload_locks[key] = asyncio.Lock()
        return self.upload_locks[key]

    def _finish_unauthorized(self, auth_data):
        """Return a standardized NOT_AUTHORIZED error.

        The upload client (``upload.js``) reads error text from ``data.message``,
        so every error response on these endpoints must use that shape.
        """
        return self.finish_json(
            403,
            {
                "status": "error",
                "error": "NOT_AUTHORIZED",
                "data": {
                    "message": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    )
                },
            },
        )

    def _parse_and_validate_request(
        self, accepted_types: list, lang: str = None
    ) -> bool:
        """Parses headers and runs path traversal validations.
        Returns False if error response sent."""
        self.chunk_hash = self.request.headers.get("chunkHash", 0)
        self.file_id = self.request.headers.get("fileId")
        self.chunked = self.request.headers.get("chunked", False)
        self.filename = self.request.headers.get("fileName", None)
        self.location = self.request.headers.get("location", None)
        self.chunk_index = self.request.headers.get("chunkId")
        self.temp_dir = Path(
            self.controller.project_root, "temp", self.file_id
        ).resolve()

        try:
            self.helper.validate_traversal(
                Path(self.controller.project_root, "temp").resolve(),
                self.temp_dir.resolve(),
            )
        except ValueError as why:
            logger.exception(FAILED_MSG, str(why))
            self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "BAD REQUEST",
                    "data": {"message": str(why)},
                },
            )
            return False

        if (
            len(accepted_types) > 0
            and self.file_helper.check_mime_types(self.filename) not in accepted_types
        ):  # If accepted types has nothing then we'll accept everything
            message = f"Invalid File Type only accepts {accepted_types}"
            if self.invalid_type_key:
                message = self.helper.translation.translate(
                    "validators", self.invalid_type_key, lang
                )
            self.finish_json(
                422,
                {
                    "status": "error",
                    "error": "INVALID FILE TYPE",
                    "data": {"message": message},
                },
            )
            return False

        return True

    def _has_enough_space(self, upload_dir: Path, file_size: int) -> bool:
        _, _, free = shutil.disk_usage(upload_dir)
        return free > file_size

    async def _process_non_chunked(self, upload_dir: Path):
        """Directly writes complete files."""
        file_path = os.path.join(upload_dir, self.filename)
        async with await anyio.open_file(file_path, "wb") as file:
            chunk = self.request.body
            if chunk:
                await file.write(chunk)

        logger.info(f"File upload completed. Filename: {self.filename}")
        return self.finish_json(
            200,
            {"status": "completed", "data": {"message": "File uploaded successfully"}},
        )

    async def _process_chunked(
        self, upload_dir: Path, total_chunks: int, auth_data, server_id=None
    ):
        """Processes incoming file chunks, validates integrity, and merges them."""
        os.makedirs(self.temp_dir, exist_ok=True)
        content_length = int(self.request.headers.get("Content-Length", 0))

        if content_length <= 0 or not self.filename or self.chunk_index is None:
            logger.error(
                "File upload failed. Filename: %s Error: Validation Failed",
                self.filename,
            )
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "BAD_REQUEST",
                    "data": {"message": "Invalid request parameters"},
                },
            )

        calculated_hash = self.helper.crypto_helper.calculate_buffer_hash(
            self.request.body
        )
        if str(self.chunk_hash) != str(calculated_hash):
            logger.error(
                "File upload failed. Filename: %s Error: INVALID HASH",
                self.filename,
            )
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_HASH",
                    "data": {
                        "message": "Hash received does not match reported sent hash.",
                        "chunk_id": self.chunk_index,
                    },
                },
            )

        file_path = Path(upload_dir, self.filename)
        chunk_path = Path(self.temp_dir, f"{self.filename}.part{self.chunk_index}")

        try:
            self.helper.validate_traversal(
                Path(self.temp_dir).resolve(), chunk_path.resolve()
            )
        except ValueError as why:
            logger.exception(FAILED_MSG, str(why))
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "BAD REQUEST",
                    "data": {"message": str(why)},
                },
            )

        async with self.get_lock(self.file_id):
            async with await anyio.open_file(chunk_path, "wb") as f:
                await f.write(self.request.body)

            received_chunks = [
                f
                for f in os.listdir(self.temp_dir)
                if f.startswith(f"{self.filename}.part")
            ]

            if len(received_chunks) == total_chunks:
                await self._assemble_chunks(file_path, total_chunks, auth_data)
                self._cleanup_temp_resources()

                if not self._strip_exif(file_path) and self.image_upload:
                    # Not a valid raster image: drop it and report cleanly rather
                    # than letting the request 500 with an HTML page.
                    os.remove(file_path)
                    return self.finish_json(
                        422,
                        {
                            "status": "error",
                            "error": "INVALID FILE TYPE",
                            "data": {"message": "Uploaded file is not a valid image"},
                        },
                    )

                logger.info(
                    "File upload completed. Filename: %s Path: %s",
                    self.filename,
                    file_path,
                )
                self.controller.management.add_to_audit_log(
                    auth_data[4]["user_id"],
                    f"Uploaded file {self.filename}",
                    server_id,
                    self.get_remote_ip(),
                )
                return self.finish_json(
                    200,
                    {
                        "status": "completed",
                        "data": {"message": "File uploaded successfully"},
                    },
                )

            return self.finish_json(
                200,
                {
                    "status": "partial",
                    "data": {"message": f"Chunk {self.chunk_index} received"},
                },
            )

    async def _assemble_chunks(self, file_path, total_chunks, auth_data):
        """Stitches all file pieces back together sequentially."""
        async with await anyio.open_file(file_path, "wb") as outfile:
            for i in range(total_chunks):
                WebSocketManager().broadcast_user(
                    auth_data[4]["user_id"],
                    "upload_process",
                    {
                        "cur_file": i,
                        "total_files": total_chunks,
                        "file_id": self.file_id,
                    },
                )
                chunk_file = os.path.join(self.temp_dir, f"{self.filename}.part{i}")
                async with await anyio.open_file(chunk_file, "rb") as infile:
                    await outfile.write(await infile.read())
                try:
                    await anyio.Path(chunk_file).unlink(missing_ok=True)
                except OSError as why:
                    logger.exception("Failed to remove chunk file with error: %s", why)

    def _cleanup_temp_resources(self):
        try:
            self.file_helper.del_dirs(self.temp_dir)
        except OSError as why:
            logger.exception("Failed to remove temp dir with error: %s", why)

    def _strip_exif(self, image_path) -> bool:
        """Removes EXIF metadata from uploaded imagery.

        Returns False when the file isn't a decodable raster image, leaving it
        on disk untouched so the caller can decide whether that's an error.
        """
        logger.debug("Stripping exif data from image")
        try:
            with Image.open(image_path) as image:
                image_data = list(image.getdata())
                image_no_exif = Image.new(image.mode, image.size)
                image_no_exif.putdata(image_data)
                image_no_exif.save(image_path)
        except (UnidentifiedImageError, OSError) as why:
            logger.error("Could not process image %s: %s", image_path, why)
            return False
        return True
