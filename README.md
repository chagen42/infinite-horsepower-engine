# Infinite Horsepower - all-in-one automotive POD engine

One Python file runs the whole pipeline:

```
Replicate (artwork) -> OpenAI (title, description, SEO tags) -> PNG + JSON on disk
    -> upload to Printify -> create the product -> publish
```

No separate upload script, no copy/paste between tools.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Fill in `.env`:

| Variable | Where to get it |
| --- | --- |
| `OPENAI_API_KEY` | platform.openai.com -> API keys |
| `REPLICATE_API_TOKEN` | replicate.com/account/api-tokens |
| `PRINTIFY_API_TOKEN` | Printify -> Settings -> API tokens |
| `PRINTIFY_SHOP_ID` | `python bot.py --shops` |

Then validate everything before your first run — read-only, no images generated, no
products created, and no secret values printed:

```bash
python bot.py --check
python bot.py --list-products
python bot.py --catalog poster
```

`--check` reports each key as `set (N chars)` or `not set`, confirms the OpenAI
and Replicate keys are accepted, lists the Printify shops, and verifies that the
configured blueprint and print provider actually exist. It exits non-zero if
anything is broken, so it works in CI or a Render pre-deploy step.

`--catalog` prints the blueprint id, every print provider that fulfils it, and
the size/colour options your variants can use. Pin anything unusual with
`PRINTIFY_BLUEPRINT_ID`, `PRINTIFY_PRINT_PROVIDER_ID`, `PRINTIFY_SIZES` or
`PRINTIFY_COLORS`.

## Run

```bash
python bot.py                        # one poster: art -> product -> published
python bot.py --count 3              # three designs
python bot.py --product tshirt       # or canvas, hoodie, sticker, phone_case
python bot.py --dry-run              # art + copy only, no Printify calls
python bot.py --no-publish           # create the product as a draft
python bot.py --concept jdm-night    # lock the art direction
python bot.py --from-existing my.png # reuse an image you already have
python bot.py --list-concepts        # see all 20 briefs
python bot.py --publish-existing <product-id>   # publish a draft you already have
```

Artwork (`*.png`) and the full listing record (`*.json`) are written to
`OUTPUT_DIR` (default `./generated`). The JSON holds the prompt, title,
description, tags, Printify image id, product id and publish status, so a run
is auditable after the fact.

## Art styles

`ART_STYLE` (or `--style`) picks the art direction:

- **`photo`** (default) - ultra-realistic automotive photography: full-frame,
  35mm at f/2, low three-quarter angle, true paint reflections, shallow depth of
  field. Written to avoid the words that pull a model back toward
  illustration ("flat", "screen print", "limited palette"). Pairs with the six
  modern sports-car briefs: `supercar-dusk`, `hypercar-wet`, `gt-racer`,
  `canyon-super`, `coastal-coupe`, `studio-super`.
- **`poster`** - the retro screen-print look: limited palette, halftone grain,
  bold shapes. Every concept carries its own illustration descriptor for this.

```bash
python bot.py --concept supercar-dusk              # photoreal
python bot.py --concept supercar-dusk --style poster
```

For more realism than flux-schnell can give, point `REPLICATE_MODEL` at
`black-forest-labs/flux-1.1-pro` - slower and pricier per image, noticeably
sharper bodywork and reflections.

## What the JSON record looks like

```json
{
  "slug": "20261115T142233Z-muscle-sunset-a1b2c3",
  "product_type": "poster",
  "title": "Muscle Car Sunset - American Muscle Wall Art",
  "description": "...",
  "tags": ["muscle car", "sunset", "automotive art", "..."],
  "artwork": { "path": "generated/....png", "width": 1024, "height": 1536 },
  "printify_upload": { "image_id": "66f0...", "preview_url": "https://..." },
  "printify_product": { "id": "66f1...", "variants": 6, "published": true },
  "status": "published"
}
```

## Products and defaults

| Product | Blueprint | Default price | Default sizes |
| --- | --- | --- | --- |
| `poster` | 282 Matte Vertical Posters | $29.99 | 12x18, 16x24, 24x36 |
| `canvas` | 555 Stretched Canvas | $49.99 | 16x20, 24x30 |
| `tshirt` | 6 Gildan 5000 | $32.99 | S-2XL, 5 colours |
| `hoodie` | 77 Gildan 18500 | $54.99 | S-2XL, 5 colours |
| `sticker` | 400 Kiss-Cut Stickers | $4.99 | first 12 |
| `phone_case` | 268 Slim Phone Cases | $24.99 | first 12 |

Sizes and colours are matched against the options Printify actually reports for
that blueprint, so a wrong default degrades to "all in-stock variants" with a
warning instead of a failed run. Prices are per variant and must exceed the
provider's cost.

## Deploying to Render

`render.yaml` defines a Cron Job (`infinite-horsepower-engine-cron`) that runs
`python bot.py --count 1` daily at 14:00 UTC.

1. Render Dashboard -> New -> Blueprint -> pick this repo.
2. Add the four secrets when prompted (they are `sync: false`, so they are never
   stored in git).
3. Trigger a manual run and read the logs.

The cron filesystem is ephemeral: the PNG and JSON disappear after the run, but
the Printify product and the uploaded image persist. If you need the metadata
kept forever, add a durable store (S3/R2 or a database) or commit
`generated/*.json` from a machine that keeps them.

If your existing `infinite-horsepower-engine` service is a Web Service, switch
its type to Cron Job (or delete it) before deploying the blueprint, otherwise
the name collides.

## Caveats worth knowing

- **Publishing needs a sales channel.** `publish.json` only works when the shop
  has a connected channel (Etsy, Shopify, etc.). If publishing fails, the run
  logs the reason and the product stays a draft - it is not an error. Connect a
  store in Printify, then publish anything already created with
  `python bot.py --publish-existing <product-id>`; the matching JSON record is
  updated with the real result.
- **Apparel and DTG need transparency.** A poster prints the full rectangle, so
  any background is fine. A t-shirt print of a rectangular image prints the
  background too, which looks wrong on non-white garments. For `tshirt` and
  `hoodie`, remove the background from the PNG (or generate on a flat colour and
  cut it out) before the Printify step.
- **Print resolution.** flux-schnell tops out at 1 megapixel: 832x1216 at 2:3,
  about 69 DPI at 12x18 and 35 DPI at 24x36. Print providers want 150-300 DPI,
  so finished art needs an upscale pass (Real-ESRGAN on Replicate) or a
  higher-resolution model before it is genuinely print-ready.
- **Text artifacts.** The prompt asks for no lettering, but image models still
  invent badges and plate numbers (a recent run rendered "969" on a tail
  panel). Check each artwork before it ships.
- **Costs.** Every run spends Replicate GPU seconds and OpenAI tokens. Use
  `--dry-run` while iterating on prompts.
- **Rate limits.** Uploads and product creation retry on 429/5xx with backoff.
