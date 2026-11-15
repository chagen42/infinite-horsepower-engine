#!/usr/bin/env python3
"""Infinite Horsepower - all-in-one automotive print-on-demand engine.

One command runs the whole pipeline:

    Replicate (artwork)
        -> OpenAI (title / description / SEO tags)
        -> save PNG + JSON metadata
        -> upload image to Printify
        -> create the product
        -> publish it to the connected sales channel

Quick start
-----------
    pip install -r requirements.txt
    cp .env.example .env          # fill in the keys
    python bot.py --shops         # find PRINTIFY_SHOP_ID
    python bot.py --catalog poster
    python bot.py                 # generate + upload + create + publish

Useful switches
---------------
    python bot.py --check          # validate keys/settings, prints no secrets
    python bot.py --list-concepts
    python bot.py --list-products
    python bot.py --count 3 --product tshirt
    python bot.py --style poster          # retro screen print instead of photo
    python bot.py --concept jdm-night --dry-run
    python bot.py --from-existing ./generated/my-art.png --product poster
    python bot.py --publish-existing 66f1a2b3c4d5e6f708091a2b
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import random
import re
import struct
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import NamedTuple
from pathlib import Path

import requests

PRINTIFY_API = "https://api.printify.com/v1"
MAX_TAGS = 13          # Etsy-compatible tag count
MAX_TAG_LENGTH = 20    # Etsy-compatible tag length
DEFAULT_OUTPUT_DIR = "./generated"


# --------------------------------------------------------------------------
# Product catalogue
# --------------------------------------------------------------------------
# Blueprint IDs come from Printify's public catalogue
# (GET /v1/catalog/blueprints.json). The print provider is resolved at
# runtime unless you pin PRINTIFY_PRINT_PROVIDER_ID, because availability
# differs per account and per country.
#
# sizes/colors are *defaults*: they are matched against the variant options
# that Printify reports for the blueprint. If a default matches nothing the
# bot falls back to every in-stock variant and tells you how to pin it.

PRODUCTS: dict[str, dict] = {
    "poster": {
        "label": "poster",
        "blueprint_id": 282,
        "blueprint_name": "Matte Vertical Posters",
        "print_provider_id": None,
        "price_cents": 2999,
        "sizes": "12x18,16x24,24x36",
        "colors": "",
        "aspect_ratio": "2:3",
        "art_direction": (
            "vertical wall-art print, centered subject, clean uncluttered "
            "background, generous margins, no frame, no mockup"
        ),
        "max_variants": 12,
    },
    "canvas": {
        "label": "canvas print",
        "blueprint_id": 555,
        "blueprint_name": "Stretched Canvas",
        "print_provider_id": None,
        "price_cents": 4999,
        "sizes": "16x20,24x30",
        "colors": "",
        "aspect_ratio": "4:5",
        "art_direction": (
            "gallery canvas art, rich painterly texture, centered subject, "
            "edge-to-edge composition, no frame, no mockup"
        ),
        "max_variants": 8,
    },
    "tshirt": {
        "label": "t-shirt",
        "blueprint_id": 6,
        "blueprint_name": "Unisex Heavy Cotton Tee (Gildan 5000)",
        "print_provider_id": None,
        "price_cents": 3299,
        "sizes": "s,m,l,xl,2xl",
        "colors": "Black,White,Navy,Dark Heather,Sport Grey",
        "aspect_ratio": "4:5",
        "art_direction": (
            "bold centered graphic sized for a chest print, flat single-color "
            "background, strong silhouette, no garment mockup"
        ),
        "max_variants": 40,
    },
    "hoodie": {
        "label": "hoodie",
        "blueprint_id": 77,
        "blueprint_name": "Unisex Heavy Blend Hooded Sweatshirt (Gildan 18500)",
        "print_provider_id": None,
        "price_cents": 5499,
        "sizes": "s,m,l,xl,2xl",
        "colors": "Black,White,Navy,Dark Heather,Sport Grey",
        "aspect_ratio": "4:5",
        "art_direction": (
            "bold centered graphic sized for a chest print, flat single-color "
            "background, strong silhouette, no garment mockup"
        ),
        "max_variants": 40,
    },
    "sticker": {
        "label": "sticker",
        "blueprint_id": 400,
        "blueprint_name": "Kiss-Cut Stickers",
        "print_provider_id": None,
        "price_cents": 499,
        "sizes": "",
        "colors": "",
        "aspect_ratio": "1:1",
        "art_direction": (
            "die-cut sticker design, thick white outline, bold flat colors, "
            "isolated subject, no background scenery"
        ),
        "max_variants": 12,
    },
    "phone_case": {
        "label": "phone case",
        "blueprint_id": 268,
        "blueprint_name": "Slim Phone Cases",
        "print_provider_id": None,
        "price_cents": 2499,
        "sizes": "",
        "colors": "",
        "aspect_ratio": "9:16",
        "art_direction": (
            "vertical full-bleed phone case design, main subject in the upper "
            "third, edge-to-edge artwork, no phone mockup"
        ),
        "max_variants": 12,
    },
}

PRODUCT_ALIASES = {
    "poster": "poster", "posters": "poster",
    "canvas": "canvas", "canvas print": "canvas", "canvas prints": "canvas",
    "tshirt": "tshirt", "t-shirt": "tshirt", "tee": "tshirt",
    "tshirts": "tshirt", "t-shirts": "tshirt", "shirt": "tshirt",
    "hoodie": "hoodie", "hoodies": "hoodie", "sweatshirt": "hoodie",
    "sticker": "sticker", "stickers": "sticker",
    "phonecase": "phone_case", "phone_case": "phone_case",
    "phone case": "phone_case", "phone cases": "phone_case", "case": "phone_case",
}


# --------------------------------------------------------------------------
# Automotive concepts
# --------------------------------------------------------------------------
# Each concept is a self-contained art brief. Add your own: the metadata
# prompt is derived from these fields, so new entries get SEO copy for free.

AUTOMOTIVE_CONCEPTS: list[dict] = [
    {
        "id": "muscle-sunset",
        "name": "Muscle Car Sunset",
        "prompt": (
            "a 1969 American muscle car seen from the rear three-quarter, wide "
            "rear tires, long shadows, parked on an empty desert highway at sunset"
        ),
        "style": "retro screen-print poster, limited three-color palette, halftone grain",
        "palette": "burnt orange, deep purple, cream",
        "mood": "nostalgic, warm, cinematic",
        "keywords": ["muscle car", "sunset", "american muscle art", "car silhouette"],
    },
    {
        "id": "jdm-night",
        "name": "JDM Night Run",
        "prompt": (
            "a 1990s Japanese coupe on a rain-slicked Tokyo expressway at night, "
            "neon signs reflected in the wet asphalt, motion blur on the wheels"
        ),
        "style": "cel-shaded illustration, high contrast neon glow, crisp line work",
        "palette": "electric cyan, magenta, near-black",
        "mood": "fast, moody, nocturnal",
        "keywords": ["jdm", "night drive", "tokyo", "neon car art"],
    },
    {
        "id": "offroad-canyon",
        "name": "Canyon Runner",
        "prompt": (
            "a lifted 4x4 truck crawling up a red rock canyon trail, dust plume "
            "behind it, long golden-hour shadows across the sandstone"
        ),
        "style": "painterly travel-poster illustration, warm light, bold shapes",
        "palette": "terracotta, sand, deep teal shadow",
        "mood": "adventurous, rugged, expansive",
        "keywords": ["offroad", "4x4", "canyon", "overland"],
    },
    {
        "id": "garage-blueprint",
        "name": "V8 Blueprint",
        "prompt": (
            "a technical blueprint line drawing of an overhead-valve V8 engine, "
            "exploded view with measurement callouts and dimension arrows"
        ),
        "style": "drafting-table blueprint, fine white line work on deep blue, grid paper",
        "palette": "engineering blue, white, faint cyan",
        "mood": "precise, industrial, collectible",
        "keywords": ["v8", "engine blueprint", "garage art", "mechanic gift"],
    },
    {
        "id": "pit-stop",
        "name": "Pit Stop 1967",
        "prompt": (
            "a 1960s grand-prix pit crew swarming a open-wheel racer, jack handles "
            "up, helmets and goggles, tire smoke drifting across the pit lane"
        ),
        "style": "vintage racing poster, screen-print texture, simplified figures",
        "palette": "racing green, ochre, off-white",
        "mood": "heroic, vintage, kinetic",
        "keywords": ["vintage racing", "race car", "pit crew", "motorsport art"],
    },
    {
        "id": "turbo-torque",
        "name": "Turbo Cutaway",
        "prompt": (
            "an exploded cutaway diagram of a turbocharger, compressor wheel and "
            "turbine housing separated along a centre axis, airflow arrows"
        ),
        "style": "industrial cutaway illustration, metallic shading, clean labels-free line work",
        "palette": "gunmetal, copper, pale grey",
        "mood": "technical, powerful, modern",
        "keywords": ["turbo", "boost", "turbocharger", "car parts art"],
    },
    {
        "id": "rat-rod",
        "name": "Rat Rod Ink",
        "prompt": (
            "a chopped matte-black rat rod with exposed engine and pinstriped "
            "flames, skull shift knob, parked in front of a corrugated garage"
        ),
        "style": "bold black ink illustration, heavy line weight, sparse red accents",
        "palette": "ink black, bone white, oxblood red",
        "mood": "rebellious, gritty, hand-built",
        "keywords": ["rat rod", "hot rod", "pinstripe", "garage decor"],
    },
    {
        "id": "drift-smoke",
        "name": "Drift Smoke",
        "prompt": (
            "a drift car mid-slide through a mountain switchback, rear wheels "
            "throwing thick white tire smoke, headlights cutting the haze"
        ),
        "style": "duotone action poster, high-contrast silhouette, speed lines",
        "palette": "charcoal, bone, signal orange",
        "mood": "loud, fast, fearless",
        "keywords": ["drift", "drift car", "tire smoke", "jdm poster"],
    },
    {
        "id": "v8-heart",
        "name": "V8 Heart",
        "prompt": (
            "an anatomical human heart fused with a V8 engine block, intake "
            "runners becoming arteries, spark plugs where valves should be"
        ),
        "style": "dark surreal illustration, fine engraving detail, dramatic lighting",
        "palette": "oxblood, brass, deep black",
        "mood": "obsessive, dramatic, collector",
        "keywords": ["v8 heart", "car lover gift", "gearhead", "mechanic art"],
    },
    {
        "id": "rally-mud",
        "name": "Rally Mud",
        "prompt": (
            "a rally car airborne over a forest crest, mud spraying from all four "
            "wheels, co-driver silhouetted in the cabin, pines behind"
        ),
        "style": "retro rally poster, grainy print texture, dynamic diagonal composition",
        "palette": "forest green, mud brown, pale sky",
        "mood": "wild, mechanical, adventurous",
        "keywords": ["rally", "rally car", "motorsport", "car art print"],
    },
    {
        "id": "lowrider-boulevard",
        "name": "Lowrider Boulevard",
        "prompt": (
            "a candy-painted lowrider cruising a palm-lined boulevard at dusk, "
            "chrome wire wheels, hydraulics lifted at the front"
        ),
        "style": "1970s lowrider magazine illustration, airbrushed gradients, chrome highlights",
        "palette": "candy purple, chrome silver, dusk pink",
        "mood": "smooth, proud, unmistakable",
        "keywords": ["lowrider", "chrome", "boulevard", "custom car art"],
    },
    {
        "id": "piston-sunset",
        "name": "Piston Sunset",
        "prompt": (
            "a single forged piston and connecting rod standing like a monument on "
            "a flat salt lake, sun setting directly behind the crown"
        ),
        "style": "minimal silhouette poster, wide empty sky, subtle dithering",
        "palette": "amber, dusty rose, slate",
        "mood": "quiet, monumental, iconic",
        "keywords": ["piston", "engine part", "salt flats", "auto art"],
    },
    {
        "id": "diesel-torque",
        "name": "Diesel Torque",
        "prompt": (
            "a heavy-duty pickup with chrome exhaust stacks at a truck stop at "
            "night, wet asphalt, sodium lights, steep front three-quarter angle"
        ),
        "style": "gritty cinematic poster, deep shadows, sharp chrome reflections",
        "palette": "sodium amber, steel blue, black",
        "mood": "heavy, capable, blue-collar",
        "keywords": ["diesel", "truck art", "pickup", "trucker gift"],
    },
    {
        "id": "kustom-flames",
        "name": "Kustom Flames",
        "prompt": (
            "a chopped 1950s kustom coupe with scalloped flame paint, whitewall "
            "tires, parked outside a neon drive-in"
        ),
        "style": "1950s kustom-kulture illustration, pinstripe detailing, bold flat color",
        "palette": "cherry red, cream, chrome",
        "mood": "playful, retro, show-car",
        "keywords": ["hot rod", "kustom", "flames", "drive-in"],
    },
    #
    # Modern sports-car briefs. These read best in photo style; in poster style
    # they fall back to the illustration descriptor each one carries.
    #
    {
        "id": "supercar-dusk",
        "name": "Supercar at Dusk",
        "prompt": (
            "a modern mid-engine supercar in deep metallic blue parked on a "
            "rooftop terrace at dusk, city skyline behind it, LED tail lights lit"
        ),
        "style": "glossy automotive magazine illustration, high contrast, reflective paint",
        "palette": "midnight blue, amber city glow, black",
        "mood": "premium, still, atmospheric",
        "keywords": ["supercar", "mid engine", "city dusk", "sports car", "supercar art"],
    },
    {
        "id": "hypercar-wet",
        "name": "Hypercar in the Rain",
        "prompt": (
            "a low-slung hypercar on a rain-soaked city street at night, neon "
            "reflections in the wet asphalt, brake discs glowing orange through "
            "the wheels"
        ),
        "style": "cinematic night illustration, wet reflections, strong rim light",
        "palette": "magenta, cyan, black",
        "mood": "fast, expensive, electric",
        "keywords": ["hypercar", "night street", "wet asphalt", "exotic car", "supercar"],
    },
    {
        "id": "gt-racer",
        "name": "GT Race Car",
        "prompt": (
            "a GT-class endurance race car cornering hard on a racetrack, front "
            "wheel loaded, tire smoke at the limit of grip, blurred grandstands"
        ),
        "style": "motorsport action poster, motion blur, aggressive cropping",
        "palette": "white, racing red, asphalt grey",
        "mood": "loud, precise, competitive",
        "keywords": ["race car", "motorsport", "endurance racing", "track day", "gt racing"],
    },
    {
        "id": "canyon-super",
        "name": "Canyon Supercar",
        "prompt": (
            "a silver supercar on a narrow canyon road at golden hour, rock walls "
            "on both sides, long shadows raking across the asphalt"
        ),
        "style": "landscape automotive illustration, warm light, wide vista",
        "palette": "silver, terracotta, deep shadow",
        "mood": "open road, confident, warm",
        "keywords": ["supercar", "canyon road", "sports car", "golden hour", "road trip"],
    },
    {
        "id": "coastal-coupe",
        "name": "Coastal Classic",
        "prompt": (
            "an air-cooled classic German sports coupe on a coastal highway at "
            "sunrise, ocean on one side, chrome bumper catching the light"
        ),
        "style": "warm analog-photo illustration, soft grain, pastel sky",
        "palette": "sand, sea blue, warm chrome",
        "mood": "calm, timeless, early morning",
        "keywords": ["classic coupe", "air cooled", "coastal road", "sports car", "car photo"],
    },
    {
        "id": "studio-super",
        "name": "Studio Supercar",
        "prompt": (
            "a supercar in a dark photo studio, rim lighting tracing the bodywork, "
            "glossy floor reflecting the sills, three-quarter front view"
        ),
        "style": "studio product illustration, deep blacks, precise highlights",
        "palette": "black, graphite, cool white light",
        "mood": "clinical, expensive, sculptural",
        "keywords": ["supercar", "studio shot", "sports car art", "rim light", "exotic car"],
    },
]

# Generic shop tags used to top up every listing. Kept deliberately subject-
# neutral: the concept keywords and the model's own tags supply the specifics.
AUTOMOTIVE_TAGS = [
    "automotive art",
    "car art",
    "car poster",
    "garage decor",
    "gearhead gift",
    "car lover gift",
    "man cave decor",
    "vintage car",
    "car enthusiast",
    "car artwork",
    "motorsport",
]


# --------------------------------------------------------------------------
# Errors and env helpers
# --------------------------------------------------------------------------

class BotError(RuntimeError):
    """A configuration or API problem that should stop the run."""


class PrintifyError(BotError):
    """Printify answered with an error status."""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def configure_console() -> None:
    """Windows consoles default to cp1252, but model copy contains unicode
    (curly quotes, inch marks, em dashes). Without this, a listing title can
    raise UnicodeEncodeError in the middle of a run."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def load_env() -> None:
    """Load .env from the working directory (or next to bot.py)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    here = Path(__file__).resolve().parent
    for candidate in (Path.cwd() / ".env", here / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            return
    load_dotenv()


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise BotError(f"{name} must be an integer, got {raw!r}") from exc


def env_float(name: str, default: float) -> float:
    raw = env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise BotError(f"{name} must be a number, got {raw!r}") from exc


def env_bool(name: str, default: bool) -> bool:
    raw = env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def require(name: str, hint: str = "") -> str:
    value = env(name)
    if not value:
        message = f"{name} is not set"
        if hint:
            message += f" - {hint}"
        raise BotError(message)
    return value


def normalize_product(value: str | None) -> str:
    if not value:
        return "poster"
    key = PRODUCT_ALIASES.get(value.strip().lower())
    if key is None:
        options = ", ".join(sorted(PRODUCTS))
        raise BotError(f"unknown product {value!r}. Choose one of: {options}")
    return key


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------

def slugify(text: str, limit: int = 48) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return cleaned[:limit] or "artwork"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def image_extension(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def png_size(path: Path) -> tuple[int, int] | None:
    """Read width/height straight from the PNG header (no Pillow needed)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return int(width), int(height)


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def norm_option(text: object) -> str:
    """Normalise a Printify option value: '12" x 18"' -> '12x18'."""
    value = str(text or "").lower()
    value = value.replace("\u00d7", "x").replace("\u2033", "").replace('"', "")
    value = value.replace("\u2019", "").replace("'", "")
    return re.sub(r"\s+", "", value)


