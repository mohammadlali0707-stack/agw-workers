#!/usr/bin/env python3
"""Generate an image or video via the Gemini/AI Studio API and save it to disk.

Deterministic: one prompt in, one media file out. No agent loop involved.
Requires GEMINI_API_KEY in the environment and the google-genai package.
"""
import argparse
import os
import sys
import time

from google import genai
from google.genai import types

DEFAULT_IMAGE_MODEL = "imagen-4.0-generate-001"
DEFAULT_VIDEO_MODEL = "veo-3.0-generate-001"
VIDEO_POLL_SECONDS = 10
VIDEO_TIMEOUT_SECONDS = 600


def generate_image(client: "genai.Client", prompt: str, model: str, out_path: str) -> None:
    response = client.models.generate_images(
        model=model,
        prompt=prompt,
        config=types.GenerateImagesConfig(number_of_images=1),
    )
    if not response.generated_images:
        raise RuntimeError("API returned zero images")
    response.generated_images[0].image.save(out_path)


def generate_video(client: "genai.Client", prompt: str, model: str, out_path: str) -> None:
    operation = client.models.generate_videos(model=model, prompt=prompt)
    waited = 0
    while not operation.done:
        if waited >= VIDEO_TIMEOUT_SECONDS:
            raise TimeoutError(f"video generation did not finish within {VIDEO_TIMEOUT_SECONDS}s")
        time.sleep(VIDEO_POLL_SECONDS)
        waited += VIDEO_POLL_SECONDS
        operation = client.operations.get(operation)
    if not operation.response or not operation.response.generated_videos:
        raise RuntimeError("API returned zero videos")
    video = operation.response.generated_videos[0]
    client.files.download(file=video.video)
    video.video.save(out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--media-type", choices=["image", "video"], required=True)
    parser.add_argument("--out", required=True, help="output file path")
    parser.add_argument("--image-model", default=os.environ.get("GEMINI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL))
    parser.add_argument("--video-model", default=os.environ.get("GEMINI_VIDEO_MODEL", DEFAULT_VIDEO_MODEL))
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        sys.exit(1)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    client = genai.Client(api_key=api_key)

    if args.media_type == "image":
        generate_image(client, args.prompt, args.image_model, args.out)
    else:
        generate_video(client, args.prompt, args.video_model, args.out)

    print(args.out)


if __name__ == "__main__":
    main()
