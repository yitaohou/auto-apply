#!/usr/bin/env python3
"""laya_eval.py — offline evaluation and fine-tuning helpers for the Laya decision layer.

    laya_eval.py report  [--data ~/job-search]            agreement / confidence report from data/decide_shadow.jsonl
    laya_eval.py rules   [--data ~/job-search]            re-predict every field_rules.csv row against the live server
    laya_eval.py export  [--data ~/job-search] [--holdout 0.1] [--out DIR] [--synth] [--real-as-eval]
                                                          clean laya-train rows (dedup, last label wins) → DIR/train.jsonl, eval.jsonl
                                                          --synth adds field_synth.py rows to train only; --real-as-eval puts every
                                                          real shadow row in eval (the honest test while real pages are few)
    laya_eval.py train   [--data ~/job-search] [--epochs 4] [--freeze-encoder] [--synth] [--real-as-eval]
                                                          export, then run .venv-laya/bin/laya-train → data/laya/checkpoint
                                                          (restart laya-serve afterwards: decide.py picks the checkpoint up as model "autoapply-ft")
    laya_eval.py eval    [--data ~/job-search] [--rows DIR/eval.jsonl] [--model NAME] [--max-len N]
                                                          re-predict eval rows against the live server (default model = the loaded checkpoint);
                                                          also scores classify.py's search_eval.jsonl: --model autoapply-search --max-len 4096

Runs under the system python (urllib only); `train` shells out to the venv. Switch-over criterion from the plan:
≥ 50 pages, Q1+Q2 agreement ≥ 90 %, and wrong answers at p ≥ 0.85 under 2 % — then settings.csv decide_model=laya.
"""
import argparse, collections, json, os, random, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import laya_client as laya  # noqa: E402

DATA_DEFAULT = Path.home() / "job-search"


def load_rows(data):
    p = Path(data).expanduser() / "data" / "decide_shadow.jsonl"
    if not p.exists():
        sys.exit(f"no shadow log yet: {p} (run decide.py with decide_model=shadow)")
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def bucket(p):
    if p is None:
        return "not asked"
    return "≥0.85" if p >= laya.ADOPT_CACHE else ("0.60–0.85" if p >= laya.ADOPT else "<0.60")


def report_rows(rows, title):
    print(f"\n== {title}: {len(rows)} rows")
    pages = {r.get("meta", {}).get("job_dir") for r in rows}
    print(f"pages: {len(pages)}")
    for q in sorted({r.get("meta", {}).get("q") for r in rows}, key=lambda x: (x not in ("q1", "q2"), x)):
        qs = [r for r in rows if r.get("meta", {}).get("q") == q]
        if not qs:
            continue
        for r in qs:  # expected is keyed by the question id (q1, q2_<class>)
            r["_exp"] = next(iter(r["expected"].values()))
        asked = [r for r in qs if r["meta"].get("laya_choice") is not None]
        agree = sum(1 for r in asked if r["meta"]["laya_choice"] == r["_exp"])
        print(f"\n{q}: {len(qs)} rows, {len(asked)} asked, agreement {agree}/{len(asked)}" + (f" = {agree / len(asked):.0%}" if asked else ""))
        bk = collections.defaultdict(lambda: [0, 0])
        for r in asked:
            b = bucket(r["meta"].get("laya_p"))
            bk[b][1] += 1
            bk[b][0] += r["meta"]["laya_choice"] == r["_exp"]
        for b in ("≥0.85", "0.60–0.85", "<0.60"):
            ok, n = bk[b]
            if n:
                print(f"  p {b:9} {ok}/{n} right" + (f" ({ok / n:.0%})" if n else ""))
        hi_wrong = sum(1 for r in asked if (r["meta"].get("laya_p") or 0) >= laya.ADOPT_CACHE and r["meta"]["laya_choice"] != r["_exp"])
        print(f"  high-confidence errors: {hi_wrong}/{len(asked)}" + (f" = {hi_wrong / len(asked):.1%}" if asked else "") + "  (switch-over needs < 2 %)")
        conf = collections.Counter((r["_exp"], r["meta"]["laya_choice"]) for r in asked if r["meta"]["laya_choice"] != r["_exp"])
        if conf:
            print("  top confusions (expected → laya): " + ", ".join(f"{e}→{l} ×{n}" for (e, l), n in conf.most_common(6)))
        per = collections.defaultdict(lambda: [0, 0])
        for r in asked:
            per[r["_exp"]][1] += 1
            per[r["_exp"]][0] += r["meta"]["laya_choice"] == r["_exp"]
        print("  per label: " + ", ".join(f"{k} {ok}/{n}" for k, (ok, n) in sorted(per.items())))


