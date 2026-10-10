# Publishing the dataset on Hugging Face

Dataset repository to create: **`LocalNetDynamics`**, which becomes
`ezharjan/LocalNetDynamics` under the account `ezharjan` and is served at
https://huggingface.co/datasets/ezharjan/LocalNetDynamics. The dataset card,
`CITATION.cff` and the README of this repository already point at that
address, so the name must be used exactly as written.

Everything that goes on the Hub lives in the `huggingface/` folder. It is a
generated copy (the folder is listed in `.gitignore`) and is rebuilt from the
root of this repository by `scripts/package_huggingface.py`, so there is never
a second version of any file to keep in sync:

```text
huggingface/
├── README.md          the dataset card (a copy of DATASET_CARD.md, YAML front matter included)
├── LICENSE            MIT, Alexander Ezharjan
├── CITATION.cff       citation metadata for the dataset
├── requirements.txt
├── dataset/           localnet_dynamics.parquet and .csv, summary.json, analysis.json, raw/
├── figures/           png/ and pdf/, the ten figures the card embeds
├── scripts/           collector, builder, annotation, analysis, plotting, packaging
└── tests/             pytest suite
```

The Hub renders the file named `README.md` at the root of a dataset
repository as the dataset card; there is no separate card file on the Hub.
That is why `DATASET_CARD.md` from this repository is placed in the folder
under the name `README.md`, byte for byte. Its YAML front matter tells the
Hub to serve `dataset/localnet_dynamics.parquet` as the `train` split of the
`default` configuration, so the dataset viewer and `load_dataset()` read the
typed Parquet file and ignore the CSV files.

## 1. Regenerate the folder

From the root of this repository, with the dependencies installed
(`pip install -r requirements.txt`):

```bash
python scripts/build_dataset.py
python scripts/analyze.py
python scripts/plot_figures.py
python -m pytest tests
python scripts/package_huggingface.py
```

The last command prints the file list and the total size (about 43 MB). Open
`huggingface/README.md` once and check that the figures and the statistics
table are present. Set `date-released` in the root `CITATION.cff` to the day
of publication before this step; the packaging script copies the version and
the date from it into the dataset's `CITATION.cff`.

## 2. Install the Hugging Face CLI

The CLI is called `hf` and ships with the `huggingface_hub` package. Any one
of the following works:

```bash
pip install -U huggingface_hub
```

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://hf.co/cli/install.ps1 | iex"
```

```bash
curl -LsSf https://hf.co/cli/install.sh | bash
```

Check it with `hf --help`.

## 3. Log in

Create an access token with **write** permission at
https://huggingface.co/settings/tokens, then:

```bash
hf auth login
```

Paste the token when asked. `hf auth whoami` must print `ezharjan`.

## 4. Create the repository

```bash
hf repos create LocalNetDynamics --repo-type dataset
```

Add `--private` to create it private first and make it public from the
settings page later. The command prints the repository URL.

## 5. Upload the folder

From the root of this repository:

```bash
hf upload ezharjan/LocalNetDynamics ./huggingface . --repo-type=dataset --commit-message="LocalNet-Dynamics 1.0.0"
```

The arguments are the repository id, the local folder and the path inside
the repository (`.` for the root). The large files (the CSV and Parquet
files) are stored with Git LFS by the Hub automatically; nothing has to be
configured for that. The command prints the URL of the uploaded tree when it
finishes.

## 6. Check the result

Open https://huggingface.co/datasets/ezharjan/LocalNetDynamics and confirm:

- the card renders with its ten figures and the statistics table;
- the "Files and versions" tab shows `dataset/`, `figures/`, `scripts/`,
  `tests/`, `LICENSE`, `CITATION.cff` and `requirements.txt`;
- the dataset viewer shows one configuration (`default`), one split
  (`train`) and 54,617 rows with 67 columns.

Then load it once from Python:

```python
from datasets import load_dataset

ds = load_dataset("ezharjan/LocalNetDynamics", split="train")
print(ds)
```

## 7. Tag the release

```bash
hf repos tag create ezharjan/LocalNetDynamics v1.0.0 --repo-type dataset
```

## 8. DOI

Version 1.0.0 carries the DOI [10.57967/hf/10841](https://doi.org/10.57967/hf/10841), generated
from the repository's settings page on the Hub ("DOI" section). The DOI is
bound to the revision it was generated on; a later version gets its own DOI
from "Generate new DOI" on the same page. After generating one, update the
`doi:` line in the `DATASET_CITATION` block at the top of
`scripts/package_huggingface.py` (that block is the dataset's
`CITATION.cff`), the `doi` field of the `@dataset` BibTeX entry in
`README.md` and `DATASET_CARD.md`, the DOI lines in the header of both
files and the `references` entry of the root `CITATION.cff`, then run the
packaging script and upload again.

## Updating a published version

Re-run the five commands of step 1 and upload again. Adding `--delete="*"`
removes files from the Hub that no longer exist locally, which keeps the two
trees identical:

```bash
hf upload ezharjan/LocalNetDynamics ./huggingface . --repo-type=dataset --delete="*" --commit-message="LocalNet-Dynamics 1.1.0"
```

Bump `version` and `date-released` in `CITATION.cff`, and the `version`
fields of the citations in `README.md` and `DATASET_CARD.md`, before a new
tag.