def split_tokens(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [norm_option(part) for part in raw.split(",") if part.strip()]


def filter_by_option(variants: list[dict], option: str, tokens: list[str]) -> list[dict]:
    """Filter variants on one option value.

    An exact pass runs first so that "xl" does not swallow "3xl" and "black"
    does not swallow "blackberry". Substring matching is only used when nothing
    matches exactly, which covers providers that name colours "Solid Black".
    """
    if not tokens:
        return variants

    def value_of(variant: dict) -> str:
        return norm_option((variant.get("options") or {}).get(option))

    exact = [variant for variant in variants if value_of(variant) in tokens]
    if exact:
        return exact
    return [
        variant
        for variant in variants
        if any(token and token in value_of(variant) for token in tokens)
    ]


# --------------------------------------------------------------------------
# Replicate: artwork generation
# --------------------------------------------------------------------------

ART_STYLES: dict[str, dict] = {
    "photo": {
        "label": "realistic automotive photography",
        # Deliberately avoids the words the poster style uses (flat, screen
        # print, limited palette); those pull the model back toward illustration.
        "direction": (
            "ultra-realistic automotive photograph, full-frame camera, 50mm lens "
            "at f/4, wide three-quarter view taken from slightly below the "
            "beltline, the entire car from front bumper to rear bumper fully "
            "inside the frame with nothing cropped or cut off, all four wheels "
            "and the complete roofline visible, generous space around the "
            "vehicle, glossy paint with true environment reflections, realistic "
            "chrome, rubber and brake dust, dramatic natural light, deep focus "
            "across the whole car, fine surface detail, photographic grain, no "
            "illustration, no print texture, no flat colour"
        ),
        "use_concept_palette": False,
    },
    "poster": {
        "label": "retro screen-print poster",
        "direction": None,  # concept['style'] supplies this
        "use_concept_palette": True,
    },
}

STYLE_ALIASES = {
    "photo": "photo",
    "photoreal": "photo",
    "photorealistic": "photo",
    "realistic": "photo",
    "realism": "photo",
    "photography": "photo",
    "poster": "poster",
    "print": "poster",
    "illustration": "poster",
    "vector": "poster",
}

# How the car should sit in frame, per product, when shooting "photo" style.
PHOTO_FRAMING = {
    "poster": (
        "vertical composition with open sky above and clear ground below, "
        "the car sitting in the middle third"
    ),
    "canvas": "the whole car centred with foreground and background context on every side",
    "tshirt": "the entire car isolated and centred on a plain flat backdrop, product-ready",
    "hoodie": "the entire car isolated and centred on a plain flat backdrop, product-ready",
    "sticker": "the entire car isolated on a plain backdrop, bold readable silhouette",
    "phone_case": (
        "vertical composition with the entire car in the upper half and open space below"
    ),
}


def normalize_style(value: str | None) -> str:
    key = STYLE_ALIASES.get((value or "photo").strip().lower())
    if key is None:
        raise BotError(
            f"unknown style {value!r}. Choose one of: {', '.join(sorted(ART_STYLES))}"
        )
    return key


def build_art_prompt(
    concept: dict, product: dict, product_key: str, style: str = "photo"
) -> str:
    style_key = normalize_style(style)
    style_config = ART_STYLES[style_key]
    framing = PHOTO_FRAMING.get(product_key, PHOTO_FRAMING["poster"])

    if style_key == "photo":
        return (
            f"{concept['prompt']}. {style_config['direction']}. {framing}. "
            f"Mood: {concept['mood']}. No text, no lettering, no watermark, no border."
        )

    palette = f"Colour palette: {concept['palette']}. " if style_config["use_concept_palette"] else ""
    return (
        f"{concept['prompt']}. {concept['style']}. {product['art_direction']}. "
        f"{palette}Mood: {concept['mood']}. "
        "No text, no lettering, no signature, no watermark, no border."
    )


# The replicate SDK (1.0.7, the latest) cannot parse the API's current
# prediction payload: model-scoped predictions come back with "version": null
# and its pydantic model rejects that. These calls talk to the HTTP API
# directly instead, which also drops a dependency.
REPLICATE_API = "https://api.replicate.com/v1"
REPLICATE_POLL_SECONDS = 2.0
REPLICATE_TIMEOUT_SECONDS = 300


def replicate_auth() -> str:
    token = env("REPLICATE_API_TOKEN") or env("REPLICATE_API_KEY")
    if not token:
        raise BotError(
            "REPLICATE_API_TOKEN is not set - add it to .env "
            "(REPLICATE_API_KEY also works)"
        )
    return token


def replicate_http(
    method: str,
    url: str,
    token: str,
    *,
    extra_headers: dict | None = None,
    **kwargs: object,
) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    response = requests.request(method, url, headers=headers, timeout=300, **kwargs)  # type: ignore[arg-type]
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:300]
        raise BotError(f"Replicate {method} failed ({response.status_code}): {detail}")
    try:
        return response.json()
    except ValueError as exc:
        raise BotError("Replicate returned a non-JSON response") from exc


