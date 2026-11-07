"""Emulate kveritas-go canonicalSessionHash over a C-sealed PDF and diff against C's canonical_json."""
import hashlib, json, re, sys

pdf = open(sys.argv[1], "rb").read().decode("latin-1")
blk = json.loads(re.search(r"%%KVERITAS_SEAL_BEGIN%%\s*(\{.*?\})\s*%%KVERITAS_SEAL_END%%", pdf, re.S).group(1))
seal, sess, runs = blk["seal"], blk["session"], blk["runs"]
c_doc = json.loads(seal["canonical_json"])

def go_time(s):  # time.Parse(RFC3339Nano) -> Format(RFC3339Nano): trailing zeros in fraction trimmed
    m = re.match(r"(.*?)(\.(\d+))?Z$", s)
    frac = (m.group(3) or "").rstrip("0")
    return m.group(1) + ("." + frac if frac else "") + "Z"

def keep(d, fields):  # fields: name -> omitempty?
    out = {}
    for k, omit in fields.items():
        v = d.get(k)
        if v is None:
            v = {"s": ""}.get("s") if False else v
        if omit and (v in (None, "", 0, 0.0, [], {}, False)):
            continue
        out[k] = v if v is not None else _zero(k)
    return out

def _zero(k):
    return 0

GPU = {"id": 0, "gpu_util_pct": 0, "gpu_mem_used_mb": 0, "gpu_temp_c": 0, "gpu_power_w": 0}
CNT = {"cpu_time_sec": 0, "mem_used_gb": 0, "gpu_util_pct": 1, "gpu_mem_used_mb": 1, "gpu_temp_c": 1, "gpu_power_w": 1,
       "cpu_temp_c": 1, "disk_read_mb": 0, "disk_write_mb": 0, "ctx_switches": 1, "minor_faults": 1, "threads": 1,
       "cpu_freq_mhz": 1, "gpu_telemetry": 1}
def counters(c):
    o = keep(c, CNT)
    if "gpu_telemetry" in o:
        o["gpu_telemetry"] = [keep(g, GPU) for g in o["gpu_telemetry"]]
    return o
METRIC = {"name": 0, "value": 0, "step": 1, "unit": 1, "line": 0, "source": 0, "seq": 1}
CLAIM = {"metric": 0, "value": 0, "phase": 1, "line": 0, "seq": 1}
HWINFO = {"hostname": 1, "machine_id": 1, "os": 0, "arch": 0, "cpu_cores": 0, "cpu_model": 1, "mem_gb": 0, "gpu_info": 1, "gpu_count": 1, "gpu_names": 1}

def run_payload(r):
    p = {k: r.get(k) for k in ["id", "index", "command", "duration_sec", "exit_code", "pre_hashes", "post_hashes",
                               "modified_files", "stdout_hash", "stderr_hash", "stdout_lines", "env_digest"]}
    p["start_at"], p["end_at"] = go_time(r["start_at"]), go_time(r["end_at"])
    p["metrics"] = [keep(m, METRIC) for m in (r.get("metrics") or [])] if r.get("metrics") is not None else None
    p["hardware"] = keep(r.get("hardware", {}), HWINFO)
    if r.get("phases"):
        p["phases"] = [{**keep(ph, {"name": 0, "line": 0, "seq": 1}), "timestamp": go_time(ph["timestamp"]),
                        "counters": counters(ph.get("counters", {}))} for ph in r["phases"]]
    if r.get("claims"):
        p["claims"] = [keep(c, CLAIM) for c in r["claims"]]
    for k in ("metric_hash", "duration_fmt", "source_code_hash", "prov_bundle_hash", "launch", "branch"):
        if r.get(k):
            p[k] = r[k]
    if r.get("hardware_samples"):
        p["hardware_samples"] = [{"timestamp": go_time(s["timestamp"]), "counters": counters(s.get("counters", {}))}
                                 for s in r["hardware_samples"]]
    return p

go_doc = {"session_id": sess["id"], "init_at": go_time(sess["init_at"]), "machine_id": sess["machine_id"],
          "server_url": sess.get("server_url", ""), "runs": [run_payload(r) for r in runs]}
anchors = [{"run": i, "invocation": r.get("invocation") or 0, "run_digest": r["run_digest"]}
           for i, r in enumerate(runs) if r.get("run_digest")]
if anchors: go_doc["run_anchors"] = anchors
if "hmca" in c_doc: go_doc["hmca"] = c_doc["hmca"]
if "seal_claims" in c_doc: go_doc["seal_claims"] = c_doc["seal_claims"]

def gofmt(v):
    if isinstance(v, bool): return "true" if v else "false"
    if v is None: return "null"
    if isinstance(v, (int, float)):
        f = float(v)
        if f.is_integer() and abs(f) < 1e21: return str(int(f))
        return repr(f)
    if isinstance(v, str): return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list): return "[" + ",".join(gofmt(x) for x in v) + "]"
    return "{" + ",".join(json.dumps(k) + ":" + gofmt(v[k]) for k in sorted(v)) + "}"

go_bytes = gofmt(go_doc)
print("C stored data_hash :", seal["data_hash"])
print("emulated Go hash   :", hashlib.sha256(go_bytes.encode()).hexdigest())
print("PDF-embedded run keys == C signed run keys:", [sorted(r) for r in runs] == [sorted(r) for r in c_doc["runs"]])

def diff(a, b, path="$"):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a: print(f"  only in Go : {path}.{k} = {json.dumps(b[k])[:80]}")
            elif k not in b: print(f"  only in C  : {path}.{k} = {json.dumps(a[k])[:80]}")
            else: diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b): print(f"  length differs at {path}: C={len(a)} Go={len(b)}")
        for i, (x, y) in enumerate(zip(a, b)): diff(x, y, f"{path}[{i}]")
    elif gofmt(a) != gofmt(b):
        print(f"  value differs {path}: C={json.dumps(a)} Go={json.dumps(b)}")
print("differences (C signed vs Go rebuild):")
diff(c_doc, json.loads(go_bytes))
