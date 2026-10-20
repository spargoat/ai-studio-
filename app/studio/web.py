"""Web app: JSON API (/api/*) + the React UI (ui/dist, harvested from AMN Production OS).
The original server-rendered review pages stay at /classic and /job/<id>."""
import json
import threading
from pathlib import Path
from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_file, url_for
from .runner import Runner

PAGE = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>AI Studio review</title>
<style>*{box-sizing:border-box}body{font:15px system-ui;margin:0;background:#111;color:#ddd}a{color:#8cf;text-decoration:none}
header{position:sticky;top:0;background:#181818;border-bottom:1px solid #333;padding:10px 12px;z-index:2}
header h2{margin:0;font-size:17px;display:inline}nav{margin-top:6px}nav a{margin-right:14px;font-weight:600}
nav a.on{color:#fff;border-bottom:2px solid #8cf}main{padding:10px 12px}
.b{display:inline-block;padding:2px 7px;border-radius:4px;font-size:12px;margin:1px 0}
.succeeded,.done,.approved,.pass{background:#1d4d2b}.failed,.rejected,.blocked_budget,.flag{background:#5a1f1f}
.awaiting_review,.pending,.partial,.running{background:#5a4a1a}.stale,.ns{background:#333;color:#999}
.mode{float:right;font-size:12px;padding:3px 8px;border-radius:4px;background:{{'#7a1f1f' if live else '#1f3a7a'}}}
.card{background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:10px;margin:10px 0}
.row{display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:6px}
.ver{flex:0 0 min(78vw,320px);scroll-snap-align:start;background:#141414;border:1px solid #2a2a2a;border-radius:8px;padding:8px}
.ver.cur{border-color:#4a4}.ver img,.ver video{width:100%;max-height:420px;object-fit:contain;border-radius:6px;background:#000}
.err{color:#f88;white-space:pre-wrap;font-size:12px}.sm{font-size:12px;color:#aaa}
form.rv{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}form.rv input{flex:1 1 100%;padding:10px;border-radius:6px;border:1px solid #444;background:#222;color:#eee;font-size:15px}
button{background:#333;color:#eee;border:1px solid #555;border-radius:6px;padding:10px 14px;font-size:15px;cursor:pointer}
button.ok{background:#1d4d2b}button.no{background:#5a1f1f}
.jobs a.j{display:block;padding:10px;border-bottom:1px solid #2a2a2a}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.grid .ver{flex:none}</style></head><body>
<header><span class=mode>{{'LIVE' if live else 'DRY RUN'}}</span><h2><a href="/classic">AI Studio</a></h2>
<nav><a href="/">App</a><a href="/classic" class="{{'on' if tab=='jobs' else ''}}">Jobs</a><a href="/classic/library" class="{{'on' if tab=='library' else ''}}">Library</a></nav>
<div class=sm>spend ${{'%.2f'|format(spend)}} of ${{caps[0]}} cap</div></header><main>
{% macro badges(sc) %}{% if sc %}<span class="b {{'pass' if sc.passed else 'flag'}}">{{'PASS' if sc.passed else 'FLAG'}}</span>
<span class=b style="background:#223">identity {{'%.2f'|format(sc.identity) if sc.identity is not none else 'n/a'}}</span>
{% if sc.visual %}<span class="b ns">{{sc.visual.width}}x{{sc.visual.height}} · sharp {{sc.visual.sharpness|int}}</span>{% endif %}
<span class="b ns" title="needs an LLM key">copy/brand/engagement: not scored</span>
{% for f in sc.flags %}<div class=err>{{f}}</div>{% endfor %}{% endif %}{% endmacro %}
{% macro media(p, poster=None) %}{% set u = '/file?p=' ~ p %}
{% if p.endswith('.png') or p.endswith('.jpg') %}<img src="{{u}}" loading=lazy>{% elif p.endswith('.mp4') %}<video src="{{u}}" controls playsinline preload=none poster="{{'/file?p=' ~ poster if poster else ''}}"></video>
{% elif p.endswith('.mp3') or p.endswith('.wav') %}<audio src="{{u}}" controls style="width:100%"></audio>{% else %}<a href="{{u}}">{{p.split('/')[-1]}}</a>{% endif %}{% endmacro %}
{% if tab == 'library' %}
<form method=get><input name=q value="{{q or ''}}" placeholder="search prompts, jobs, templates" style="width:100%;padding:10px;border-radius:6px;border:1px solid #444;background:#222;color:#eee"></form>
<div class=grid>{% for r in lib %}<div class=ver>{{media(r.path)}}<div class=sm>{% if r.promoted %}<span class="b approved">★ promoted</span>{% endif %}
{{r.persona}} · {{r.template}}<br><a href="/job/{{r.job_id}}">{{r.job_id}}</a>/{{r.stage}} v{{r.version}} [{{r.mode}}]</div>
{% if r.identity is not none %}<span class="b {{'pass' if r.passed else 'flag'}}">id {{'%.2f'|format(r.identity)}}</span>{% endif %}
{% if not r.promoted %}<form method=post action="/classic/library/{{r.id}}/promote"><button style="padding:6px 10px;font-size:13px">Promote</button></form>{% endif %}</div>
{% else %}<p>Library empty. Run <code>studio library backfill</code>.</p>{% endfor %}</div>
{% elif not job %}<div class=jobs>{% for j in jobs %}<a class=j href="/job/{{j.id}}"><b>{{j.id}}</b> <span class="b {{j.status}}">{{j.status}}</span>
<div class=sm>{{j.persona}} · {{j.template}} · {{j.mode}} · est ${{'%.2f'|format(j.est)}} · {{j.created_at[:16]}}</div></a>{% endfor %}</div>
{% else %}
<h3 style="margin:4px 0">{{job.id}} <span class="b {{job.status}}">{{job.status}}</span></h3>
<div class=sm>{{job.persona}} · {{job.template}}</div><p class=sm><i>{{job.brief.script}}</i></p>
<form method=post action="/job/{{job.id}}/resume"><button>Run / resume due stages</button></form>
{% for s in stages %}{% if s.runs %}<div class=card id="st-{{s.id}}" style="scroll-margin-top:90px"><b>{{s.id}}</b> {% if s.review %}<span class="b pending">review gate</span>{% endif %}
<span class=sm>{{s.provider}} · {{s.model}}{% if s.model != s.default_model %} <span class="b ns" title="template default">default {{s.default_model}}</span>{% endif %} · {{s.runs|length}} version{{'s' if s.runs|length > 1 else ''}}</span>
<div class=row>{% for r in s.runs|reverse %}<div class="ver {{'cur' if loop.first else ''}}">
<b>v{{r.version}}</b> <span class="b {{r.status}}">{{r.status}}</span> <span class=sm>{{r.model}}</span>
{% if r.review_status %}<span class="b {{r.review_status}}">{{r.review_status}}</span>{% endif %}{% if r.stale %}<span class="b stale">stale</span>{% endif %}
{% if r.output_path %}<div style="margin:6px 0">{{media(r.output_path, r.poster)}}</div>{% endif %}
{{badges(r.score)}}
{% if r.exports %}<div class=sm>exports: {% for a,p in r.exports.items() %}<a href="/file?p={{p}}">{{a}}</a> {% endfor %}</div>{% endif %}
<div class=sm>{{r.mode}} · ${{'%.3f'|format(r.actual_cost_usd or 0)}} · tries {{r.attempts}}</div>
{% if r.review_note %}<div class=sm>note: {{r.review_note}}</div>{% endif %}
{% if r.error and r.status!='succeeded' %}<div class=err>{{r.error[:300]}}</div>{% endif %}</div>{% endfor %}</div>
{% if s.runs and s.runs[-1].status == 'succeeded' %}<form class=rv method=post action="/job/{{job.id}}/review/{{s.id}}">
<input name=note placeholder="Reason (required to reject)"><button class=ok name=v value=approved>Approve v{{s.runs[-1].version}}</button>
<button class=no name=v value=rejected>Reject</button></form>{% endif %}
<form method=post action="/job/{{job.id}}/rerun/{{s.id}}" style="margin-top:6px"><button>Rerun {{s.id}}</button></form></div>{% endif %}{% endfor %}
<details><summary>Events</summary>{% for e in events %}<div class=sm>{{e.created_at[11:19]}} {{e.stage or ''}} {{e.level}}: {{e.message}}</div>{% endfor %}</details>
{% endif %}</main></body></html>"""


def create_app(cfg, live=False):
    app = Flask(__name__)

    @app.before_request
    def database_required():
        from .db import missing_on_vercel
        if request.path == "/healthz" or not missing_on_vercel():
            return None
        msg = "Database not configured"
        if request.path.startswith("/api/"):
            return jsonify({"error": msg}), 503
        return msg, 503

    from .auth import init as auth_init
    auth_init(app)                                   # shared-password sign-in (no-op unless configured/required)
    from .db import missing_on_vercel
    if missing_on_vercel():
        return app                                   # no Runner, so nothing opens /tmp SQLite or writes
    runner = Runner(cfg, live=live, log=lambda m: None)
    data_dir = Path(cfg["_data_dir"]).resolve()

    def bg(fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    def ctx(**kw):
        return dict(live=live, spend=runner.db.effective_spend(), caps=runner.caps(), **kw)

    @app.get("/classic")
    def index():
        jobs = runner.db.jobs()
        for j in jobs:
            c = runner.db.job_costs(j["id"]); j["est"], j["act"] = c["est"], c["act"]
            j["est"] = sum((r["est_cost_usd"] or 0) for r in runner.db.runs(j["id"]))
        return render_template_string(PAGE, job=None, tab="jobs", jobs=jobs, **ctx())

    @app.get("/job/<jid>")
    def job(jid):
        j = runner.db.job(jid) or abort(404)
        stages = []
        for d in runner.stage_defs(j):
            sc = cfg["stages"].get(d["id"], {"provider": "local", "model": "brief-resolver"})
            runs = runner.db.runs(jid, d["id"])
            for r in runs:
                r["att"] = runner.db.attempts(r["id"])
                r["score"] = runner.db.score(r["id"])
                r["exports"] = json.loads(r["meta_json"] or "{}").get("exports")
                r["poster"] = json.loads(r["meta_json"] or "{}").get("poster")
                r["stale"] = r["status"] == "succeeded" and runner.is_stale(jid, r)
            last = runs[-1] if runs else None             # what this job actually ran, not the template default
            stages.append({"id": d["id"], "review": d.get("review"),
                           "provider": (last or {}).get("provider") or sc["provider"],
                           "model": (last or {}).get("model") or sc["model"],
                           "default_model": sc["model"], "runs": runs})
        return render_template_string(PAGE, job=j, tab="jobs", stages=stages, events=runner.db.events(jid, 40), **ctx())

    @app.post("/job/<jid>/review/<stage>")
    def review(jid, stage):
        v, note = request.form["v"], request.form.get("note", "").strip()
        if v == "rejected" and not note:
            abort(400, "a reason is required to reject")
        runner.review(jid, stage, v, note)
        return redirect(url_for("job", jid=jid))

    @app.post("/job/<jid>/rerun/<stage>")
    def rerun(jid, stage):
        bg(runner.rerun, jid, stage)
        return redirect(url_for("job", jid=jid))

    @app.post("/job/<jid>/resume")
    def resume(jid):
        bg(runner.run_job, jid)
        return redirect(url_for("job", jid=jid))

    @app.get("/classic/library")
    def lib():
        from . import library
        q = request.args.get("q")
        return render_template_string(PAGE, job=None, tab="library", q=q,
                                      lib=library.rows(runner.db, q, limit=200), **ctx())

    @app.post("/classic/library/<int:lid>/promote")
    def lib_promote(lid):
        from . import library
        library.promote(runner.db, str(lid))
        return redirect(url_for("lib"))

    @app.get("/file")
    def file():
        from .storage import allowed_redirect
        raw = request.args["p"]
        if allowed_redirect(raw):
            return redirect(raw)
        p = Path(raw).resolve()
        roots = [data_dir, Path("/tmp")]
        if not any(root == p or root in p.parents for root in roots):
            abort(403)
        if not p.exists():
            abort(404)
        return send_file(p)

    from .api import register
    register(app, cfg, runner, live, bg)
    return app
