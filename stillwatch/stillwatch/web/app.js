/* Stillwatch dashboard.

   One bundle per day arrives from /api/day and everything on screen is drawn
   from it. Moving the slider only changes which reading is shown, so scrubbing
   never waits on the network. */

(function () {
  "use strict";

  var DAY = 1440;
  var ANCHOR_LIMIT = 6;
  var DIAL_LENGTH = 254.5;

  // What a caregiver can say when they have looked. Until one of these is
  // chosen, the only thing that ends an episode is movement, which is no help
  // to somebody who has already phoned and found everyone well.
  var ANSWERS = [
    ["fine", "All is well"],
    ["away", "She was out"],
    ["expected", "This is normal now"],
    ["helped", "Dealt with"]
  ];

  var SAID = {
    fine: "You said all was well.",
    away: "You said she was out, not still.",
    expected: "You said this is normal for her now. It has been added to what Stillwatch expects.",
    helped: "You said it was dealt with."
  };
  // The same four answers in the third person, for everybody who did not
  // press the button themselves.
  var SAID_BY = {
    fine: "checked, and all was well.",
    away: "said she was out, not still.",
    expected: "said this is normal for her now, and Stillwatch has taken it on.",
    helped: "said it was dealt with."
  };

  var MODE_WORDS = {
    members: "Sign in with your own name and your own code.",
    shared: "Sign in with the household code. Your name is kept with anything you answer."
  };

  var THEME_KEY = "stillwatch.theme";

  var WORDS = {
    NORMAL: "All normal",
    QUIET: "Quiet",
    CONCERN: "Concern",
    ALERT: "Needs checking",
    AWAY: "Out",
    UNKNOWN: "Cannot tell",
    SETTLED: "Checked"
  };

  var TINT = {
    NORMAL: "--normal",
    QUIET: "--quiet",
    CONCERN: "--concern",
    ALERT: "--alert",
    AWAY: "--away",
    UNKNOWN: "--unknown",
    SETTLED: "--settled"
  };

  var UNKNOWN_WHY = {
    blind: "Every camera that matters is offline, so there is nothing to judge.",
    no_history: "There is not enough history yet to know what normal looks like.",
    no_baseline: "No rhythm has been learned for this hour yet.",
    no_contact: "Nothing has reached Stillwatch from the cameras, so there is nothing to read."
  };

  var $ = function (id) { return document.getElementById(id); };

  var ui = {
    app: $("app"), empty: $("empty"), home: $("home"), theme: $("theme"),
    livePill: $("livePill"), dayField: $("dayField"), scenario: $("scenario"),
    who: $("who"), openNotice: $("openNotice"), gate: $("gate"), gateForm: $("gateForm"),
    gateName: $("gateName"), gatePass: $("gatePass"), gateGo: $("gateGo"),
    gateNote: $("gateNote"),
    onField: $("onField"), onDate: $("onDate"),
    standby: $("standby"), facts: $("facts"), seeDemo: $("seeDemo"), banner: $("banner"),
    hero: $("hero"), stateWord: $("stateWord"), heroClock: $("heroClock"),
    statement: $("statement"), reasons: $("reasons"),
    answer: $("answer"), answerAsk: $("answerAsk"),
    answerButtons: $("answerButtons"),
    meterLabel: $("meterLabel"), meterValue: $("meterValue"),
    fill: $("fill"), track: $("track"), meterFoot: $("meterFoot"), glance: $("glance"),
    messages: $("messages"), messageCount: $("messageCount"),
    play: $("play"), playIcon: $("playIcon"), playLabel: $("playLabel"),
    speed: $("speed"), clock: $("clock"),
    chart: $("chart"), ribbon: $("ribbon"), tracks: $("tracks"), needle: $("needle"),
    time: $("time"), quietbars: $("quietbars"), anchors: $("anchors"),
    learnedFrom: $("learnedFrom"), devices: $("devices"), footnote: $("footnote")
  };

  var day = null;
  var entries = [];
  var minute = 0;
  var timer = null;
  var refresher = null;
  var following = true;
  var viewing = null;
  var session = { locked: false, mode: "open", name: null };

  /* ---------- small helpers ---------- */

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function param(name) {
    return new URLSearchParams(location.search).get(name);
  }

  function pct(value) {
    return Math.max(0, Math.min(100, (value / DAY) * 100));
  }

  // A silence that began before midnight shows up as a negative minute of
  // this day. "00:00" for something that happened at half past ten yesterday
  // morning is not a rounding problem, it is the wrong answer.
  function whenOf(mins) {
    if (mins === null || mins === undefined) { return "none today"; }
    if (mins >= 0) { return clockOf(mins); }
    var back = Math.ceil(-mins / DAY);
    var into = mins + back * DAY;
    return clockOf(into) + (back === 1 ? " yesterday" : " " + back + " days ago");
  }

  function clockOf(mins) {
    // Floored, not rounded. The engine truncates when it writes a time into a
    // sentence, and a glance saying 08:54 beside a reason saying 08:53 reads
    // as two different events.
    var whole = Math.max(0, Math.min(DAY - 1, Math.floor(mins)));
    var hh = Math.floor(whole / 60);
    var mm = whole % 60;
    return (hh < 10 ? "0" : "") + hh + ":" + (mm < 10 ? "0" : "") + mm;
  }

  function firstLine(text) {
    return String(text || "").split(/\r?\n/)[0];
  }

  function sentenceCase(text) {
    var value = String(text || "");
    return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
  }

  // The same wording the engine uses, so a counting number never disagrees
  // with the sentence above it.
  function humanDuration(seconds) {
    if (seconds === null || seconds === undefined) { return "unknown"; }
    var minutes = Math.round(seconds / 60);
    if (minutes < 60) { return minutes + " min"; }
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    if (rest === 0) { return hours + "h"; }
    return hours + "h " + (rest < 10 ? "0" : "") + rest + "m";
  }

  /* ---------- movement that carries meaning ---------- */

  var calmly = !matchMedia("(prefers-reduced-motion: reduce)").matches;
  var lastSeconds = null;
  var counting = null;
  var countGuard = null;
  var sweepTimer = null;

  function countTo(node, seconds, text) {
    if (counting) { cancelAnimationFrame(counting); counting = null; }
    clearTimeout(countGuard);

    var jump = lastSeconds === null || seconds === null || seconds === undefined
      || Math.abs(seconds - lastSeconds) < 60;
    if (!calmly || timer !== null || jump) {
      node.textContent = text;
      lastSeconds = seconds;
      return;
    }

    var from = lastSeconds;
    var span = seconds - from;
    var began = performance.now();

    function land() {
      if (counting) { cancelAnimationFrame(counting); counting = null; }
      clearTimeout(countGuard);
      node.textContent = text;
      lastSeconds = seconds;
    }

    function step(now) {
      var share = Math.min(1, (now - began) / 460);
      if (share >= 1) { land(); return; }
      node.textContent = humanDuration(from + span * (1 - Math.pow(1 - share, 3)));
      counting = requestAnimationFrame(step);
    }

    counting = requestAnimationFrame(step);
    // Frames stop arriving when the tab is not being drawn, which would leave
    // the old number on screen. Timers keep running, so one lands it anyway.
    countGuard = setTimeout(land, 700);
  }

  function settle(quietly) {
    if (quietly || !calmly) {
      ui.app.setAttribute("data-ready", "1");
      return;
    }
    ui.app.removeAttribute("data-ready");
    void ui.app.offsetWidth;
    ui.app.setAttribute("data-ready", "1");
  }

  function sweep(quietly) {
    // A live refresh must not redraw the day every minute.
    if (quietly || !calmly) { return; }
    ui.chart.removeAttribute("data-draw");
    void ui.chart.offsetWidth;
    ui.chart.setAttribute("data-draw", "1");
    clearTimeout(sweepTimer);
    sweepTimer = setTimeout(function () {
      ui.chart.removeAttribute("data-draw");
    }, 1700);
  }

  function entryFor(key) {
    for (var i = 0; i < entries.length; i += 1) {
      if (entries[i].key === key) { return entries[i]; }
    }
    return null;
  }

  /* ---------- light and dark ---------- */

  function theme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (error) { saved = null; }
    if (saved === "light" || saved === "dark") {
      document.documentElement.dataset.theme = saved;
    }
    ui.theme.addEventListener("click", function () {
      var dark = document.documentElement.dataset.theme
        ? document.documentElement.dataset.theme === "dark"
        : matchMedia("(prefers-color-scheme: dark)").matches;
      var next = dark ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem(THEME_KEY, next); } catch (error) { /* private window */ }
    });
  }

  /* ---------- loading ---------- */

  function showGate(why) {
    ui.gate.hidden = false;
    if (why) { ui.gateNote.textContent = why; }
  }

  function paintWho() {
    ui.openNotice.hidden = session.locked;
    if (!session.locked) {
      ui.openNotice.textContent = "";
      ui.openNotice.appendChild(el("strong", null, "No passcode is set."));
      ui.openNotice.appendChild(document.createTextNode(
        " Anyone who knows this address can see when this home is empty and can"
        + " answer on the family's behalf. Set STILLWATCH_PASSCODE before a real"
        + " household is put behind it."));
    }
    if (session.locked && !session.name) {
      ui.gateNote.textContent = MODE_WORDS[session.mode] || MODE_WORDS.shared;
    }
    ui.who.hidden = !(session.locked && session.name);
    if (session.name) {
      ui.who.textContent = session.name;
      ui.who.title = "Signed in as " + session.name + ". Click to sign out.";
    }
    ui.gate.hidden = !(session.locked && !session.name);
  }

  function signIn(event) {
    event.preventDefault();
    var name = ui.gateName.value;
    ui.gateGo.disabled = true;
    ui.gateGo.textContent = "Signing in";

    fetch("/api/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, passcode: ui.gatePass.value })
    }).then(function (reply) {
      return reply.json().then(function (body) { return { ok: reply.ok, body: body }; });
    }).then(function (result) {
      ui.gateGo.disabled = false;
      ui.gateGo.textContent = "Sign in";
      if (!result.ok) {
        ui.gateNote.textContent = result.body.error || "That did not work.";
        ui.gateName.focus();
        ui.gatePass.value = "";
        ui.gatePass.focus();
        return;
      }
      // Start again from a clean page rather than booting a second time on
      // top of the first. Booting twice filled the day picker with every day
      // listed twice and left two refresh timers running.
      ui.gatePass.value = "";
      location.reload();
    }).catch(function () {
      ui.gateGo.disabled = false;
      ui.gateGo.textContent = "Sign in";
      ui.gateNote.textContent = "Could not reach Stillwatch. Try again.";
    });
  }

  function signOut() {
    fetch("/api/session", { method: "DELETE" }).then(function () {
      location.reload();
    });
  }

  function asSentence(text) {
    text = String(text || "").trim();
    if (!text) { return text; }
    text = text.charAt(0).toUpperCase() + text.slice(1);
    return /[.!?]$/.test(text) ? text : text + ".";
  }


  function fail(message) {
    ui.app.setAttribute("aria-busy", "false");
    // The opening line is a placeholder for the moment before any data has
    // arrived. If none is coming, it must not sit there claiming to be
    // reading something.
    ui.statement.textContent = "";
    ui.stateWord.textContent = "";
    ui.empty.hidden = false;
    ui.empty.textContent = message;
  }

  function boot() {
    theme();

    Promise.all([
      fetch("/api/scenarios").then(function (reply) { return reply.json(); }),
      fetch("/api/health").then(function (reply) { return reply.json(); })
        .catch(function () { return {}; })
    ]).then(function (both) {
      var index = both[0];
      var health = both[1] || {};

      session.locked = Boolean(health.locked);
      session.mode = health.mode || "shared";
      session.name = health.name || null;
      paintWho();

      ui.home.textContent = index.persona || "The household";
      entries = index.scenarios || [];

      if (!entries.length) {
        fail("No days to show yet. Once cameras are connected this fills in on its own.");
        return;
      }

      var liveEntry = entryFor("live");
      ui.dayField.hidden = entries.length < 2;

      entries.forEach(function (entry) {
        var option = el("option", null,
          (entry.title || entry.key) + (entry.demo ? " (demonstration)" : ""));
        option.value = entry.key;
        ui.scenario.appendChild(option);
      });

      var wanted = param("scenario");
      var chosen = entryFor(wanted) ? wanted : entries[0].key;

      // Locked out and asking for the household gets you the recorded days
      // instead, rather than an error page with nothing on it.
      if (session.locked && !session.name) {
        var open = entries.filter(function (entry) { return entry.demo; });
        if (open.length) { chosen = open[0].key; }
      }
      ui.scenario.value = chosen;

      // The badge says what is on the screen, not what the server happens to
      // have. It used to appear whenever a live day existed at all, so the
      // header read "Live" above a recorded day that says in its own banner
      // that nothing in it came from this home.
      ui.livePill.hidden = !(liveEntry && chosen === "live");

      // A home connected this morning has nothing to show. Say so properly
      // rather than drawing six empty boxes.
      if (liveEntry && chosen === "live" && !health.stored_events && !viewing) {
        standby(health);
        return;
      }

      load(chosen);
      if (liveEntry) {
        ui.onField.hidden = false;
        syncDayPicker();
        if (!refresher) {
          refresher = setInterval(function () {
            // A finished day cannot change, so only today is refreshed.
            if (!viewing || viewing === householdToday()) {
              load(ui.scenario.value, true);
            }
          }, 60000);
        }
      }
    }).catch(function () {
      fail("Could not reach Stillwatch. Is the service running?");
    });
  }

  function standby(health) {
    ui.app.dataset.mode = "standby";
    ui.app.setAttribute("aria-busy", "false");
    ui.standby.hidden = false;
    ui.empty.hidden = true;
    settle(false);
    document.documentElement.style.setProperty("--state", "var(--normal)");

    var rows = [
      ["Service", health.ok ? "running" : "unreachable", health.ok],
      ["Webhook", health.webhook ? "ready" : "no key yet", health.webhook],
      ["Ring account", health.ring_linked ? "linked" : "not linked yet", health.ring_linked],
      ["Events stored", String(health.stored_events || 0), Boolean(health.stored_events)]
    ];

    ui.facts.textContent = "";
    rows.forEach(function (row) {
      var box = el("div", "fact");
      box.dataset.good = row[2] ? "1" : "0";
      box.appendChild(el("dt", null, row[0]));
      box.appendChild(el("dd", null, row[1]));
      ui.facts.appendChild(box);
    });

    var demos = entries.filter(function (entry) { return entry.demo; });
    ui.seeDemo.hidden = !demos.length;
  }

  function leaveStandby() {
    ui.app.dataset.mode = "";
    ui.standby.hidden = true;
  }

  function syncDayPicker() {
    if (ui.onField.hidden) { return; }
    var today = householdToday();
    ui.onDate.max = today;
    if (!viewing) { ui.onDate.value = today; }
  }


  function load(key, quietly) {
    var entry = entryFor(key);
    leaveStandby();

    ui.banner.hidden = !(entry && entry.demo);
    if (entry && entry.demo) {
      ui.banner.textContent = "";
      ui.banner.appendChild(el("strong", null, "A recorded day."));
      ui.banner.appendChild(document.createTextNode(
        " Shown so you can see how Stillwatch reads one. Nothing here came from this home."));
    }

    if (!quietly) {
      ui.app.setAttribute("aria-busy", "true");
      lastSeconds = null;
      stop();
    }

    var query = "/api/day?scenario=" + encodeURIComponent(key);
    if (viewing) { query += "&on=" + encodeURIComponent(viewing); }

    fetch(query)
      .then(function (reply) {
        if (reply.status === 401) {
          session.locked = true;
          session.name = null;
          paintWho();
          showGate("Sign in to see this household.");
          throw new Error("locked");
        }
        if (!reply.ok) {
          // The service says why in the body. Throwing that away and
          // reporting "could not load" turns a clear answer about this
          // household, such as asking for a day before it had any history,
          // into something that reads like a broken website.
          return reply.json().then(null, function () { return {}; })
            .then(function (body) {
              var stop = new Error(body.error || "no day");
              stop.explained = Boolean(body.error);
              throw stop;
            });
        }
        return reply.json();
      })
      .then(function (bundle) {
        day = bundle;
        syncDayPicker();
        ui.empty.hidden = true;
        draw();
        ui.app.setAttribute("aria-busy", "false");
        settle(quietly);
        sweep(quietly);
      })
      .catch(function (error) {
        if (String(error && error.message) === "locked") {
          ui.app.setAttribute("aria-busy", "false");
          return;
        }
        fail(error && error.explained
          ? asSentence(error.message)
          : "Could not load that day.");
      });
  }

  /* ---------- drawing the parts that do not move ---------- */

  function draw() {
    var readings = day.readings || [];
    if (!readings.length) {
      fail(day.live
        ? "Connected and waiting. Nothing has come through from the cameras yet today."
        : "Nothing recorded for this day.");
      return;
    }

    var last = readings[readings.length - 1].minute;
    ui.time.max = String(Math.round(last));
    ui.time.step = String(day.step_minutes || 5);

    drawRibbon(readings);
    drawTracks();
    drawMessages();
    drawQuiet();
    drawDevices();
    drawDialTicks();
    drawFootnote();

    var wanted = param("t");
    var start = last;
    if (wanted && /^\d{1,2}:\d{2}$/.test(wanted)) {
      var bits = wanted.split(":");
      start = Math.min(last, Number(bits[0]) * 60 + Number(bits[1]));
    } else if (!day.is_today && day.notifications && day.notifications.length) {
      start = day.notifications[0].minute;
    }
    show(following || !day.is_today ? start : minute);
  }

  function drawRibbon(readings) {
    ui.ribbon.textContent = "";
    var step = day.step_minutes || 5;
    var run = null;

    readings.forEach(function (reading) {
      if (run && run.state === reading.state) {
        run.end = reading.minute + step;
        return;
      }
      if (run) { ui.ribbon.appendChild(segment(run)); }
      run = { state: reading.state, start: reading.minute, end: reading.minute + step };
    });
    if (run) { ui.ribbon.appendChild(segment(run)); }
  }

  function segment(run) {
    var node = el("span", "seg");
    node.dataset.state = run.state;
    node.style.animationDelay = (pct(run.start) / 100) * 0.55 + "s";
    node.style.left = pct(run.start) + "%";
    // 2px of surface between neighbours, rather than a border around each.
    node.style.width = "calc(" + Math.max(0.12, pct(run.end) - pct(run.start)) + "% - 2px)";
    node.title = WORDS[run.state] + ", " + clockOf(run.start) + " to " + clockOf(run.end);
    return node;
  }

  function drawTracks() {
    ui.tracks.textContent = "";
    var today = day.today || {};
    var dings = day.dings || [];
    var outages = day.outages || [];

    (day.devices || []).forEach(function (device) {
      var row = el("div", "trk");
      row.dataset.device = device.device_id;

      var name = el("span", "trk-name");
      name.appendChild(el("span", null, device.name));
      name.appendChild(el("em", null, device.zone_class === "transit" ? "way out" : "inside"));
      row.appendChild(name);

      var rail = el("div", "rail");

      outages.forEach(function (span) {
        if (span.device_id !== device.device_id) { return; }
        var off = el("span", "off");
        off.style.animationDelay = (pct(span.from) / 100) * 0.7 + 0.2 + "s";
        off.style.left = pct(span.from) + "%";
        off.style.width = Math.max(0.15, pct(span.to) - pct(span.from)) + "%";
        off.title = "Offline " + clockOf(span.from) + " to " + clockOf(span.to);
        rail.appendChild(off);
      });

      (today[device.device_id] || []).forEach(function (at) {
        var tick = el("span", "tick");
        tick.style.animationDelay = (pct(at) / 100) * 0.7 + 0.2 + "s";
        tick.style.left = pct(at) + "%";
        tick.title = "Movement at " + clockOf(at);
        rail.appendChild(tick);
      });

      dings.forEach(function (ding) {
        if (ding.device_id !== device.device_id) { return; }
        var mark = el("span", "ding");
        mark.style.animationDelay = (pct(ding.minute) / 100) * 0.7 + 0.2 + "s";
        mark.style.left = pct(ding.minute) + "%";
        mark.title = "Door at " + clockOf(ding.minute);
        rail.appendChild(mark);
      });

      row.appendChild(rail);
      ui.tracks.appendChild(row);
    });
  }

  function drawMessages() {
    ui.messages.textContent = "";
    var notices = day.notifications || [];

    ui.messageCount.textContent = notices.length ? notices.length + " sent" : "";

    if (!notices.length) {
      ui.messages.appendChild(el("p", "msg-none",
        "Nothing was worth sending. A quiet day means a silent phone."));
      return;
    }

    notices.forEach(function (notice) {
      var card = el("div", "msg");
      card.dataset.u = notice.urgency;

      var top = el("div", "msg-top");
      top.appendChild(el("span", "msg-time", clockOf(notice.minute)));
      top.appendChild(el("span", "msg-tag", notice.urgency));
      card.appendChild(top);

      card.appendChild(el("p", "msg-subject",
        sentenceCase(String(notice.subject || "").replace(/^Stillwatch:\s*/, ""))));
      card.appendChild(el("p", "msg-body", firstLine(notice.body)));

      card.tabIndex = 0;
      card.addEventListener("click", function () { stop(); show(notice.minute); });
      card.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          stop();
          show(notice.minute);
        }
      });

      ui.messages.appendChild(card);
    });
  }

  // Where quiet turns into concern, and concern into needing somebody. Drawn
  // once, from the same numbers the engine judges on.
  function drawDialTicks() {
    var ladder = day.ladder || {};
    var alertAt = ladder.alert_at || 2.5;
    ui.track.textContent = "";

    [ladder.quiet_at || 1, ladder.concern_at || 1.5].forEach(function (level) {
      var turn = (135 + 270 * Math.min(1, level / alertAt)) * Math.PI / 180;
      var line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", (70 + 48 * Math.cos(turn)).toFixed(1));
      line.setAttribute("y1", (70 + 48 * Math.sin(turn)).toFixed(1));
      line.setAttribute("x2", (70 + 60 * Math.cos(turn)).toFixed(1));
      line.setAttribute("y2", (70 + 60 * Math.sin(turn)).toFixed(1));
      ui.track.appendChild(line);
    });
  }

  function drawQuiet() {
    ui.quietbars.textContent = "";
    var quiet = (day.baseline || {}).quiet || [];
    var text = (day.baseline || {}).quiet_text || [];
    var top = Math.max.apply(null, quiet.concat([1]));

    quiet.forEach(function (seconds, hour) {
      var bar = el("div", "bar");
      // Square root keeps a half hour of daytime quiet visible next to eight
      // hours of night. The exact figure is on the bar itself.
      var height = top > 0 ? Math.sqrt(seconds / top) * 100 : 0;
      bar.style.height = Math.max(2, height) + "%";
      bar.dataset.hour = String(hour);
      bar.title = clockOf(hour * 60) + " onwards, up to " + (text[hour] || "no data");
      ui.quietbars.appendChild(bar);
    });
  }

  function drawDevices() {
    var rhythm = (day.baseline || {}).rhythm || {};
    ui.devices.textContent = "";

    (day.devices || []).forEach(function (device) {
      var row = el("li");
      row.dataset.device = device.device_id;
      row.dataset.zone = device.zone_class;
      row.appendChild(el("span", "dev-dot"));
      row.appendChild(el("span", "dev-name", device.name));
      row.appendChild(rhythmOf(rhythm[device.device_id] || [], device.name));
      row.appendChild(el("span", "dev-zone",
        device.zone_class === "transit" ? "way out" : "inside"));
      ui.devices.appendChild(row);
    });
  }

  // How often this one camera sees her, hour by hour, out of her own history.
  // Scaled against its own busiest hour rather than against the whole house,
  // because a quiet hallway and a busy kitchen are each worth seeing in shape.
  function rhythmOf(hours, name) {
    var strip = el("span", "dev-rhythm");
    var top = 0;
    hours.forEach(function (rate) { top = Math.max(top, rate); });
    strip.title = top
      ? name + ", busiest around " + clockOf(hours.indexOf(top) * 60)
      : name + " rarely sees anyone at any hour";

    for (var hour = 0; hour < 24; hour += 1) {
      var rate = hours[hour] || 0;
      var tick = el("i");
      tick.style.height = (top ? Math.max(6, (rate / top) * 100) : 6) + "%";
      if (top && rate >= top * 0.75) { tick.dataset.peak = "1"; }
      tick.style.opacity = top ? String(0.35 + 0.65 * (rate / top)) : "0.18";
      strip.appendChild(tick);
    }
    return strip;
  }

  // Times belong to the house, never to whoever is looking. A daughter in
  // Denmark reading "no movement since 23:00" needs that to mean eleven at
  // night in her mother's home, so the only thing translated for the viewer
  // is the difference between the two clocks.
  // The day picker belongs to the house as well. It used to read the viewer's
  // UTC date, which is not even the viewer's own date. A household in Douala
  // therefore saw yesterday for the hour after its own midnight, and one in
  // Tokyo for nine hours of every day, with today unreachable because the
  // picker is capped at the same wrong value.
  function householdToday() {
    var shift = (day && typeof day.utc_offset_minutes === "number")
      ? day.utc_offset_minutes
      : -new Date().getTimezoneOffset();
    return new Date(Date.now() + shift * 60000).toISOString().slice(0, 10);
  }


  function clockNote() {
    var here = -new Date().getTimezoneOffset();
    var there = day.utc_offset_minutes;
    if (there === null || there === undefined) {
      return "Times are the household's own clock.";
    }
    var gap = there - here;
    if (gap === 0) {
      return "Times are the household's own clock, which matches yours.";
    }
    var hours = Math.floor(Math.abs(gap) / 60);
    var mins = Math.abs(gap) % 60;
    var span = (hours ? hours + (hours === 1 ? " hour" : " hours") : "")
      + (hours && mins ? " " : "")
      + (mins ? mins + " min" : "");
    return "Times are the household's own clock, " + span
      + (gap > 0 ? " ahead of yours." : " behind yours.");
  }

  function drawFootnote() {
    var observed = 0;
    var days = (day.baseline || {}).days_observed || {};
    Object.keys(days).forEach(function (key) { observed += days[key]; });

    var clause = [day.weekday + " " + day.day];
    if (observed) { clause.push("judged against " + observed + " days of her own history"); }
    ui.footnote.textContent = (day.live
      ? "Live from the cameras themselves"
      : "Replaying " + String(day.scenario.title || day.scenario.key).toLowerCase())
      + ", " + clause.join(", ") + ". " + clockNote();

    ui.learnedFrom.textContent = observed
      ? "Built from " + observed + " days of her own routine. Time she spent out of the "
        + "house does not count towards it, so a long afternoon at the market never "
        + "becomes what Stillwatch expects of a quiet morning."
      : "";
  }

  /* ---------- the part that moves ---------- */

  function readingAt(mins) {
    var readings = day.readings;
    var best = readings[0];
    for (var i = 0; i < readings.length; i += 1) {
      if (readings[i].minute <= mins) { best = readings[i]; } else { break; }
    }
    return best;
  }

  function show(mins) {
    if (!day || !day.readings || !day.readings.length) { return; }
    var last = day.readings[day.readings.length - 1].minute;
    minute = Math.max(0, Math.min(last, mins));
    ui.time.value = String(Math.round(minute));
    ui.clock.textContent = clockOf(minute);
    ui.needle.style.left = pct(minute) + "%";
    paint(readingAt(minute));
  }

  function paint(reading) {
    var state = reading.state;
    document.documentElement.style.setProperty("--state", "var(" + TINT[state] + ")");
    ui.hero.dataset.state = state;
    ui.stateWord.textContent = WORDS[state] || state;
    ui.heroClock.textContent = "as at " + clockOf(reading.minute);

    ui.statement.textContent = reading.headline;

    ui.reasons.textContent = "";
    (reading.reasons || []).forEach(function (why) {
      ui.reasons.appendChild(el("li", null, sentenceCase(why)));
    });

    paintAnswer(reading);
    paintMeter(reading);
    paintAnchors(reading);
    paintDevices(reading);

    var began = reading.began_minute === null || reading.began_minute === undefined
      ? Math.floor(reading.minute / 60)
      : Math.floor(reading.began_minute / 60);
    Array.prototype.forEach.call(ui.quietbars.children, function (bar) {
      bar.dataset.now = Number(bar.dataset.hour) === began ? "1" : "0";
    });
  }

  function paintAnswer(reading) {
    var episode = reading.silence_began;
    var worrying = reading.state === "CONCERN" || reading.state === "ALERT"
      || reading.state === "SETTLED";
    if (!episode || !worrying || !day.is_today) {
      ui.answer.hidden = true;
      return;
    }

    ui.answer.hidden = false;
    var said = (day.answers || {})[episode];
    ui.answerButtons.textContent = "";

    if (said) {
      ui.answerAsk.textContent = said.by
        ? said.by + " " + (SAID_BY[said.outcome] || "answered this.")
        : (SAID[said.outcome] || "You have answered this.");
      return;
    }

    if (session.locked && !session.name) {
      ui.answerAsk.textContent = "Sign in to answer for this household.";
      return;
    }

    ui.answerAsk.textContent = "Have you been able to check on " + (day.persona || "her") + "?";
    ANSWERS.forEach(function (pair) {
      var button = el("button", "answer-button", pair[1]);
      button.type = "button";
      button.addEventListener("click", function () { answerWith(episode, pair[0], button); });
      ui.answerButtons.appendChild(button);
    });
  }

  function answerWith(episode, outcome, button) {
    Array.prototype.forEach.call(ui.answerButtons.children, function (other) {
      other.disabled = true;
    });
    button.textContent = "Saving";

    fetch("/api/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ episode: episode, outcome: outcome })
    }).then(function (reply) {
      if (!reply.ok) { throw new Error("refused"); }
      return reply.json();
    }).then(function () {
      // Read the whole day again rather than patching the screen. The answer
      // changes what the engine says, not just what this panel shows: the
      // state, the colour, the band on the chart and what the family were
      // told all move together.
      day.answers = day.answers || {};
      day.answers[episode] = { episode: episode, outcome: outcome, by: session.name };
      ui.answerAsk.textContent = SAID[outcome] || "Thank you.";
      ui.answerButtons.textContent = "";
      load(ui.scenario.value, true);
    }).catch(function () {
      button.textContent = "Could not save, try again";
      Array.prototype.forEach.call(ui.answerButtons.children, function (other) {
        other.disabled = false;
      });
    });
  }

  function paintMeter(reading) {
    var alertAt = (day.ladder || {}).alert_at || 2.5;
    var out = reading.state === "AWAY";

    ui.meterLabel.textContent = out ? "Out for" : "Still for";
    countTo(ui.meterValue, reading.silence_seconds,
            reading.silence_text || "no time at all");

    var share = reading.ratio === null || reading.ratio === undefined
      ? 0
      : Math.min(1, reading.ratio / alertAt);
    // The arc is 270 degrees of a 54 unit circle, so 254.5 units long. Showing
    // it is a matter of pulling its dash back by the part that is not reached.
    ui.fill.style.strokeDashoffset = String(DIAL_LENGTH * (1 - share));

    var foot = [];
    if (reading.state === "UNKNOWN") {
      foot.push(UNKNOWN_WHY[reading.unknown_reason] || "Not enough to go on yet.");
    } else if (out) {
      foot.push(reading.departure_at
        ? "A door opened as the quiet began, so this is time out of the house."
        : "Away from home.");
      if (reading.absence_unusual) { foot.push("Longer than she is usually out."); }
    } else if (reading.threshold_text) {
      foot.push("Normally up to " + reading.threshold_text + " at this hour.");
    }
    if (reading.capped_by_outage && reading.last_device_name) {
      foot.push("Held back because the " + reading.last_device_name + " camera cannot see.");
    }
    ui.meterFoot.textContent = foot.join(" ");

    var down = (reading.devices_down || []).length;
    var total = (day.devices || []).length;
    var rows = [
      ["Last movement", reading.silence_began ? whenOf(reading.began_minute) : "none today"],
      ["Where", reading.last_device_name || "nowhere yet"],
      ["Cameras", down ? (total - down) + " of " + total + " watching" : total + " watching"]
    ];

    ui.glance.textContent = "";
    rows.forEach(function (row) {
      var item = el("div", "glance-row");
      item.appendChild(el("dt", null, row[0]));
      item.appendChild(el("dd", null, row[1]));
      ui.glance.appendChild(item);
    });
  }

  function paintAnchors(reading) {
    var anchors = (day.baseline || {}).anchors || [];
    var missed = {};
    (reading.missed_anchors || []).forEach(function (item) {
      missed[item.device_id + "|" + item.window] = true;
    });

    ui.anchors.textContent = "";
    if (!anchors.length) {
      ui.anchors.appendChild(el("li", null, "No habits learned yet."));
      return;
    }

    var isMissed = function (a) { return Boolean(missed[a.device_id + "|" + a.window]); };
    var ordered = anchors.filter(isMissed).concat(anchors.filter(function (a) {
      return !isMissed(a);
    }));
    var shown = ordered.slice(0, ANCHOR_LIMIT);

    shown.forEach(function (anchor) {
      var row = el("li");
      row.dataset.missed = isMissed(anchor) ? "1" : "0";
      row.appendChild(el("span", "anchor-time", anchor.usual));

      var what = el("span", "anchor-what");
      what.appendChild(document.createTextNode(anchor.where || anchor.sentence));
      if (row.dataset.missed === "1") {
        what.appendChild(el("span", "anchor-flag", "not seen yet"));
      }
      what.appendChild(el("small", null,
        Math.round(anchor.hit_rate * 100) + "% of " + anchor.observed_days + " days"));
      row.appendChild(what);

      ui.anchors.appendChild(row);
    });

    var rest = ordered.length - shown.length;
    if (rest > 0) {
      ui.anchors.appendChild(el("li", "anchor-rest",
        rest + (rest === 1 ? " more habit" : " more habits") + " learned, not shown"));
    }
  }

  function paintDevices(reading) {
    var down = {};
    (reading.devices_down || []).forEach(function (id) { down[id] = true; });
    Array.prototype.forEach.call(ui.devices.children, function (row) {
      row.dataset.down = down[row.dataset.device] ? "1" : "0";
    });
    Array.prototype.forEach.call(ui.tracks.children, function (row) {
      row.dataset.down = down[row.dataset.device] ? "1" : "0";
    });
  }

  /* ---------- playing the day ---------- */

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    ui.play.setAttribute("aria-pressed", "false");
    ui.playLabel.textContent = "Play";
    ui.playIcon.setAttribute("d", "M4.5 2.6v10.8l8.4-5.4z");
  }

  function start() {
    if (!day || !day.readings || !day.readings.length) { return; }
    var step = day.step_minutes || 5;
    var last = day.readings[day.readings.length - 1].minute;
    if (minute >= last) { show(0); }

    ui.play.setAttribute("aria-pressed", "true");
    ui.playLabel.textContent = "Pause";
    ui.playIcon.setAttribute("d", "M4 2.6h3v10.8H4zm5 0h3v10.8H9z");

    timer = setInterval(function () {
      if (minute >= last) { stop(); return; }
      show(minute + step);
    }, 260 / Number(ui.speed.value || 1));
  }

  /* ---------- wiring ---------- */

  ui.gateForm.addEventListener("submit", signIn);
  ui.who.addEventListener("click", signOut);

  ui.scenario.addEventListener("change", function () {
    following = true;
    load(ui.scenario.value);
  });

  ui.onDate.addEventListener("change", function () {
    var chosen = ui.onDate.value;
    viewing = chosen || null;
    following = true;
    ui.standby.hidden = true;
    ui.app.dataset.mode = "";
    load(ui.scenario.value);
  });

  ui.seeDemo.addEventListener("click", function () {
    var demos = entries.filter(function (entry) { return entry.demo; });
    if (!demos.length) { return; }
    var pick = demos.filter(function (entry) { return entry.key === "fall"; })[0] || demos[0];
    ui.scenario.value = pick.key;
    load(pick.key);
  });

  ui.time.addEventListener("input", function () {
    stop();
    show(Number(ui.time.value));
    following = Number(ui.time.value) >= Number(ui.time.max);
  });

  ui.play.addEventListener("click", function () {
    if (timer) { stop(); } else { start(); }
  });

  ui.speed.addEventListener("change", function () {
    if (timer) { stop(); start(); }
  });

  ui.ribbon.addEventListener("click", function (event) {
    var box = ui.ribbon.getBoundingClientRect();
    if (!box.width) { return; }
    stop();
    show(((event.clientX - box.left) / box.width) * DAY);
  });

  document.addEventListener("keydown", function (event) {
    if (event.target !== document.body) { return; }
    if (event.key === " ") {
      event.preventDefault();
      if (timer) { stop(); } else { start(); }
      return;
    }
    // Stepping through the day is the one thing on this page worth doing
    // slowly, and a slider you have to hit with a mouse is no use to somebody
    // who cannot. Five minutes a press, an hour with shift held.
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") { return; }
    event.preventDefault();
    stop();
    var step = (event.shiftKey ? 60 : 5) * (event.key === "ArrowLeft" ? -1 : 1);
    show(Math.max(0, Math.min(DAY, (Number(ui.time.value) || 0) + step)));
  });

  boot();
})();
