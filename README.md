# gedge_bot_paper

Source codes for bench tools to get data referenced in paper.

Companion reproduction artifacts for

> Hongyu Luo, Sun Tie, John Wang, **Compute Regeneration through
> Re-Productization: Reorganizing Retired Edge AI Silicon into a Delivered
> Multilingual Service Robot**, arXiv:2611.11698 [cs.RO], 2026.
> doi:[10.48550/arXiv.2611.11698](https://doi.org/10.48550/arXiv.2611.11698)
> · [arXiv abs](https://arxiv.org/abs/2611.11698)

## Contents

| Path | What it holds |
| --- | --- |
| `scripts/` | 24 Python benchmark, static-audit, replay and aggregation scripts used for the November measurement campaign |
| `evidence/device/` | 57 per-run measurement JSON records plus 9 verbatim on-device runner shell scripts |
| `evidence/analysis/` | 8 derived analysis results (boundary isolation, integration surface, control graph, product-loop and full-runtime replay) |
| `evidence/rebuilt/` | 12 reconstructed demonstration-run records |
| `evidence/*.json` | 8 contract, firmware-static and smoke-test result files |
| `paper/` | The exact seven LaTeX source files submitted to arXiv (main text, `.bbl`, bibliography, four TikZ figures) |
| `tools/check_public_release.py` | Pre-release gate: private-file scan, credential scan and evidence-hash verification |

Every script and evidence file is byte-identical to the one used in the study,
because the JSON records embed `script_sha256` hashes. See
[REPRODUCING.md](REPRODUCING.md) for environment variables, what can be rerun
without the robot, and the meaning of the evidence-status fields.

## Not included

Private patent materials, the proprietary runtime and STM32 firmware source
snapshots, internal audit notes, raw audio captures and internal reports are
excluded. The full inclusion/exclusion record is in [AUDIT.md](AUDIT.md).

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 tools/check_public_release.py
```

The on-device AI benchmarks require a Jetson-class device, a compatible model
stack and the private runtime source tree; they are published for transparency,
not as a one-command reproduction.

## Citing

Please cite the paper, not this repository. A ready-made citation is in
[`CITATION.cff`](CITATION.cff) (also machine-readable as
[`codemeta.json`](codemeta.json)).

```bibtex
@misc{luo2026compute,
  title  = {Compute Regeneration through Re-Productization: Reorganizing Retired
            Edge AI Silicon into a Delivered Multilingual Service Robot},
  author = {Hongyu Luo and Sun Tie and John Wang},
  year   = {2026}, eprint = {2611.11698}, archivePrefix = {arXiv},
  primaryClass = {cs.RO}, doi = {10.48550/arXiv.2611.11698},
  url    = {https://arxiv.org/abs/2611.11698}
}
```

## License

- Code and LaTeX sources: [MIT](LICENSE)
- Measurement records and documentation: [CC BY 4.0](DATA_LICENSE.md)

## Status

The arXiv v1 manuscript states that no public artifact repository is asserted.
A v2 replacement (arXiv submission `8364299`) replaces that sentence with a
Data/Code Availability statement pointing to this repository at release
`v1.0.0` and to the Zenodo version DOI `10.5281/zenodo.24152092`, and adds both
links to the submission Comments. **The replacement was submitted on
2026-11-12 and is awaiting arXiv announcement**; until it is announced, arXiv
v1 remains the public version and the DOI `10.48550/arXiv.2611.11698` is
unchanged.

The artifact archive is published on Zenodo
(doi:[10.5281/zenodo.24152092](https://doi.org/10.5281/zenodo.24152092),
<https://zenodo.org/records/24152092>, version 1.0.0, Software, Open).
`CITATION.cff` now carries the software version DOI; the concept DOI
`10.5281/zenodo.24152091` always resolves to the latest version. Please keep
citing the paper itself for the research claims. Several physical experiments
listed in the manuscript's reproduction plan remain open; the evidence JSON
flags which results are measured, contract-derived or replay-only.

