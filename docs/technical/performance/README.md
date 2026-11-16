# Performance evidence

These pages preserve measurements, methodology, and limitations. Compare the
same selection, worker count, instrumentation, and conditions before claiming an
improvement; a historical result isn't a current service guarantee.

## Choose an investigation

Start with the report closest to the behavior you are measuring:

| Investigation                                      | What it covers                                                  |
| -------------------------------------------------- | --------------------------------------------------------------- |
| [Core optimization results](core-optimizations.md) | Implemented changes, preserved contracts, and paired benchmarks |
| [Core baseline](core-investigation.md)             | Pre-optimization findings, alternatives, and reproduction       |
| [Terminal latency](terminal-latency.md)            | Historical UI blocking and tool-presentation findings           |
| [Test-suite runtime](test-suite.md)                | Sequential measurements, component split, and remaining work    |

Raw core benchmark JSON is retained in [`benchmarks/`](benchmarks/). The
relevant report links each sample to its conditions. No provider-cost or
model-quality improvement follows merely from faster local code.

For tool-use checks, open [Evaluation](../evaluation/README.md).

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
