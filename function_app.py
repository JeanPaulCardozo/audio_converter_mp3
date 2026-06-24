import azure.functions as func
import logging
import base64
import tempfile
import subprocess
import shutil
import stat
import os
import json
import glob
import uuid
import binascii
from datetime import datetime, timedelta, timezone

import requests
import msal
import filetype
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SOURCE_FFMPEG = os.path.join(BASE_DIR, "ffmpeg-bin", "ffmpeg")
TARGET_FFMPEG = "/tmp/ffmpeg"

DEFAULT_CHUNK_DURATION_SECONDS = 60
DEFAULT_AUDIO_BITRATE = "64k"
DEFAULT_AUDIO_CHANNELS = 1
DEFAULT_SAS_EXPIRY_MINUTES = 120

STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
BLOB_CONTAINER_NAME = os.getenv("BLOB_CONTAINER_NAME", "audio-output")

FFMPEG_PATH = TARGET_FFMPEG

AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


# -----------------------------------------------------------------------------
# Startup helpers
# -----------------------------------------------------------------------------

def ensure_ffmpeg_ready() -> None:
    if not os.path.exists(TARGET_FFMPEG):
        shutil.copy2(SOURCE_FFMPEG, TARGET_FFMPEG)
        os.chmod(TARGET_FFMPEG, os.stat(TARGET_FFMPEG).st_mode | stat.S_IEXEC)


def get_blob_service_client() -> BlobServiceClient:
    if not STORAGE_CONNECTION_STRING:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not configured.")
    return BlobServiceClient.from_connection_string(STORAGE_CONNECTION_STRING)


# Ensure ffmpeg is ready on module load
ensure_ffmpeg_ready()


# -----------------------------------------------------------------------------
# General helpers
# -----------------------------------------------------------------------------

def json_response(payload: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, ensure_ascii=False),
        mimetype="application/json",
        status_code=status_code
    )


def run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    cmd = [
        FFMPEG_PATH,
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        *args
    ]

    return subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )


def cleanup_paths(paths: list[str]) -> None:
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def cleanup_directory(path: str | None) -> None:
    try:
        if path and os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def parse_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


# -----------------------------------------------------------------------------
# Blob helpers
# -----------------------------------------------------------------------------

def upload_file_to_blob(blob_service_client: BlobServiceClient, local_file_path: str, blob_name: str) -> str:
    container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)
    blob_client = container_client.get_blob_client(blob_name)

    with open(local_file_path, "rb") as data:
        blob_client.upload_blob(data, overwrite=True)

    return blob_client.url


def generate_blob_sas_url(
    blob_service_client: BlobServiceClient,
    blob_name: str,
    expiry_minutes: int
) -> str:
    container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)
    blob_client = container_client.get_blob_client(blob_name)

    account_name = blob_service_client.account_name
    account_key = blob_service_client.credential.account_key

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=BLOB_CONTAINER_NAME,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)
    )

    return f"{blob_client.url}?{sas_token}"


# -----------------------------------------------------------------------------
# Request parsing
# -----------------------------------------------------------------------------

def extract_request_data(req: func.HttpRequest) -> tuple[bytes, dict]:
    content_type = (req.headers.get("content-type") or "").lower()

    options = {
        "chunk_duration_seconds": DEFAULT_CHUNK_DURATION_SECONDS,
        "audio_bitrate": DEFAULT_AUDIO_BITRATE,
        "audio_channels": DEFAULT_AUDIO_CHANNELS,
        "sas_expiry_minutes": DEFAULT_SAS_EXPIRY_MINUTES,
        "return_private_urls": False
    }

    if "application/json" in content_type:
        try:
            body = req.get_json()
        except ValueError:
            raise ValueError("The body does not contain valid JSON.")

        content_base64 = body.get("content_base64")
        if not content_base64:
            raise ValueError("The 'content_base64' property is missing.")

        try:
            audio_bytes = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("The value of 'content_base64' is not valid base64.")

        options["chunk_duration_seconds"] = int(body.get("chunk_duration_seconds", DEFAULT_CHUNK_DURATION_SECONDS))
        options["audio_bitrate"] = str(body.get("audio_bitrate", DEFAULT_AUDIO_BITRATE))
        options["audio_channels"] = int(body.get("audio_channels", DEFAULT_AUDIO_CHANNELS))
        options["sas_expiry_minutes"] = int(body.get("sas_expiry_minutes", DEFAULT_SAS_EXPIRY_MINUTES))
        options["return_private_urls"] = parse_bool(body.get("return_private_urls", False))

        return audio_bytes, options

    audio_bytes = req.get_body()
    if not audio_bytes:
        raise ValueError("The binary body is empty.")

    options["chunk_duration_seconds"] = int(req.params.get("chunk_duration_seconds", DEFAULT_CHUNK_DURATION_SECONDS))
    options["audio_bitrate"] = str(req.params.get("audio_bitrate", DEFAULT_AUDIO_BITRATE))
    options["audio_channels"] = int(req.params.get("audio_channels", DEFAULT_AUDIO_CHANNELS))
    options["sas_expiry_minutes"] = int(req.params.get("sas_expiry_minutes", DEFAULT_SAS_EXPIRY_MINUTES))
    options["return_private_urls"] = parse_bool(req.params.get("return_private_urls", False))

    return audio_bytes, options


