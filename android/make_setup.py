"""Writes android/assets/setup.html: the Android app's setup page, in the same look as the Windows app's (its styles
and mark are taken from app.py, so the two never drift apart). build.py runs this before building the app.

    py android\\make_setup.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import app  # noqa: E402
import instantshare as textshare  # noqa: E402

BODY = """
<div class="wrap">
  <div class="brand">{mark}</div>
  <div id="away" hidden>
    <h1 id="awayTitle"></h1>
    <p class="lead" id="awayText"></p>
    <div class="row"><button class="btn primary" id="retry">Try again</button>
      <button class="btn" id="other">Choose another computer</button></div>
  </div>
  <div id="choose" hidden>
    <h1>Set up Instant Share on this phone</h1>
    <p class="lead">Instant Share keeps its history on one computer on your Wi-Fi. Choose it below: Instant Share must be
    running there, and this phone on the same Wi-Fi.</p>
    <section class="card">
      <h2>The computer that keeps the history</h2>
      <p class="status" id="status"><span class="spin"></span>Looking for Instant Share on your Wi-Fi…</p>
      <div class="found" id="found"></div>
      <button class="link" id="again" hidden>Look again</button>
      <label class="small" for="addr">Not listed? Its name or address:</label>
      <div class="row"><input id="addr" placeholder="e.g. OFFICE-PC or 192.168.1.20" autocomplete="off"
        autocapitalize="off" spellcheck="false"><button class="btn" id="go">Connect</button></div>
      <p class="err" id="err"></p>
    </section>
  </div>
  <div id="pair" hidden>
    <h1>Join Instant Share on <span id="pairHost"></span></h1>
    <p class="lead" id="pairWhy" hidden></p>
    <p class="lead">Each device joins once, with a code. On a device that has joined already (the computer that keeps
    the history, for example), open Settings (the gear) and choose <b>Add a device</b>. Type the code it shows here:</p>
    <section class="card">
      <h2>Pairing code</h2>
      <div class="row"><input id="code" inputmode="numeric" maxlength="7" placeholder="000 000" autocomplete="off"
        style="font: 600 22px/1 ui-monospace, monospace; letter-spacing: .2em; text-align: center">
        <button class="btn primary" id="join">Join</button></div>
      <p class="err" id="perr"></p>
      <button class="link" id="other2">Choose another computer</button>
    </section>
  </div>
  <div class="foot"><span>This phone: <b id="me"></b></span><span>Instant Share {version} · SCAFFApps</span>
    <span style="flex:1"></span><button class="btn" id="back" hidden>Back</button></div>
</div>
<script>
const A = window.TextShareAndroid;
const $ = (s) => document.querySelector(s);
const q = new URLSearchParams(location.search);
let calls = 0;
const waiting = {};
window.textShareDone = (call, answer) => { const r = waiting[call]; delete waiting[call]; if (r) r(answer); };
const ask = (method, ...args) => new Promise((resolve) => { const call = ++calls; waiting[call] = resolve; A[method](call, ...args); });
const info = JSON.parse(A.info());
$("#me").textContent = info.me;
$("#back").hidden = !info.host;
const busy = (on) => document.querySelectorAll("#choose button, #choose input").forEach((b) => { b.disabled = on; });
const fail = (r) => { busy(false); $("#err").textContent = (r && r.error) || "Something went wrong."; };

async function find() {
  $("#again").hidden = true;
  $("#found").textContent = "";
  $("#status").innerHTML = '<span class="spin"></span>Looking for Instant Share on your Wi-Fi…';
  const r = await ask("find");
  const found = (r && r.found) || [];
  $("#status").textContent = found.length ? "Found on your Wi-Fi:"
    : "None found. Is Instant Share running on the computer that keeps the history, and this phone on the same Wi-Fi?";
  for (const f of found) {
    const b = document.createElement("button");
    const left = document.createElement("span"), right = document.createElement("small");
    left.textContent = f.name;
    right.textContent = (info.host && info.host.toLowerCase() === f.name.toLowerCase() ? "connected · " : "") + f.address;
    b.append(left, right);
    b.addEventListener("click", async () => {
      busy(true); $("#err").textContent = "";
      const res = await ask("join", f.name, f.address, f.port);
      if (!res.ok) fail(res);
      else if (res.pair) pairing(res.host, "");
    });
    $("#found").append(b);
  }
  $("#again").hidden = false;
}