def cmd_report(a):
    rows = load_rows(a.data)
    report_rows(rows, "decide_shadow.jsonl")
    return 0


def cmd_rules(a):
    """field_rules.csv rows → states → live predictions; the cached source is the label."""
    import csv
    p = Path(a.data).expanduser() / "data" / "field_rules.csv"
    with open(p, newline="") as f:
        rules = [r for r in csv.DictReader(f) if r.get("label_norm")]
    if not rules:
        print(f"{p}: no rows"); return 0
    client = laya.LayaClient(a.data)
    if not client.ready:
        sys.exit(f"laya unavailable: {client.error}")
    fields, labels = [], []
    for i, r in enumerate(rules):
        lab = laya.label_from_decision({"source": r["source"], "label": r["label_norm"], "note": "", "by": r.get("by")})
        if not lab:
            continue
        fields.append({"id": f"r{i}", "label": r["label_norm"], "type": r["type"], "required": True, "control": {"kind": r["type"]}})
        labels.append((r, lab))
    preds = client.predict_fields("*", {}, fields)
    rows = []
    for f, (r, (cat, key)) in zip(fields, labels):
        d = {"id": f["id"], "label": r["label_norm"], "type": r["type"], "source": r["source"], "by": r.get("by"), "note": ""}
        rows += laya.shadow_rows(d, preds.get(f["id"]), {"ats": r["ats"], "label": r["label_norm"], "job_dir": "field_rules.csv"})
    report_rows(rows, f"field_rules.csv ({len(fields)} labelled rows of {len(rules)})")
    return 0


