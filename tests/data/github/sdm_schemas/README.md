# Repo data sample using sdm_schemas

This directory contains sample data from the `lsst/sdm_schemas` repository on GitHub.
The data is used to test the `post_ingest_sdm_schemas` endpoint and the SdmSchemasIngestService class in general.

`repo.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas"
```

`tree.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/trees/w.2025.10?recursive=1"
```

> ![NOTE]
> This tree is modified slightly to remove reference to unused schema browser markdown files.
> This reduces the number of blobs that need to be included in this directory to fully test the ingest service with the `w.2025.10` release of lsst/sdm_schemas.

`release.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/releases/latest"
```

`deployed_schemas_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/0c9b215caeddf9e32dad6c7d26fb067fd81e4a98"
```

`dp02_dc2_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/0ae398f33adf59e647d766eabd5a3cc0f592026e" > dp02_dc2_blob.json
```

`dp02_obscore_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/9a18717c8a1a7d0f71d989558f621ad59718487d" > dp02_obscore_blob.json
```

`dp03_10yr_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/9049111d8d24f455b96bb96f70dd521aee5c0e7c" > dp03_10yr_blob.json
```

`dp03_1yr_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/e8d4603384bfecbb41b4a6958211385d8de7412b" > dp03_1yr_blob.json
```

`dp02_md_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/6f7ae30cf2e14075a479164d2d23fc0a046a78bd" > dp02_md_blob.json
```

`dp03_md_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/d58c2ca33825ef17f13d1846f0d51b17eecfec7d" > dp03_md_blob.json
```
