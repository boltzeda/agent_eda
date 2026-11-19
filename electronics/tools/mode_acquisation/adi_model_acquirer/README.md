# adi_model_acquirer

An independent Hermes plugin that retrieves official simulation models from
Analog Devices (`analog.com`), including downloads behind its standard consent
page. Like its TI
sibling `ti_model_acquirer`, it does not alter the existing Hermes model resolver,
the schematic pipeline, or any other plugin — and it never modifies the downloaded
files: every model is stored **byte-identical** to what ADI serves.

## What it extracts

Exactly what the TI plugin extracts: officially published simulation model files
(SPICE-family: `.lib`, `.cir`, `.sub`, `.mod`, `.sp`, `.spi`, `.mdl`, `.tsm`,
`.sxsch`, `.sxsym`, `.sxlib`, plus ADI's raw `.txt` models and opt-in `.ibs`
IBIS models). Datasheets, CAD symbols/footprints, pin images, and third-party
(Ultra Librarian / SamacSys) resources are out of scope.

Given an exact ADI part number it discovers model cards on the official product
page, validates ADI media links, and downloads raw files or ZIP archives. If
ADI blocks or times out on the product page, it also tries the exact-MPN `.cir`
media name on ADI's official host. That fallback is accepted only when the
downloaded SPICE declarations match the requested exact part. For a family
page it downloads every public card in the requested format.

## Registered tool

`acquire_adi_model` (toolset `adi_model_acquirer`, emoji ⚡)

The workflow checks official `ti.com` and `analog.com` resources as equal
primary sources against the user's requirements and exact selected device,
without a vendor fallback sequence. It calls this tool for an applicable
confirmed ADI model and the TI tool for an applicable TI model. Download-source
selection follows documented device coverage, availability, and simulator
compatibility. Changing an already selected component requires approval; a
different device's model is never silently substituted.

Normally call the tool with just `part_number` and `asset_type`. It resolves
the official model URL, opens ADI's consent page when needed, and clicks its
actual consent button without asking for another confirmation. Never pass a
product page as `source_url`: use `product_url` for a known product page and
`source_url` only for a direct model media URL or a local import. The isolated
browser does not bypass the page or alter URLs.
The plugin invokes its isolated `browser_download.py` helper, scrolls the
button into view, and records license-click evidence and model hashes.
The CLI supports the same route with `--browser --source-url URL
--accept-license` and a bounded `--timeout` (maximum 180).

For a supplied local download, use `local_model_file` under project
`generated/` or `output/` plus its official `source_url`. The result is
`imported` with `acquisition_method: manual_import` and user-declared provenance.
Direct HTTP acquisition honors `ADI_HTTP_TIMEOUT_SECONDS` (default 30).

| Parameter | Type | Description |
| --- | --- | --- |
| `part_number` | string (required) | Exact ADI part number, e.g. `AD8605`, `AD620`, `ADA4530-1` |
| `asset_type` | string | `spice` (default), `pspice` (alias), `ltspice`, `simplis`, `ibis` |
| `product_url` | string | Optional official product page for discovery; normally omit |
| `source_url` | string | Direct official model media URL, or paired with `local_model_file` for import |

## Usage

Through Hermes, or standalone from the plugins directory:

```bash
python -m adi_model_acquirer.cli AD8605 --asset-type spice
```

Successful downloads are stored below `generated/adi_model_acquirer/`:

```
generated/adi_model_acquirer/<PART>/<ASSET>/<file>            # raw model file
generated/adi_model_acquirer/<PART>/<ASSET>/<archive_id>.zip  # downloaded archive
generated/adi_model_acquirer/<PART>/<ASSET>/extracted/<archive_id>/...  # zip contents
```

## Result manifest

The tool returns (and the CLI prints) an `AcquisitionResult` JSON: `status`
(`downloaded` / `unavailable` / `access_required` / `paid` / `error`), a specific
`reason_code` (e.g. `PART_NOT_FOUND`, `MODEL_NOT_PUBLISHED`,
`REQUESTED_FORMAT_NOT_AVAILABLE`, `LOGIN_REQUIRED`,
`LICENSE_ACCEPTANCE_REQUIRED`, `THIRD_PARTY_PROVIDER_REQUIRED`,
`NETWORK_ERROR`, `INVALID_DOWNLOAD`), official source/download URLs, saved file
paths with SHA-256 digests, per-card `model_resources`, `exact_part_model_found`,
`series_model_count`, and per-card `resource_errors`.

It returns a specific reason code rather than guessing or bypassing login,
licensing, payment, or third-party controls. In particular, when ADI serves its
*license agreement* interstitial instead of a model file, the plugin reports
`LICENSE_ACCEPTANCE_REQUIRED` with the URL so a human can review and accept the
agreement in a browser — the agreement is never bypassed or auto-accepted, and the
plugin keeps no cookie jar.

## Guarantees

- Read-only towards everything else: writes only inside
  `generated/adi_model_acquirer/`; never patches or rewrites model files, other
  plugins, or repo code.
- Only accesses `analog.com` / `www.analog.com` (checked before the request and
  after redirects).
- Downloads capped at 25 MiB; archives capped at 100 members; zip extraction
  rejects absolute paths and `..` traversal; only recognized model extensions
  are kept, text models must contain a `.SUBCKT`/`.MODEL` definition.
- No third-party Python dependencies: Python standard library only, running in
  the existing Hermes EDA interpreter.

## Tests

Offline (no network; fetchers are injected):

```bash
python -m unittest discover -s adi_model_acquirer/tests -v
```

## Network note

`www.analog.com` is served from Akamai and is unreachable from some networks
(including the current development host — `ti.com` works, `analog.com` times
out). On such networks a live run still returns a clean JSON manifest with
`reason_code: NETWORK_ERROR` (never a traceback). Verify live behavior from a
network with analog.com access: a `DOWNLOADED` result should match
`curl -sL <model_url> | sha256sum` of the saved file, and a
`LICENSE_ACCEPTANCE_REQUIRED` result from the license interstitial is also a
correct outcome.
