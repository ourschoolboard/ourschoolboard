# Contributing

Open an issue before substantial architectural work. Keep changes inside the
documented public scope, add focused tests, and preserve source provenance and
the collector sandbox boundary.

Before opening a pull request, run:

```sh
npm test
npm run check
python3 -m unittest discover -s tests -p 'test_*.py'
```

Never commit credentials, source-document corpora, district-specific personal
information, or generated production data.