def validate_options(options: dict) -> None:
    if options["chunk_duration_seconds"] <= 0:
        raise ValueError("chunk_duration_seconds must be greater than 0.")

    if options["audio_channels"] not in (1, 2):
        raise ValueError("audio_channels must be 1 or 2.")

    if options["sas_expiry_minutes"] <= 0:
        raise ValueError("sas_expiry_minutes must be greater than 0.")


# -----------------------------------------------------------------------------
# SharePoint helpers
# -----------------------------------------------------------------------------

def get_graph_access_token() -> str:
    if not all([AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET]):
        raise RuntimeError(
            "AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET must be configured."
        )

    authority = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}"
    app = msal.ConfidentialClientApplication(
        client_id=AZURE_CLIENT_ID,
        authority=authority,
        client_credential=AZURE_CLIENT_SECRET
    )

    result = app.acquire_token_for_client(scopes=[GRAPH_SCOPE])

    if "access_token" not in result:
        desc = result.get("error_description", result.get("error", "unknown"))
        raise RuntimeError(f"Could not acquire Microsoft Graph token: {desc}")

    return result["access_token"]


def download_from_sharepoint(sharepoint_url: str) -> bytes:
    token = get_graph_access_token()
    auth_headers = {"Authorization": f"Bearer {token}"}

    # Encode the URL using the Graph API u! sharing-link convention
    encoded = base64.urlsafe_b64encode(sharepoint_url.encode("utf-8")).rstrip(b"=").decode("ascii")
    share_id = f"u!{encoded}"

    # Resolve the sharing link to a pre-authenticated download URL
    meta_resp = requests.get(
        f"https://graph.microsoft.com/v1.0/shares/{share_id}/driveItem",
        headers=auth_headers,
        timeout=30
    )

    if meta_resp.status_code == 200:
        download_url = meta_resp.json().get("@microsoft.graph.downloadUrl")
        if download_url:
            file_resp = requests.get(download_url, timeout=120)
            file_resp.raise_for_status()
            return file_resp.content

    # Fallback: authenticated direct download (works for direct file paths)
    direct_resp = requests.get(sharepoint_url, headers=auth_headers, timeout=120, allow_redirects=True)

    if direct_resp.status_code == 200:
        return direct_resp.content

    raise RuntimeError(
        f"Could not download the file from SharePoint (HTTP {direct_resp.status_code}). "
        "Check that the URL is correct and that the app has Files.Read.All permission."
    )


def extract_sharepoint_request_data(req: func.HttpRequest) -> tuple[str, dict]:
    try:
        body = req.get_json()
    except ValueError:
        raise ValueError("The body does not contain valid JSON.")

    sharepoint_url = (body.get("sharepoint_url") or "").strip()
    if not sharepoint_url:
        raise ValueError("The 'sharepoint_url' property is missing or empty.")

    options = {
        "chunk_duration_seconds": int(body.get("chunk_duration_seconds", DEFAULT_CHUNK_DURATION_SECONDS)),
        "audio_bitrate": str(body.get("audio_bitrate", DEFAULT_AUDIO_BITRATE)),
        "audio_channels": int(body.get("audio_channels", DEFAULT_AUDIO_CHANNELS)),
        "sas_expiry_minutes": int(body.get("sas_expiry_minutes", DEFAULT_SAS_EXPIRY_MINUTES)),
        "return_private_urls": parse_bool(body.get("return_private_urls", False))
    }

    return sharepoint_url, options


# -----------------------------------------------------------------------------
# Audio processing
# -----------------------------------------------------------------------------

def detect_input_type(audio_bytes: bytes) -> tuple[str, str]:
    kind = filetype.guess(audio_bytes)
    if kind:
        return kind.extension, kind.mime
    return "bin", "application/octet-stream"


def convert_to_mp3(
    input_path: str,
    output_path: str,
    audio_bitrate: str,
    audio_channels: int
) -> subprocess.CompletedProcess:
    return run_ffmpeg([
        "-y",
        "-i", input_path,
        "-map", "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-c:a", "libmp3lame",
        "-ac", str(audio_channels),
        "-b:a", audio_bitrate,
        output_path
    ])


