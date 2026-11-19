"""Material types and size limits accepted by HTTP and the manual terminal."""
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
UPLOAD_TYPES = {
    "application/pdf", "text/plain", "image/png", "image/jpeg", "image/webp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
