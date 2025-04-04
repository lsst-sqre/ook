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

`apdb_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/5e8b93c689bc95f4d548f76537377ef5e9a4d350" > apdb_blob.json
```

`cdb_latiss.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/c5e6c6d7756761d74a9aa33c6de79cfed506eb2c" > cdb_latiss.json
```

`db_lsstcomcam_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/e40b9e2155f25d61f66288fa2f1c5f3819729d0b" > db_lsstcomcam_blob.json
```

`cdb_lsstcomcamsim_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/5e6042de0885c9059ad7f365b71bc78dc5a79611" > cdb_lsstcomcamsim.json
```

`cdb_startrackerfast_blob.json

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/5d263ae227e9b8c7e334872f2eb7cc10ec9803de" > cdb_startrackerfast_blob.json
```

`cdb_startrackernarrow_blob.json

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/7c6eec3924b8e2f072b414b2b4e3a67b3c6e8ead" > cdb_startrackernarrow_blob.json
```

`cdb_startrackerwide_blob.json`

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/647727555e51da224ac81142c170843d6487b0a2" > cdb_startrackerwide_blob.json
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

`hsc_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/e22ceff6006074c9ed5e4f74e6b174c3d0ca5c69" > hsc_blob.json
```

`imsim_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/a698e6a8d242787971be456df1fd79b2bba21aca" > imsim_blob.json
```

`obsloctap_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/4662004c391af1b28bde441371f570cf88608a49" > obsloctap_blob.json
```

`oga_live_obscore_blob.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/git/blobs/a0cd8939f59757eaf177a49dfa0ffda0450154e7" > oga_live_obscore_blob.json
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
