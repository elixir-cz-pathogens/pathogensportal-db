# Diagrams

| Diagram | Shows |
|---|---|
| [overview](overview.png) | the data flow and the metadata flow in one picture |
| [data-flow](data-flow.png) | every source, scraper, CSV file, generator and output file |
| [metadata-flow](metadata-flow.png) | how publisher metadata and the YAML registries become the `meta` block |

Each diagram is a `.drawio` file (the editable source) with a `.png` export of the same name.

## Editing

Open the `.drawio` file in [draw.io / diagrams.net](https://app.diagrams.net/), in the desktop
application, or with the draw.io extension of VS Code. Then re-export the PNG and commit both
files.

Avoid backticks in labels: the PNG export typeset text between backticks as a formula.

## Exporting the PNG files

From the desktop application: *File → Export as → PNG*, zoom 200 %, border 20.

From the command line, with Docker — this exports every diagram in the directory:

```bash
cd docs/diagrams
rm -f *.png
docker run --rm -v "$PWD:/data" rlespinasse/drawio-export \
  --format png --scale 2 --border 20 --output . --remove-page-suffix
```

Remove the old PNG files first, as shown; the export did not finish for us when they were still
present. The container writes the files as `root`; fix the owner afterwards if needed:

```bash
sudo chown "$(id -u):$(id -g)" *.png
```
