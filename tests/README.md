# Tests

```sh
pip install -e '.[test]'
pytest                                   # unit + harness tests: made-up data, offline, under a second
pytest tests/unit                        # only the tests of UnityPackFF itself
```

## Layout

| Path | What |
| --- | --- |
| `unit/` | **UnityPackFF itself**, on made-up data, one file per area of the library named after it (`test_asset_read.py`, `test_texture.py`, ...). No network, no client files |
| `harness/` | Tests of our own helpers below, so they can be trusted as a yardstick |
| `synth.py` | Builders for made-up type trees, serialized files and archives (plain `struct.pack`) |
| `digest.py` | Stable digests of what unitypack reads |

`synth.py` shares no code with unitypack: unit tests feed the library hand-written bytes and compare its output with hand-written bytes, so the library's reader and writer can't agree on a bug and still pass.

## Known bugs (strict xfails)

A known library bug is a test marked `xfail(strict=True)`: it is expected to fail, and the run fails if it passes, so fixing a bug forces removing its marker.

| Test | Bug |
| --- | --- |
| `unit/test_assetbundle.py::test_two_files` | `unitypack.load()` reads only the first file of an archive |
| `unit/test_asset_save.py::test_save_is_byte_identical` | `Asset.save()` starts objects on 4 bytes, Unity on 8 |
| `unit/test_asset_save.py::test_save_keeps_trailing_padding` | `BinaryWriter.align()` seeks instead of writing, so padding after an object's last field is lost |
| `unit/test_asset_save.py::test_source_still_reads_after_save`, `test_save_twice` | `Asset.save()` overwrites each object's offset and size, so the asset can't be read or saved again afterwards |
| `unit/test_texture.py::test_decode_rgb565`, `test_decode_argb4444`; `unit/test_modding.py::test_rgb16_round_trip` | Red and blue swapped when decoding RGB565 and ARGB4444 (the importer writes them correctly) |
| `unit/test_export.py::test_compressed_normals_are_unit_length` | Compressed normals: z = 1 − x² − y² without the square root, so every exported normal is wrong |

Not covered yet: cross-archive pointers (`UnityEnvironment`), `bin/` scripts, `AudioClip` export.