def start_prediction(token: str, model: str, payload: dict) -> dict:
    """"owner/name" runs the model's latest version; a bare hash pins a version."""
    if "/" in model:
        url = f"{REPLICATE_API}/models/{model}/predictions"
        body: dict[str, object] = {"input": payload}
    else:
        url = f"{REPLICATE_API}/predictions"
        body = {"version": model, "input": payload}
    # Replicate only accepts wait values from 1 to 60.
    return replicate_http("POST", url, token, extra_headers={"Prefer": "wait=60"}, json=body)


def wait_for_prediction(token: str, prediction: dict) -> dict:
    deadline = time.time() + REPLICATE_TIMEOUT_SECONDS
    while prediction.get("status") in {"starting", "processing"}:
        if time.time() > deadline:
            raise BotError(
                f"Replicate prediction still running after {REPLICATE_TIMEOUT_SECONDS}s"
            )
        time.sleep(REPLICATE_POLL_SECONDS)
        poll_url = (prediction.get("urls") or {}).get("get")
        if not poll_url:
            raise BotError("Replicate returned no polling URL")
        prediction = replicate_http("GET", str(poll_url), token)
    if prediction.get("status") != "succeeded":
        raise BotError(
            f"Replicate prediction {prediction.get('status')}: {prediction.get('error')}"
        )
    return prediction


