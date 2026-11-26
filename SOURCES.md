# Sources for the executable MRP baseline

Raw downloads are local inputs under `data/temp_downloads/` and are excluded from Git. An alternate source directory can be supplied with `--input`. The adapters use the files below. The SHA-256 values identify the copies used for the validation runs.

## OIL-041 daily synthetic scenario

Place the CSVs directly in `data/temp_downloads/oil041/`. XpertSystems supplies synthetic maintenance consumption, part and facility masters, and an undated inventory snapshot. The publisher labels the sample CC BY-NC 4.0. The local copy came from revision `f0b09e6fa638189e22fcd395e133069de877dfa1`. [Publisher's pinned sample](https://huggingface.co/datasets/xpertsystems/oil041-sample/tree/f0b09e6fa638189e22fcd395e133069de877dfa1)

| File | SHA-256 |
|---|---|
| spare_parts_master.csv | `2b0da65c01ef0c855d087f9d55c735fc9b28f0389ca6a9125e298623c638d9be` |
| facility_master.csv | `2f74ff8ed6db68534a07bc38826a39f54c2b3e859b4dbef4230a28c731b12222` |
| maintenance_consumption.csv | `cdcb8bd5979655f2f21a29ddcd483883144dcf73c3f2dabbbfafa555b1914e1d` |
| inventory_levels.csv | `3eb6d88a48ddd3df29f29df503b63da96e0ef33473737be43b5203688c2aad82` |

## SPDF monthly industrial oil series

Place both original workbook halves in `data/temp_downloads/spdf-main/`, keeping the space in the second filename. Daniel de Haan's 2021 repository *GitHub repository for benchmarking spare parts demand forecasting for intermittent demand* distributes the workbooks alongside other industrial and simulated benchmarks. Each workbook's `Info` sheet defines the units of lead time, stock fields, equipment counts, whole-year blanks and the unreliable month. The source currency, original operator, specific offshore facilities and stock snapshot date are unverified. See the repository's citation metadata and the local assessment for provenance limits. [SPDF repository](https://github.com/danieldehaan96/spdf) · [Dataset assessment](docs/dataset_inventory.md)

| File | SHA-256 |
|---|---|
| Data_deel1a.xls | `388cbcab4770176485a8aa51707741ea0b50f89da63e1b8f25de11e87924dc0b` |
| Data _deel1b.xls | `ce6de323441d431dcb4d85ba5a51299733ea4faafd87e2c79b82e5f8c8877444` |

The monthly adapter requires `xlrd==2.0.2`. The source values and adjustments in the run outputs support tracing a chosen SKU and month back to the original workbook.
