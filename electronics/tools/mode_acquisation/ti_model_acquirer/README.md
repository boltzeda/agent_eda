# TI Model Acquirer

This independent package retrieves only publicly downloadable simulation models
from TI's official product pages. It does not alter the existing Hermes model
resolver or schematic pipeline.

When enabled as a Hermes project plugin, it registers the
`acquire_ti_model` tool in the `ti_model_acquirer` toolset. The tool returns
the acquirer's JSON evidence manifest and stores successful downloads below
`generated/ti_model_acquirer/`.

It has no third-party Python dependencies: it uses only the Python standard
library and runs in the existing Hermes EDA interpreter. It does not need a
separate virtual environment.

Run its offline tests:

```bash
python -m unittest discover -s ti_model_acquirer/tests -v
```

Run a live TI lookup and download:

```bash
python -m ti_model_acquirer.cli LM741 --asset-type pspice
```

The command prints a JSON manifest. It returns a specific reason code rather
than guessing or bypassing login, licensing, payment, or third-party controls.