def prediction_urls(prediction: dict) -> list[str]:
    output = prediction.get("output")
    if isinstance(output, str):
        return [output]
    if isinstance(output, list):
        return [str(item) for item in output if item]
    return []


def generate_image(prompt: str, aspect_ratio: str, out_dir: Path, stem: str) -> Path:
    """Run Replicate and write the artwork to disk. Returns the image path."""
    token = replicate_auth()
    model = env("REPLICATE_MODEL", "black-forest-labs/flux-schnell")
    payload: dict[str, object] = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "output_format": "png",
        "num_outputs": 1,
    }
    # flux-schnell defaults to 1 megapixel (~832x1216 at 2:3), which is thin
    # for a poster. Models without this input ignore REPLICATE_EXTRA_INPUT's
    # equivalent, so it stays opt-in.
    megapixels = env("REPLICATE_MEGAPIXELS")
    if megapixels:
        payload["megapixels"] = megapixels

    extra = env("REPLICATE_EXTRA_INPUT")
    if extra:
        try:
            payload.update(json.loads(extra))
        except json.JSONDecodeError as exc:
            raise BotError("REPLICATE_EXTRA_INPUT must be valid JSON") from exc

    print(f"  [art] {model} ({aspect_ratio}) ...")
    prediction = start_prediction(token, model, payload)
    prediction = wait_for_prediction(token, prediction)
    urls = prediction_urls(prediction)
    if not urls:
        raise BotError("Replicate returned no image")

    response = requests.get(urls[0], timeout=180)
    response.raise_for_status()
    data = response.content
    if not data:
        raise BotError("Replicate returned an empty image")

    path = out_dir / f"{stem}{image_extension(data)}"
    path.write_bytes(data)
    if path.suffix == ".webp":
        raise BotError(
            f"Replicate returned a WEBP image ({path.name}); Printify only accepts "
            "PNG or JPEG. Set REPLICATE_MODEL to one that honours output_format=png."
        )
    print(f"  [art] saved {path.name} ({path.stat().st_size / 1024:.0f} KB)")
    return path


# --------------------------------------------------------------------------
# OpenAI: metadata
# --------------------------------------------------------------------------

METADATA_SYSTEM = (
    "You are a senior e-commerce copywriter for Infinite Horsepower, a US shop "
    "selling automotive wall art and gear to gearheads. You write listings that "
    "rank in search and read like a human wrote them: concrete, confident, no "
    "hype words like 'stunning' or 'perfect', no emoji. Always answer with a "
    "single valid JSON object and nothing else."
)