def export_rows(data, out_dir, holdout, synth=False, real_as_eval=False):
    rows = load_rows(data)
    # dedup on (state, question id): the latest label wins (a later merge may correct an earlier rule)
    uniq = {}
    for r in rows:
        q = next(iter(r["questions"]))
        uniq[(r["state"], q)] = {"state": r["state"], "questions": r["questions"], "expected": r["expected"]}
    clean = list(uniq.values())
    random.Random(7).shuffle(clean)
    n_eval = len(clean) if real_as_eval else int(len(clean) * holdout)
    train = clean[n_eval:]
    n_synth = 0
    if synth:
        import field_synth
        srows = field_synth.generate()
        eval_keys = {(r["state"], next(iter(r["questions"]))) for r in clean[:n_eval]}
        srows = [{"state": r["state"], "questions": r["questions"], "expected": r["expected"]} for r in srows if (r["state"], next(iter(r["questions"]))) not in eval_keys]
        n_synth = len(srows)
        train = train + srows
        random.Random(7).shuffle(train)
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    (out / "train.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in train))
    (out / "eval.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in clean[:n_eval]))
    print(f"export: {len(rows)} shadow rows → {len(clean)} unique → train {len(clean) - n_eval} real + {n_synth} synthetic, eval {n_eval} real in {out}")
    return out, len(train)


def cmd_export(a):
    export_rows(a.data, a.out or (Path(a.data).expanduser() / "data" / "laya" / "rows"), a.holdout, a.synth, a.real_as_eval)
    return 0


def cmd_train(a):
    data = Path(a.data).expanduser()
    out, n = export_rows(a.data, a.out or (data / "data" / "laya" / "rows"), a.holdout, a.synth, a.real_as_eval)
    if n < 20:
        sys.exit(f"only {n} training rows — collect more shadow pages first")
    exe = os.environ.get("LAYA_TRAIN") or (data / ".venv-laya" / "bin" / "laya-train")
    if not Path(exe).exists():
        sys.exit(f"{exe} missing — README: Local decision model")
    ckpt = data / "data" / "laya" / "checkpoint"
    cmd = [str(exe), "--data", str(out / "train.jsonl"), "--out", str(ckpt), "--base", laya.BASE_MODEL,
           "--epochs", str(a.epochs), "--micro-batch", "8", "--shuffle-options", "--device", "mps"]
    if (out / "eval.jsonl").stat().st_size:
        cmd += ["--eval", str(out / "eval.jsonl")]
    if a.freeze_encoder:
        cmd.append("--freeze-encoder")
    print("running: " + " ".join(cmd))
    rc = subprocess.call(cmd)
    if rc == 0:
        print(f"checkpoint → {ckpt}\nrestart the server to load it:  pkill -f laya-serve   (decide.py restarts it with LAYA_EXTRA_MODELS={{\"{laya.FT_MODEL}\": …}})")
    return rc


def cmd_eval(a):
    """eval rows → live predictions → the same report as `report`. Rows sharing a state are asked
    together (one request per batch of states, every question those states carry), so the same
    command covers typed-decisions rows (one question each) and classify.py's search_eval.jsonl
    (eight questions per JD; pass --model autoapply-search --max-len 4096)."""
    p = Path(a.rows or (Path(a.data).expanduser() / "data" / "laya" / "rows" / "eval.jsonl"))
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    client = laya.LayaClient(a.data)
    if not client.ready:
        sys.exit(f"laya unavailable: {client.error}")
    model = a.model or client.model
    by_state = collections.OrderedDict()
    for r in rows:
        by_state.setdefault(r["state"], []).append(r)
    states = list(by_state)
    out = []
    for i in range(0, len(states), a.batch):
        chunk = states[i:i + a.batch]
        qs = {}
        for st in chunk:
            for r in by_state[st]:
                qs.update(r["questions"])
        res = client.batch(chunk, qs, model=model, max_len=a.max_len, timeout=900)
        if len(res) != len(chunk):
            sys.exit(f"laya batch failed at {i}/{len(states)}: {client.error}")
        for st, ans in zip(chunk, res):
            for r in by_state[st]:
                qid = next(iter(r["questions"]))
                a_ = ans.get(qid) or {}
                out.append({"state": st, "questions": r["questions"], "expected": r["expected"],
                            "meta": {"q": "q1" if qid == "q1" else ("q2" if qid.startswith("q2") else qid), "job_dir": r.get("meta", {}).get("id", "eval"),
                                     "laya_choice": a_.get("choice"), "laya_p": a_.get("p")}})
        print(f"  {min(i + a.batch, len(states))}/{len(states)} states", file=sys.stderr)
    report_rows(out, f"{p.name} on model {model}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("report", cmd_report), ("rules", cmd_rules), ("export", cmd_export), ("train", cmd_train), ("eval", cmd_eval)):
        p = sub.add_parser(name)
        p.add_argument("--data", default=str(DATA_DEFAULT))
        if name in ("export", "train"):
            p.add_argument("--holdout", type=float, default=0.1)
            p.add_argument("--out", default=None)
            p.add_argument("--synth", action="store_true", help="add field_synth.py rows to the training set")
            p.add_argument("--real-as-eval", action="store_true", help="every real shadow row goes to eval")
        if name == "eval":
            p.add_argument("--rows", default=None)
            p.add_argument("--model", default=None)
            p.add_argument("--max-len", type=int, default=None)
            p.add_argument("--batch", type=int, default=16)
        if name == "train":
            p.add_argument("--epochs", type=int, default=4)
            p.add_argument("--freeze-encoder", action="store_true")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
