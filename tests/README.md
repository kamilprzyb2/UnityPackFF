# Tests

```sh
pip install -e '.[test]'
pytest                                   # unit + harness tests: made-up data, offline, under a second
pytest tests/unit                        # only the tests of UnityPackFF itself
pytest -m integration                    # curated archives of all three builds (~170 MB download)
pytest -m integration --build 1013       # one build
pytest --sweep                           # every archive of every build (2.2 GB); not usable yet, see below
pytest -m integration --record           # rewrite tests/expected/<build>/ from this run
```

## Layout

| Path | What |
| --- | --- |
| `unit/` | **UnityPackFF itself**, on made-up data, one file per area of the library named after it (`test_asset_read.py`, `test_texture.py`, ...). No network, no client files |
| `harness/` | Tests of our own helpers below (`bundles.py`, `serialized.py`, `digest.py`), so they can be trusted as a yardstick |
| `tools/` | Command-line tools behind the integration tests, with their own [README](tools/README.md) |
| `integration/` | UnityPackFF on real archives: one case per operation and archive, parametrized over the builds |
| `manifests/` | The three pinned launcher manifests: names, sizes and SHA-256 hashes only |
| `expected/` | Expected values, one folder per build and one file per area (`meshes.json`, `clips.json`, ...): header fields, counts, class IDs, digests. Never client data |
| `bundles.py` | Build registry, download cache, hash checks, an independent UnityWeb archive reader |
| `serialized.py` | An independent parser for Unity 2.5 serialized files (formats 5 to 7). The survey's instrument: it reads files UnityPackFF can't open yet and checks each file is understood to its last byte. Unit tests don't use it; it can go once UnityPackFF opens every file and checks that itself |
| `synth.py` | Builders for made-up type trees, serialized files and archives (plain `struct.pack`) |
| `digest.py` | Stable digests of what unitypack reads |

`synth.py` shares no code with unitypack: unit tests feed the library hand-written bytes and compare its output with hand-written bytes, so the library's reader and writer can't agree on a bug and still pass.

## Known bugs (strict xfails)

A known library bug is a test marked `xfail(strict=True)`: it is expected to fail, and the run fails if it passes, so fixing a bug forces removing its marker.

| Test | Bug |
| --- | --- |
| `unit/test_assetbundle.py::test_two_files`, `integration/test_archives.py::test_unitypack_load` (multi-file archives) | `unitypack.load()` reads only the first file of an archive |
| `integration/test_survey.py::test_survey` (2 archives) | Format 7: 64-bit object IDs truncated to 32 bits collide |
| `unit/test_asset_save.py::test_save_is_byte_identical` | `Asset.save()` starts objects on 4 bytes, Unity on 8 |
| `unit/test_asset_save.py::test_save_keeps_trailing_padding` | `BinaryWriter.align()` seeks instead of writing, so padding after an object's last field is lost |
| `unit/test_texture.py::test_decode_rgb565`, `test_decode_argb4444`; `unit/test_modding.py::test_rgb16_round_trip` | Red and blue swapped when decoding RGB565 and ARGB4444 (the importer writes them correctly) |
| `unit/test_export.py::test_compressed_normals_are_unit_length` | Compressed normals: z = 1 − x² − y² without the square root, so every exported normal is wrong |

Not covered yet: cross-archive pointers (`UnityEnvironment`), `bin/` scripts, `AudioClip` export.

## Builds

| Alias | Build | Archives |
| --- | --- | --- |
| `104` | beta-20100104 (Original) | 383 + `main.unity3d` |
| `728` | beta-20100728 | 390 + `main.unity3d` |
| `1013` | beta-20111013 (Academy) | 454 + `main.unity3d` |

Archives are downloaded on first use into `~/.cache/unitypackff-tests/<build>/` (override with `UNITYPACKFF_CACHE`) and checked against the manifest. Unpacked files go to `<build>/unpacked/<archive>/`, also checked file by file. If you already have a build on disk, point `UNITYPACKFF_MIRROR` at the directory (several can be joined with `:`). Files whose hash matches are copied from it instead of downloaded.

If an archive can't be fetched (a CDN moved, or no network), its tests skip with the reason; with `UNITYPACKFF_REQUIRE_ARCHIVES=1`, as in CI, they fail instead.

These are official builds, whose archives on the CDN don't change. Servers that update their client in place (Retrobution, for one) serve only their current version, so they can't be pinned and aren't tested here.

**Moving a pin**: replace the manifest in `manifests/`, update `PINS` in `harness/test_bundles.py`, re-record with `--record`, check the tools' output by eye, and commit all of it together.

## Expected values

Integration tests call `expected.check("area/name", value)`. Without `--record` it compares with `name` in `tests/expected/<build>/<area>.json`; with `--record` it stores the value. Each area has a file of its own, so tests of different features don't touch the same file. Keys that a run doesn't touch are kept. Review the diff of a re-record like code.

## CI

GitHub Actions run the unit tests on every push to `master` and every pull request (`.github/workflows/unit.yml`, on the oldest and a current Python), and the integration tests on every pull request and push to `master` that changes more than Markdown files (`integration.yml`, one job per build, in parallel). The integration jobs cache the downloaded archives, keyed by `manifests/` and `bundles.py`, so the CDN is only asked again when those change, and the `ffbuildtool` binary. A missing archive fails the job instead of skipping its tests. Nothing is required to pass before merging.

## Running every archive (not done yet)

The integration tests cover the curated set (13–14 archives per build). Running them over every archive needs three changes first.

1. **Make `--sweep` pass.** Recorded values exist only for the curated archives, so on any other archive the comparing tests fail with "no expected value" (`test_unpack_matches_manifest`, `test_survey`). The plan is to compare recorded values for the curated set only, and for every other archive check what must always hold: it unpacks to exactly the manifest's files, the metadata parses to the last byte, UnityPackFF opens every file and reads every object (apart from known bugs), and a round trip leaves objects unchanged. Recording everything instead would grow `expected/` from about 150 KB to about 5 MB, and every reader change would re-record it.
2. **Make the suite safe to run in parallel** with pytest-xdist (`pytest -n 4`). Comparing is already safe. Two things are not:
   - Downloading and unpacking: two processes working on the same archive write the same `.part` file and the same unpacked files. Use per-process temporary names and atomic renames; in CI, also fill the cache first with `python3 -m tests.tools.fetch --build all --all`.
   - `--record`: each process saves the whole expected file with only its own results, so processes overwrite each other. Merge on save, or refuse `--record` together with `-n`.
3. **A CI job for it:**
   - cache the 2.2 GB of archives between runs, keyed by the hashes of `manifests/` (it fits GitHub's 10 GB cache limit);
   - install ffbuildtool for the round-trip tests;
   - expect reading every object of all 1,230 archives to take about 35 CPU-minutes, so 4 or more cores.

## Tools

`tools/` holds command-line tools that run the same operations as the integration tests on the pinned builds and write files to inspect by hand, one per kind of test. Run them from the repository root as modules, e.g. `python3 -m tests.tools.fetch --build 104`. [tools/README.md](tools/README.md) lists them and how to call each.
