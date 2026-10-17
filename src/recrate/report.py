"""One static report page per research object: what was checked, what matched, what differs and why, and every run.

``recrate report <agent>`` writes ``<agent>/report.html``: a single self-contained file (figures embedded) that a
reviewer or editor can open without installing anything. Everything on it is read from the agent's manifest and
its run crates, so the page, the export bundle and a deposit cannot disagree.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Any

from .agent import PaperAgent
from .model import STEPS, assumptions
from .record import find_runs

esc = html.escape
FAVICON = (Path(__file__).parent / "favicon.svg").read_text(encoding="utf-8") if (Path(__file__).parent / "favicon.svg").exists() else ""
# The ReCRATE mark (brackets around a tick), coloured by the page's own tokens so it follows light and dark mode
MARK = ('<svg viewBox="0 0 100 100" aria-hidden="true" fill="none" stroke-linecap="round" stroke-linejoin="round">'
        '<path class="ink" stroke-width="8" d="M34 14 C24 14 25 28 25 37 C25 45 20 50 13 50 C20 50 25 55 25 63 C25 72 '
        '24 86 34 86"/><path class="ink" stroke-width="8" d="M66 14 C76 14 75 28 75 37 C75 45 80 50 87 50 C80 50 75 55 '
        '75 63 C75 72 76 86 66 86"/><g opacity="0.4"><path class="ink" stroke-width="6" d="M50 33 A17 17 0 1 1 41.5 '
        '35.28"/><path class="ink inkfill" stroke-width="2" d="M43.5 30.28 L36.5 35.28 L43.5 40.28 Z" '
        'transform="rotate(150 41.5 35.28)"/></g><path class="tick" stroke-width="6" d="M42.5 50.5 L48 56 L58 44.5"/>'
        '</svg>')
BADGE = {"passed": "ok", "partial": "partial", "failed": "bad", "not checked": "none"}


def _table(head: list[str], rows: list[list[Any]], marks: list[str] | None = None) -> str:
    body = "".join(f"<tr class='{(marks or [''] * len(rows))[i]}'>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                   for i, r in enumerate(rows))
    return f"<div class='scroll'><table><thead><tr>{''.join(f'<th>{esc(h)}</th>' for h in head)}</tr></thead>" \
           f"<tbody>{body}</tbody></table></div>"


def _img(path: Path) -> str:
    data = base64.b64encode(path.read_bytes()).decode()
    return f"<img alt='{esc(path.stem)}' src='data:image/png;base64,{data}'>"


def _method(agent: PaperAgent, m) -> str:
    from .figures import comparison_from_run
    from .followup import followups_for

    parts = [f"<h3>{esc(m.name)} <span class='tag'>{esc(m.language)}</span>"
             + (f" <span class='tag'>{esc(m.readiness)}</span>" if m.readiness else "") + "</h3>",
             f"<p class='muted'>{esc(m.description[:400])}</p>"]
    if m.ladder:
        parts.append(_table(["Step", "Status", "Why"], [
            [esc(label), f"<span class='b {BADGE.get(m.ladder[k]['status'], 'none')}'>{esc(m.ladder[k]['status'])}</span>",
             esc(m.ladder[k]["reason"])] for k, label in STEPS if k in m.ladder]))
    for g in m.gaps:
        parts.append(f"<p class='muted'>Gap: {esc(g)}</p>")
    run = Path(m.run_crate) if m.run_crate else None
    comp = comparison_from_run(run) if run and run.exists() else None
    if comp and comp.measures:
        ms = sorted(comp.measures, key=lambda x: x.within)
        parts.append(f"<h4>Same numbers: {comp.count} within tolerance</h4>")
        parts.append(_table(["Value", "Published", "Reproduced", "Difference", "Tolerance (source)", "Within"], [
            [esc(x.measure), f"{x.reported:g}", f"{x.reproduced:g}",
             f"±{x.rel_diff:.3g}" if x.absolute else f"{x.rel_diff:.1%}", esc(x.tolerance_label),
             "yes" if x.within else "<b>no</b>"] for x in ms], ["" if x.within else "miss" for x in ms]))
    fig = run / "outputs" / "figures" / "published_vs_reproduced.png" if run else None
    if fig and fig.exists():
        parts.append(f"<figure>{_img(fig)}<figcaption>Plotted from this run's outputs.</figcaption></figure>")
    done = followups_for(agent.runs_dir, agent.spec(m.id)) if agent.runs_dir.exists() else []
    if done:
        parts.append("<h4>Follow-up checks</h4><p class='muted'>Each is its own recorded run; the result above "
                     "is never replaced.</p><ul>" + "".join(f"<li><b>{esc(d['followup'] or '')}</b>: "
                                                           f"{esc(d['headline'] or '')} <span class='muted'>({esc(d['run'])}, by {esc(d['by'] or '')})</span></li>" for d in done) + "</ul>")
    if run:
        parts.append(f"<p class='muted'>Run crate: <code>{esc(str(run.relative_to(agent.path)) if run.is_relative_to(agent.path) else run.name)}</code></p>")
    return "<section class='card'>" + "".join(parts) + "</section>"


def build_report(agent_dir: str | Path, out: str | Path | None = None) -> Path:
    agent = PaperAgent.load(agent_dir)
    card = agent.card()
    out = Path(out or agent.path / "report.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    sections = [f"<p class='verdict'>{esc(card['verdict'])}</p>"]
    if card.get("draft"):
        sections.append("<p class='draft'>Draft crate: drafted from the paper, not yet confirmed by a named person.</p>")
    sections += [_method(agent, m) for m in agent.methods]
    if agent.has_methods:
        spec = agent.spec()
        said = assumptions(spec, agent.environment)
        if said:
            sections.append("<section class='card'><h3>What the paper doesn't say</h3><ul>"
                            + "".join(f"<li>{esc(a)}</li>" for a in said) + "</ul></section>")
    env = agent.environment
    if env and env.software:
        sections.append("<section class='card'><h3>Software as of publication"
                        + (f" ({esc(env.published)})" if env.published else "") + "</h3>"
                        + _table(["Software", "Version", "How the version was determined"],
                                 [[esc(s.name), esc(s.version or "unknown"), esc(s.note)] for s in env.software])
                        + "</section>")
    if agent.has_methods:
        runs = sorted(find_runs(agent.spec(), agent.runs_dir), key=lambda r: r["when"] or "", reverse=True)
        sections.append("<section class='card'><h3>Every run</h3><p class='muted'>The record is append-only: "
                        "differing and failed runs stay.</p>" + _table(
                            ["When", "Kind", "Method", "Who", "Result", "Review"],
                            [[esc((r["when"] or "")[:16].replace("T", " ")), esc(r["mode"] or ""), esc(r["method"] or ""),
                              esc(r["by"] or ""), esc((r.get("purpose") or "") + " " + (r["headline"] or "")[:300]),
                              esc(r.get("conclusion") or r.get("approved") or "")] for r in runs]) + "</section>")
    sections.append(f"<section class='card'><h3>Use it</h3><p>Ask this research object questions and run its "
                    f"methods from Claude or any MCP client:</p><pre>recrate serve {esc(str(agent.path))}</pre></section>")
    title = card.get("title") or agent.name
    doi = card.get("doi") or ""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ReCRATE report</title>
<link rel="icon" href="data:image/svg+xml;base64,{base64.b64encode(FAVICON.encode()).decode()}">
<style>
:root{{--bg:#F4F4F0;--card:#fff;--fg:#14181F;--muted:#5f6967;--line:#e2dfd6;--ok:#1f7a4d;--partial:#a2580b;--bad:#b3261e;--miss:#fbe9e2;--accent:#2A78D6}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151716;--card:#1e2120;--fg:#F4F4F0;--muted:#a3aaa7;--line:#323735;--ok:#6fcf97;--partial:#f2b46d;--bad:#ff8a80;--miss:#3a2620;--accent:#7FB0F0}}}}
:root[data-theme="dark"]{{--bg:#151716;--card:#1e2120;--fg:#F4F4F0;--muted:#a3aaa7;--line:#323735;--ok:#6fcf97;--partial:#f2b46d;--bad:#ff8a80;--miss:#3a2620;--accent:#7FB0F0}}
body{{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,sans-serif;margin:0;padding:24px 16px}}
main{{max-width:960px;margin:0 auto}} h1{{font-size:1.45rem;margin:0 0 .2rem}} h3{{margin:.2rem 0 .6rem}} h4{{margin:1rem 0 .4rem}}
.muted{{color:var(--muted);font-size:.9rem}} .card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}}
.verdict{{font-size:1.1rem;font-weight:600;border-left:4px solid var(--accent);padding:6px 12px;background:var(--card)}}
.draft{{color:var(--partial);font-weight:600}} .tag{{font-size:.75rem;border:1px solid var(--line);border-radius:99px;padding:1px 8px;color:var(--muted);font-weight:400}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:.88rem}} th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--muted);font-weight:500}} tr.miss td{{background:var(--miss)}}
.b{{font-weight:600}} .b.ok{{color:var(--ok)}} .b.partial{{color:var(--partial)}} .b.bad{{color:var(--bad)}} .b.none{{color:var(--muted)}}
img{{max-width:100%;height:auto;background:#fff;border-radius:6px}} pre{{white-space:pre-wrap;word-break:break-all;background:var(--bg);padding:8px;border-radius:6px}}
code{{word-break:break-all}} a{{color:var(--accent)}}
.brand{{display:flex;align-items:center;gap:8px}} .brand svg{{width:26px;height:26px}} .brand .ink{{stroke:var(--fg)}} .brand .inkfill{{fill:var(--fg)}} .brand .tick{{stroke:var(--accent)}}
</style></head><body><main>
<p class="muted brand">{MARK}<span>ReCRATE report</span></p><h1>{esc(title)}</h1>
<p class="muted">{f'<a href="{esc(doi)}">{esc(doi)}</a> · ' if doi.startswith('http') else ''}crate: {esc(card.get('crate_source') or '')}
{(' · published ' + esc(card['published'])) if card.get('published') else ''}</p>
{''.join(sections)}
<p class="muted">Every number on this page comes from running the methods; none was written by a language model.</p>
</main></body></html>"""
    out.write_text(page, encoding="utf-8")
    return out
