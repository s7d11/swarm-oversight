"""Reproduce the kveritas-c -> kveritas-go seal interop findings.

  .venv/bin/python tools/kveritas_interop/repro.py /path/to/test-key.pem OUTDIR
Then: kveritas verify OUTDIR/destroy_first.pdf --offline   (Go: 'Runs: 0' — bug 1)
      kveritas verify OUTDIR/deferred.pdf --offline        (Go: TAMPERED data hash mismatch — bug 2)
      python3 -I tools/kveritas_interop/go_canon_emul.py OUTDIR/deferred.pdf
"""
import ctypes
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import kv  # noqa: E402

key, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)

def session(name):
    d = os.path.join(out, name)
    shutil.rmtree(d, ignore_errors=True)
    s = kv.Session(storage_dir=d, project_dir=os.getcwd())
    s.run_begin("smoke"); s.phase("episodes")
    s.metric("leaked", 1, "bool", step="s0"); s.claim("leak_rate", 0.5, "episodes")
    return s

# Bug 1: destroy the run before sealing (what the README C quickstart pattern does)
s = session("destroy_first")
run = s._run
kv._run_end(run, 0); kv._run_destroy(run); s._run = None
s.save(); s.seal(key, os.path.join(out, "destroy_first.pdf")); s.close()

# Bug 2: keep the run alive until after sealing (kv.py's workaround)
s = session("deferred")
s.run_end(0); s.save(); s.seal(key, os.path.join(out, "deferred.pdf")); s.close()
print("wrote", os.path.join(out, "destroy_first.pdf"), "and", os.path.join(out, "deferred.pdf"))
