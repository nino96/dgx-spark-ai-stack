#!/usr/bin/env python3
"""
evals/lm-eval/compare.py -- print task/metric deltas between archived lm-eval runs.

stdlib only. Reads JSON result files written by run.sh under
~/ai-data/state/evals/<model>/<UTC date-time>.json (filenames sort chronologically because they
use a zero-padded UTC timestamp), and prints deltas between consecutive runs, newest last.

Usage:
    compare.py <model> [--last N] [--state-root PATH]

    <model>       model name as used in run.sh / the served_model_name (matches the
                  ~/ai-data/state/evals/<model>/ directory name)
    --last N      how many of the most recent archived runs to include in the comparison
                  (default: 2, i.e. just newest-vs-previous)
    --state-root  override the evals state root (default: ~/ai-data/state/evals)
"""

import argparse
import json
import os
import sys


def load_runs(model_dir, last_n):
    if not os.path.isdir(model_dir):
        print("no archived runs found under: %s" % model_dir, file=sys.stderr)
        return []

    files = sorted(f for f in os.listdir(model_dir) if f.endswith(".json"))
    if last_n is not None:
        files = files[-last_n:]

    runs = []
    for fname in files:
        path = os.path.join(model_dir, fname)
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            print("skipping unreadable result file %s: %s" % (path, e), file=sys.stderr)
            continue
        runs.append((fname, data))
    return runs


def flatten_metrics(run_json):
    """lm-eval result files look like {"results": {"<task>": {"<metric,filter>": value, ...},
    ...}, ...}. Flatten to {(task, metric): value} for numeric metrics only."""
    out = {}
    results = run_json.get("results", {})
    for task, metrics in results.items():
        if not isinstance(metrics, dict):
            continue
        for metric_key, value in metrics.items():
            if metric_key == "alias":
                continue
            if isinstance(value, (int, float)):
                out[(task, metric_key)] = value
    return out


def marker(delta):
    if delta > 1e-9:
        return "▲"  # ▲
    if delta < -1e-9:
        return "▼"  # ▼
    return "="


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare metric deltas across archived lm-eval runs for one model."
    )
    parser.add_argument("model", help="model name (matches the evals state subdirectory)")
    parser.add_argument(
        "--last", type=int, default=2, help="number of most recent runs to compare (default: 2)"
    )
    parser.add_argument(
        "--state-root",
        default=os.path.expanduser("~/ai-data/state/evals"),
        help="root directory containing <model>/<timestamp>.json archives",
    )
    args = parser.parse_args(argv)

    if args.last < 2:
        print("--last must be >= 2 (need at least two runs to diff)", file=sys.stderr)
        return 2

    model_dir = os.path.join(args.state_root, args.model)
    runs = load_runs(model_dir, args.last)

    if len(runs) < 2:
        print(
            "need at least 2 archived runs for %s to compare, found %d in %s"
            % (args.model, len(runs), model_dir)
        )
        return 1

    print("model: %s (%d runs considered, %s)" % (args.model, len(runs), model_dir))

    for i in range(1, len(runs)):
        prev_name, prev_json = runs[i - 1]
        cur_name, cur_json = runs[i]
        prev_metrics = flatten_metrics(prev_json)
        cur_metrics = flatten_metrics(cur_json)

        print("\n%s -> %s" % (prev_name, cur_name))

        keys = sorted(set(prev_metrics) | set(cur_metrics))
        if not keys:
            print("  (no comparable numeric metrics found)")
            continue

        for task, metric in keys:
            old_v = prev_metrics.get((task, metric))
            new_v = cur_metrics.get((task, metric))
            if old_v is None:
                print("  [new]  %-24s %-20s -> %.4f" % (task, metric, new_v))
                continue
            if new_v is None:
                print("  [gone] %-24s %-20s    %.4f -> (removed)" % (task, metric, old_v))
                continue
            delta = new_v - old_v
            print(
                "  %s %-24s %-20s %.4f -> %.4f (%+.4f)"
                % (marker(delta), task, metric, old_v, new_v, delta)
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
