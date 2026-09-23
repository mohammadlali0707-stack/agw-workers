#!/usr/bin/env python3
import os
import sys
from pathlib import Path

VIDEO = Path("out/final.mp4")


def clean(value):
    return (value or "").strip().strip('"').strip("'")


def main():
    client_id = clean(os.environ.get("YOUTUBE_CLIENT_ID"))
    client_secret = clean(os.environ.get("YOUTUBE_CLIENT_SECRET"))
    refresh_token = clean(os.environ.get("YOUTUBE_REFRESH_TOKEN"))
    if not client_id or not client_secret or not refresh_token:
        print("YouTube secrets missing; skip upload. Artifact only.")
        return
    if not VIDEO.exists() or VIDEO.stat().st_size == 0:
        print("out/final.mp4 missing", file=sys.stderr)
        sys.exit(1)

    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    tags = [t.strip() for t in os.environ.get("YT_TAGS", "").split(",") if t.strip()]
    privacy = os.environ.get("YT_PRIVACY") or "unlisted"
    if privacy not in ("unlisted", "private", "public"):
        privacy = "unlisted"
    body = {
        "snippet": {
            "title": (os.environ.get("YT_TITLE") or "GPTpedia")[:100],
            "description": os.environ.get("YT_DESC") or "",
            "tags": tags,
            "categoryId": "27",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    try:
        youtube = build("youtube", "v3", credentials=creds)
        media = MediaFileUpload(
            str(VIDEO), chunksize=-1, resumable=True, mimetype="video/mp4"
        )
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        response = request.execute()
    except RefreshError as err:
        print("YouTube refresh token is invalid (invalid_grant).")
        print(
            "Generate a new refresh token with the SAME client id and secret, "
            "then update YOUTUBE_REFRESH_TOKEN."
        )
        print(err)
        return
    except HttpError as err:
        print("YouTube API rejected the upload.")
        print(err)
        return
    video_id = response.get("id")
    print(video_id)
    print("https://youtube.com/watch?v=" + str(video_id))


if __name__ == "__main__":
    main()
