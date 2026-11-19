# Bundled Circuit Synth source

This plugin bundles the Circuit Synth source from the adjacent
`circuit-synth/src` checkout, including its local modifications, with the
matching `pyproject.toml`, `README.md`, and MIT `LICENSE`.

The bundled source is a snapshot. Changes made later in the sibling checkout
must be copied into this directory and reviewed before they are included in an
Agent-EDA release. Git metadata is intentionally excluded. The sibling source
checkout is left untouched by this vendoring step.
