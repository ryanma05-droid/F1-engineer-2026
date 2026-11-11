"""The app window's page: one self-contained HTML file (no internet needed), kept as a Python
string so the installed app needs no extra data files. It polls /api/state once a second."""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Race Engineer</title>
<style>
:root {
  --bg: #0e0f14; --panel: #171821; --panel-2: #1f2130; --line: #2b2e40; --ink: #f2f2f5;
  --ink-2: #b4b6c4; --ink-3: #7d8095; --red: #e10600; --red-2: #ff4b45; --gain: #3987e5;
  --loss: #e66767; --good: #0ca30c; --warn: #fab219;
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.45 "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif; }
button { font: inherit; color: inherit; }
.num { font-variant-numeric: tabular-nums; }
.app { max-width: 1240px; margin: 0 auto; padding: 0 20px 32px; }
/* top bar */
.top { display: flex; align-items: center; gap: 14px; padding: 16px 0 12px; flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 17px;
  letter-spacing: .01em; margin-right: auto; }
.mark { width: 30px; height: 22px; background:
  repeating-linear-gradient(115deg, var(--red) 0 5px, transparent 5px 9px); border-radius: 3px; }
.chip { display: inline-flex; align-items: center; gap: 7px; background: var(--panel);
  border: 1px solid var(--line); border-radius: 99px; padding: 5px 12px; font-size: 12.5px;
  color: var(--ink-2); white-space: nowrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink-3); }
