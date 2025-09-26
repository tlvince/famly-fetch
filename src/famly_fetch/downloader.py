#!/usr/bin/env python3

"""Fetch all famly.co pictures of your kid

Auth has two versions:

 - "non-v2" has a `?accessToken=XXX` as a GET-parameter
 - v2-urls demands a `x-famly-accesstoken: XXX` header

"""

import json
import os
import shutil
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import click
import piexif
import piexif.helper

from famly_fetch.api_client import ApiClient
from famly_fetch.image import BaseImage, Image, SecretImage


class FamlyDownloader:
    def __init__(
        self,
        email: str,
        password: str,
        pictures_folder: Path,
        stop_on_existing: bool,
        user_agent: str | None = None,
        access_token: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        no_text_comments: bool = False,
    ):
        self._pictures_folder: Path = pictures_folder
        self._pictures_folder.mkdir(parents=True, exist_ok=True)

        self.stop_on_existing = stop_on_existing
        self.latitude = latitude
        self.longitude = longitude
        self.no_text_comments = no_text_comments
        self.state_file = self._pictures_folder / "state.json"
        self.downloaded_images = self.load_state()

        self._apiClient = ApiClient(user_agent=user_agent, access_token=access_token)
        if not access_token:
            self._apiClient.login(email, password)

    def load_state(self):
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {}

    def save_state(self):
        with open(self.state_file, 'w') as f:
            json.dump(self.downloaded_images, f)

    def mark_as_downloaded(self, img_id: str):
        self.downloaded_images[img_id] = datetime.now(timezone.utc).isoformat()

    def get_all_children(self):
        my_info = self._apiClient.me_me_me()
        all_children = []

        # Current children
        if my_info and "roles2" in my_info:
            for role in my_info["roles2"]:
                all_children.append((role["targetId"], role["title"]))

        # Previous children (that's what they call it)
        prev_children = []
        if my_info and "behaviors" in my_info:
            for ele in my_info["behaviors"]:
                if ele["id"] == "ShowPreviousChildren":
                    prev_children = ele["payload"]["children"]

        for child in prev_children:
            all_children.append((child["childId"], child["name"]["firstName"]))

        return all_children

    def download_images_from_notes(self, child_id, first_name):
        click.secho(
            f"Downloading learning journey images for {first_name}...", fg="green"
        )
        next_ref = None

        while True:
            click.echo("Fetching next 100 notes")
            batch = self._apiClient.get_child_notes(
                child_id, cursor=next_ref, first=100
            )
            if batch and "result" in batch:
                click.echo(f"{len(batch['result'])} fetched.")
            else:
                click.echo("0 fetched.")
                break

            for _i, note in enumerate(batch.get("result", [])):
                text = note["text"] + " - " + note["createdBy"]["name"]["fullName"]
                date = note["createdAt"]

                for img_dict in note["images"]:
                    img = SecretImage.from_dict(
                        img_dict, date_override=date, text_override=None if self.no_text_comments else text
                    )
                    click.echo(f" - image {img.img_id} from note at {img.date}")

                    file_path = self.download_file_path(img, f"{first_name}-note")
                    if img.img_id in self.downloaded_images:
                        click.secho(
                            f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                            fg="yellow",
                        )
                        if self.stop_on_existing:
                            return
                        else:
                            continue
                    self.fetch_image(img, file_path)
                    self.mark_as_downloaded(img.img_id)

            next_ref = batch.get("next") if batch else None

            if not next_ref:
                break

        self.save_state()

    def download_images_from_learning_journey(self, child_id, first_name):
        click.secho(
            f"Downloading learning journey images for {first_name}...", fg="green"
        )

        next_cursor = None

        while True:
            click.echo("Fetching next 100 learning journey entries")
            batch = self._apiClient.learning_journey_query(
                child_id, cursor=next_cursor, first=100
            )
            if batch and "results" in batch:
                click.echo(f"{len(batch['results'])} fetched.")
            else:
                click.echo("0 fetched.")
                break

            for _i, observation in enumerate(batch.get("results", [])):
                text = (
                    observation["remark"]["body"]
                    + " - "
                    + observation["createdBy"]["name"]["fullName"]
                )
                date = observation["status"]["createdAt"]

                for img_dict in observation["images"]:
                    img = SecretImage.from_dict(
                        img_dict, date_override=date, text_override=None if self.no_text_comments else text
                    )
                    click.echo(f" - image {img.img_id} from observation at {img.date}")

                    file_path = self.download_file_path(img, f"{first_name}-journey")
                    if img.img_id in self.downloaded_images:
                        click.secho(
                            f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                            fg="yellow",
                        )
                        if self.stop_on_existing:
                            return
                        else:
                            continue
                    self.fetch_image(img, file_path)
                    self.mark_as_downloaded(img.img_id)

            next_cursor = batch.get("next") if batch else None

            if not next_cursor:
                break

        self.save_state()

    def download_tagged_images(self, child_id, first_name):
        """Download images by childId"""
        click.secho(f"Downloading tagged images for {first_name}...", fg="green")

        imgs = self._apiClient.make_api_request(
            "GET", "/api/v2/images/tagged", params={"childId": child_id}
        )

        if imgs:
            click.echo(f"Fetching {len(imgs)} tagged images for {first_name}")
        else:
            click.echo("0 tagged images found")
            return

        for img_no, img_dict in enumerate(imgs, start=1):
            img = Image.from_dict(img_dict)
            click.echo(f" - image {img.img_id} at {img.date} ({img_no}/{len(imgs)})")

            file_path = self.download_file_path(img, first_name)
            if img.img_id in self.downloaded_images:
                click.secho(
                    f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                    fg="yellow",
                )
                if self.stop_on_existing:
                    return
                else:
                    continue

            # sleep for 1s to avoid 400 errors
            time.sleep(1)
            self.fetch_image(img, file_path)
            self.mark_as_downloaded(img.img_id)

        self.save_state()

    def download_images_from_messages(self):
        click.secho("Downloading images from messages...", fg="green")

        conv_ids = self._apiClient.make_api_request("GET", "/api/v2/conversations")
        if conv_ids:
            click.echo(f"Found {len(conv_ids)} conversations")
        else:
            click.echo("0 conversations found")
            return

        for conv_id in reversed(conv_ids):
            conversation = self._apiClient.make_api_request(
                "GET", "/api/v2/conversations/%s" % (conv_id["conversationId"])
            )
            if conversation and "messages" in conversation:
                for msg in reversed(conversation["messages"]):
                    text = msg["body"] + " - " + msg["author"]["title"]
                    date = msg["createdAt"]

                    for img_dict in msg["images"]:
                        img = Image.from_dict(
                            img_dict, date_override=date, text_override=None if self.no_text_comments else text
                        )

                        click.echo(f" - image {img.img_id} from message at {img.date}")

                        file_path = self.download_file_path(img, "message")

                        if img.img_id in self.downloaded_images:
                            click.secho(
                                f"Image {img.img_id} already downloaded, {'stopping download' if self.stop_on_existing else 'skipping'}.",
                                fg="yellow",
                            )
                            if self.stop_on_existing:
                                return
                            else:
                                continue
                        self.fetch_image(img, file_path)
                        self.mark_as_downloaded(img.img_id)

        self.save_state()

    def download_file_path(self, img: BaseImage, filename_prefix: str) -> Path:
        """Generate the file path for the downloaded image."""

        file_ext = os.path.splitext(urlparse(img.url).path)[1].lower()
        captured_date = img.date.strftime("%Y%m%d_%H%M%S")
        return Path(
            self._pictures_folder,
            f"{captured_date}_{img.img_id}{file_ext}",
        )

    def fetch_image(self, img: BaseImage, file_path: Path):
        req = urllib.request.Request(url=img.url)

        captured_date_for_exif = img.date.strftime("%Y:%m:%d %H:%M:%S")

        with urllib.request.urlopen(req) as r, open(file_path, "wb") as f:
            if r.status != 200:
                raise Exception(f"Broken! {r.read().decode('utf-8')}")
            shutil.copyfileobj(r, f)

        try:
            piexif.load(str(file_path.resolve()))
        except piexif.InvalidImageDataError:
            click.secho(
                "Not a JPEG/TIFF or corrupted image, skip exif updating.", fg="yellow"
            )
            return

        # Prepare the EXIF data
        exif_dict = {
            "Exif": {piexif.ExifIFD.DateTimeOriginal: captured_date_for_exif.encode()}
        }

        if img.text:
            exif_dict["Exif"][piexif.ExifIFD.UserComment] = (
                piexif.helper.UserComment.dump(img.text, encoding="unicode")
            )

        # Add GPS data if latitude and longitude are provided
        if self.latitude is not None and self.longitude is not None:
            from fractions import Fraction

            def to_deg(value, loc):
                if value < 0:
                    loc_value = loc[0]
                elif value > 0:
                    loc_value = loc[1]
                else:
                    loc_value = ""
                abs_value = abs(value)
                deg = int(abs_value)
                t1 = (abs_value - deg) * 60
                min_val = int(t1)
                sec = round((t1 - min_val) * 60, 2)
                return deg, min_val, sec, loc_value

            def to_rational(number):
                f = Fraction(number).limit_denominator(10000)
                return (f.numerator, f.denominator)

            lat_deg = to_deg(self.latitude, ["S", "N"])
            lng_deg = to_deg(self.longitude, ["W", "E"])

            exiv_lat = (to_rational(lat_deg[0]), to_rational(lat_deg[1]), to_rational(lat_deg[2]))
            exiv_lng = (to_rational(lng_deg[0]), to_rational(lng_deg[1]), to_rational(lng_deg[2]))

            exif_dict["GPS"] = {  # type: ignore[assignment]
                piexif.GPSIFD.GPSVersionID: (2, 0, 0, 0),
                piexif.GPSIFD.GPSLatitudeRef: lat_deg[3].encode(),
                piexif.GPSIFD.GPSLatitude: exiv_lat,
                piexif.GPSIFD.GPSLongitudeRef: lng_deg[3].encode(),
                piexif.GPSIFD.GPSLongitude: exiv_lng,
            }

        exif_bytes = piexif.dump(exif_dict)

        # Write the EXIF data to the image
        piexif.insert(exif_bytes, str(file_path.resolve()))
