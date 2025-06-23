# Sample data for lsst/lsst-texmf

```
http --download -o repo.json get "https://api.github.com/repos/lsst/lsst-texmf" "Accept:application/vnd.github+json" "X-GitHub-Api-Version:2022-11-28"
http --download -o authordb-contents.json get "https://api.github.com/repos/lsst/lsst-texmf/contents/etc/authordb.yaml?ref=main" "Accept:application/vnd.github.object+json" "X-GitHub-Api-Version:2022-11-28"
http --download -o authordb.yaml get "https://api.github.com/repos/lsst/lsst-texmf/contents/etc/authordb.yaml?ref=main" "Accept:application/vnd.github.raw+json" "X-GitHub-Api-Version:2022-11-28"
http --download -o glossarydefs.csv get "https://api.github.com/repos/lsst/lsst-texmf/contents/etc/glossarydefs.csv?ref=65d9fcab97424f934cd0245f57c190ae2a269cd5" "Accept:application/vnd.github.raw+json" "X-GitHub-Api-Version:2022-11-28"
http --download -o glossarydefs_es.csv get "https://api.github.com/repos/lsst/lsst-texmf/contents/etc/glossarydefs_es.csv?ref=65d9fcab97424f934cd0245f57c190ae2a269cd5" "Accept:application/vnd.github.raw+json" "X-GitHub-Api-Version:2022-11-28"
http --download -o glossarydefs-contents.json get "https://api.github.com/repos/lsst/lsst-texmf/contents/etc/glossarydefs.csv?ref=65d9fcab97424f934cd0245f57c190ae2a269cd5" "Accept:application/vnd.github.object+json" "X-GitHub-Api-Version:2022-11-28"
http --download -o glossarydefs-es-contents.json get "https://api.github.com/repos/lsst/lsst-texmf/contents/etc/glossarydefs_es.csv?ref=65d9fcab97424f934cd0245f57c190ae2a269cd5" "Accept:application/vnd.github.object+json" "X-GitHub-Api-Version:2022-11-28"
```
