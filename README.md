# DDMRP-in-Maritime-Supply-Chains

MRP baseline for spare-parts/MRO experiments. From this directory:

```powershell
python -m unittest discover -s tests -v
python -m src.mrp --output results/mrp_oil041
```

Python 3.10+. Default OIL-041 execution needs no third-party packages; `python -m pip install -r requirements.txt` enables the optional SPDF Excel adapter.

See [model assumptions, commands and outputs](docs/mrp_baseline.md) and [dataset assessment](docs/dataset_inventory.md). Raw downloads remain unchanged. Run directories contain canonical inputs, MRP plans, simulation ledgers, orders, KPIs and provenance.

For an executable walkthrough with worked calculations, plots, KPIs and validation, open [notebooks/mrp_model.ipynb](notebooks/mrp_model.ipynb). Install its separate dependencies with `python -m pip install -r notebooks/requirements.txt`, select that environment as the kernel, and run all cells.
