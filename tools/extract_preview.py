"""Rebuild the preview from the already-public source archive. Offline, no keys."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

SOURCE_SHA256 = '398ba6b94380d1539e79e02f065b1a27f85361bf740cefe9ac37d17c2616826f'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path,help='original public gradtrades gzip (see provenance URL)')
    p.add_argument('destination',type=Path,help='new gzip path; existing files are never overwritten')
    a=p.parse_args()
    digest=hashlib.sha256()
    with a.source.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): digest.update(chunk)
    if digest.hexdigest()!=SOURCE_SHA256: p.error('source checksum mismatch')
    grads=[]
    with gzip.open(a.source,'rb') as f:
        for line in f:
            row=json.loads(line)
            if row.get('type')=='grad': grads.append(row)
    mints={r['mint'] for r in sorted(grads,key=lambda r:(r['t'],r['mint']))[:5]}
    lines=[]
    with gzip.open(a.source,'rb') as f:
        for line in f:
            r=json.loads(line)
            if r.get('mint') in mints or r.get('type') in ('down','up'): lines.append(line)
    raw=b''.join(lines)
    with a.destination.open('xb') as f: f.write(gzip.compress(raw,mtime=0))
    print(json.dumps({'rows':len(lines),'mints':len(mints),'uncompressed_sha256':hashlib.sha256(raw).hexdigest()}))
    # gzip headers/compression can differ across Python versions; raw line bytes do not.


if __name__=='__main__': main()
