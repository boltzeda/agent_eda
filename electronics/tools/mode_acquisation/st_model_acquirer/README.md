# st_model_acquirer

An independent Hermes plugin that retrieves only publicly downloadable
simulation models from official STMicroelectronics resources (`st.com`). Like
the sibling `ti_model_acquirer`, it does not alter the existing Hermes model
resolver, schematic pipeline, or another plugin.

## What it extracts

Given an exact ST part number, the plugin queries ST's public resource index,
selects only official typed model resources, downloads each matching artifact,
validates it, and returns a JSON evidence manifest. ST publishes both ZIP
archives and raw model files, so both forms are supported.

Supported asset types are `spice` (default), `pspice`, `ltspice`, `simplis`,
`simetrix`, and `eldo`. Recognized SPICE-family files include `.lib`, `.cir`,
`.sub`, `.mod`, `.sp`, `.spi`, `.mdl`, and `.txt`; ST's SIMPLIS-family
`.tsm`, `.sxsch`, `.sxsym`, and `.sxlib` files are also supported. Datasheets,
CAD symbols/footprints, IBIS files, technical reports, and third-party resources
are out of scope.

## Registered tool

`acquire_st_model` (toolset `st_model_acquirer`, emoji ⚡)

| Parameter | Type | Description |
| --- | --- | --- |
| `part_number` | string (required) | Exact ST part number, e.g. `A6986F`, `TSC101`, `STL110N8F8` |
| `asset_type` | string | `spice`, `pspice`, `ltspice`, `simplis`, `simetrix`, or `eldo` |

## Usage

From the Hermes plugins directory:

```bash
python -m st_model_acquirer.cli A6986F --asset-type pspice
```

If the resource index omits a model listed on the official exact-part product
page, supply that page for a fallback lookup:

```bash
python -m st_model_acquirer.cli TSZ121 --asset-type pspice --product-url https://www.st.com/en/amplifiers-and-comparators/tsz121.html
```

This fallback downloads only exact-part matches from official ST model links.
It requires network access to both the product page and the model file; it does
not bypass a license or login requirement.

Successful downloads are stored below `generated/st_model_acquirer/`:

```text
generated/st_model_acquirer/<PART>/<ASSET>/<file>              # raw model
generated/st_model_acquirer/<PART>/<ASSET>/<archive>.zip       # original ZIP
generated/st_model_acquirer/<PART>/<ASSET>/extracted/<archive>/... # ZIP models
```

The model bytes and original ZIP bytes are stored unchanged. Extracted output
is limited to recognized simulation-model files.

## Result manifest

The tool returns (and the CLI prints) an `AcquisitionResult` JSON with `status`,
a specific `reason_code`, official source/download URLs, saved paths and SHA-256
digests, per-resource evidence, exact-part coverage, series coverage, and
per-resource errors. It reports access controls rather than trying to bypass
myST login, license acceptance, payment, or third-party controls.

## Safety guarantees

- Writes only below `generated/st_model_acquirer/` by default.
- Accepts requests and redirects only on `st.com` or its subdomains.
- Caps responses at 25 MiB, ZIPs at 100 members, and expanded data at 50 MiB.
- Rejects absolute paths, `..` traversal, and symbolic links in ZIPs.
- Keeps only recognized model extensions and content-sniffs text models for a
  `.SUBCKT` or `.MODEL` definition.
- Uses only the Python standard library and does not auto-accept agreements.

## Tests

Offline tests use injected HTTP responses and require no network:

```bash
python -m unittest discover -s st_model_acquirer/tests -v
```
