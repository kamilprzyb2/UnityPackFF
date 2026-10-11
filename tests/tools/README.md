# Tools

Command-line tools that run the same operations as the integration tests, but write their results to files you can inspect by hand. Each integration test calls a function from one of these scripts and asserts on what it returns, so what you look at here is exactly what's tested.

| Script | What it does | Used by |
| --- | --- | --- |
| `fetch.py` | Downloads archives of the pinned builds into the cache, checks their hashes, unpacks them | everything (the other scripts fetch what they need on their own too) |
| `survey.py` | Describes every file: format, type trees, class IDs, what UnityPackFF can read | `integration/test_survey.py` |
| `roundtrip.py` | ffbuildtool extract → UnityPackFF save → ffbuildtool pack → re-open and compare; optionally a whole playable build | `integration/test_roundtrip.py` |
| `meshes.py` | Exports every mesh of an archive as OBJ, plus the skin weights of skinned meshes | `integration/test_meshes.py` |
| `clips.py` | Reads every AnimationClip of an archive and samples each curve at the clip's rate | `integration/test_clips.py` |
| `_common.py` | The options all the scripts share (not run directly) | |

Run them from the repository root (`UnityPackFF/`) as modules of the `tests` package: `python3 -m tests.tools.<name>`, without `.py`.

## Options every script takes

| Option | Meaning | Default |
| --- | --- | --- |
| `--build X` | `104`, `728`, `1013` or `all`; repeat it for several | `all` |
| `--bundle NAME` | one archive, by its name in the manifest (e.g. `TableData.resourceFile`, `Map_04_02.unity3d`, `main.unity3d`); repeatable | the curated set |
| `--all` | every archive of the build instead of the curated set | off |
| `--out DIR` | where results are written (`fetch.py` doesn't write any) | `out/` |
| `--jobs N` | parallel workers (`survey.py`) or downloads (`fetch.py`) | based on the CPU count |

The **curated set** is 13–14 archives per build that cover each kind of content (table data, map tiles, icons, music, characters, `main.unity3d`), listed in `CURATED` in [`tests/bundles.py`](../bundles.py). With `--all` you get every archive: 384 (104), 391 (728), and 455 (1013), 2.2 GB in total.

Archives go to `~/.cache/unitypackff-tests/<build>/`, unpacked files to `<build>/unpacked/<archive>/`. Set `UNITYPACKFF_CACHE` to use another directory. Set `UNITYPACKFF_MIRROR` to a directory that already holds a build's archives, and they're copied from there (after a hash check) instead of downloaded.

## fetch.py

```sh
python3 -m tests.tools.fetch --build 1013                    # Academy's curated set
python3 -m tests.tools.fetch --build 104 --bundle Icons.resourceFile
python3 -m tests.tools.fetch --build all --all               # everything
```

Prints one line per build: how many archives are ready and how many failed, with the reason for each failure. It exits with code 1 if any failed. Archives already in the cache are not downloaded again.

## survey.py

```sh
python3 -m tests.tools.survey --build 104 --bundle TableData.resourceFile
python3 -m tests.tools.survey --build 1013                   # Academy's curated set
python3 -m tests.tools.survey --build all --all              # every archive of every build
```

Extra option: `--no-read` only opens files, without reading every object (much faster).

Writes:

| File | Contents |
| --- | --- |
| `out/survey/<build>.json` | one record per archive (header, which files `unitypack.load()` exposes) and per inner file (size, hash, format, type trees, class IDs, object count and alignment, externals, what UnityPackFF read, its errors, a digest of all objects) |
| `out/survey/summary.md` | one table per build: formats, type trees, class IDs, what UnityPackFF opens and reads |

## roundtrip.py

Needs [ffbuildtool](https://github.com/OpenFusionProject/ffbuildtool) on `PATH` (`cargo install --git https://github.com/OpenFusionProject/ffbuildtool`), or `FFBUILDTOOL=/path/to/ffbuildtool`.

```sh
python3 -m tests.tools.roundtrip --build 104 --bundle TableData.resourceFile
python3 -m tests.tools.roundtrip --build 1013 --bundle Map_04_02.unity3d --bundle Icons.resourceFile
```

For each archive:

1. `ffbuildtool extract-bundle` unpacks it.
2. Every serialized file is opened and saved again with UnityPackFF, without changes; other files (DLLs) are copied.
3. `ffbuildtool pack-bundle` packs the saved files.
4. The new archive is read back and compared: the same files in the same order, and every object reading back the same as before.

It prints `ok` or `DIFFERENT` per archive (plus `(file order changed)` if ffbuildtool reordered the files), and any save errors.

Extra options:

| Option | Meaning | Default |
| --- | --- | --- |
| `--level N` | LZMA compression level for `pack-bundle` | 4 |
| `--client-build DIR` | also lay out a whole playable build (below) | off |
| `--asset-url URL` | where that build will be served from | `http://localhost:8000` |
| `--name NAME` | the build's name in its manifest | `<build>-unitypackff` |

Writes `out/roundtrip/<build>.json` (per file: original and saved SHA-256, whether the bytes are identical, object digests before and after) and the working files in `out/roundtrip/work/<build>/<archive>/`: `extracted/`, `saved/` and `packed/<archive>`.

Archives are processed one at a time; `--jobs` has no effect here.

### A build to try in the real client

`--client-build` copies the whole build from the cache into `DIR/<build>/` (the build must be fetched with `--all` first), swaps in the round-tripped archives, and writes `DIR/<build>/manifest.json` with `ffbuildtool gen-manifest`. The manifest gets a new UUID and names the original build as its parent.

```sh
python3 -m tests.tools.fetch --build 104 --all
python3 -m tests.tools.roundtrip --build 104 --bundle TableData.resourceFile \
    --client-build out/client --asset-url http://localhost:8000
python3 -m http.server 8000 -d out/client
```

Then add `out/client/beta-20100104/manifest.json` to the launcher as a custom game version and play against an OpenFusion server built for that protocol (104 here).

## meshes.py

```sh
python3 -m tests.tools.meshes --build 104 --bundle FutureNano.resourceFile
python3 -m tests.tools.meshes --build 1013 --bundle Nano_001.resourceFile --bundle CharacterSelection_wear_face.resourceFile
```

Prints how many meshes each archive has and how many of them are skinned. Writes, per serialized file, into `out/meshes/<build>/<archive>/<file>/`:

| File | Contents |
| --- | --- |
| `<path ID>_<name>.obj` | the mesh as `OBJMesh` exports it |
| `<path ID>_<name>.skin.json` | skinned meshes only: per vertex, its `[bone index, weight]` pairs (1 to 4, adding up to 1). The bone index points into the `m_Bones` list of the mesh's SkinnedMeshRenderer |

and `out/meshes/<build>.json` with the statistics the integration test checks (vertex and triangle counts, OBJ digest, how many vertices have how many weights).

## clips.py

```sh
python3 -m tests.tools.clips --build 104 --bundle FutureNano.resourceFile
python3 -m tests.tools.clips --build 1013 --bundle Nano_001.resourceFile
```

Prints how many clips each archive has and how many store their rotations compressed. Writes, per serialized file, `out/clips/<build>/<archive>/<file>/<path ID>_<name>.json` for each clip: its name, sample rate, length and events, then per curve its kind (`rotation`, `position`, `scale` or `float`), the transform path it moves (for a `float` curve, path and property), its keys, and its values at every frame from 0 to the clip's length. Rotations are sampled as `evaluate()` returns them, without normalizing. Also writes `out/clips/<build>.json` with the statistics the integration test checks (curve and key counts per kind, length, event names, a digest of the samples).
