"""Report and poster figures from a finished campaign's analysis files.

  python tools/figures.py <run summary dir> [--out docs/figures]

<run summary dir> is a folder with the run's small files, e.g. fetched from the dataset:
  .venv/bin/hf download s7d11/swarm-kveritas-logs --repo-type dataset \
      --include "runs/swarmKV1/*/*.md" "runs/swarmKV1/*/episodes.jsonl" --local-dir /tmp/kv && \
  python tools/figures.py /tmp/kv/runs/swarmKV1

Figures (PNG at 300 dpi, SVG and PDF; light print style; colours: dataviz reference slots 1-3, validated all-pairs):
  fig_monitor_auroc   AUROC [95% CI] of 9 monitors on leaked attacks vs benign + near-miss, for three views:
                      the agents' log as left, the log after a stealthy cover-up, the tamper-evident record + audit
  fig_splitting       leak rate [Wilson 95% CI]: single request vs split across workers; re-routing on vs off
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"   # categorical slots 1-3 (validated all-pairs, light)
PAIR = (S1, S2)                                # two-series charts
# --palette poster (docs/poster, sage-and-purple theme): the record is purple; validated all-pairs (light), with the
# yellow below 3:1 contrast relieved by the legend and value labels
POSTER = {"S1": "#eda100", "S2": "#e34948", "S3": "#6250d6", "PAIR": ("#6250d6", "#e34948")}
SUFFIX = ""
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE,
                     "svg.fonttype": "none"})


def auroc_rows(md: str) -> list[dict]:
    rows = []
    for line in open(md):
        m = re.match(r"\|\s*(leaked|attempted)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*\[([\d.]+),\s*([\d.]+)\]", line)
        if m:
            rows.append({"pos": m[1], "monitor": m[2], "view": m[3], "auroc": float(m[6]), "lo": float(m[7]), "hi": float(m[8])})
    return rows


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return 0, 0, 0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0, c - h), min(1, c + h)


def save(fig, out: str, name: str) -> None:
    for ext in ("png", "svg", "pdf"):
        fig.savefig(os.path.join(out, f"{name}{SUFFIX}.{ext}"), dpi=300, bbox_inches="tight")
    plt.close(fig)


def fig_monitor_auroc(run: str, out: str) -> None:
    rows = [r for r in auroc_rows(os.path.join(run, "t1", "analysis_monitor_scores_sweep.md")) if r["pos"] == "leaked"]
    views = [("naive", "agents' log, as left", S1), ("naive_scripted", "log after a stealthy cover-up", S2),
             ("chain_audit_scripted", "tamper-evident record + audit", S3)]
    get = {(r["monitor"], r["view"]): r for r in rows}
    mons = sorted({r["monitor"] for r in rows}, key=lambda m: get.get((m, "chain_audit_scripted"), {"auroc": 0})["auroc"])
    fig, ax = plt.subplots(figsize=(6.4, 3.9))
    ax.axvline(0.5, color=GRID, lw=1, ls=(0, (4, 3)), zorder=0)
    ax.text(0.5, len(mons) - 0.35, "chance", color=INK2, fontsize=7.5, ha="center", va="bottom")
    for k, (v, label, col) in enumerate(views):
        off = (k - 1) * 0.24
        for i, m in enumerate(mons):
            r = get.get((m, v))
            if not r:
                continue
            ax.plot([r["lo"], r["hi"]], [i + off] * 2, color=col, lw=2, solid_capstyle="round", zorder=2)
            ax.scatter([r["auroc"]], [i + off], s=42, color=col, edgecolor=SURFACE, linewidth=1.6, zorder=3,
                       label=label if i == 0 else None)
    ax.set_yticks(range(len(mons)))
    ax.set_yticklabels([m.replace("local:", "") + (" (local)" if m.startswith("local:") else "") for m in mons])
    ax.set_xlim(0, 1.02)
    ax.set_xlabel("AUROC, leaked attacks vs benign + near-miss episodes  [95% bootstrap CI]")
    ax.grid(axis="x", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=8, handletextpad=0.3, columnspacing=1.2)
    save(fig, out, "fig_monitor_auroc")


def _eps(run: str, group: str) -> list[dict]:
    p = os.path.join(run, group, "episodes.jsonl")
    return [e for e in map(json.loads, open(p)) if e.get("error") is None] if os.path.exists(p) else []


def fig_splitting(run: str, out: str) -> dict:
    t1, rr = _eps(run, "t1"), _eps(run, "t2_reroute")
    hosted = lambda e: not e["models"]["worker"].startswith("local:")
    lf = lambda e: e["models"]["worker"] == "local:lfm3-1.7b"
    k = lambda es: (sum(e["leaked"] for e in es), len(es))
    a = {"Hosted models (7)": (k([e for e in t1 if hosted(e) and e["mode"] == "single"]),
                               k([e for e in t1 if hosted(e) and e["mode"] == "attack"])),
         "LFM3-1.7B (local)": (k([e for e in t1 if lf(e) and e["mode"] == "single"]),
                               k([e for e in t1 if lf(e) and e["mode"] == "attack"]))}
    b = {w: (k([e for e in rr if e["models"]["worker"] == m and not e.get("no_reroute")]),
             k([e for e in rr if e["models"]["worker"] == m and e.get("no_reroute")]))
         for w, m in (("Qwen 3.8 27B", "qwen3.8-27b"), ("LFM3-1.7B (local)", "local:lfm3-1.7b"))}
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.5), sharex=True)
    for ax, data, labels, title in ((axes[0], a, ("asked directly (single)", "split across 4 workers"), "a. Splitting the task"),
                                    (axes[1], b, ("re-routing refused work", "no re-routing"), "b. Re-routing refused steps")):
        names = list(data)
        for j, (lab, col) in enumerate(zip(labels, PAIR)):
            for i, n in enumerate(names):
                kk, nn = data[n][j]
                p, lo, hi = wilson(kk, nn)
                y = i + (j - 0.5) * 0.32
                ax.plot([lo, hi], [y, y], color=col, lw=2, solid_capstyle="round")
                ax.scatter([p], [y], s=42, color=col, edgecolor=SURFACE, linewidth=1.6, zorder=3, label=lab if i == 0 else None)
                ax.text(hi + 0.03, y, f"{kk}/{nn}", va="center", fontsize=7.5, color=INK2)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names)
        ax.set_xlim(0, 1.18)
        ax.set_xticks([0, 0.5, 1])
        ax.set_xticklabels(["0%", "50%", "100%"])
        ax.set_ylim(-0.6, len(names) - 0.4)
        ax.set_title(title, loc="left", fontsize=9, color=INK)
        ax.grid(axis="x", color=GRID, lw=0.6)
        ax.set_axisbelow(True)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.legend(loc="lower left", bbox_to_anchor=(-0.02, 0.98), ncol=1, frameon=False, fontsize=7.5, handletextpad=0.3)
        ax.set_xlabel("leak rate [Wilson 95% CI]", fontsize=8)
        ax.set_title(title, loc="left", fontsize=9, color=INK, pad=30)
    fig.tight_layout(w_pad=3)
    save(fig, out, "fig_splitting")
    return {"splitting": {n: data for n, data in a.items()}, "rerouting": b}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--palette", choices=["paper", "poster"], default="paper", help="poster: sage-and-purple theme, files *_poster.*")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "figures"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.palette == "poster":
        global S1, S2, S3, PAIR, SUFFIX
        S1, S2, S3, PAIR, SUFFIX = POSTER["S1"], POSTER["S2"], POSTER["S3"], POSTER["PAIR"], "_poster"
    fig_monitor_auroc(a.run, a.out)
    print(json.dumps(fig_splitting(a.run, a.out)))
    print(f"figures -> {a.out}")


if __name__ == "__main__":
    main()