def split_mp3(
    full_output_path: str,
    output_pattern: str,
    chunk_duration_seconds: int
) -> subprocess.CompletedProcess:
    return run_ffmpeg([
        "-y",
        "-i", full_output_path,
        "-f", "segment",
        "-segment_time", str(chunk_duration_seconds),
        "-reset_timestamps", "1",
        "-c", "copy",
        output_pattern
    ])


# -----------------------------------------------------------------------------
# Main function
# -----------------------------------------------------------------------------

@app.route(route="AudioToMP3Converter", methods=["POST"])
def AudioToMP3Converter(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing audio conversion and chunk upload.")

    input_path = None
    full_output_path = None
    temp_dir = None
    generated_files = []

    try:
        blob_service_client = get_blob_service_client()

        try:
            audio_bytes, options = extract_request_data(req)
            validate_options(options)
        except ValueError as e:
            return json_response({"error": str(e)}, 400)

        input_extension, input_mime = detect_input_type(audio_bytes)

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{input_extension}") as temp_input:
            temp_input.write(audio_bytes)
            input_path = temp_input.name

        full_output_path = input_path.rsplit(".", 1)[0] + "_full.mp3"

        convert_result = convert_to_mp3(
            input_path=input_path,
            output_path=full_output_path,
            audio_bitrate=options["audio_bitrate"],
            audio_channels=options["audio_channels"]
        )

        if convert_result.returncode != 0:
            return json_response(
                {
                    "error": "It was not possible to convert the file to MP3.",
                    "detected_input_mime": input_mime,
                    "detected_input_extension": input_extension,
                    "ffmpeg_error": convert_result.stderr
                },
                500
            )

        temp_dir = tempfile.mkdtemp()
        output_pattern = os.path.join(temp_dir, "chunk_%03d.mp3")

        split_result = split_mp3(
            full_output_path=full_output_path,
            output_pattern=output_pattern,
            chunk_duration_seconds=options["chunk_duration_seconds"]
        )

        if split_result.returncode != 0:
            return json_response(
                {
                    "error": "It was not possible to split the converted MP3 into chunks.",
                    "detected_input_mime": input_mime,
                    "detected_input_extension": input_extension,
                    "ffmpeg_error": split_result.stderr
                },
                500
            )

        generated_files = sorted(glob.glob(os.path.join(temp_dir, "chunk_*.mp3")))

        if not generated_files:
            return json_response(
                {
                    "error": "No output chunks were generated.",
                    "detected_input_mime": input_mime,
                    "detected_input_extension": input_extension
                },
                500
            )

        request_id = str(uuid.uuid4())

        full_blob_name = f"{request_id}/full.mp3"
        full_private_url = upload_file_to_blob(blob_service_client, full_output_path, full_blob_name)
        full_sas_url = generate_blob_sas_url(
            blob_service_client,
            full_blob_name,
            options["sas_expiry_minutes"]
        )

        chunks = []
        for index, chunk_path in enumerate(generated_files, start=1):
            chunk_blob_name = f"{request_id}/chunk_{index:03d}.mp3"
            chunk_private_url = upload_file_to_blob(blob_service_client, chunk_path, chunk_blob_name)
            chunk_sas_url = generate_blob_sas_url(
                blob_service_client,
                chunk_blob_name,
                options["sas_expiry_minutes"]
            )

            chunk_item = {
                "index": index,
                "file_name": os.path.basename(chunk_path),
                "blob_name": chunk_blob_name,
                "output_mime": "audio/mpeg",
                "output_extension": "mp3",
                "file_size_bytes": os.path.getsize(chunk_path),
                "download_url": chunk_sas_url
            }

            if options["return_private_urls"]:
                chunk_item["private_url"] = chunk_private_url

            chunks.append(chunk_item)

        response_body = {
            "request_id": request_id,
            "detected_input_mime": input_mime,
            "detected_input_extension": input_extension,
            "output_mime": "audio/mpeg",
            "output_extension": "mp3",
            "audio_bitrate": options["audio_bitrate"],
            "audio_channels": options["audio_channels"],
            "chunk_duration_seconds": options["chunk_duration_seconds"],
            "sas_expiry_minutes": options["sas_expiry_minutes"],
            "chunk_count": len(chunks),
            "full_file": {
                "blob_name": full_blob_name,
                "file_size_bytes": os.path.getsize(full_output_path),
                "download_url": full_sas_url
            },
            "chunks": chunks
        }

        if options["return_private_urls"]:
            response_body["full_file"]["private_url"] = full_private_url

        return json_response(response_body, 200)

    except Exception as e:
        logging.exception("Unexpected error in the conversion and upload process.")
        return json_response({"error": str(e)}, 500)

    finally:
        cleanup_paths([input_path, full_output_path])
        cleanup_directory(temp_dir)


# -----------------------------------------------------------------------------
# SharePoint endpoint
# -----------------------------------------------------------------------------

@app.route(route="SharePointAudioConverter", methods=["POST"])
def SharePointAudioConverter(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing SharePoint audio conversion and chunk upload.")

    input_path = None
    full_output_path = None
    temp_dir = None

    try:
        blob_service_client = get_blob_service_client()

        try:
            sharepoint_url, options = extract_sharepoint_request_data(req)
            validate_options(options)
        except ValueError as e:
            return json_response({"error": str(e)}, 400)

        try:
            audio_bytes = download_from_sharepoint(sharepoint_url)
        except RuntimeError as e:
            return json_response({"error": str(e)}, 502)

        if not audio_bytes:
            return json_response({"error": "The downloaded file from SharePoint is empty."}, 400)

        input_extension, input_mime = detect_input_type(audio_bytes)

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{input_extension}") as temp_input:
            temp_input.write(audio_bytes)
            input_path = temp_input.name

        full_output_path = input_path.rsplit(".", 1)[0] + "_full.mp3"

        convert_result = convert_to_mp3(
            input_path=input_path,
            output_path=full_output_path,
            audio_bitrate=options["audio_bitrate"],
            audio_channels=options["audio_channels"]
        )

        if convert_result.returncode != 0:
            return json_response(
                {
                    "error": "It was not possible to convert the file to MP3.",
                    "detected_input_mime": input_mime,
                    "detected_input_extension": input_extension,
                    "ffmpeg_error": convert_result.stderr
                },
                500
            )

        temp_dir = tempfile.mkdtemp()
        output_pattern = os.path.join(temp_dir, "chunk_%03d.mp3")

        split_result = split_mp3(
            full_output_path=full_output_path,
            output_pattern=output_pattern,
            chunk_duration_seconds=options["chunk_duration_seconds"]
        )

        if split_result.returncode != 0:
            return json_response(
                {
                    "error": "It was not possible to split the converted MP3 into chunks.",
                    "detected_input_mime": input_mime,
                    "detected_input_extension": input_extension,
                    "ffmpeg_error": split_result.stderr
                },
                500
            )

        generated_files = sorted(glob.glob(os.path.join(temp_dir, "chunk_*.mp3")))

        if not generated_files:
            return json_response(
                {
                    "error": "No output chunks were generated.",
                    "detected_input_mime": input_mime,
                    "detected_input_extension": input_extension
                },
                500
            )

        request_id = str(uuid.uuid4())

        full_blob_name = f"{request_id}/full.mp3"
        full_private_url = upload_file_to_blob(blob_service_client, full_output_path, full_blob_name)
        full_sas_url = generate_blob_sas_url(blob_service_client, full_blob_name, options["sas_expiry_minutes"])

        chunks = []
        for index, chunk_path in enumerate(generated_files, start=1):
            chunk_blob_name = f"{request_id}/chunk_{index:03d}.mp3"
            chunk_private_url = upload_file_to_blob(blob_service_client, chunk_path, chunk_blob_name)
            chunk_sas_url = generate_blob_sas_url(blob_service_client, chunk_blob_name, options["sas_expiry_minutes"])

            chunk_item = {
                "index": index,
                "file_name": os.path.basename(chunk_path),
                "blob_name": chunk_blob_name,
                "output_mime": "audio/mpeg",
                "output_extension": "mp3",
                "file_size_bytes": os.path.getsize(chunk_path),
                "download_url": chunk_sas_url
            }

            if options["return_private_urls"]:
                chunk_item["private_url"] = chunk_private_url

            chunks.append(chunk_item)

        response_body = {
            "request_id": request_id,
            "sharepoint_url": sharepoint_url,
            "detected_input_mime": input_mime,
            "detected_input_extension": input_extension,
            "output_mime": "audio/mpeg",
            "output_extension": "mp3",
            "audio_bitrate": options["audio_bitrate"],
            "audio_channels": options["audio_channels"],
            "chunk_duration_seconds": options["chunk_duration_seconds"],
            "sas_expiry_minutes": options["sas_expiry_minutes"],
            "chunk_count": len(chunks),
            "full_file": {
                "blob_name": full_blob_name,
                "file_size_bytes": os.path.getsize(full_output_path),
                "download_url": full_sas_url
            },
            "chunks": chunks
        }

        if options["return_private_urls"]:
            response_body["full_file"]["private_url"] = full_private_url

        return json_response(response_body, 200)

    except Exception as e:
        logging.exception("Unexpected error in the SharePoint conversion process.")
        return json_response({"error": str(e)}, 500)

    finally:
        cleanup_paths([input_path, full_output_path])
        cleanup_directory(temp_dir)