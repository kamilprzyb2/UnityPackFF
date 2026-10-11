# Tests

```sh
pip install -e '.[test]'
pytest                                   # unit + harness tests: made-up data, offline, under a second
pytest tests/unit                        # only the tests of UnityPackFF itself
pytest -m integration                    # curated archives of all four builds (~200 MB download)
pytest -m integration --build 1013       # one build
pytest --sweep                           # every archive of every build (2.7 GB); not usable yet, see below
pytest -m integration --record           # rewrite tests/expected/<build>.json from this run
```

## Layout

| Path | What |
| --- | --- |
| `unit/` | **UnityPackFF itself**, on made-up data. One file per area: `test_utils.py` (binary I/O), `test_asset_read.py` / `test_asset_save.py` (serialized files), `test_assetbundle.py` (archives), `test_texture.py`, `test_export.py` (OBJ, skin weights), `test_modding.py` (imports), `test_environment.py` (pointers into other files), `test_scripts.py` (`bin/` scripts). No network, no client files |
| `harness/` | Tests of our own helpers below (`bundles.py`, `serialized.py`, `digest.py`), so they can be trusted as a yardstick |
| `demo/` | Command-line scripts behind the integration tests, with their own [README](demo/README.md) |
| `integration/` | UnityPackFF on real archives: one case per operation and archive, parametrized over the builds |
| `manifests/` | The four pinned launcher manifests: names, sizes and SHA-256 hashes only |
| `expected/` | Per-build expected values: header fields, counts, class IDs, digests. Never client data |
| `bundles.py` | Build registry, download cache, hash checks, an independent UnityWeb archive reader |
| `serialized.py` | An independent parser for Unity 2.5 serialized files (formats 5 to 7). The survey's instrument: it reads files UnityPackFF can't open yet and checks each file is understood to its last byte. Unit tests don't use it; it can go once UnityPackFF opens every file and checks that itself |
| `synth.py` | Builders for made-up type trees, serialized files and archives (plain `struct.pack`) |
| `digest.py` | Stable digests of what unitypack reads |

`synth.py` shares no code with unitypack: unit tests feed the library hand-written bytes and compare its output with hand-written bytes, so the library's reader and writer can't agree on a bug and still pass.

## Known bugs (strict xfails)

A known library bug is a test marked `xfail(strict=True)`: it is expected to fail, and the run fails if it passes, so fixing a bug forces removing its marker.

There are none at the moment.

Not covered yet: `UnityEnvironment` beyond finding files (`discover`, `archive:` URLs), `bin/` scripts other than `index_bundles.py` and `dumpxdt.py` (`integration/test_scripts.py`), `AudioClip` export.

## Builds

| Alias | Build | Archives |
| --- | --- | --- |
| `104` | beta-20100104 (Original) | 383 + `main.unity3d` |
| `728` | beta-20100728 | 390 + `main.unity3d` |
| `1013` | beta-20111013 (Academy) | 454 + `main.unity3d` |
| `retro` | retrobution-20260821 | 360 + `main.unity3d` |

Archives are downloaded on first use into `~/.cache/unitypackff-tests/<build>/` (override with `UNITYPACKFF_CACHE`) and checked against the manifest. Unpacked files go to `<build>/unpacked/<archive>/`, also checked file by file. If you already have a build on disk, point `UNITYPACKFF_MIRROR` at the directory (several can be joined with `:`). Files whose hash matches are copied from it instead of downloaded.

If an archive can't be fetched (a CDN moved, or no network), its tests skip with the reason.

**Moving a pin** (Retrobution's API serves only its current version): replace the manifest in `manifests/`, update `PINS` in `unit/test_bundles.py`, re-record with `--record`, check the demo output by eye, and commit all of it together.

## Expected values

Integration tests call `expected.check(key, value)`. Without `--record` it compares with `tests/expected/<build>.json`; with `--record` it stores the value. Keys that a run doesn't touch are kept. Review the diff of a re-record like code.

## Running every archive (for CI, not done yet)

The integration tests cover the curated set (13–14 archives per build). Running them over every archive, as CI should, needs three changes first.

1. **Make `--sweep` pass.** Recorded values exist only for the curated archives, so on any other archive the comparing tests fail with "no expected value" (`test_unpack_matches_manifest`, `test_survey`). The plan is to compare recorded values for the curated set only, and for every other archive check what must always hold: it unpacks to exactly the manifest's files, the metadata parses to the last byte, UnityPackFF opens every file and reads every object (apart from known bugs), and a round trip leaves objects unchanged. Recording everything instead would grow `expected/` from about 150 KB to about 5 MB, and every reader change would re-record it.
2. **Make the suite safe to run in parallel** with pytest-xdist (`pytest -n 4`). Comparing is already safe. Two things are not:
   - Downloading and unpacking: two processes working on the same archive write the same `.part` file and the same unpacked files. Use per-process temporary names and atomic renames; in CI, also fill the cache first with `python3 tests/demo/fetch.py --build all --all`.
   - `--record`: each process saves the whole expected file with only its own results, so processes overwrite each other. Merge on save, or refuse `--record` together with `-n`.
3. **The CI job:**
   - cache the 2.7 GB of archives between runs, keyed by the hashes of `manifests/` (it fits GitHub's 10 GB cache limit);
   - install ffbuildtool for the round-trip tests;
   - expect reading every object of all 1,591 archives to take about 45 CPU-minutes, so 4 or more cores.

## Demos

`demo/` holds command-line scripts that run the same operations as the integration tests and write files to inspect by hand: fetching archives, the format survey and the ffbuildtool round trip. How to call them: [demo/README.md](demo/README.md).
