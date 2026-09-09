# Sphinx object inventory samples

Real `objects.inv` payloads, kept so the parser is exercised against bytes a
Sphinx site actually published rather than only against ones the tests
compress themselves. The synthetic inventories the tests build inline cover
the format's edges; these cover the shapes real documentation sites take.

`pipelines.lsst.io.objects.inv`:

```
curl -sfL https://pipelines.lsst.io/objects.inv \
  -o pipelines.lsst.io.objects.inv
```

Fetched 2026-09-02 (upstream `Last-Modified: Thu, 06 Aug 2026 16:03:54 GMT`,
`ETag: "2ef6c403d3413ab38dd4469a009d58c9"`) and committed unmodified.

This is the inventory PRD #237 registers first, and it is a useful sample
beyond its size: 29,629 objects across the `py` and `std` Sphinx domains,
with both URI (`$`) and display-name (`-`) abbreviations throughout. It also
carries the awkward real-world case the hierarchy strategy has to get right
— `lsst.afw.table` is documented only as a `std:label` for its module page,
never as a `py:module`, so its classes have no parent in the `py` domain.