.dot.live { background: var(--good); box-shadow: 0 0 0 3px rgba(12,163,12,.2); }
.dot.wait { background: var(--warn); }
.dot.rec { background: var(--red); animation: pulse 1.6s infinite; }
@keyframes pulse { 50% { opacity: .35; } }
/* tabs */
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); margin-bottom: 18px; }
.tab { background: none; border: 0; padding: 10px 16px; cursor: pointer; color: var(--ink-3);
  font-weight: 600; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.tab[aria-selected="true"] { color: var(--ink); border-bottom-color: var(--red); }
.tab:hover { color: var(--ink); }
.view { display: none; } .view.on { display: block; }
/* cards */
.grid { display: grid; gap: 16px; grid-template-columns: 1.25fr 1fr; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 18px; }
.card h3 { margin: 0 0 12px; font-size: 12px; text-transform: uppercase; letter-spacing: .12em;
  color: var(--ink-3); font-weight: 700; display: flex; align-items: center; gap: 8px; }
.card h3 .grow { flex: 1; }
.muted { color: var(--ink-3); } .small { font-size: 12.5px; }
/* session hero */
.hero { background: linear-gradient(135deg, #1b1d2a, #15161f); position: relative; overflow: hidden; }
.hero::after { content: ""; position: absolute; right: -30px; top: 0; bottom: 0; width: 120px;
  background: repeating-linear-gradient(115deg, rgba(225,6,0,.55) 0 8px, transparent 8px 16px);
  opacity: .5; pointer-events: none; }
.track { font-size: 26px; font-weight: 750; line-height: 1.1; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 16px;
  position: relative; z-index: 1; }
.stat .k { font-size: 11px; color: var(--ink-3); text-transform: uppercase; letter-spacing: .1em; }
.stat .v { font-size: 24px; font-weight: 700; }
.stat .v small { font-size: 13px; color: var(--ink-3); font-weight: 500; }
.wearbar { height: 6px; background: var(--panel-2); border-radius: 3px; margin-top: 6px; overflow: hidden; }
.wearbar i { display: block; height: 100%; background: var(--warn); border-radius: 3px; }
.waiting { padding: 6px 0 2px; }
.waiting b { font-size: 20px; display: block; margin-bottom: 4px; }
/* radio feed */
.feed { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; max-height: 330px;
  overflow: auto; }
.feed li { display: grid; grid-template-columns: 62px 1fr; gap: 10px; padding: 8px 10px;
  background: var(--panel-2); border-radius: 8px; }
.feed li:first-child { outline: 1px solid rgba(225,6,0,.5); }
.feed .t { color: var(--ink-3); font-size: 12px; padding-top: 1px; }
.feed .lap { color: var(--ink-3); font-size: 11.5px; }
.empty { color: var(--ink-3); padding: 10px 0; }
.feed li.empty { display: block; }
/* push to talk */
.ptt { display: flex; gap: 16px; align-items: center; }
.pad { width: 128px; flex: none; }
.pad .bumper { transition: fill .1s; }
.pad.held .bumper { fill: var(--red); }
.ptt-text b { font-size: 18px; display: block; }
.examples { margin: 8px 0 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: 6px; }
.examples li { background: var(--panel-2); border-radius: 99px; padding: 3px 10px; font-size: 12.5px;
  color: var(--ink-2); }
.qa { list-style: none; margin: 14px 0 0; padding: 0; display: grid; gap: 8px; }
.qa li { padding: 8px 10px; background: var(--panel-2); border-radius: 8px; font-size: 13px; }
.qa .q { color: var(--ink-2); } .qa .a { margin-top: 2px; }
/* coaching card */
.coach-head { display: flex; gap: 22px; align-items: flex-end; flex-wrap: wrap; margin-bottom: 14px; }
.coach-head .big { font-size: 30px; font-weight: 750; line-height: 1; }
.coach-head .big.loss { color: var(--loss); } .coach-head .big.gain { color: var(--gain); }
.coach-head .k { font-size: 11px; color: var(--ink-3); text-transform: uppercase; letter-spacing: .1em; margin-bottom: 4px; }
.crows { display: grid; grid-template-columns: 92px 1fr 64px 92px; gap: 6px 12px; align-items: center; }
.crows .n { font-weight: 600; font-size: 13px; text-transform: capitalize; }
.crows .v { text-align: right; font-size: 13px; font-weight: 600; }
.crows .adv { grid-column: 1 / -1; font-size: 12.5px; color: var(--ink-2); margin: -2px 0 6px;
  padding-left: 10px; border-left: 2px solid var(--red); }
.badge { display: inline-block; font-size: 10.5px; font-weight: 700; padding: 1px 6px; border-radius: 99px;
  margin-left: 4px; vertical-align: 1px; text-transform: uppercase; letter-spacing: .04em; }
.badge.focus { background: rgba(225,6,0,.18); color: var(--red-2); }
.badge.fixed { background: rgba(12,163,12,.18); color: #5fd35f; }
.repfocus { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line); font-size: 13px; }
.repfocus .k { font-size: 11px; color: var(--ink-3); text-transform: uppercase; letter-spacing: .1em; margin-bottom: 6px; }
.repfocus .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.repfocus .chip { background: var(--panel-2); border: 1px solid var(--line); border-radius: 99px; padding: 3px 10px;
  text-transform: capitalize; font-weight: 600; }
.repfocus .chip.done { border-color: rgba(12,163,12,.5); color: #5fd35f; }
.repfocus .chip span { color: var(--ink-3); font-weight: 500; margin-left: 4px; text-transform: none; }
.fb { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; max-height: 300px; overflow: auto; }
.fb li { display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: start;
  padding: 8px 10px; background: var(--panel-2); border-radius: 8px; font-size: 13px; }
.fb .meta { color: var(--ink-3); font-size: 12px; margin-top: 2px; }
.fb .now { color: var(--good); }
.fb button { background: none; border: 1px solid var(--line); border-radius: 6px; color: var(--ink-3);
  padding: 2px 8px; font-size: 12px; cursor: pointer; }
.fb button:hover { color: var(--ink); border-color: #444862; }
/* pre-race tyre plan */
.pre-form { display: grid; grid-template-columns: 1fr 76px auto; gap: 8px; align-items: end; }
.pre-form label { display: block; font-size: 11px; color: var(--ink-3); text-transform: uppercase;
  letter-spacing: .1em; margin-bottom: 4px; }
.pre-form select, .pre-form input { width: 100%; background: var(--panel-2); color: var(--ink);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px; font: inherit; font-size: 13px; }
.pre-head { margin-top: 14px; font-size: 12px; color: var(--ink-3); }
.pre-said { margin-top: 4px; font-size: 13.5px; }
.pre-rows { display: grid; grid-template-columns: 74px 1fr 52px; gap: 8px 10px; align-items: center;
  margin-top: 12px; }
.pre-rows .n { font-weight: 600; font-size: 13px; }
.pre-rows .v { text-align: right; font-size: 12.5px; font-weight: 600; color: var(--ink-2); }
.pre-rows .dim { opacity: .72; }
.stints { display: flex; gap: 2px; height: 22px; border-radius: 6px; overflow: hidden; }
.stints i { display: flex; align-items: center; justify-content: center; font-style: normal;
  font-size: 11px; font-weight: 700; color: #15161f; white-space: nowrap; overflow: hidden; }
.stints .Soft { background: #e8453c; color: #fff; } .stints .Medium { background: #f2c94c; }
.stints .Hard { background: #e9e9ee; }
.badge.pick { background: rgba(225,6,0,.18); color: var(--red-2); }
.badge.tight { background: rgba(242,201,76,.16); color: #f2c94c; }
/* segmented tempo */
.seg { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.seg.two { grid-template-columns: repeat(2, 1fr); }
.seg.three { grid-template-columns: repeat(3, 1fr); }
.opt .who { display: flex; align-items: baseline; gap: 8px; }
.opt .who em { font-style: normal; font-size: 11px; color: var(--ink-3); text-transform: uppercase; letter-spacing: .08em; }
.opt.joke[aria-pressed="true"] { border-color: #f2c94c; background: #2a2412; }
.opt { text-align: left; background: var(--panel-2); border: 1px solid var(--line);
  border-radius: 10px; padding: 12px; cursor: pointer; }
.opt:hover { border-color: #444862; }
.opt[aria-pressed="true"] { border-color: var(--red); background: #26161a; }
.opt b { display: block; font-size: 15px; margin-bottom: 2px; }
.opt span { color: var(--ink-3); font-size: 12.5px; }
.vol { display: flex; align-items: center; gap: 12px; margin-top: 14px; }
.vol label { font-size: 13px; color: var(--ink-2); min-width: 54px; }
.vol input[type=range] { flex: 1; min-width: 0; accent-color: var(--red); height: 28px; }
.vol output { font-variant-numeric: tabular-nums; min-width: 44px; text-align: right; font-weight: 600; }
.tip { margin-top: 12px; font-size: 13px; color: var(--ink-2); background: var(--panel-2);
  border-left: 3px solid var(--red); padding: 9px 12px; border-radius: 0 8px 8px 0; }
.tip b { color: var(--ink); }
.say { color: var(--ink); font-weight: 600; white-space: nowrap; }
/* controller choice */
.ctrls { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.ctrl { background: var(--panel-2); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px; cursor: pointer; text-align: center; }
.ctrl:hover { border-color: #444862; }
.ctrl[aria-pressed="true"] { border-color: var(--red); background: #26161a; }
.ctrl svg { width: 120px; height: 76px; display: block; margin: 0 auto 8px; }
.ctrl b { display: block; font-size: 15px; }
.ctrl span { color: var(--ink-3); font-size: 12.5px; }
.btnkey { display: inline-block; min-width: 30px; padding: 1px 7px; border-radius: 6px;
  background: var(--red); color: #fff; font-weight: 700; font-size: 12.5px; text-align: center; }
/* report */
.rhead { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.rhead h2 { margin: 0; font-size: 20px; margin-right: auto; }
.btn { background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px;
  padding: 8px 14px; cursor: pointer; font-weight: 600; }
.btn:hover { border-color: #444862; }
.btn.primary { background: var(--red); border-color: var(--red); color: #fff; }
.btn:disabled { opacity: .5; cursor: default; }
/* on/off switch */
.toggle { display: flex; align-items: center; gap: 12px; cursor: pointer; font-size: 14px; }
.toggle input { appearance: none; -webkit-appearance: none; margin: 0; flex: none; width: 44px;
  height: 24px; border-radius: 99px; background: var(--panel-2); border: 1px solid var(--line);
  position: relative; cursor: pointer; transition: background .15s, border-color .15s; }
.toggle input::after { content: ""; position: absolute; top: 2px; left: 2px; width: 18px;
  height: 18px; border-radius: 50%; background: var(--ink-3); transition: transform .15s, background .15s; }
.toggle input:checked { background: var(--red); border-color: var(--red); }
.toggle input:checked::after { transform: translateX(20px); background: #fff; }
.toggle input:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
.toggle input:disabled { opacity: .5; cursor: default; }
.ovrow { display: flex; align-items: center; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
.kpi .v { font-size: 26px; font-weight: 750; }
.kpi .k { font-size: 11px; color: var(--ink-3); text-transform: uppercase; letter-spacing: .1em; }
.kpi .s { font-size: 12.5px; color: var(--ink-3); }
.themes { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  margin-bottom: 16px; }
.theme { position: relative; }
.theme .rank { position: absolute; top: 14px; right: 16px; font-size: 40px; font-weight: 800;
  color: rgba(225,6,0,.22); line-height: 1; }
.theme h4 { margin: 0; font-size: 17px; padding-right: 50px; }
.theme .cost { color: var(--loss); font-size: 22px; font-weight: 750; margin: 6px 0 2px; }
.theme .cost small { font-size: 13px; color: var(--ink-3); font-weight: 500; }
.theme p { margin: 8px 0 0; color: var(--ink-2); font-size: 13.5px; }
.theme .try { color: var(--ink); }
.corners { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.corners span { background: var(--panel-2); border-radius: 6px; padding: 2px 8px; font-size: 12px; }
.corners b { color: var(--loss); font-weight: 600; }
.conf { display: inline-block; font-size: 11px; padding: 1px 8px; border-radius: 99px;
  border: 1px solid var(--line); color: var(--ink-3); margin-left: 6px; vertical-align: 2px; }
.conf.solid { color: #8fb9ef; border-color: #2c4c78; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.bars { display: grid; grid-template-columns: 160px 1fr 64px; gap: 8px 12px; align-items: center; }
.bars .n { font-weight: 600; font-size: 13px; }
.bars .n small { display: block; color: var(--ink-3); font-weight: 400; font-size: 11.5px; }
.bars .v { text-align: right; font-weight: 600; font-size: 13px; }
.dbar { position: relative; height: 12px; }
.dbar::before { content: ""; position: absolute; left: 50%; top: -3px; bottom: -3px; width: 1px;
  background: var(--line); }
.dbar i { position: absolute; top: 0; height: 100%; border-radius: 3px; }
ul.good { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
ul.good li { padding-left: 24px; position: relative; color: var(--ink-2); font-size: 13.5px; }
ul.good li::before { content: "\2713"; position: absolute; left: 0; top: 1px; width: 16px; height: 16px;
  border-radius: 50%; background: var(--good); color: #fff; font-size: 10px; text-align: center;
  line-height: 16px; font-weight: 700; }
.pdf { width: 100%; height: 820px; border: 1px solid var(--line); border-radius: 12px; background: #fff; }
.legend { display: flex; gap: 14px; font-size: 12px; color: var(--ink-3); margin-top: 10px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px;
  vertical-align: -1px; }
/* update banner */
.upd { display: none; align-items: center; gap: 12px; flex-wrap: wrap; margin: 0 0 14px; padding: 12px 14px;
  border: 1px solid rgba(225,6,0,.45); background: rgba(225,6,0,.10); border-radius: 12px; font-size: 14px; }
.upd.on { display: flex; }
.upd .grow { flex: 1; min-width: 200px; }
.upd a { color: var(--ink); }
.upd .bar { height: 4px; background: var(--line); border-radius: 2px; margin-top: 6px; overflow: hidden; }
.upd .bar i { display: block; height: 100%; background: var(--red); }
/* welcome */
.overlay { position: fixed; inset: 0; background: rgba(5,6,10,.82); display: none;
  align-items: center; justify-content: center; padding: 20px; z-index: 10; }
.overlay.on { display: flex; }
.wizard { background: var(--panel); border: 1px solid var(--line); border-radius: 16px;
  width: min(760px, 100%); padding: 28px; }
.wizard .step { font-size: 12px; color: var(--red-2); text-transform: uppercase;
  letter-spacing: .14em; font-weight: 700; }
.wizard h2 { margin: 6px 0 6px; font-size: 24px; }
.wizard .lede { color: var(--ink-2); margin: 0 0 18px; }
.wizard .actions { display: flex; justify-content: space-between; margin-top: 20px; }
@media (max-width: 900px) {
  .grid, .cols { grid-template-columns: 1fr; }
  .kpis, .stats { grid-template-columns: repeat(2, 1fr); }
  .ctrls, .seg, .seg.two, .seg.three { grid-template-columns: 1fr; }
}
</style></head>
<body>
<div class="app">
  <div class="top">
    <div class="brand"><span class="mark"></span>Race Engineer</div>
    <span class="chip" id="conn"><span class="dot"></span><span>Starting…</span></span>
    <span class="chip" id="rec"><span class="dot"></span><span>Recording off</span></span>
    <span class="chip" id="tempoChip">Tempo: Normal</span>
  </div>
  <div class="upd" id="upd" role="status"></div>
  <div class="tabs" role="tablist">
    <button class="tab" role="tab" data-view="live" aria-selected="true">Live</button>
    <button class="tab" role="tab" data-view="report" aria-selected="false">Driver report</button>
    <button class="tab" role="tab" data-view="settings" aria-selected="false">Settings</button>
  </div>

  <!-- LIVE -->
  <section class="view on" id="view-live">
    <div class="grid">
      <div style="display:grid;gap:16px;align-content:start">
        <div class="card hero" id="session"></div>
        <div class="card"><h3><span class="grow">Team radio</span><span class="muted small" id="quietNote"></span></h3>
          <ul class="feed" id="feed"></ul></div>
        <div class="card"><h3><span class="grow">Coaching</span><span class="muted small" id="coachVs"></span></h3>
          <div id="coach"></div></div>
      </div>
      <div style="display:grid;gap:16px;align-content:start">
        <div class="card"><h3><span class="grow">Pre-race tyre plan</span><span class="muted small" id="preNote"></span></h3>
          <div class="pre-form"><div><label for="preTrack">Track</label><select id="preTrack"></select></div>
            <div><label for="preLaps">Laps</label><input id="preLaps" type="number" min="1" max="99" inputmode="numeric"></div>
            <button class="btn primary" id="preGo">Plan it</button></div>
          <div id="prerace"></div></div>
        <div class="card"><h3>Push to talk</h3><div id="ptt"></div></div>
        <div class="card"><h3>Engineer tempo</h3><div class="seg" id="tempoLive"></div>
          <div class="tip" id="tempoTipLive"></div></div>
        <div class="card"><h3><span class="grow">Logged questions</span><span class="muted small" id="fbCount"></span></h3>
          <div id="feedback"></div></div>
        <div class="card" id="uploadCard"><h3><span class="grow">Recordings to GitHub</span><span class="muted small" id="upNote"></span></h3>
          <div id="uploads"></div></div>
      </div>
    </div>
  </section>

  <!-- REPORT -->
  <section class="view" id="view-report">
    <div class="rhead"><h2>Your driver report</h2>
      <span class="muted small" id="rWhen"></span>
      <button class="btn" id="openPdf">Open PDF</button>
      <button class="btn primary" id="build">Build new report</button></div>
    <div id="report"><div class="empty">Loading…</div></div>
  </section>

  <!-- SETTINGS -->
  <section class="view" id="view-settings">
    <div class="card" style="margin-bottom:16px"><h3>Your controller (push to talk)</h3>
      <div id="ctrlSettings"></div></div>
    <div class="card" style="margin-bottom:16px"><h3><span class="grow">Engineer voice</span><span class="muted small" id="voiceNote"></span></h3>
      <div class="seg three" id="voiceSettings"></div>
      <div class="vol"><label for="volume">Volume</label>
        <input type="range" id="volume" min="0" max="100" step="5" value="70" aria-describedby="volumeTip">
        <output id="volumeOut" for="volume">70%</output></div>
      <div class="muted small" id="volumeTip">70% is how loud it was before. Let go and the engineer says a line at the new volume.</div>
      <div class="vol"><label for="driverName">Your name</label>
        <input type="text" id="driverName" maxlength="30" placeholder="mate" style="flex:1"></div>
      <div class="muted small">What GP calls you on the radio ("Okay Ryan, ..."). Empty: "mate".</div>
      <div class="tip" id="voiceTip"></div></div>
    <div class="card" style="margin-bottom:16px"><h3>Engineer tempo (default)</h3>
      <div class="seg" id="tempoSettings"></div><div class="tip" id="tempoTipSettings"></div></div>
    <div class="card" style="margin-bottom:16px"><h3>Corner advice</h3>
      <div class="seg two" id="detailSettings"></div>
      <div class="tip">Say <span class="say">“give me the numbers”</span> or
        <span class="say">“keep it simple”</span> to switch mid-race.</div></div>
    <div class="card" style="margin-bottom:16px"><h3><span class="grow">Lap delta overlay</span><span class="muted small" id="ovNote"></span></h3>
      <div id="ovSettings"></div></div>
    <div class="card" style="margin-bottom:16px"><h3><span class="grow">Updates</span><span class="muted small" id="updVer"></span></h3>
      <div id="updSettings"></div></div>
    <div class="card"><h3>Game telemetry</h3><div class="small" id="telemetry"></div></div>
  </section>
</div>

<div class="overlay" id="welcome"><div class="wizard" id="wizard"></div></div>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const TEMPOS = [
  ["terse", "Quiet", "Essentials only: lap times, box calls, warnings. Short and sparse."],
  ["normal", "Normal", "Lap times, tyres, strategy, corners and battles. The default."],
  ["chatty", "Chatty", "Everything, with more detail: reasons, praise, gaps, extra context."],
  ["coaching", "Coaching", "Where to find speed each lap against the fastest car on track. Less race chatter."],
];
const TRACKS = __TRACKS__;
const ENGINEERS = __ENGINEERS__;
const DETAILS = [
  ["plain", "Plain", "“Brake slightly later, then carry a bit more speed through the corner.”"],
  ["specific", "Specific", "“Braking fifteen metres earlier, minimum speed eight k p h down.”"],
];
// Repaint only when the HTML changes, so buttons aren't replaced under the mouse every second.
function paint(el, html) { if (el._html === html) return false; el._html = html; el.innerHTML = html; return true; }
let S = null, reportStamp = undefined, wizardStep = 0, wizardChoice = {};

function signed(v, unit = "", d = 2) {
  if (v === null || v === undefined) return "—";
  return (v > 0 ? "+" : v < 0 ? "−" : "±") + Math.abs(v).toFixed(d) + unit;
}

async function post(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json",
    "X-Engineer": "1"}, body: JSON.stringify(body || {})});
  return r.json();
}

function padSvg(label, held) {
  // A controller seen from the top, with the left shoulder button lit.
  return `<svg viewBox="0 0 120 76" class="pad${held ? " held" : ""}" aria-hidden="true">
    <path d="M20 26 Q22 14 36 14 L84 14 Q98 14 100 26 L112 58 Q115 70 104 70 Q96 70 90 60 L80 48
      L40 48 L30 60 Q24 70 16 70 Q5 70 8 58 Z" fill="#2b2e40" stroke="#444862" stroke-width="1.5"/>
    <rect class="bumper" x="18" y="4" width="30" height="10" rx="4" fill="#e10600"/>
    <rect x="72" y="4" width="30" height="10" rx="4" fill="#444862"/>
    <text x="33" y="12" text-anchor="middle" font-size="7.5" font-weight="700" fill="#fff"
      font-family="Segoe UI, Arial">${esc(label)}</text>
    <circle cx="40" cy="32" r="6" fill="#1f2130"/><circle cx="80" cy="32" r="6" fill="#1f2130"/></svg>`;
}

function tempoTip() {
  const b = S && S.controller.type !== "off" ? S.controller.button : null;
  const how = b ? `hold <span class="btnkey">${esc(b)}</span> and say` : "say (with push to talk on)";
  return `<b>Tip:</b> change it mid-race by voice: ${how} <span class="say">“talk more”</span>,
    <span class="say">“keep it short”</span> or <span class="say">“back to normal”</span>.
    <span class="say">“Be quiet”</span> goes silent except box calls and answers;
    <span class="say">“coaching mode”</span> for where the time is each lap.`;
}

function tempoButtons(el, current, onPick) {
  if (!paint(el, TEMPOS.map(([k, n, d]) => `<button class="opt" data-k="${k}"
    aria-pressed="${k === current}"><b>${n}</b><span>${d}</span></button>`).join(""))) return;
  el.querySelectorAll(".opt").forEach(b => b.onclick = () => onPick(b.dataset.k));
}

function controllerButtons(el, current, onPick, consolePlayer) {
  if (consolePlayer) {
    paint(el, `<p class="muted">You play on a console: the engineer listens to the game over
      Wi-Fi, but push to talk needs the controller plugged into this PC, so it's off.</p>`);
    return;
  }
  const opts = [["xbox", "Xbox controller", "LB", "Hold LB to talk"],
                ["playstation", "PlayStation controller", "L1", "Hold L1 to talk"],
                ["off", "Console only (no push to talk)", "", "The engineer volunteers more: options with pros and cons, push and manage calls, gaps"]];
  if (!paint(el, `<div class="ctrls">` + opts.map(([k, n, key, d]) => `<button class="ctrl"
    data-k="${k}" aria-pressed="${k === current}">${key ? padSvg(key, false) :
    `<svg viewBox="0 0 120 76" aria-hidden="true"><circle cx="60" cy="38" r="22" fill="none"
    stroke="#444862" stroke-width="3"/><path d="M45 23 L75 53" stroke="#444862" stroke-width="3"/></svg>`}
    <b>${n}</b><span>${d}</span></button>`).join("") + `</div>`)) return;
  el.querySelectorAll(".ctrl").forEach(b => b.onclick = () => onPick(b.dataset.k));
}

async function setTempo(t) { S = await post("/api/settings", {tempo: t}); render(); }
async function setController(c) { S = await post("/api/settings", {controller: c}); render(); }

/* ---- live ---- */
function renderSession() {
  const s = S.session, el = $("#session");
  if (!s) {
    el.innerHTML = `<h3>Session</h3><div class="waiting"><b>${S.connection.live ?
      "Connected — waiting for a session" : "Waiting for F1 25"}</b>
      <span class="muted">${S.connection.live ? "Start a session and your race appears here." :
      `Turn on UDP telemetry in F1 25 (port ${S.connection.port}). Settings tab has the steps.`}</span></div>`;
    return;
  }
  const lap = s.lap ? (s.total_laps ? `${s.lap}<small> / ${s.total_laps}</small>` : s.lap) : "—";
  const pos = s.position ? `P${s.position}${s.cars ? `<small> / ${s.cars}</small>` : ""}` : "—";
  el.innerHTML = `<h3><span class="grow">${esc(s.session)}</span>${s.safety_car ?
      '<span class="chip" style="color:#fab219">Safety car</span>' : ""}</h3>
    <div class="track">${esc(s.track)}</div>
    <div class="stats">
      <div class="stat"><div class="k">Lap</div><div class="v num">${lap}</div></div>
      <div class="stat"><div class="k">Position</div><div class="v num">${pos}</div></div>
      <div class="stat"><div class="k">Last lap</div><div class="v num" style="font-size:19px">${s.last_lap || "—"}</div>
        <div class="muted small num">best ${s.best_lap || "—"}</div></div>
      <div class="stat"><div class="k">Tyres</div><div class="v" style="font-size:19px">${esc(s.tyre || "—")}</div>
        ${s.wear !== null ? `<div class="muted small num">worst wear ${s.wear}%</div>
        <div class="wearbar"><i style="width:${Math.min(100, s.wear)}%"></i></div>` : ""}</div>
    </div>`;
}

function renderFeed() {
  paint($("#feed"), S.radio.length ? S.radio.map(r => `<li><span class="t num">${esc(r.at)}</span>
    <span>${esc(r.text)}${r.lap ? ` <span class="lap">· lap ${r.lap}</span>` : ""}</span></li>`).join("")
    : `<li class="empty" style="background:none">The engineer's calls appear here as they're said.</li>`);
  $("#quietNote").textContent = S.quiet ? "Radio silence: box calls and answers only" : "";
}

function renderPtt() {
  const c = S.controller, el = $("#ptt");
  if (S.console_player || c.type === "off") {
    el.innerHTML = `<p class="muted" style="margin:0">${S.console_player ? "Off on console: the controller is on the console, not this PC. Console-only mode is on: the engineer volunteers options, push and manage calls and gaps."
      : "Off: console-only mode, the engineer volunteers options, push and manage calls and gaps. Pick a controller in Settings to ask questions."}</p>`;
    return;
  }
  const status = {ready: c.connected ? "Controller connected" : "No controller found yet — plug it in",
    starting: "Loading speech recognition…", off: "Off", error: c.error || "Not available"}[c.state] || c.state;
  paint(el, `<div class="ptt"><div class="pad${c.held ? " held" : ""}">${padSvg(c.button, c.held)}</div>
    <div class="ptt-text"><b>${c.held ? "Listening…" : `Hold <span class="btnkey">${esc(c.button)}</span> to ask`}</b>
    <span class="muted small">${esc(status)}</span>
    <ul class="examples"><li>How are the tyres?</li><li>Gap ahead?</li><li>What's the plan?</li>
      <li>Where am I losing time?</li></ul></div></div>
    ${S.questions.length ? `<ul class="qa">${S.questions.slice(0, 3).map(q => `<li><div class="q">“${esc(q.heard)}”</div>
      <div class="a">${esc(q.answer || "—")}</div></li>`).join("")}</ul>` : ""}`);
}

function lapTime(ms) {
  const m = Math.floor(ms / 60000), s = (ms % 60000) / 1000;
  return m + ":" + s.toFixed(3).padStart(6, "0");
}
function gapText(ms) {
  if (ms === null || ms === undefined) return "—";
  return (ms < 0 ? "−" : "+") + (Math.abs(ms) / 1000).toFixed(2) + "s";
}

function trendSvg(vals) {
  // Loss per lap at this corner (newest right); the line under is "matching the fastest".
  if (!vals || vals.length < 2) return "";
  const w = 92, h = 26, p = 3, top = Math.max(...vals.map(Math.abs), 0.1);
  const y = v => h / 2 - v / top * (h / 2 - p);
  const pts = vals.map((v, i) => [p + i * (w - 2 * p) / (vals.length - 1), y(v)]);
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><line x1="0" x2="${w}"
    y1="${h / 2}" y2="${h / 2}" stroke="#2b2e40"/><path d="${pts.map((q, i) => (i ? "L" : "M") + q[0].toFixed(1) + ","
    + q[1].toFixed(1)).join(" ")}" fill="none" stroke="#b4b6c4" stroke-width="1.5"/><circle cx="${pts.at(-1)[0].toFixed(1)}"
    cy="${pts.at(-1)[1].toFixed(1)}" r="3" fill="${vals.at(-1) > 0 ? "#e66767" : "#3987e5"}"/></svg>`;
}

function idealLap(i) {
  if (!i) return `<div class="repfocus muted">Ideal lap: after three clean laps this stint.</div>`;
  const where = i.losses.map(x => `<span class="chip">${esc(x.name)}<span>+${x.s.toFixed(2)} s</span></span>`).join("");
  return `<div class="repfocus"><div class="k">Ideal lap · last ${i.laps.length} laps, this stint</div>
    <div style="margin-bottom:6px"><b class="num" style="font-size:18px">${lapTime(i.ms)}</b>
    <span class="num" style="margin-left:8px;color:${i.gap_ms > 0 ? "var(--loss)" : "var(--gain)"}">L${i.lap} ${gapText(i.gap_ms)}</span></div>
    ${where ? `<div class="chips">${where}</div>` : ""}</div>`;
}
function reportFocus(r) {
  if (!r) return `<div class="repfocus muted">Build a driver report (Report tab) and the engineer coaches
    your usual weak corners first.</div>`;
  const chips = r.corners.map(x => `<span class="chip${x.level ? " done" : ""}">${esc(x.name)}<span>${x.level
    ? "level now" : "+" + x.loss_s.toFixed(2) + " s usual"}</span></span>`).join("");
  return `<div class="repfocus"><div class="k">From your report</div>${chips ? `<div class="chips">${chips}</div>` :
    `<div class="muted" style="margin-bottom:6px">No report corners at this track yet.</div>`}${r.habit
    ? `<div>Habit to work on: <b>${esc(r.habit)}</b></div>` : ""}</div>`;
}
function renderCoach() {
  const c = S.coaching, el = $("#coach");
  const hint = S.tempo === "coaching" ? "" : `<div class="tip" style="margin-top:12px">Switch the tempo to
    <b>Coaching</b> (or say <span class="say">“coaching mode”</span>) to hear this after every lap.</div>`;
  $("#coachVs").textContent = c.laps ? `vs ${c.bench} · ${c.laps} lap${c.laps === 1 ? "" : "s"}` : "";
  if (!c.laps) {
    paint(el, `<p class="muted" style="margin:0">After your first clean lap, each lap is compared with the
      fastest lap on track, corner by corner.</p>` + idealLap(c.ideal) + reportFocus(c.report) + hint);
    return;
  }
  const span = Math.max(0.2, ...c.corners.map(x => Math.abs(x.delta_s)));
  const rows = c.corners.map(x => `<span class="n">${esc(x.name)}${x.focus ? '<span class="badge focus">focus</span>' : ""}${x.fixed ?
      '<span class="badge fixed">fixed</span>' : ""}</span>${dbar(x.delta_s, span)}
    <span class="v num" style="color:${x.delta_s > 0 ? "var(--loss)" : "var(--gain)"}">${signed(x.delta_s, "", 2)}</span>
    <span>${trendSvg(x.trend)}</span>${x.focus && x.advice ? `<div class="adv">${esc(x.advice[0].toUpperCase() + x.advice.slice(1))}.</div>` : ""}`).join("");
  paint(el, `<div class="coach-head">
      <div><div class="k">Last lap · L${c.lap}</div><div class="big num ${c.gap_ms > 0 ? "loss" : "gain"}">${gapText(c.gap_ms)}</div></div>
      <div><div class="k">Best</div><div class="num" style="font-size:18px;font-weight:700">${gapText(c.best_gap_ms)}</div></div>
      <div><div class="k">First lap</div><div class="num muted" style="font-size:18px;font-weight:700">${gapText(c.first_gap_ms)}</div></div>
    </div>
    <div class="crows">${rows}</div>
    <div class="legend"><span><i style="background:var(--loss)"></i>slower than ${esc(c.bench)} here</span>
      <span><i style="background:var(--gain)"></i>quicker</span><span>line: last ${Math.min(6, c.laps)} laps</span></div>` + idealLap(c.ideal) + reportFocus(c.report) + hint);
}

const LAPS_HOW = {"last race here": "same as your last race here", "usual distance": "your usual race distance",
  "game": "from the game", "said": ""};
let preTouched = false;
function initPre() {
  $("#preTrack").innerHTML = `<option value="">Pick a track</option>` +
    TRACKS.map(t => `<option value="${t[0]}">${esc(t[1])}</option>`).join("");
  $("#preTrack").onchange = () => { preTouched = true; };
  $("#preLaps").oninput = () => { preTouched = true; };
  $("#preGo").onclick = planPre;
}
async function planPre() {
  const t = $("#preTrack").value, l = $("#preLaps").value, go = $("#preGo");
  go.disabled = true; go.textContent = "Working…"; $("#preNote").textContent = "";
  try {
    const r = await post("/api/prerace", {track_id: t === "" ? null : +t, laps: l === "" ? null : +l});
    if (r.prerace) { S = r; preTouched = false; }
    if (r.error) $("#preNote").textContent = {no_track: "pick a track", racing: "the race is on"}[r.error] || r.error;
  } finally { go.disabled = false; go.textContent = "Plan it"; render(); }
}
function stintBar(st, laps) {
  return `<div class="stints">${st.stints.map(x => `<i class="${x.compound}" style="flex:${Math.max(1, x.to - x.from)}"
    title="${x.compound}: laps ${x.from + 1}–${x.to}${x.wear != null ? `, about ${x.wear}% worn at the end` : ""}">${x.to < laps ?
    "L" + x.to : ""}${x.wear != null ? ` · ${x.wear}%` : ""}</i>`).join("")}</div>`;
}
function renderPre() {
  const p = S.prerace, d = p.defaults, el = $("#prerace");
  if (!preTouched && document.activeElement !== $("#preTrack") && document.activeElement !== $("#preLaps")) {
    $("#preTrack").value = d.track_id ?? ""; $("#preLaps").value = d.laps ?? "";
  }
  $("#preGo").disabled = p.racing || p.busy;
  const ask = S.controller.type !== "off" ? `hold <span class="btnkey">${esc(S.controller.button)}</span> and ask` : "ask";
  const how = `<div class="tip">Before the race, ${ask} <span class="say">“what tyres should I start on?”</span> or
    <span class="say">“strategy for Suzuka, fifteen laps”</span>. Said by itself when qualifying ends.</div>`;
  const pl = p.plan;
  let body = "";
  if (p.racing) body = `<p class="muted" style="margin:12px 0 0">The race is on: the plan comes on the radio from lap two.</p>`;
  else if (!pl) body = `<p class="muted" style="margin:12px 0 0">The game sends nothing in the setup menus, so this uses
    your history: pick the track and laps (or leave them as guessed) and press Plan it.</p>` + how;
  else if (pl.error === "no_laps") body = `<p class="muted" style="margin:12px 0 0">How many laps? Enter the race length.</p>`;
  else if (pl.error) body = `<p class="muted" style="margin:12px 0 0">Couldn't work that out.</p>`;
  else {
    const why = LAPS_HOW[pl.how] ? ` · ${LAPS_HOW[pl.how]}` : "";
    body = `<div class="pre-head">${esc(pl.track)} · ${pl.laps} laps${esc(why)}${pl.grid ? " · on the grid" : ""}</div>
      <div class="pre-said">${esc(pl.said)}</div>` + (pl.starts.length ? `<div class="pre-rows">${pl.starts.map(st =>
      `<span class="n${st.best ? "" : " dim"}">${st.compound}${st.best ? '<span class="badge pick">pick</span>' :
      st.tight ? '<span class="badge tight">tight</span>' : ""}</span><div class="${st.best ? "" : "dim"}">${stintBar(st, pl.laps)}</div>
      <span class="v num">${st.best ? "" : Math.abs(st.delta_s) < 0.05 ? "same" : signed(st.delta_s, "s", 1)}</span>`).join("")}</div>
      <div class="legend"><span>bar: the race, one block per set · L = box at the end of that lap · % = wear when it comes off</span></div>` : "") + how;
  }
  paint(el, body);
}

function renderUploads() {
  const u = S.uploads, card = $("#uploadCard");
  if (!u) { card.style.display = "none"; return; }
  card.style.display = "";
  $("#upNote").textContent = u.auto ? "auto" : "auto off";
  const n = u.waiting.length;
  let head;
  if (u.uploading) head = `<b>Uploading</b> <span class="num">${esc(u.uploading)}</span>…`;
  else if (n && u.racing && u.auto) head = `${n} waiting · they go up when you're not in a session`;
  else if (n) head = `${n} recording${n === 1 ? "" : "s"} waiting to upload`;
  else head = `All recordings uploaded`;
  const err = u.error ? `<div class="tip">Last upload failed: ${esc(u.error)}</div>` : "";
  const done = u.done.length ? `<div class="muted small" style="margin-top:6px">Sent this run: ${u.done.map(esc).join(", ")}</div>` : "";
  if (paint($("#uploads"), `<div style="display:flex;gap:10px;align-items:center"><div class="small" style="flex:1">${head}</div>
      <button class="btn" id="upNow" ${n && !u.uploading ? "" : "disabled"}>Upload now</button></div>${done}${err}`))
    $("#upNow").onclick = async () => { S = await post("/api/upload"); render(); };
}

function renderFeedback() {
  const f = S.feedback, el = $("#feedback");
  $("#fbCount").textContent = f.count ? `${f.count} open` : "";
  const b = S.controller.type !== "off" ? `hold <span class="btnkey">${esc(S.controller.button)}</span> and say` : "say";
  const how = `<div class="tip" style="margin-top:${f.count ? 12 : 0}px">Didn't get a good answer? ${b}
    <span class="say">“log that”</span>, or <span class="say">“log the following question: …”</span>.
    It's saved with the race at that moment and written up when the race ends${f.file ?
    ` in <span class="num">${esc(f.file)}</span>` : ""}.</div>`;
  if (!f.on) { paint(el, `<p class="muted" style="margin:0">Off while replaying.</p>`); return; }
  if (!paint(el, (f.count ? `<ul class="fb">${f.open.map(q => `<li><div>“${esc(q.question)}”
    <div class="meta">${q.track ? esc(q.track) + " · " : ""}${q.lap ? "lap " + q.lap : "before the start"}${q.matches ?
    ` · <span class="now">handled in a newer version: check</span>` : ""}${q.answer ? ` · said: ${esc(q.answer)}` : ""}</div></div>
    <button data-id="${esc(q.id)}" title="Don't answer this one">Dismiss</button></li>`).join("")}</ul>` : "") + how)) return;
  el.querySelectorAll("button[data-id]").forEach(x => x.onclick = async () => {
    S = await post("/api/feedback/dismiss", {id: x.dataset.id}); render(); });
}

function renderTop() {
  const c = S.connection, conn = $("#conn"), rec = $("#rec");
  conn.innerHTML = c.live ? `<span class="dot live"></span><span>Connected to F1 25 · ${c.rate} packets/s</span>`
    : `<span class="dot wait"></span><span>Waiting for F1 25 on port ${c.port}</span>`;
  const r = S.recording;
  rec.innerHTML = r.on && r.file ? `<span class="dot rec"></span><span>Recording · ${r.mb ?? 0} MB</span>`
    : r.on ? `<span class="dot"></span><span>Ready to record</span>` : `<span class="dot"></span><span>Recording off</span>`;
  const name = (TEMPOS.find(t => t[0] === S.tempo) || [0, S.tempo])[1];
  $("#tempoChip").textContent = `Tempo: ${name}${S.quiet ? " (silent)" : ""}`;
}

/* ---- report ---- */
function spark(vals) {
  if (!vals || vals.length < 2) return `<span class="muted small">Trend after another session</span>`;
  const w = 150, h = 34, p = 4, top = Math.max(...vals, 0.05);
  const pts = vals.map((v, i) => [p + i * (w - 2 * p) / (vals.length - 1), h - p - v / top * (h - 2 * p)]);
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><line x1="${p}" x2="${w - p}" y1="${h - p}"
    y2="${h - p}" stroke="#2b2e40"/><path d="${pts.map((q, i) => (i ? "L" : "M") + q[0].toFixed(1) + "," + q[1].toFixed(1)).join(" ")}"
    fill="none" stroke="#e66767" stroke-width="2"/>${pts.map(q => `<circle cx="${q[0].toFixed(1)}" cy="${q[1].toFixed(1)}"
    r="2.5" fill="#e66767"/>`).join("")}</svg>`;
}

function dbar(v, span) {
  const w = Math.min(Math.abs(v) / span, 1) * 50;
  return `<div class="dbar"><i style="${v < 0 ? `right:50%` : `left:50%`};width:${w}%;background:${v < 0 ? "var(--gain)" : "var(--loss)"}"></i></div>`;
}

async function loadReport() {
  const R = await (await fetch("/api/report")).json();
  const el = $("#report");
  if (!R.sessions) {
    el.innerHTML = `<div class="card"><b>No report yet.</b><p class="muted">Race a few laps with the engineer
      running, then press <b>Build new report</b> (it also builds itself once a day after you race).
      ${R.pending ? `${R.pending} recorded session(s) are waiting to be analysed.` : ""}</p></div>`;
    return;
  }
  const top = R.themes[0];
  const tspan = Math.max(0.5, ...R.tracks.map(t => Math.abs(t.pct))) * 1.1;
  const yspan = Math.max(0.5, ...R.types.map(t => Math.abs(t.relative))) * 1.1;
  el.innerHTML = `
    <div class="kpis">
      <div class="card kpi"><div class="k">Pace vs quickest AI</div>
        <div class="v num" style="color:${R.overall_pct < 0 ? "var(--gain)" : "var(--loss)"}">${signed(R.overall_pct, "%")}</div>
        <div class="s">median clean lap, all tracks</div></div>
      <div class="card kpi"><div class="k">Work on first</div>
        <div class="v" style="font-size:17px;line-height:1.25;margin-top:4px">${top ? esc(top.title) : "Nothing stands out"}</div>
        <div class="s">${top ? `about ${top.cost_s.toFixed(2)} s a lap` : "keep racing for more data"}</div></div>
      <div class="card kpi"><div class="k">Typical vs your ideal lap</div>
        <div class="v num">${R.ideal_gap_s.toFixed(2)}s</div><div class="s">per lap, vs your best corners of the last 5 laps</div></div>
      <div class="card kpi"><div class="k">Evidence</div>
        <div class="v num">${R.laps}<span class="muted" style="font-size:14px"> laps</span></div>
        <div class="s">${R.sessions} session${R.sessions === 1 ? "" : "s"}${R.pending ? ` · ${R.pending} new not in it yet` : ""}</div></div>
    </div>
    <h3 class="muted" style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;margin:4px 0 10px">Major themes — where your time goes</h3>
    <div class="themes">${R.themes.length ? R.themes.map((t, i) => `<div class="card theme">
      <div class="rank">${i + 1}</div><h4>${esc(t.title)}<span class="conf ${t.confidence === "solid" ? "solid" : ""}">${t.confidence}</span></h4>
      <div class="cost num">${t.cost_s.toFixed(2)}s <small>per lap</small></div>
      <div>${spark(t.trend)}</div>
      <p>${esc(t.detail)}</p><p class="try"><b>Try:</b> ${esc(t.advice)}</p>
      <div class="corners">${t.corners.map(c => `<span>${esc(c[0])} <b>${c[1].toFixed(2)}s</b></span>`).join("")}</div>
    </div>`).join("") : `<div class="card muted">No habit costs more than a few hundredths a lap yet.</div>`}</div>
    <div class="cols">
      <div class="card"><h3>Strengths</h3><ul class="good">${R.strengths.map(s => `<li>${esc(s)}</li>`).join("") ||
        "<li>More sessions needed to call a strength.</li>"}</ul></div>
      <div class="card"><h3>Tracks</h3><div class="bars">${R.tracks.map(t => `<div class="n">${esc(t.track)}
        <small>P${t.rank.toFixed(0)} pace of ${t.cars} · ${t.laps} laps · ${t.confidence}</small></div>${dbar(t.pct, tspan)}
        <div class="v num">${signed(t.pct, "%")}</div>`).join("")}</div>
        <div class="legend"><span><i style="background:var(--gain)"></i>quicker than the AI</span>
        <span><i style="background:var(--loss)"></i>slower</span></div></div>
    </div>
    <div class="card" style="margin-bottom:16px"><h3>Corner types — against your average corner</h3>
      <div class="bars" style="grid-template-columns:260px 1fr 64px">${R.types.map(t => `<div class="n">${esc(t.name)}
      <small>${signed(t.pct, "%", 1)} vs the AI</small></div>${dbar(t.relative, yspan)}<div class="v num">${signed(t.relative, "%", 1)}</div>`).join("")}</div></div>
    ${S && S.report.pdf ? `<div class="card"><h3><span class="grow">Full report (PDF)</span></h3>
      <iframe class="pdf" src="/report/latest.pdf#view=FitH" title="Driver report PDF"></iframe></div>` : ""}`;
}

function renderReportHead() {
  const r = S.report;
  $("#rWhen").textContent = r.building ? "Building a new report…" : r.generated ? `Last built ${r.generated}` : "";
  $("#build").disabled = r.building;
  $("#build").textContent = r.building ? "Building…" : "Build new report";
  $("#openPdf").disabled = !r.pdf;
  if (r.stamp !== reportStamp && $("#view-report").classList.contains("on")) {
    reportStamp = r.stamp; loadReport();
  }
}

/* ---- settings ---- */
async function setDetail(d) { S = await post("/api/settings", {detail: d}); render(); }

const VOICE_STATE = {downloading: "downloading the voice…", loading: "loading the voice…",
  failed: "", ready: "", off: "voice is off"};
async function setEngineer(k) { S = await post("/api/settings", {engineer: k}); render(); }
let volumeDragging = false;
async function setVolume(p) { S = await post("/api/settings", {volume: p}); volumeDragging = false; render(); }
function renderVolume() {
  const r = $("#volume"), out = $("#volumeOut");
  if (!r._wired) {
    r._wired = true;
    r.oninput = () => { volumeDragging = true; out.textContent = r.value + "%"; };
    r.onchange = () => setVolume(parseInt(r.value, 10));
  }
  const v = S.voice.volume ?? 70;
  if (!volumeDragging && document.activeElement !== r) { r.value = v; out.textContent = v + "%"; }
}
function renderDriverName() {
  const el = $("#driverName");
  if (!el._wired) {
    el._wired = true;
    el.onchange = async () => { S = await post("/api/settings", {driver_name: el.value}); render(); };
  }
  if (document.activeElement !== el) el.value = S.voice.driver_name || "";
}
function renderVoice() {
  renderVolume();
  renderDriverName();
  const v = S.voice, el = $("#voiceSettings");
  if (paint(el, ENGINEERS.map(e => `<button class="opt${e.joke ? " joke" : ""}" data-k="${e.key}"
    aria-pressed="${e.key === v.engineer}"><div class="who"><b>${esc(e.name)}</b><em>${esc(e.accent)}</em></div>
    <span>${esc(e.blurb)}</span></button>`).join("")))
    el.querySelectorAll(".opt").forEach(b => b.onclick = () => setEngineer(b.dataset.k));
  $("#voiceNote").textContent = VOICE_STATE[v.state] ?? "";
  const cur = ENGINEERS.find(e => e.key === v.engineer);
  $("#voiceTip").innerHTML = v.error ? esc(v.error) + ". Check the internet connection and pick again."
    : `Picking one says a line in that voice. A voice is downloaded the first time (20–110 MB).` +
      (cur && cur.joke ? ` <b>${esc(cur.name)}</b> still says every call in full; he just adds to it.` : "");
}

/* ---- updates ---- */
async function updateAction(action, extra) { S = await post("/api/update", Object.assign({action}, extra || {})); render(); }
function renderUpdate() {
  const u = S.update || {}, el = $("#upd");
  const show = u.available && (!u.dismissed || u.state === "downloading" || u.state === "ready");
  el.classList.toggle("on", !!show);
  if (show) {
    const link = u.page ? ` <a href="${esc(u.page)}" target="_blank" rel="noopener">What's new</a>` : "";
    let act = "", sub = "";
    if (!u.can_install) {
      act = u.asset ? `<a class="btn" href="${esc(u.asset)}" target="_blank" rel="noopener">Download</a>` : "";
    } else if (u.state === "downloading") {
      sub = `<div class="bar"><i style="width:${Math.round(u.progress * 100)}%"></i></div>`;
      act = `<span class="muted small">Downloading… ${Math.round(u.progress * 100)}%</span>`;
    } else if (u.state === "ready") {
      act = `<button class="btn primary" data-a="install">Install and restart</button>`;
      sub = `<div class="muted small">Closes the engineer (recordings and settings are kept), installs, and starts it again.</div>`;
    } else {
      act = `<button class="btn primary" data-a="download">Download and install</button>`;
      if (u.error) sub = `<div class="small" style="color:var(--loss)">${esc(u.error)}. Try again, or use the link.</div>`;
    }
    const later = u.state === "ready" || u.state === "downloading" ? "" : `<button class="btn" data-a="dismiss">Not now</button>`;
    if (paint(el, `<div class="grow"><b>Race Engineer ${esc(u.latest)} is out</b> <span class="muted">(you have ${esc(u.current)})</span>.${link}${sub}</div>${act}${later}`))
      el.querySelectorAll("[data-a]").forEach(b => b.onclick = () => updateAction(b.dataset.a));
  }
  $("#updVer").textContent = u.current ? "Version " + u.current : "";
  const box = $("#updSettings");
  if (!u.enabled) {
    paint(box, `<p class="muted small" style="margin:0">Running from the project folder: update with <span class="say">git pull</span>. The installed app checks for new versions by itself.</p>`);
    return;
  }
  const state = u.available ? `Version ${esc(u.latest)} is available.` : u.checked ? "You're on the latest version." : "Checking for new versions…";
  if (paint(box, `<label class="small" style="display:flex;gap:10px;align-items:center;margin-bottom:10px">
      <input type="checkbox" id="updAuto" ${u.auto_download ? "checked" : ""}> Download new versions automatically
      <span class="muted">(never during a session; installing is always your click)</span></label>
      <div class="small">${state} <button class="btn" id="updCheck" style="margin-left:8px">Check now</button></div>
      ${u.last_install_failed ? `<div class="small" style="color:var(--loss);margin-top:8px">The last update didn't finish installing. Close Race Engineer and run the setup file from the release page, or send ${esc(u.install_log || "install.log")}.</div>` : ""}`)) {
    $("#updAuto").onchange = e => updateAction("auto", {auto_download: e.target.checked});
    $("#updCheck").onclick = () => updateAction("check");
  }
}

/* ---- lap delta overlay ---- */
const OV_NOTE = {off: "", starting: "opening…", on: "on", moving: "moving", failed: ""};
async function overlayAction(body, path) { S = await post(path || "/api/settings", body); render(); }
function renderOverlay() {
  const o = S.overlay || {available: false}, el = $("#ovSettings");
  $("#ovNote").textContent = OV_NOTE[o.state] ?? "";
  if (!o.available) {
    paint(el, `<p class="muted small" style="margin:0">The overlay sits over the game on a Windows PC. ` +
      (S.console_player ? "For console players a full-screen version is coming." : "It isn't available on this computer.") + `</p>`);
    return;
  }
  const moving = o.state === "moving", live = o.state === "on" || moving;
  const hint = moving ? "Drag it into place, scroll on it to resize, then double-click it or press Lock here."
    : o.state === "starting" ? "Opening the overlay…"
    : o.error ? esc(o.error) + "." : live ? "Locked: clicks go straight through to the game." : "Turn it on to place it.";
  if (paint(el, `<label class="toggle"><input type="checkbox" id="ovOn" role="switch" ${o.enabled ? "checked" : ""}
        ${o.state === "starting" ? "disabled" : ""}> Show my live delta over the game</label>
      <div class="ovrow"><button class="btn${moving ? " primary" : ""}" id="ovMove" ${o.enabled && o.state !== "starting" ? "" : "disabled"}>
        ${moving ? "Lock here" : "Move overlay"}</button><span class="muted small" id="ovHint">${hint}</span></div>
      <div class="tip">Your time against your last clean lap at every point of the lap:
        <b style="color:var(--loss)">red</b> slower, <b style="color:#3ddc84">green</b> quicker, plus what each corner gained or lost.
        Needs F1 25 in <b>Borderless Windowed</b> (in the game's graphics settings): full screen hides it.</div>`)) {
    $("#ovOn").onchange = e => overlayAction({overlay: e.target.checked});
    $("#ovMove").onclick = () => overlayAction({action: moving ? "lock" : "move"}, "/api/overlay");
  }
}

function renderSettings() {
  renderOverlay();
  controllerButtons($("#ctrlSettings"), S.controller.type, setController, S.console_player);
  const del = $("#detailSettings");
  if (paint(del, DETAILS.map(([k, n, d]) => `<button class="opt" data-k="${k}"
    aria-pressed="${k === S.detail}"><b>${n}</b><span>${d}</span></button>`).join("")))
    del.querySelectorAll(".opt").forEach(b => b.onclick = () => setDetail(b.dataset.k));
  renderVoice();
  tempoButtons($("#tempoSettings"), S.default_tempo, setTempo);
  $("#tempoTipSettings").innerHTML = tempoTip();
  $("#telemetry").innerHTML = S.console_player
    ? `In F1 25 on your console: Settings → Telemetry Settings → UDP Telemetry <b>On</b>, UDP IP Address = this PC's IP,
       port <b>${S.connection.port}</b>, format <b>2025</b>, send rate <b>60 Hz</b>.`
    : `In F1 25: Settings → Telemetry Settings → UDP Telemetry <b>On</b>, port <b>${S.connection.port}</b>,
       format <b>2025</b>, send rate <b>60 Hz</b>. Recordings are saved in <span class="muted">${esc(S.recording.folder)}</span>.`;
}

/* ---- welcome (first time only) ---- */
function renderWizard() {
  const ov = $("#welcome"), w = $("#wizard");
  if (S.setup_done) { ov.classList.remove("on"); return; }
  ov.classList.add("on");
  const steps = S.console_player ? ["tempo"] : ["controller", "tempo"];
  const step = steps[Math.min(wizardStep, steps.length - 1)];
  const n = `Step ${steps.indexOf(step) + 1} of ${steps.length}`;
  if (step === "controller") {
    const cur = wizardChoice.controller || null;     // nothing pre-picked: it's their choice
    if (!paint(w, `<div class="step">${n}</div><h2>Which controller do you race with?</h2>
      <p class="lede">You talk to your engineer by holding a shoulder button:
      <span class="btnkey">L1</span> on PlayStation, <span class="btnkey">LB</span> on Xbox.
      Ask things like “how are the tyres?” or “gap ahead?”.</p><div id="wctrl"></div>
      <div class="actions"><span></span><button class="btn primary" id="wnext" ${cur ? "" : "disabled"}>Next</button></div>`)) return;
    controllerButtons($("#wctrl"), cur, k => { wizardChoice.controller = k; renderWizard(); }, false);
    $("#wnext").onclick = async () => { S = await post("/api/settings", {controller: wizardChoice.controller || cur});
      wizardStep = 1; render(); };
  } else {
    const cur = wizardChoice.tempo || S.default_tempo;
    if (!paint(w, `<div class="step">${n}</div><h2>How much should your engineer talk?</h2>
      <p class="lede">Pick a default. You can change it any time here or over the radio.</p>
      <div class="seg" id="wtempo"></div><div class="tip">${tempoTip()}</div>
      <div class="actions">${steps.length > 1 ? `<button class="btn" id="wback">Back</button>` : "<span></span>"}
      <button class="btn primary" id="wdone">Start racing</button></div>`)) return;
    tempoButtons($("#wtempo"), cur, k => { wizardChoice.tempo = k; renderWizard(); });
    if ($("#wback")) $("#wback").onclick = () => { wizardStep = 0; renderWizard(); };
    $("#wdone").onclick = async () => { S = await post("/api/settings", {tempo: cur, setup_done: true}); render(); };
  }
}

function render() {
  if (!S) return;
  renderTop(); renderSession(); renderFeed(); renderCoach(); renderPre(); renderPtt(); renderFeedback();
  renderUploads();
  renderUpdate();
  renderReportHead();
  tempoButtons($("#tempoLive"), S.tempo, setTempo);
  $("#tempoTipLive").innerHTML = tempoTip();
  renderSettings();
  if (!S.setup_done || $("#welcome").classList.contains("on")) renderWizard();
}

async function tick() {
  try { S = await (await fetch("/api/state")).json(); render(); }
  catch (e) { $("#conn").innerHTML = `<span class="dot"></span><span>Engineer stopped — close this window</span>`; }
}

document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.setAttribute("aria-selected", x === t));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("on", v.id === "view-" + t.dataset.view));
  if (t.dataset.view === "report") { reportStamp = S ? S.report.stamp : undefined; loadReport(); }
});
$("#build").onclick = async () => { await post("/api/report/build"); tick(); };
$("#openPdf").onclick = () => window.open("/report/latest.pdf", "_blank");
initPre(); tick(); setInterval(tick, 1000);
</script>
</body></html>
"""


def _tracks_json() -> str:
    import json
    from engineer.ingest.formats.f1_2025 import TRACKS
    from engineer.radio.phrases import say_track
    return json.dumps(sorted(([k, say_track(k)] for k in TRACKS), key=lambda t: t[1]))


def _engineers_json() -> str:
    import json
    from engineer.radio.engineers import ENGINEERS
    accent = {"northern": "Northern", "scottish": "Scottish", "english": "English",
              "southern": "Southern", "american": "American", "intern": "Work experience"}
    return json.dumps([{"key": e.key, "name": e.name, "blurb": e.blurb,
                        "accent": accent.get(e.key, ""), "joke": e.personality is not None}
                       for e in ENGINEERS])


PAGE = PAGE.replace("__TRACKS__", _tracks_json()).replace("__ENGINEERS__", _engineers_json())