def generate_metadata(concept: dict, product: dict) -> dict:
    """Ask OpenAI for title/description/tags/alt text. Returns a clean dict."""
    api_key = require("OPENAI_API_KEY", "add it to .env or the Render dashboard")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise BotError("the openai package is missing: pip install -r requirements.txt") from exc

    client = OpenAI(api_key=api_key)
    model = env("OPENAI_MODEL", "gpt-4o-mini")

    user_prompt = f"""Write the listing metadata for a {product['label']} called "{concept['name']}".

Artwork: {concept['prompt']}.
Style: {concept['style']}.
Palette: {concept['palette']}.
Mood: {concept['mood']}.

Return JSON with exactly these keys:
- "title": 45-70 characters, the product name plus a short descriptive hook. No all-caps words, no quotes.
- "description": 90-160 words, plain text with 3 short bullet lines starting with "- " (use \\n between lines). Mention the subject, the mood, where it belongs (garage, workshop, man cave, office), and who it is for. If it is apparel or a case, mention that the print is durable.
- "tags": array of exactly {MAX_TAGS} lowercase search tags, each at most {MAX_TAG_LENGTH} characters, no duplicates, mixing broad automotive terms with specific ones from this artwork.
- "alt_text": at most 125 characters describing the artwork for screen readers.

Return only the JSON object."""

    print(f"  [meta] {model} ...")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": METADATA_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.9,
    )
    raw = (response.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(strip_code_fences(raw))
    if not isinstance(data, dict):
        raise BotError("OpenAI returned metadata that is not a JSON object")

    title = str(data.get("title") or concept["name"]).strip()
    description = str(data.get("description") or "").strip() or fallback_description(concept, product)
    alt_text = str(data.get("alt_text") or title).strip()[:125]
    raw_tags = data.get("tags") or []

    return {
        "title": title[:110],
        "description": description,
        "alt_text": alt_text,
        "model_tags": [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else [],
    }


def fallback_description(concept: dict, product: dict) -> str:
    return (
        f"{concept['name']} - an automotive {product['label']} built around "
        f"{concept['prompt']}.\n\n"
        f"- {concept['style'].capitalize()}\n"
        f"- Palette: {concept['palette']}\n"
        f"- Mood: {concept['mood']}\n\n"
        "Made for garages, workshops and offices that smell faintly of gasoline."
    )


def default_tags(*groups: object) -> list[str]:
    """Merge tag sources into a clean, Etsy-safe list (13 tags, 20 chars each)."""
    tags: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if group is None:
            continue
        if isinstance(group, str):
            candidates = [group]
        else:
            candidates = list(group)  # type: ignore[arg-type]
        for raw in candidates:
            tag = str(raw).lower()
            tag = re.sub(r"[^a-z0-9 &'-]", " ", tag)
            tag = re.sub(r"\s+", " ", tag).strip()
            if len(tag) > MAX_TAG_LENGTH:
                clipped = tag[:MAX_TAG_LENGTH]
                # cut on a word boundary; drop single words that cannot fit
                tag = clipped.rsplit(" ", 1)[0].strip() if " " in clipped else ""
            if not tag or tag in seen:
                continue
            seen.add(tag)
            tags.append(tag)
            if len(tags) == MAX_TAGS:
                return tags
    return tags


def save_metadata(record: dict, out_dir: Path) -> Path:
    """Write the listing record next to the artwork as JSON."""
    path = out_dir / f"{record['slug']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  [meta] saved {path}")
    return path


def update_record_publish_state(
    product_id: str, published: bool, detail: str = ""
) -> Path | None:
    """Find the metadata record for a product and correct its publish state."""
    out_dir = Path(env("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)).expanduser()
    if not out_dir.is_dir():
        return None
    for path in sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        info = record.get("printify_product")
        if not isinstance(info, dict) or str(info.get("id")) != str(product_id):
            continue
        info["published"] = published
        if published:
            info.pop("publish_error", None)
            record["status"] = "published"
        else:
            if detail:
                info["publish_error"] = detail
            record["status"] = "draft"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
    return None


# --------------------------------------------------------------------------
# Printify: HTTP helpers
# --------------------------------------------------------------------------

def printify_token() -> str:
    return require(
        "PRINTIFY_API_TOKEN",
        "create one at Printify -> Settings -> API tokens",
    )


def printify_request(
    method: str,
    path: str,
    *,
    token: str,
    json_body: dict | None = None,
    params: dict | None = None,
    files: dict | None = None,
    attempts: int = 3,
) -> requests.Response:
    url = f"{PRINTIFY_API}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    response: requests.Response | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                files=files,
                timeout=120,
            )
        except requests.RequestException as exc:
            if attempt == attempts:
                raise BotError(f"Printify request failed: {exc}") from exc
            time.sleep(2 ** attempt)
            continue

        if response.status_code in {429, 500, 502, 503, 504} and attempt < attempts:
            try:
                wait = float(response.headers.get("Retry-After", "") or 0)
            except ValueError:
                wait = 0
            time.sleep(max(wait, 2 ** attempt))
            continue
        return response

    assert response is not None
    return response


def printify_json(method: str, path: str, *, token: str, **kwargs: object) -> object:
    response = printify_request(method, path, token=token, **kwargs)  # type: ignore[arg-type]
    if response.status_code >= 400:
        raise PrintifyError(
            f"Printify {method} {path} failed with {response.status_code}",
            status=response.status_code,
            body=response.text[:600],
        )
    try:
        return response.json()
    except ValueError as exc:
        raise BotError(f"Printify returned non-JSON for {path}") from exc


# --------------------------------------------------------------------------
# Printify: shop / catalog
# --------------------------------------------------------------------------

def list_shops(token: str | None = None) -> list[dict]:
    token = token or printify_token()
    shops = printify_json("GET", "/shops.json", token=token)
    return shops if isinstance(shops, list) else []


def resolve_shop_id(token: str | None = None) -> int:
    token = token or printify_token()
    configured = env("PRINTIFY_SHOP_ID")
    if configured:
        return int(configured)

    shops = list_shops(token)
    if len(shops) == 1:
        shop_id = int(shops[0]["id"])
        print(f"  [shop] using the only shop on the account: {shop_id}")
        return shop_id

    lines = [f"    {shop.get('id')}  {shop.get('title')}" for shop in shops] or ["    (none found)"]
    raise BotError(
        "PRINTIFY_SHOP_ID is not set and the account has "
        f"{len(shops)} shops. Pick one and add it to .env:\n" + "\n".join(lines)
    )


def resolve_print_provider(
    token: str,
    blueprint_id: int,
    requested: int | None = None,
    providers: list | None = None,
) -> tuple[int, str]:
    if requested:
        return int(requested), str(requested)

    if providers is None:
        providers = printify_json(
            "GET", f"/catalog/blueprints/{blueprint_id}/print_providers.json", token=token
        )
    if not isinstance(providers, list) or not providers:
        raise BotError(f"no print providers available for blueprint {blueprint_id}")

    chosen = next((p for p in providers if int(p.get("id", 0)) == 99), providers[0])
    provider_id = int(chosen["id"])
    title = str(chosen.get("title") or provider_id)
    print(f"  [print] provider {provider_id} {title}")
    return provider_id, title


def fetch_variants(token: str, blueprint_id: int, provider_id: int) -> list[dict]:
    payload = printify_json(
        "GET",
        f"/catalog/blueprints/{blueprint_id}/print_providers/{provider_id}/variants.json",
        token=token,
        params={"show-out-of-stock": 1},
    )
    variants = payload.get("variants") if isinstance(payload, dict) else None
    if not variants:
        raise BotError(
            f"blueprint {blueprint_id} / provider {provider_id} returned no variants"
        )
    return list(variants)


def available_options(variants: list[dict], option: str) -> list[str]:
    values = {
        str((variant.get("options") or {}).get(option))
        for variant in variants
        if (variant.get("options") or {}).get(option)
    }
    return sorted(values)


def select_variants(
    variants: list[dict],
    *,
    sizes: str | None,
    colors: str | None,
    max_variants: int,
    strict_sizes: bool,
) -> list[dict]:
    """Filter catalogue variants by size/colour, then cap the list."""
    size_tokens = split_tokens(sizes)
    color_tokens = split_tokens(colors)

    if size_tokens:
        matched = filter_by_option(variants, "size", size_tokens)
        if matched:
            variants = matched
        elif strict_sizes:
            options = ", ".join(available_options(variants, "size")) or "(none reported)"
            raise BotError(
                f"no variants match PRINTIFY_SIZES={sizes!r}. Available sizes: {options}"
            )
        else:
            print(f"  [warn] no size match for {sizes!r}; using all in-stock variants")

    if color_tokens:
        matched = filter_by_option(variants, "color", color_tokens)
        if matched:
            variants = matched
        else:
            options = ", ".join(available_options(variants, "color")) or "(none reported)"
            print(f"  [warn] no colour match for {colors!r}; available: {options}")

    if max_variants > 0 and len(variants) > max_variants:
        print(
            f"  [warn] {len(variants)} variants matched; enabling {max_variants} "
            "spread across the requested colours and sizes"
        )
        variants = _balanced_slice(variants, size_tokens, color_tokens, max_variants)
    return variants


def _balanced_slice(
    variants: list[dict],
    size_tokens: list[str],
    color_tokens: list[str],
    limit: int,
) -> list[dict]:
    """Trim a variant list without starving colours or size runs.

    Truncating the catalogue order gives a wall of one colour, so instead the
    list is grouped by colour, each group is sorted by the requested size
    order, and variants are taken round-robin until the limit is reached.
    """

    def rank(value: str, tokens: list[str]) -> int:
        return tokens.index(value) if value in tokens else len(tokens)

    def color_of(variant: dict) -> str:
        return norm_option((variant.get("options") or {}).get("color")) or "_"

    def size_of(variant: dict) -> str:
        return norm_option((variant.get("options") or {}).get("size")) or "_"

    groups: dict[str, list[dict]] = {}
    for variant in variants:
        groups.setdefault(color_of(variant), []).append(variant)
    for group in groups.values():
        group.sort(key=lambda v: (rank(size_of(v), size_tokens), size_of(v)))

    ordered = sorted(groups.items(), key=lambda item: (rank(item[0], color_tokens), item[0]))
    picked: list[dict] = []
    while len(picked) < limit and any(group for _, group in ordered):
        for _, group in ordered:
            if not group:
                continue
            picked.append(group.pop(0))
            if len(picked) >= limit:
                break

    picked.sort(key=lambda v: (rank(color_of(v), color_tokens), rank(size_of(v), size_tokens)))
    return picked


def variant_positions(variant: dict) -> list[str]:
    return [
        str(placeholder.get("position"))
        for placeholder in (variant.get("placeholders") or [])
        if placeholder.get("position")
    ]


def pick_position(variants: list[dict], requested: str | None = None) -> str:
    if requested:
        return requested
    seen: list[str] = []
    for variant in variants:
        for position in variant_positions(variant):
            if position not in seen:
                seen.append(position)
    if not seen:
        raise BotError("Printify reported no print positions for these variants")
    for preferred in ("front", "default", "back"):
        if preferred in seen:
            return preferred
    return seen[0]


# --------------------------------------------------------------------------
# Printify: upload, product, publish
# --------------------------------------------------------------------------

def upload_to_printify(image_path: str | Path, token: str | None = None) -> dict:
    """Upload a local image to the Printify media library. Returns the upload dict."""
    token = token or printify_token()
    path = Path(image_path)
    if not path.exists():
        raise BotError(f"image not found: {path}")

    raw = path.read_bytes()
    payload = {
        "file_name": path.name,
        "contents": base64.b64encode(raw).decode("ascii"),
    }
    response = printify_request("POST", "/uploads/images.json", token=token, json_body=payload)

    if response.status_code in {400, 415, 422}:
        # Older accounts/mock servers only take multipart uploads.
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        print("  [print] retrying upload as multipart/form-data")
        response = printify_request(
            "POST",
            "/uploads/images.json",
            token=token,
            files={"file": (path.name, raw, mime)},
        )

    if response.status_code >= 400:
        raise PrintifyError(
            f"image upload failed ({response.status_code})",
            status=response.status_code,
            body=response.text[:600],
        )

    data = response.json()
    if not data.get("id"):
        raise BotError(f"Printify upload returned no image id: {data}")
    print(f"  [print] uploaded image id {data['id']}")
    return data


def build_product_payload(
    *,
    title: str,
    description: str,
    tags: list[str],
    blueprint_id: int,
    print_provider_id: int,
    image_id: str,
    variants: list[dict],
    position: str,
    price_cents: int,
    scale: float = 1.0,
    print_on_side: str | None = None,
) -> dict:
    """Assemble the POST body for /v1/shops/{shop}/products.json."""
    eligible = [v for v in variants if position in variant_positions(v)]
    if not eligible:
        positions = sorted({p for v in variants for p in variant_positions(v)})
        raise BotError(
            f"print position {position!r} is not available on these variants; "
            f"available: {', '.join(positions) or 'none'}"
        )
    variant_ids = [int(v["id"]) for v in eligible]

    payload: dict[str, object] = {
        "title": title[:120],
        "description": description,
        "blueprint_id": int(blueprint_id),
        "print_provider_id": int(print_provider_id),
        "variants": [
            {"id": variant_id, "price": int(price_cents), "is_enabled": True}
            for variant_id in variant_ids
        ],
        "print_areas": [
            {
                "variant_ids": variant_ids,
                "placeholders": [
                    {
                        "position": position,
                        "images": [
                            {
                                "id": image_id,
                                "x": 0.5,
                                "y": 0.5,
                                "scale": float(scale),
                                "angle": 0,
                            }
                        ],
                    }
                ],
            }
        ],
        "tags": tags,
        "visible": True,
    }
    if print_on_side:
        payload["print_details"] = {"print_on_side": print_on_side}
    return payload


def create_printify_product(
    metadata: dict,
    image_id: str,
    product: str = "poster",
    *,
    shop_id: int | None = None,
    token: str | None = None,
    price_cents: int | None = None,
    sizes: str | None = None,
    colors: str | None = None,
    scale: float | None = None,
    position: str | None = None,
) -> dict:
    """Create a Printify product from an uploaded image. Returns the product dict."""
    token = token or printify_token()
    product_key = normalize_product(product)
    config = PRODUCTS[product_key]
    shop_id = int(shop_id) if shop_id else resolve_shop_id(token)

    blueprint_env = env("PRINTIFY_BLUEPRINT_ID")
    blueprint_id = int(blueprint_env) if blueprint_env else int(config["blueprint_id"])
    provider_env = env("PRINTIFY_PRINT_PROVIDER_ID")
    provider_id, _ = resolve_print_provider(
        token, blueprint_id, int(provider_env) if provider_env else config["print_provider_id"]
    )

    variants = fetch_variants(token, blueprint_id, provider_id)
    chosen = select_variants(
        variants,
        sizes=sizes if sizes is not None else env("PRINTIFY_SIZES", config["sizes"]),
        colors=colors if colors is not None else env("PRINTIFY_COLORS", config["colors"]),
        max_variants=env_int("PRINTIFY_MAX_VARIANTS", int(config["max_variants"])),
        strict_sizes=env("PRINTIFY_SIZES") is not None or sizes is not None,
    )
    if not chosen:
        raise BotError("no in-stock variants to enable for this blueprint")

    chosen_position = pick_position(
        chosen, position or env("PRINTIFY_PLACEHOLDER_POSITION") or None
    )
    resolved_price = price_cents or env_int("PRINTIFY_PRICE_CENTS", int(config["price_cents"]))
    resolved_scale = scale if scale is not None else env_float("PRINTIFY_IMAGE_SCALE", 1.0)

    payload = build_product_payload(
        title=metadata["title"],
        description=metadata["description"],
        tags=list(metadata.get("tags") or []),
        blueprint_id=blueprint_id,
        print_provider_id=provider_id,
        image_id=image_id,
        variants=chosen,
        position=chosen_position,
        price_cents=resolved_price,
        scale=resolved_scale,
        print_on_side=(
            env("PRINTIFY_PRINT_ON_SIDE", "regular") if product_key == "canvas" else None
        ),
    )

    print(
        f"  [print] creating {product_key}: {len(chosen)} variants, "
        f"${resolved_price / 100:.2f}, position {chosen_position}"
    )
    created = printify_json(
        "POST", f"/shops/{shop_id}/products.json", token=token, json_body=payload
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise BotError(f"Printify did not return a product id: {created}")
    print(f"  [print] product id {created['id']}")
    # The create response lists every variant of the blueprint; only the chosen
    # ones are enabled. Keep the real number for the metadata record.
    created["_enabled_variants"] = len(chosen)
    return created


class PublishResult(NamedTuple):
    ok: bool
    status: int
    message: str


def publish_product(product_id: str, shop_id: int, token: str | None = None) -> PublishResult:
    """Send the product to the connected sales channel."""
    token = token or printify_token()
    body = {
        "title": True,
        "description": True,
        "images": True,
        "variants": True,
        "tags": True,
        "keyFeatures": True,
        "shipping_template": True,
    }
    response = printify_request(
        "POST",
        f"/shops/{shop_id}/products/{product_id}/publish.json",
        token=token,
        json_body=body,
    )
    if response.status_code >= 400:
        message = response.text[:400].strip()
        print(
            f"  [warn] publish failed ({response.status_code}): "
            f"{message[:240]} - the product stays a draft in Printify"
        )
        return PublishResult(False, response.status_code, message)
    print("  [print] published to the sales channel")
    return PublishResult(True, response.status_code, "")


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def select_concepts(count: int, requested: str | None, seed: int | None) -> list[dict]:
    pool = AUTOMOTIVE_CONCEPTS
    if requested:
        needle = requested.strip().lower()
        matches = [
            concept
            for concept in pool
            if needle in concept["id"].lower() or needle in concept["name"].lower()
        ]
        if not matches:
            names = ", ".join(concept["id"] for concept in pool)
            raise BotError(f"no concept matches {requested!r}. Available: {names}")
        return [matches[index % len(matches)] for index in range(count)]

    rng = random.Random(seed) if seed is not None else random.Random()
    if count <= len(pool):
        return rng.sample(pool, count)
    picks = list(pool)
    while len(picks) < count:
        picks.append(pool[len(picks) % len(pool)])
    return picks


def run_pipeline(args: argparse.Namespace) -> int:
    product_key = normalize_product(args.product or env("PRINTIFY_PRODUCT_TYPE"))
    config = PRODUCTS[product_key]
    style_key = normalize_style(args.style or env("ART_STYLE", "photo"))
    out_dir = Path(args.output_dir or env("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    concepts = select_concepts(args.count, args.concept, args.seed)

    print("=" * 68)
    print("Infinite Horsepower - automotive POD engine")
    print(f"  product : {product_key} ({config['blueprint_name']})")
    print(f"  style   : {style_key} ({ART_STYLES[style_key]['label']})")
    print(f"  output  : {out_dir.resolve()}")
    print(f"  items   : {len(concepts)}")
    print(f"  printify: {'off (dry run)' if args.dry_run else (args.from_existing or 'on')}")
    print("=" * 68)

    token = None
    shop_id = None
    if not args.dry_run:
        token = printify_token()
        shop_id = resolve_shop_id(token)

    failures: list[str] = []
    results: list[dict] = []

    for index, concept in enumerate(concepts, start=1):
        print(f"\n[{index}/{len(concepts)}] {concept['name']} ({concept['id']})")
        stem = f"{utc_stamp()}-{concept['id']}-{uuid.uuid4().hex[:6]}"
        record: dict = {
            "slug": stem,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "product_type": product_key,
            "concept": {"id": concept["id"], "name": concept["name"]},
            "status": "started",
        }

        try:
            if args.from_existing:
                image_path = Path(args.from_existing).expanduser().resolve()
                if not image_path.exists():
                    raise BotError(f"--from-existing file not found: {image_path}")
                record["artwork"] = {"path": str(image_path), "source": "existing"}
            else:
                prompt = build_art_prompt(concept, config, product_key, style_key)
                record["art_style"] = style_key
                record["art_prompt"] = prompt
                image_path = generate_image(prompt, config["aspect_ratio"], out_dir, stem)
                record["artwork"] = {"path": str(image_path), "source": "replicate"}

            size = png_size(image_path)
            if size:
                record["artwork"]["width"], record["artwork"]["height"] = size

            metadata = generate_metadata(concept, config)
            tags = default_tags(
                concept.get("keywords") or [],
                metadata.get("model_tags") or [],
                AUTOMOTIVE_TAGS,
            )
            metadata["tags"] = tags
            record.update(
                {
                    "title": metadata["title"],
                    "description": metadata["description"],
                    "alt_text": metadata["alt_text"],
                    "tags": tags,
                }
            )
            print(f"  [meta] {metadata['title']}")
            print(f"  [meta] {len(tags)} tags: {', '.join(tags[:6])}...")

            if args.dry_run:
                record["status"] = "dry-run"
                results.append({"concept": concept["name"], "status": "dry-run", "image": str(image_path)})
                print("  [skip] dry run - no Printify calls")
                continue

            upload = upload_to_printify(image_path, token)
            record["printify_upload"] = {
                "image_id": upload.get("id"),
                "preview_url": upload.get("preview_url"),
            }

            resolved_price = args.price_cents or env_int(
                "PRINTIFY_PRICE_CENTS", int(config["price_cents"])
            )
            product = create_printify_product(
                metadata,
                str(upload["id"]),
                product_key,
                shop_id=shop_id,
                token=token,
                price_cents=resolved_price,
            )
            record["printify_product"] = {
                "id": product.get("id"),
                "blueprint_id": product.get("blueprint_id"),
                "print_provider_id": product.get("print_provider_id"),
                "variants": int(product.get("_enabled_variants") or 0),
                "price_cents": resolved_price,
            }

            published = False
            publish_error = ""
            if args.no_publish or not env_bool("PRINTIFY_PUBLISH", True):
                print("  [skip] publishing disabled")
            else:
                result = publish_product(str(product["id"]), int(shop_id), token)
                published, publish_error = result.ok, result.message
            record["printify_product"]["published"] = published
            if publish_error:
                record["printify_product"]["publish_error"] = publish_error
            record["status"] = "published" if published else "draft"
            results.append(
                {
                    "concept": concept["name"],
                    "status": record["status"],
                    "product_id": product.get("id"),
                    "image": str(image_path),
                }
            )
        except (BotError, PrintifyError, requests.RequestException) as exc:
            message = str(exc)
            if isinstance(exc, PrintifyError) and exc.body:
                message = f"{message}: {exc.body}"
            print(f"  [error] {message}")
            record["status"] = "failed"
            record["error"] = message
            failures.append(f"{concept['name']}: {message}")
        finally:
            if record.get("title"):
                save_metadata(record, out_dir)

    print("\n" + "=" * 68)
    ok = [item for item in results if item["status"] in {"published", "draft"}]
    for item in results:
        suffix = f" -> product {item['product_id']}" if item.get("product_id") else ""
        print(f"  {item['status']:<10} {item['concept']}{suffix}")
    dry = sum(1 for item in results if item["status"] == "dry-run")
    print(f"  {len(ok)} completed, {dry} dry-run, {len(failures)} failed")
    print("=" * 68)

    if failures:
        for failure in failures:
            print(f"  [failed] {failure}")
        return 1
    return 0


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

def env_file_path() -> Path | None:
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
        if candidate.exists():
            return candidate
    return None


def error_detail(exc: Exception) -> str:
    lines = str(exc).strip().splitlines()
    head = lines[0][:180] if lines else ""
    return f"{type(exc).__name__}: {head}" if head else type(exc).__name__


def failure_status(exc: Exception) -> str:
    """Rejected credentials and missing models are fatal; anything else warns."""
    code = getattr(exc, "status_code", None)
    return "fail" if code in {401, 403, 404} else "warn"


def cmd_check() -> int:
    """Validate settings and credentials. Never prints a secret value.

    Read-only: it lists shops, fetches a model record and looks up a blueprint.
    It never generates an image, creates a product or publishes anything.
    """
    results: list[tuple[str, str, str]] = []

    def record(status: str, label: str, detail: str = "") -> None:
        results.append((status, label, detail))

    print("Configuration check (secret values are never displayed)")
    print("-" * 68)

    env_path = env_file_path()
    record(
        "ok" if env_path else "warn",
        ".env",
        str(env_path) if env_path else "not found - using the process environment only",
    )

    out_dir = Path(env("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)).expanduser()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / f".write-probe-{uuid.uuid4().hex[:8]}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        record("ok", "OUTPUT_DIR", f"{out_dir} (writable)")
    except OSError as exc:
        record("fail", "OUTPUT_DIR", f"{out_dir} is not writable: {exc}")

    for module in ("requests", "openai", "dotenv"):
        found = importlib.util.find_spec(module) is not None
        record(
            "ok" if found else "fail",
            f"package {module}",
            "installed" if found else "missing - pip install -r requirements.txt",
        )

    openai_key = env("OPENAI_API_KEY")
    replicate_token = env("REPLICATE_API_TOKEN")
    replicate_fallback = env("REPLICATE_API_KEY")
    printify_token_value = env("PRINTIFY_API_TOKEN")
    token = replicate_token or replicate_fallback

    record(
        "ok" if openai_key else "fail",
        "OPENAI_API_KEY",
        f"set ({len(openai_key)} chars)" if openai_key else "not set",
    )
    if replicate_token:
        record("ok", "REPLICATE_API_TOKEN", f"set ({len(replicate_token)} chars)")
    elif replicate_fallback:
        record(
            "ok",
            "REPLICATE_API_KEY",
            f"set ({len(replicate_fallback)} chars, used as the fallback)",
        )
    else:
        record("fail", "REPLICATE_API_TOKEN", "not set")
    record(
        "ok" if printify_token_value else "fail",
        "PRINTIFY_API_TOKEN",
        f"set ({len(printify_token_value)} chars)"
        if printify_token_value
        else "not set - required before uploads can run",
    )

    if openai_key:
        model = env("OPENAI_MODEL", "gpt-4o-mini")
        try:
            from openai import OpenAI

            OpenAI(api_key=openai_key).models.retrieve(model)
            record("ok", "OpenAI auth", f"key accepted, model {model} available")
        except Exception as exc:  # the SDK raises a family of error types
            record(failure_status(exc), "OpenAI auth", error_detail(exc))

    if token:
        try:
            account = replicate_http("GET", f"{REPLICATE_API}/account", token)
            record(
                "ok",
                "Replicate auth",
                f"key accepted (account {account.get('username', 'unknown')})",
            )
        except Exception as exc:
            record("fail", "Replicate auth", error_detail(exc))

        model = env("REPLICATE_MODEL", "black-forest-labs/flux-schnell")
        if "/" in model:
            try:
                info = replicate_http("GET", f"{REPLICATE_API}/models/{model}", token)
                version = str((info.get("latest_version") or {}).get("id") or "")
                record(
                    "ok" if version else "warn",
                    "Replicate model",
                    f"{model} (latest version {version[:12]}...)"
                    if version
                    else f"{model} has no runnable version",
                )
            except Exception as exc:
                record(failure_status(exc), "Replicate model", error_detail(exc))
        else:
            record("warn", "Replicate model", f"{model} is a pinned version, not checked")

    shops: list = []
    if printify_token_value:
        try:
            shops = list_shops(printify_token_value)
            record("ok", "Printify auth", f"key accepted, {len(shops)} shop(s)")
        except Exception as exc:
            record("fail", "Printify auth", error_detail(exc))

    configured_shop = env("PRINTIFY_SHOP_ID")
    shop_ids = {str(shop.get("id")) for shop in shops}
    if not printify_token_value:
        record(
            "warn",
            "PRINTIFY_SHOP_ID",
            f"{configured_shop} (unverifiable without a token)"
            if configured_shop
            else "not set - run `python bot.py --shops` once the token is in",
        )
    elif configured_shop:
        if configured_shop in shop_ids:
            title = next(
                (str(s.get("title")) for s in shops if str(s.get("id")) == configured_shop), ""
            )
            record("ok", "PRINTIFY_SHOP_ID", f"{configured_shop} {title}".strip())
        else:
            record(
                "fail",
                "PRINTIFY_SHOP_ID",
                f"{configured_shop} is not on this account; available: "
                f"{', '.join(sorted(shop_ids)) or 'none'}",
            )
    else:
        record("warn", "PRINTIFY_SHOP_ID", "not set - run `python bot.py --shops`")

    config: dict | None = None
    try:
        product_key = normalize_product(env("PRINTIFY_PRODUCT_TYPE"))
        config = PRODUCTS[product_key]
        record(
            "ok",
            "product type",
            f"{product_key} -> blueprint {config['blueprint_id']} ({config['blueprint_name']})",
        )
    except BotError as exc:
        record("fail", "product type", str(exc))

    if config:
        try:
            price = env_int("PRINTIFY_PRICE_CENTS", int(config["price_cents"]))
            record("ok" if price > 0 else "fail", "price", f"${price / 100:.2f} per variant")
        except BotError as exc:
            record("fail", "price", str(exc))
        try:
            scale = env_float("PRINTIFY_IMAGE_SCALE", 1.0)
            record("ok" if scale > 0 else "fail", "PRINTIFY_IMAGE_SCALE", str(scale))
        except BotError as exc:
            record("fail", "PRINTIFY_IMAGE_SCALE", str(exc))

    if config and printify_token_value:
        try:
            blueprint_id = env_int("PRINTIFY_BLUEPRINT_ID", int(config["blueprint_id"]))
        except BotError as exc:
            record("fail", "PRINTIFY_BLUEPRINT_ID", str(exc))
            blueprint_id = 0
        if blueprint_id:
            try:
                providers = printify_json(
                    "GET",
                    f"/catalog/blueprints/{blueprint_id}/print_providers.json",
                    token=printify_token_value,
                )
                providers = providers if isinstance(providers, list) else []
                record(
                    "ok" if providers else "fail",
                    "blueprint",
                    f"{blueprint_id}: {len(providers)} print provider(s)",
                )
                pinned = env("PRINTIFY_PRINT_PROVIDER_ID")
                if pinned:
                    available = {str(provider.get("id")) for provider in providers}
                    record(
                        "ok" if pinned in available else "fail",
                        "print provider",
                        f"{pinned} fulfils blueprint {blueprint_id}"
                        if pinned in available
                        else f"{pinned} does not fulfil blueprint {blueprint_id}; "
                        f"available: {', '.join(sorted(available)) or 'none'}",
                    )
            except Exception as exc:
                record("fail", "blueprint", error_detail(exc))

    print()
    width = max((len(label) for _, label, _ in results), default=10)
    icons = {"ok": "[ok]  ", "warn": "[warn]", "fail": "[fail]"}
    for status, label, detail in results:
        print(f"  {icons[status]} {label:<{width}}  {detail}")

    failures = [label for status, label, _ in results if status == "fail"]
    warnings = [label for status, label, _ in results if status == "warn"]
    print("-" * 68)
    if failures:
        print(f"  {len(failures)} problem(s), {len(warnings)} warning(s): {', '.join(failures)}")
        return 1
    print(
        f"  All checks passed ({len(warnings)} warning(s)). "
        "Read-only: nothing was generated or created."
    )
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_publish_existing(product_id: str) -> int:
    """Publish a product that already exists in Printify (e.g. an old draft)."""
    token = printify_token()
    shop_id = resolve_shop_id(token)
    product_id = product_id.strip()
    if not product_id:
        raise BotError("--publish-existing needs a Printify product id")

    try:
        product = printify_json(
            "GET", f"/shops/{shop_id}/products/{product_id}.json", token=token
        )
    except PrintifyError as exc:
        raise BotError(
            f"product {product_id} is not in shop {shop_id} ({exc.status}): {exc.body}"
        ) from exc
    if not isinstance(product, dict):
        raise BotError(f"Printify returned an unexpected payload for {product_id}")

    print(f"Product {product_id} in shop {shop_id}")
    print(f"  title  : {product.get('title')}")
    print(f"  visible: {product.get('visible')} | locked: {product.get('is_locked')}")

    result = publish_product(product_id, int(shop_id), token)
    if result.ok:
        refreshed = printify_json(
            "GET", f"/shops/{shop_id}/products/{product_id}.json", token=token
        )
        if isinstance(refreshed, dict):
            print(f"  linked : {refreshed.get('external')}")

    record = update_record_publish_state(product_id, result.ok, result.message)
    if record is not None:
        print(f"  record : {record.name} -> {'published' if result.ok else 'draft'}")
    else:
        print("  record : no matching metadata record in OUTPUT_DIR")
    return 0 if result.ok else 1


def cmd_shops() -> int:
    token = printify_token()
    shops = list_shops(token)
    if not shops:
        print("No shops on this Printify account. Create/connect one first.")
        return 1
    print("Shops on this account (use the id as PRINTIFY_SHOP_ID):")
    for shop in shops:
        print(f"  {shop.get('id')}  {shop.get('title')}  [{shop.get('sales_channel', 'n/a')}]")
    return 0


def cmd_catalog(product: str | None) -> int:
    key = normalize_product(product or env("PRINTIFY_PRODUCT_TYPE"))
    config = PRODUCTS[key]
    token = printify_token()

    blueprint_env = env("PRINTIFY_BLUEPRINT_ID")
    blueprint_id = int(blueprint_env) if blueprint_env else int(config["blueprint_id"])
    print(f"{key}: blueprint {blueprint_id} ({config['blueprint_name']})")

    providers = printify_json(
        "GET", f"/catalog/blueprints/{blueprint_id}/print_providers.json", token=token
    )
    if not isinstance(providers, list) or not providers:
        print("  no print providers returned")
        return 1
    print("  print providers (pin one with PRINTIFY_PRINT_PROVIDER_ID):")
    for provider in providers:
        marker = "  <- default" if int(provider.get("id", 0)) == 99 else ""
        print(f"    {provider.get('id'):>5}  {provider.get('title')}{marker}")

    provider_id, _ = resolve_print_provider(
        token,
        blueprint_id,
        int(env("PRINTIFY_PRINT_PROVIDER_ID")) if env("PRINTIFY_PRINT_PROVIDER_ID") else None,
        providers=providers,
    )
    variants = fetch_variants(token, blueprint_id, provider_id)
    sizes = available_options(variants, "size")
    colors = available_options(variants, "color")
    print(f"  variants for provider {provider_id}: {len(variants)}")
    if sizes:
        print(f"  sizes : {', '.join(sizes[:40])}")
    if colors:
        print(f"  colors: {', '.join(colors[:20])}{' ...' if len(colors) > 20 else ''}")
    print(f"  defaults: sizes={config['sizes'] or '(all)'} colors={config['colors'] or '(all)'}")
    return 0


def cmd_list_products() -> int:
    print("Product types (pass with --product or set PRINTIFY_PRODUCT_TYPE):")
    for key, config in PRODUCTS.items():
        print(
            f"  {key:<11} blueprint {config['blueprint_id']:<4} "
            f"${config['price_cents'] / 100:>6.2f}  {config['blueprint_name']}"
        )
    return 0


def cmd_list_concepts() -> int:
    print(f"{len(AUTOMOTIVE_CONCEPTS)} automotive concepts:")
    for concept in AUTOMOTIVE_CONCEPTS:
        print(f"  {concept['id']:<20} {concept['name']}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bot.py",
        description="Automotive print-on-demand engine: art -> metadata -> Printify.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python bot.py                         one poster, full pipeline\n"
            "  python bot.py --count 3               three designs\n"
            "  python bot.py --product tshirt        different product type\n"
            "  python bot.py --dry-run               generate art + copy only\n"
            "  python bot.py --from-existing a.png   skip generation, reuse an image\n"
        ),
    )
    parser.add_argument("--count", type=int, default=1, help="how many designs to produce (default 1)")
    parser.add_argument("--product", help="poster | canvas | tshirt | hoodie | sticker | phone_case")
    parser.add_argument("--concept", help="lock the art direction to one concept id or name")
    parser.add_argument("--seed", type=int, help="deterministic concept selection")
    parser.add_argument(
        "--style",
        help="photo (default, realistic) or poster (retro screen print)",
    )
    parser.add_argument("--dry-run", action="store_true", help="generate art + metadata, skip Printify")
    parser.add_argument("--no-publish", action="store_true", help="create the product but leave it a draft")
    parser.add_argument("--price-cents", type=int, help="override the retail price for every variant")
    parser.add_argument("--output-dir", help="where artwork and JSON metadata are written")
    parser.add_argument("--from-existing", help="reuse an image file instead of calling Replicate")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate keys and settings (never prints secrets) and exit",
    )
    parser.add_argument("--shops", action="store_true", help="list Printify shops and exit")
    parser.add_argument(
        "--publish-existing",
        metavar="PRODUCT_ID",
        help="publish an existing Printify product id and exit",
    )
    parser.add_argument(
        "--catalog",
        nargs="?",
        const="_default_",
        help="show blueprints, providers, sizes for a product type and exit",
    )
    parser.add_argument("--list-products", action="store_true", help="show supported product types")
    parser.add_argument("--list-concepts", action="store_true", help="show art concepts")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_console()
    load_env()
    args = parse_args(argv)

    try:
        if args.list_products:
            return cmd_list_products()
        if args.list_concepts:
            return cmd_list_concepts()
        if args.check:
            return cmd_check()
        if args.shops:
            return cmd_shops()
        if args.publish_existing:
            return cmd_publish_existing(args.publish_existing)
        if args.catalog is not None:
            return cmd_catalog(None if args.catalog == "_default_" else args.catalog)
        if args.count < 1:
            raise BotError("--count must be at least 1")
        return run_pipeline(args)
    except BotError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[stopped]", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
