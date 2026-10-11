# Demo scripts

Command-line tools that run the same operations as the integration tests, but write their results to files you can inspect by hand. Each integration test calls a function from one of these scripts and asserts on what it returns, so what you look at here is exactly what's tested.

| Script | What it does | Used by |
| --- | --- | --- |
| `fetch.py` | Downloads archives of the pinned builds into the cache, checks their hashes, unpacks them | everything (the other scripts fetch what they need on their own too) |
| `survey.py` | Describes every file: format, type trees, class IDs, what UnityPackFF can read | `integration/test_survey.py` |
| `roundtrip.py` | ffbuildtool extract → UnityPackFF save → ffbuildtool pack → re-open and compare; optionally a whole playable build | `integration/test_roundtrip.py` |
| `meshes.py` | Exports every mesh of an archive as OBJ, plus the skin weights of skinned meshes | `integration/test_meshes.py` |
| `clips.py` | Reads every AnimationClip of an archive and samples each curve at the clip's rate | `integration/test_clips.py` |
| `terrain.py` | Dumps every terrain of an archive with `bin/dump_terrain.py` and compares Unity's level-of-detail data with what `TerrainData` works out | `integration/test_terrain.py` |
| `gltf.py` | Exports every prefab of an archive, or a map tile's whole scene, as glTF (`.glb`) and runs the Khronos validator on them when it's installed | `integration/test_gltf.py` |
| `mapdata.py` | Reads the attribute grid and colours of map tiles and the music zones of `main.unity3d`, and dumps them with `bin/dump_mapdata.py` | `integration/test_mapdata.py` |
| `_common.py` | The options all the scripts share (not run directly) | |

Run them from the repository root (`UnityPackFF/`).

## Options every script takes

