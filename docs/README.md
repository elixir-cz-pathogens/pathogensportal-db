# Documentation

Start with [architecture.md](architecture.md) — it has the diagrams and links everything else.

| Document | Read it when you want to know… |
|---|---|
| [architecture.md](architecture.md) | how the phases fit together and why they are built this way |
| [data-sources.md](data-sources.md) | what a given source provides, how its scraper works and what can go wrong with it |
| [snapshots.md](snapshots.md) | how downloads are archived and how to reproduce an old result |
| [data-model.md](data-model.md) | what is in PostgreSQL and how each CSV is mapped into it |
| [chart-generation.md](chart-generation.md) | how a CSV becomes a chart JSON, and what every generated file contains |
| [metadata.md](metadata.md) | how a chart learns its unit, source, freshness and caveats |
| [analytics/anomaly-detection.md](analytics/anomaly-detection.md) | how the early-warning signals are computed and validated |
| [analytics/flu-mem.md](analytics/flu-mem.md) | how the influenza thresholds, the trend and the forecast are computed |
| [HOLDOUT.md](HOLDOUT.md) | which outbreak episodes are frozen for the final evaluation |
| [deployment.md](deployment.md) | how the pipeline runs on the server and how a release reaches the portal |
| [development.md](development.md) | how to run the tests and how to add a source or a chart |
| [diagrams/](diagrams/) | the draw.io sources of the flowcharts and how to re-export them |
