# MRP baseline for maritime and offshore spare parts

This thesis model simulates periodic replenishment of independently demanded spares at a single pooled stockpoint per part. It produces a dated order book, an inventory ledger and service and inventory measures for future policy comparisons.

From the repository root, with Python 3.10 or newer and the OIL-041 source files in place, run

```powershell
python -m unittest discover -s tests -v
python -m src.mrp --output results/local/oil041
```

The daily baseline uses the Python standard library. Install `requirements.txt` for the monthly Excel adapter or `notebooks/requirements.txt` for the executable walkthrough. Each CLI run needs a new output directory.

- Locate the raw files and check their hashes in the [source guide](SOURCES.md).
- Follow one part through forecasting, ordering, receipt and scoring in the [notebook](notebooks/mrp_model.ipynb).
- See the equations, policy interface, output definitions and comparison protocol in the [model specification](docs/mrp_baseline.md).
- Read the [review and validation results](results/README.md) for measured changes and the status of archived outputs. The [dataset inventory](docs/dataset_inventory.md) covers the wider thesis collection.