| Option | Meaning | Default |
| --- | --- | --- |
| `--build X` | `104`, `728`, `1013`, `retro` or `all`; repeat it for several | `all` |
| `--bundle NAME` | one archive, by its name in the manifest (e.g. `TableData.resourceFile`, `Map_04_02.unity3d`, `main.unity3d`); repeatable | the curated set |
| `--all` | every archive of the build instead of the curated set | off |
| `--out DIR` | where results are written (`fetch.py` doesn't write any) | `out/` |
| `--jobs N` | parallel workers (`survey.py`) or downloads (`fetch.py`) | based on the CPU count |

The **curated set** is 13–14 archives per build that cover each kind of content (table data, map tiles, icons, music, characters, `main.unity3d`), listed in `CURATED` in [`tests/bundles.py`](../bundles.py). With `--all` you get every archive: 384 (104), 391 (728), 455 (1013) and 361 (Retrobution), 2.7 GB in total.

Archives go to `~/.cache/unitypackff-tests/<build>/`, unpacked files to `<build>/unpacked/<archive>/`. Set `UNITYPACKFF_CACHE` to use another directory. Set `UNITYPACKFF_MIRROR` to a directory that already holds a build's archives, and they're copied from there (after a hash check) instead of downloaded.

## fetch.py

```sh
python3 tests/demo/fetch.py --build 1013                    # Academy's curated set
python3 tests/demo/fetch.py --build 104 --bundle Icons.resourceFile
python3 tests/demo/fetch.py --build all --all               # everything
```

Prints one line per build: how many archives are ready and how many failed, with the reason for each failure. It exits with code 1 if any failed. Archives already in the cache are not downloaded again.

## survey.py

```sh
python3 tests/demo/survey.py --build 104 --bundle TableData.resourceFile
python3 tests/demo/survey.py --build 1013                   # Academy's curated set
python3 tests/demo/survey.py --build all --all              # every archive of every build
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
python3 tests/demo/roundtrip.py --build 104 --bundle TableData.resourceFile
python3 tests/demo/roundtrip.py --build 1013 --bundle Map_04_02.unity3d --bundle Icons.resourceFile
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
python3 tests/demo/fetch.py --build 104 --all
python3 tests/demo/roundtrip.py --build 104 --bundle TableData.resourceFile \
    --client-build out/client --asset-url http://localhost:8000
python3 -m http.server 8000 -d out/client
```

Then add `out/client/beta-20100104/manifest.json` to the launcher as a custom game version and play against an OpenFusion server built for that protocol (104 here).

## meshes.py

```sh
python3 tests/demo/meshes.py --build 104 --bundle FutureNano.resourceFile
python3 tests/demo/meshes.py --build 1013 --bundle Nano_001.resourceFile --bundle CharacterSelection_wear_face.resourceFile
```

Prints how many meshes each archive has and how many of them are skinned. Writes, per serialized file, into `out/meshes/<build>/<archive>/<file>/`:

| File | Contents |
| --- | --- |
| `<path ID>_<name>.obj` | the mesh as `OBJMesh` exports it |
| `<path ID>_<name>.skin.json` | skinned meshes only: per vertex, its `[bone index, weight]` pairs (1 to 4, adding up to 1). The bone index points into the `m_Bones` list of the mesh's SkinnedMeshRenderer |

and `out/meshes/<build>.json` with the statistics the integration test checks (vertex and triangle counts, OBJ digest, how many vertices have how many weights).

## clips.py

```sh
python3 tests/demo/clips.py --build 104 --bundle FutureNano.resourceFile
python3 tests/demo/clips.py --build retro --bundle Retro_shared_part2.resourceFile
```

Prints how many clips each archive has and how many store their rotations compressed. Writes, per serialized file, `out/clips/<build>/<archive>/<file>/<path ID>_<name>.json` for each clip: its name, sample rate, length and events, then per curve its kind (`rotation`, `position`, `scale` or `float`), the transform path it moves (for a `float` curve, path and property), its keys, and its values at every frame from 0 to the clip's length. Rotations are sampled as `evaluate()` returns them, without normalizing. Also writes `out/clips/<build>.json` with the statistics the integration test checks (curve and key counts per kind, length, event names, a digest of the samples).

## terrain.py

```sh
python3 tests/demo/terrain.py --build 104                  # DongResources_04_02 (Orchid Bay)
python3 tests/demo/terrain.py --build 1013 --bundle DongResources_07_08.resourceFile
```

Without `--bundle` or `--all` it takes `DongResources_04_02.resourceFile`, the one terrain archive in the curated set. Prints, per terrain, its size, how many shifted vertices it has, and how Unity's level-of-detail data compares with a recomputation from the heights:

| Count | Meaning |
| --- | --- |
| `minmax_equal` / `minmax_one_step` | patch min/max heights equal to ours as float32, or one float32 step away (Unity's own rounding) |
| `minmax_other` | further apart (0 on every terrain checked) |
| `error_equal` | patch error equal to the bilinear one, to 0.1% |
| `error_larger` | larger: only on terrains with shifts, which the engine also counts |
| `error_smaller` | smaller (0 on every terrain checked) |

Writes `out/terrain/<build>/<archive>/<file>.png` and `.shifts.json` as `bin/dump_terrain.py` writes them (a 16-bit heightmap, north up, and the shifted vertices), and `out/terrain/<build>.json` with the statistics the integration test checks.

## gltf.py

```sh
python3 tests/demo/gltf.py --build 104 --bundle FutureNano.resourceFile
python3 tests/demo/gltf.py --build all --bundle World_shared_part1.resourceFile
python3 tests/demo/gltf.py --build 1013 --bundle Map_04_02.unity3d    # a whole scene
```

For a `Map_XX_YY.unity3d` archive it exports the scene instead: every active GameObject, the terrain (shifts applied, splats blended into a base map) and skinned, animated props; the archives the scene points into (its DongResources, World_shared parts, ...) are unpacked and resolved too.

Exports every prefab (a GameObject listed in the archive's AssetBundle container) with `unitypack.gltf`: its Transform tree, meshes and materials (textures embedded), skins and animation clips. Pointers into other archives (most textures) resolve against the build's curated archives only, unpacked as needed, so the result doesn't depend on what else is in the cache.

Writes `out/gltf/<build>/<archive>/<container path>.glb` and `out/gltf/<build>.json`: per prefab, its node, mesh, primitive, material, skin and animation counts, image sizes, a digest of every accessor, the exporter's warnings and, with the validator, its error and warning counts.

**The Khronos glTF validator** is optional. Install the npm package somewhere and point `UNITYPACKFF_GLTF_VALIDATOR` at that folder (Node.js must be on `PATH`):

```sh
mkdir -p ~/gltf-validator && npm install --prefix ~/gltf-validator gltf-validator
export UNITYPACKFF_GLTF_VALIDATOR=~/gltf-validator
```

`integration/test_gltf.py` then also requires every exported file to pass it with no errors and no warnings.

## mapdata.py

```sh
python3 tests/demo/mapdata.py --build 104                  # Map_04_02 and main.unity3d
python3 tests/demo/mapdata.py --build 1013 --bundle Map_07_08.unity3d
```

Without `--bundle` or `--all` it takes `Map_04_02.unity3d` and `main.unity3d`. Prints which kinds of map data each file has. Writes `out/mapdata/<build>/<file>.json` as `bin/dump_mapdata.py` writes them (the attribute grid as rows, z = 0 first, with a count per flag; fog, sky and light colours; music and ambient zones) and `out/mapdata/<build>.json` with the statistics the integration test checks.