function choose() { $("#away").hidden = true; $("#choose").hidden = false; find(); }
$("#again").addEventListener("click", find);
const go = async () => {
  if (!$("#addr").value.trim()) { $("#addr").focus(); return; }
  busy(true); $("#err").textContent = "";
  const res = await ask("connect", $("#addr").value);
  if (!res.ok) { fail(res); $("#addr").focus(); }
  else if (res.pair) pairing(res.host, "");
};

/* joining: the code shown on a device that has joined already (the phone checks it without sending it) */
function pairing(host, why) {
  busy(false);
  $("#away").hidden = $("#choose").hidden = true;
  $("#pair").hidden = false;
  $("#pairHost").textContent = host || JSON.parse(A.info()).host || "the computer";
  $("#pairWhy").textContent = why || ""; $("#pairWhy").hidden = !why;
  $("#perr").textContent = "";
  setTimeout(() => $("#code").focus(), 50);
}
$("#code").addEventListener("input", (e) => {
  const d = e.target.value.replace(/\\D/g, "").slice(0, 6);
  e.target.value = d.length > 3 ? d.slice(0, 3) + " " + d.slice(3) : d;
});
const join = async () => {
  const c = $("#code").value.replace(/\\D/g, "");
  if (c.length !== 6) { $("#perr").textContent = "The code has 6 digits."; return; }
  $("#join").disabled = true; $("#join").textContent = "Checking…"; $("#perr").textContent = "";
  const res = await ask("pair", c);
  if (!res.ok) { $("#perr").textContent = res.error || "Could not join."; $("#join").disabled = false; $("#join").textContent = "Join"; $("#code").select(); }
};
$("#join").addEventListener("click", join);
$("#code").addEventListener("keydown", (e) => { if (e.key === "Enter") join(); });
$("#other2").addEventListener("click", () => { $("#pair").hidden = true; choose(); });
$("#go").addEventListener("click", go);
$("#addr").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
$("#back").addEventListener("click", () => A.back());
$("#retry").addEventListener("click", () => { $("#retry").disabled = true; $("#retry").textContent = "Trying…"; A.retry(); });
$("#other").addEventListener("click", choose);

if (q.get("pair") && info.host) {               // set up, but not joined (yet, or any more)
  pairing(info.host, q.get("why") || "");
} else if (q.get("unreachable") && info.host) {        // set up, but the computer does not answer: say so, try again by itself
  $("#awayTitle").textContent = "Can't reach " + info.host;
  $("#awayText").textContent = "Instant Share must be running on " + info.host + ", and this phone must be on the same "
    + "Wi-Fi. Trying again every few seconds…";
  $("#away").hidden = false;
  setTimeout(() => A.retry(), 6000);
} else {
  choose();
}
</script>
"""

EXTRA_STYLE = """
.wrap { padding-top: 8px }
.card { padding: 18px }
.foot { flex-wrap: wrap }
@media (max-width: 520px) { body { justify-content: flex-start; padding: 28px 18px } h1 { font-size: 21px } }
"""


def main():
    body = BODY.replace("{mark}", app.MARK).replace("{version}", textshare.VERSION)
    page = ('<!DOCTYPE html><html lang="en" data-theme="dark"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Instant Share</title>'
            '<script>document.documentElement.dataset.theme = new URLSearchParams(location.search).get("theme") === "light"'
            ' ? "light" : "dark";</script>'
            f"<style>{app.PAGE_STYLE}{EXTRA_STYLE}</style></head><body>{body}</body></html>")
    out = HERE / "assets" / "setup.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
