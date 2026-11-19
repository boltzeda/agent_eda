# NXP Model Acquirer

This independent Hermes plugin retrieves publicly downloadable SPICE, PSpice,
and SIMPLIS models referenced by official NXP Semiconductors product pages. It
does not alter the generic model resolver or the schematic pipeline.

When enabled, it registers `acquire_nxp_model` in the
`nxp_model_acquirer` toolset. Successful files are stored below
`generated/nxp_model_acquirer/`, and every result includes an evidence manifest
with official source URLs and SHA-256 hashes.

The implementation uses only Python's standard library. It accepts downloads
only from `nxp.com`, rejects unsafe ZIP paths and symlinks, ignores S-parameter
files such as `.s2p`, and reports explicit reason codes when NXP requires login,
license acceptance, payment, or a third-party provider.

Run the offline tests from the Hermes plugins directory:

```bash
python -m unittest discover -s nxp_model_acquirer/tests -v
```

Run a live lookup:

```bash
python -m nxp_model_acquirer.cli BF862 --asset-type spice
```

NXP may return an edge-service block page for automated requests. In that case
the command reports `DOWNLOAD_BLOCKED`; it does not treat the HTML as a model.
