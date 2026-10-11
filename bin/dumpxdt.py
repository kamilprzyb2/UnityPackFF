#!/usr/bin/env python3

import json
import sys
from unitypack.asset import Asset

# usage: dumpxdt.py [TableData file] [output]
INPUT = sys.argv[1] if len(sys.argv) > 1 else 'CustomAssetBundle-1dca92eecee4742d985b799d8226666d'
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else 'xdt.json'
XDTPATH = 'xdtdatas.asset'

def main(tabledata):
    xdtdata = tabledata.get_object_by_path(XDTPATH).read()

    out = {}
    for tname, table in xdtdata.items():
        out[tname] = {}
        try:
            for dname, data in table.items():
                out[tname][dname] = data
        except:
            out[tname] = '<err>'

    with open(OUTPUT, 'w') as f:
        json.dump(out, f, indent=4)


with open(INPUT, 'rb') as f:
    tabledata = Asset.from_file(f)
    main(tabledata)
