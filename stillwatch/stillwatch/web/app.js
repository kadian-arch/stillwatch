/* Stillwatch dashboard.

   One bundle per day arrives from /api/day and everything on screen is drawn
   from it. Moving the slider only changes which reading is shown, so scrubbing
   never waits on the network. */

(function () {
  "use strict";

  var DAY = 1440;
  var ANCHOR_LIMIT = 6;
  var THEME_KEY = "stillwatch.theme";

  var WORDS = {
    NORMAL: "All normal",
    QUIET: "Quiet",
    CONCERN: "Concern",
    ALERT: "Needs checking",
    AWAY: "Out",
    UNKNOWN: "Cannot tell"
  };

  var TINT = {
    NORMAL: "--normal",
    QUIET: "--quiet",
    CONCERN: "--concern",
    ALERT: "--alert",
    AWAY: "--away",
    UNKNOWN: "--unknown"
  };

  var UNKNOWN_WHY = {
    blind: "Every camera that matters is offline, so there is nothing to judge.",
    no_history: "There is not enough history yet to know what normal looks like.",
    no_baseline: "No rhythm has been learned for this hour yet."
  };

  var $ = function (id) { return document.getElementById(id); };

  var ui = {
    app: $("app"), empty: $("empty"), home: $("home"), theme: $("theme"),
    livePill: $("livePill"), dayField: $("dayField"), scenario: $("scenario"),
    standby: $("standby"), facts: $("facts"), seeDemo: $("seeDemo"), banner: $("banner"),
    hero: $("hero"), stateWord: $("stateWord"), heroClock: $("heroClock"),
    statement: $("statement"), reasons: $("reasons"),
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

  function clockOf(mins) {
    var whole = Math.max(0, Math.min(DAY - 1, Math.round(mins)));
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

  function fail(message) {
    ui.app.setAttribute("aria-busy", "false");
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

      ui.home.textContent = index.persona || "The household";
      entries = index.scenarios || [];

      if (!entries.length) {
        fail("No days to show yet. Once cameras are connected this fills in on its own.");
        return;
      }

      var liveEntry = entryFor("live");
      ui.livePill.hidden = !liveEntry;
      ui.dayField.hidden = entries.length < 2;

      entries.forEach(function (entry) {
        var option = el("option", null,
          (entry.title || entry.key) + (entry.demo ? " (demonstration)" : ""));
        option.value = entry.key;
        ui.scenario.appendChild(option);
      });

      var wanted = param("scenario");
      var chosen = entryFor(wanted) ? wanted : entries[0].key;
      ui.scenario.value = chosen;

      // A home connected this morning has nothing to show. Say so properly
      // rather than drawing six empty boxes.
      if (liveEntry && chosen === "live" && !health.stored_events) {
        standby(health);
        return;
      }

      load(chosen);
      if (liveEntry && !refresher) {
        refresher = setInterval(function () { load(ui.scenario.value, true); }, 60000);
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

    fetch("/api/day?scenario=" + encodeURIComponent(key))
      .then(function (reply) {
        if (!reply.ok) { throw new Error("no day"); }
        return reply.json();
      })
      .then(function (bundle) {
        day = bundle;
        ui.empty.hidden = true;
        draw();
        ui.app.setAttribute("aria-busy", "false");
        settle(quietly);
        sweep(quietly);
      })
      .catch(function () { fail("Could not load that day."); });
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
    drawFootnote();

    var ladder = day.ladder || {};
    if (ladder.alert_at) {
      var quietMark = document.querySelector(".mark-quiet");
      var concernMark = document.querySelector(".mark-concern");
      if (quietMark) { quietMark.style.left = (ladder.quiet_at / ladder.alert_at) * 100 + "%"; }
      if (concernMark) { concernMark.style.left = (ladder.concern_at / ladder.alert_at) * 100 + "%"; }
    }

    var wanted = param("t");
    var start = last;
    if (wanted && /^\d{1,2}:\d{2}$/.test(wanted)) {
      var bits = wanted.split(":");
      start = Math.min(last, Number(bits[0]) * 60 + Number(bits[1]));
    } else if (!day.live && day.notifications && day.notifications.length) {
      start = day.notifications[0].minute;
    }
    show(following || !day.live ? start : minute);
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
    ui.devices.textContent = "";
    (day.devices || []).forEach(function (device) {
      var row = el("li");
      row.dataset.device = device.device_id;
      row.appendChild(el("span", "dev-dot"));
      row.appendChild(el("span", "dev-name", device.name));
      row.appendChild(el("span", "dev-zone",
        device.zone_class === "transit" ? "way out" : "inside"));
      ui.devices.appendChild(row);
    });
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
      + ", " + clause.join(", ")
      + ". Times are the household's own clock.";

    var excluded = (day.baseline || {}).excluded_absences;
    ui.learnedFrom.textContent = observed
      ? "Learned from " + observed + " days" + (excluded
          ? ", with " + excluded + " trips out left out so they could not stretch what counts as normal."
          : ".")
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

  function paintMeter(reading) {
    var alertAt = (day.ladder || {}).alert_at || 2.5;
    var out = reading.state === "AWAY";

    ui.meterLabel.textContent = out ? "Out for" : "Still for";
    countTo(ui.meterValue, reading.silence_seconds,
            reading.silence_text || "no time at all");

    var share = reading.ratio === null || reading.ratio === undefined
      ? 0
      : Math.min(1, reading.ratio / alertAt);
    ui.fill.style.width = (share * 100) + "%";

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
      ["Last movement", reading.silence_began ? clockOf(reading.began_minute) : "none today"],
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

  ui.scenario.addEventListener("change", function () {
    following = true;
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
    }
  });

  boot();
})();
