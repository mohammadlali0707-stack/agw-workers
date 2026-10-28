"""cluster_chat package.

Provides attachment storage, room message management, and bridge support
for the Cluster Chat infrastructure across Mohammad, Telegram, web rooms,
and LeadDesk agents (Argon, Cookie, Coca, AGY workers).
"""

from cluster_chat.attachments import (
    DEFAULT_MAX_UPLOAD_MB,
    DEFAULT_PUBLIC_BASE_URL,
    DEFAULT_UPLOAD_DIR,
    EXTENSION_MIME_MAP,
    PHOTO_MIME_TYPES,
    STANDARD_ROOMS,
    AttachmentMetadata,
    AttachmentStorageManager,
    classify_attachment_kind,
    detect_mime_type,
    is_standard_room,
    is_valid_room_name,
    sanitize_filename,
    validate_room_name,
)
from cluster_chat.client import (
    format_file_size,
    list_room_messages,
    send_room_file,
)
from cluster_chat.rooms import (
    DEFAULT_DATA_DIR,
    RoomManager,
    RoomMessage,
)
from cluster_chat.server import (
    ClusterChatRequestHandler,
    ClusterChatServer,
)
from cluster_chat.telegram_bridge import (
    MAX_TELEGRAM_PHOTO_SIZE,
    TelegramBridge,
    encode_multipart_formdata,
    extract_target_agent,
    handle_telegram_update,
    send_attachment_to_telegram,
)

__all__ = [
    "AttachmentMetadata",
    "AttachmentStorageManager",
    "RoomManager",
    "RoomMessage",
    "ClusterChatServer",
    "ClusterChatRequestHandler",
    "TelegramBridge",
    "send_room_file",
    "list_room_messages",
    "format_file_size",
    "send_attachment_to_telegram",
    "handle_telegram_update",
    "extract_target_agent",
    "encode_multipart_formdata",
    "MAX_TELEGRAM_PHOTO_SIZE",
    "STANDARD_ROOMS",
    "PHOTO_MIME_TYPES",
    "EXTENSION_MIME_MAP",
    "DEFAULT_UPLOAD_DIR",
    "DEFAULT_PUBLIC_BASE_URL",
    "DEFAULT_MAX_UPLOAD_MB",
    "DEFAULT_DATA_DIR",
    "classify_attachment_kind",
    "detect_mime_type",
    "sanitize_filename",
    "is_standard_room",
    "is_valid_room_name",
    "validate_room_name",
]
