# Repo data sample using sdm_schemas

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

`release.json`:

```
gh api -H "Accept: application/vnd.github+json" \
-H "X-GitHub-Api-Version: 2022-11-28" \
"/repos/lsst/sdm_schemas/releases/latest"
```
