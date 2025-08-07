### New features

- Added formatted address field to affiliation responses. Affiliation addresses now include a `formatted` field that contains a properly formatted address string using international standards via the google-i18n-address library. The formatting respects country-specific conventions for address layout and includes graceful fallback handling for invalid or incomplete data. This feature maintains backwards compatibility with existing API consumers.
