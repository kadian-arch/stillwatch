"use strict";

const LABELS = {
  NORMAL: "Normal",
  QUIET: "Quiet",
  CONCERN: "Concern",
  ALERT: "Alert",
  AWAY: "Out",
  UNKNOWN: "Cannot tell",
};

const PACE_MS = 120;
const DEFAULT_MINUTE = 6 * 60;
const SVG_NS = "http://www.w3.org/2000/svg";

const ui = {};
for (const id of [
  "app", "empty", "home", "scenario", "compare", "now", "play", "playLabel", "playIcon",
  "speed", "clock", "dayLabel", "strips", "time", "gauge", "gaugeNote", "heat",
  "learnedFrom", "anchors", "quietbars", "devices", "footnote", "messages",
]) {
  ui[id] = document.getElementById(id);
}

const view = {
  scenarios: [],
  primary: "",
  second: "",
  days: {},
  index: DEFAULT_MINUTE / 5,
  timer: null,
  refresh: null,
  following: false,
  strips: [],
};

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") node.setAttribute("style", value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

function shape(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function clock(minute) {
  const whole = ((Math.floor(minute) % 1440) + 1440) % 1440;
  return pad(Math.floor(whole / 60)) + ":" + pad(whole % 60);
}

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(url + " answered " + response.status);
  return response.json();
}

function showEmpty(message) {
  ui.empty.textContent = message;
  ui.empty.hidden = false;
  ui.app.classList.add("is-empty");
  ui.app.setAttribute("aria-busy", "false");
}

/* Data */

function prepare(day) {
  const byHour = {};
  for (const device of day.devices) {
    const hours = Array.from({ length: 24 }, () => []);
    for (const minute of day.today[device.device_id] || []) {
      hours[Math.min(23, Math.floor(minute / 60))].push(minute);
    }
    hours.forEach((list) => list.sort((a, b) => a - b));
    byHour[device.device_id] = hours;
  }
  day.byHour = byHour;
  day.names = Object.fromEntries(day.devices.map((d) => [d.device_id, d.name]));
  return day;
}

async function loadDay(key) {
  if (!view.days[key]) {
    view.days[key] = prepare(await getJSON("/api/day?scenario=" + encodeURIComponent(key)));
  }
  return view.days[key];
}

function current(day) {
  return day.readings[Math.min(view.index, day.readings.length - 1)];
}

function isDown(day, deviceId, from, to) {
  return day.outages.some((span) =>
    span.device_id === deviceId && span.from < to && span.to > from);
}

/* Status */

function statusCard(day, reading, labelled) {
  return el("article", { class: "status", "data-state": reading.state },
    el("div", { class: "status-top" },
      el("span", { class: "state" }, el("i", { "aria-hidden": "true" }), LABELS[reading.state] || reading.state),
      el("span", { class: "when", text: "as of " + clock(reading.minute) + ", " + day.weekday }),
      labelled ? el("span", { class: "which", text: day.scenario.title }) : null),
    el("p", { class: "headline", text: reading.headline }),
    el("ul", { class: "reasons" }, reading.reasons.map((line) => el("li", { text: line }))));
}

function renderStatus(days) {
  ui.now.replaceChildren(...days.map((day) => statusCard(day, current(day), days.length > 1)));
  ui.app.classList.toggle("compare", days.length > 1);
}

/* Strips */

function buildStrip(day, labelled) {
  const total = day.readings.length;
  const width = 1000;
  const canvas = shape("svg", {
    class: "strip",
    viewBox: "0 0 " + width + " 34",
    preserveAspectRatio: "none",
    role: "img",
    "aria-label": "States across the day for " + day.scenario.title,
  });

  let start = 0;
  for (let i = 1; i <= total; i += 1) {
    const state = day.readings[start].state;
    if (i < total && day.readings[i].state === state) continue;
    const x = (start / total) * width;
    const w = ((i - start) / total) * width;
    const run = shape("rect", { class: "run", x, y: 0, width: w + 0.5, height: 34, "data-state": state });
    run.style.fill = "var(--fill)";
    canvas.append(run);
    start = i;
  }

  for (const device of day.devices) {
    if (device.zone_class !== "transit") continue;
    for (const minute of day.today[device.device_id] || []) {
      const x = (minute / 1440) * width;
      canvas.append(shape("line", {
        class: "tick", x1: x, x2: x, y1: 0, y2: 9, "vector-effect": "non-scaling-stroke",
      }));
    }
  }
  for (const ding of day.dings) {
    const x = (ding.minute / 1440) * width;
    canvas.append(shape("circle", { class: "ding", cx: x, cy: 29, r: 3 }));
  }

  const cover = shape("rect", { x: 0, y: 0, width, height: 34 });
  cover.style.fill = "var(--track)";
  const head = shape("line", {
    class: "head", x1: 0, x2: 0, y1: 0, y2: 34, "vector-effect": "non-scaling-stroke",
  });
  canvas.append(cover, head);

  const row = el("div", { class: "strip-row" },
    labelled ? el("span", { class: "strip-label", text: day.scenario.title }) : null);
  row.append(canvas);
  return { row, cover, head, total, width };
}

function buildStrips(days) {
  view.strips = days.map((day) => buildStrip(day, days.length > 1));
  ui.strips.replaceChildren(...view.strips.map((strip) => strip.row));
}

function moveStrips() {
  for (const strip of view.strips) {
    const x = ((Math.min(view.index, strip.total - 1) + 1) / strip.total) * strip.width;
    strip.cover.setAttribute("x", x);
    strip.cover.setAttribute("width", Math.max(0, strip.width - x));
    strip.head.setAttribute("x1", x);
    strip.head.setAttribute("x2", x);
  }
}

/* Gauge */

function renderGauge(day, reading) {
  ui.gauge.replaceChildren();

  if (reading.state === "UNKNOWN" || reading.silence_seconds === null || !reading.threshold_seconds) {
    ui.gauge.append(el("div", { class: "gauge-figure", text: "Unknown" }));
    ui.gaugeNote.textContent = "There is nothing reliable to measure the quiet against right now.";
    return;
  }

  const limits = day.ladder;
  const usual = reading.threshold_seconds;
  const silence = reading.silence_seconds;
  const top = Math.max(usual * (limits.alert_at + 0.8), silence * 1.08);
  const share = (value) => Math.min(100, (value / top) * 100);

  const edges = [
    ["NORMAL", 0, usual * limits.quiet_at],
    ["QUIET", usual * limits.quiet_at, usual * limits.concern_at],
    ["CONCERN", usual * limits.concern_at, usual * limits.alert_at],
    ["ALERT", usual * limits.alert_at, top],
  ];

  const away = reading.state === "AWAY";
  const track = el("div", { class: "gauge-track" + (away ? " muted" : "") },
    edges.map(([state, low, high]) =>
      el("div", { class: "zone", "data-state": state, style: "width:" + (share(high) - share(low)) + "%" })));
  track.append(el("div", { class: "gauge-needle", style: "left:" + share(silence) + "%" }));

  const marks = el("div", { class: "gauge-marks", "aria-hidden": "true" },
    el("span", { style: "left:" + share(usual * limits.quiet_at) + "%", text: "usual" }),
    el("span", { style: "left:" + share(usual * limits.concern_at) + "%", text: "concern" }),
    el("span", { style: "left:" + share(usual * limits.alert_at) + "%", text: "alert" }));

  ui.gauge.append(
    el("div", { class: "gauge-figure" },
      reading.silence_text,
      el("small", { text: "since " + clock(reading.began_minute) })),
    track, marks);

  const startHour = clock(Math.floor(reading.began_minute / 60) * 60);
  if (away) {
    ui.gaugeNote.textContent = reading.absence_unusual
      ? "She went out as the quiet began, so it does not count against her. She has been out longer than she usually is."
      : "She went out as the quiet began, so it does not count against her.";
  } else {
    ui.gaugeNote.textContent =
      "When a quiet spell starts around " + startHour + " on a " + day.daytype +
      ", hers normally runs no longer than " + reading.threshold_text + ".";
  }
}

/* Heat grid */

function orderedDevices(day) {
  const inside = day.devices.filter((d) => d.zone_class !== "transit");
  const doors = day.devices.filter((d) => d.zone_class === "transit");
  return { inside, doors };
}

function heatRow(day, device, minute) {
  const hourNow = Math.floor(minute / 60);
  const rates = day.baseline.rhythm[device.device_id] || [];
  const cells = [el("div", { class: "label", title: device.name },
    device.name,
    el("small", { text: device.zone_class === "transit" ? "door" : "inside" }))];

  for (let hour = 0; hour < 24; hour += 1) {
    const from = hour * 60;
    const upTo = Math.min(from + 60, minute + 0.001);
    const rate = rates[hour] || 0;
    const seen = from <= minute && (day.byHour[device.device_id][hour] || []).some((m) => m <= minute);
    const down = from <= minute && isDown(day, device.device_id, from, upTo);
    const classes = ["cell"];
    if (from > minute) classes.push("future");
    if (hour === hourNow) classes.push("current");
    if (seen) classes.push("seen");
    if (down) classes.push("down");

    const tip = device.name + ", " + pad(hour) + ":00. Usually active on " +
      Math.round(rate * 100) + "% of " + day.daytype + "s." +
      (down ? " Camera offline." : seen ? " Active today." : "");
    cells.push(el("div", { class: classes.join(" "), style: "--rate:" + rate, title: tip }));
  }
  return cells;
}

function renderHeat(day, reading) {
  const minute = reading.minute;
  const hourNow = Math.floor(minute / 60);
  const { inside, doors } = orderedDevices(day);

  const header = [el("div", { class: "label" })];
  for (let hour = 0; hour < 24; hour += 1) {
    header.push(el("div", { class: "hour" + (hour === hourNow ? " current" : ""), text: pad(hour) }));
  }

  ui.heat.replaceChildren(
    ...header,
    ...inside.flatMap((device) => heatRow(day, device, minute)),
    el("div", { class: "divider" }),
    ...doors.flatMap((device) => heatRow(day, device, minute)));
}

/* Normal */

function anchorStatus(day, anchor, reading) {
  const missed = reading.missed_anchors.some((m) =>
    m.device_id === anchor.device_id && m.window === anchor.window);
  if (missed) return "missed";

  const minute = reading.minute;
  const ids = anchor.device_id === "any_interior"
    ? day.devices.filter((d) => d.zone_class !== "transit").map((d) => d.device_id)
    : [anchor.device_id];
  const kept = ids.some((id) => (day.today[id] || []).some((m) =>
    m >= anchor.low && m < anchor.high && m <= minute));
  if (kept) return "kept";
  if (anchor.device_id !== "any_interior" && anchor.low <= minute &&
      isDown(day, anchor.device_id, anchor.low, Math.min(anchor.high, minute + 0.001))) {
    return "blind";
  }
  return anchor.minute > minute ? "later" : "waiting";
}

const ANCHOR_WORDS = {
  kept: "done",
  missed: "missed",
  later: "later",
  waiting: "not yet",
  blind: "camera off",
};

const PAST_HABITS = 5;
const NEXT_HABITS = 3;

function habitual(anchors) {
  // Anchors on the whole house after the morning only restate a room anchor a
  // few minutes away, and a room anchor at the same moment as getting up is
  // the same fact twice. Keep the list to things a person would say.
  const rise = anchors.find((a) => a.device_id === "any_interior" && a.window === "morning");
  return anchors.filter((a) => {
    if (a.device_id === "any_interior") return a === rise;
    return !(rise && Math.abs(a.minute - rise.minute) < 5);
  });
}

function howOften(rate, daytype) {
  if (rate >= 0.99) return "every " + daytype;
  if (rate >= 0.9) return "almost every " + daytype;
  return "most " + daytype + "s";
}

function renderNormal(day, reading) {
  const base = day.baseline;
  const observed = Object.values(base.days_observed).reduce((a, b) => a + b, 0);
  ui.learnedFrom.textContent =
    "Learned from " + observed + " days of her history. " + base.excluded_absences +
    " quiet spells that began with her going out were left out, since an empty house says nothing about her.";

  const habits = habitual(base.anchors);
  if (!habits.length) {
    ui.anchors.replaceChildren(el("li", { text: "Not enough history yet to know her habits." }));
  } else {
    const past = habits.filter((a) => a.minute <= reading.minute).slice(-PAST_HABITS);
    const next = habits.filter((a) => a.minute > reading.minute).slice(0, NEXT_HABITS);
    ui.anchors.replaceChildren(...[...past, ...next].map((anchor) => {
      const status = anchorStatus(day, anchor, reading);
      const where = anchor.device_id === "any_interior"
        ? "Up and about"
        : "In the " + (day.names[anchor.device_id] || anchor.device_id);
      return el("li", { class: status },
        el("time", { text: anchor.usual }),
        el("span", {}, where, el("span", { class: "often", text: howOften(anchor.hit_rate, day.daytype) })),
        el("span", { class: "state-word", text: ANCHOR_WORDS[status] }));
    }));
  }

  const values = base.quiet.map((v) => v || 0);
  const top = Math.max(...values, 1);
  const focus = reading.began_minute !== null
    ? Number(clock(reading.began_minute).slice(0, 2))
    : Math.floor(reading.minute / 60);
  ui.quietbars.replaceChildren(...values.map((value, hour) =>
    el("div", {
      class: "bar" + (hour === focus ? " current" : ""),
      style: "height:" + Math.max(2, Math.sqrt(value / top) * 100) + "%",
      title: pad(hour) + ":00, up to " + base.quiet_text[hour],
    })));
}

/* Messages */

const TONES = { urgent: "Urgent", low: "Not urgent", info: "All clear" };
const MAX_MESSAGES = 4;

function outbox(day, minute, labelled) {
  const sent = (day.notifications || []).filter((n) => n.minute <= minute).reverse();
  const group = el("div", { class: "outbox" },
    labelled ? el("p", { class: "outbox-title", text: day.scenario.title }) : null);

  if (!sent.length) {
    group.append(el("p", { class: "silent", text: "Nothing sent. Nothing has needed saying." }));
    return group;
  }

  const list = el("ul", { class: "messages" });
  sent.slice(0, MAX_MESSAGES).forEach((notice, index) => {
    const lead = notice.body.split("\n")[0];
    list.append(el("li", { class: "message" + (index === 0 ? " newest" : ""), "data-urgency": notice.urgency },
      el("div", { class: "message-top" },
        el("time", { text: clock(notice.minute) }),
        el("span", { class: "message-tone", text: TONES[notice.urgency] || notice.urgency })),
      el("p", { class: "message-subject", text: notice.subject.replace(/^Stillwatch: /, "") }),
      el("p", { class: "message-lead", text: lead })));
  });
  group.append(list);
  if (sent.length > MAX_MESSAGES) {
    group.append(el("p", { class: "message-more",
      text: (sent.length - MAX_MESSAGES) + " earlier " + (sent.length - MAX_MESSAGES === 1 ? "message" : "messages") }));
  }
  return group;
}

function renderMessages(shown, minute) {
  ui.messages.replaceChildren(...shown.map((day) => outbox(day, minute, shown.length > 1)));
}

/* Devices */

function renderDevices(day, reading) {
  const minute = reading.minute;
  const { inside, doors } = orderedDevices(day);
  ui.devices.replaceChildren(...[...inside, ...doors].map((device) => {
    const down = isDown(day, device.device_id, minute, minute + 0.001);
    const seen = (day.today[device.device_id] || []).filter((m) => m <= minute).length;
    return el("li", { class: down ? "down" : "" },
      el("span", { class: "dot", "aria-hidden": "true" }),
      el("span", {},
        device.name,
        el("span", { class: "kind", text: device.zone_class === "transit" ? "At a door" : "Inside the house" })),
      el("span", { class: "count", text: down ? "Offline" : seen + " today" }));
  }));
}

/* Rendering */

function days() {
  const list = [view.days[view.primary]];
  if (view.second && view.days[view.second]) list.push(view.days[view.second]);
  return list.filter(Boolean);
}

function render() {
  const shown = days();
  if (!shown.length) return;
  const main = shown[0];
  const reading = current(main);

  ui.clock.textContent = clock(reading.minute);
  const atEnd = view.index >= main.readings.length - 1;
  ui.dayLabel.textContent = main.live && atEnd
    ? "Live, " + main.weekday + " " + main.day
    : main.weekday + " " + main.day;
  if (main.live) view.following = atEnd;
  ui.time.value = String(view.index);

  renderStatus(shown);
  moveStrips();
  renderGauge(main, reading);
  renderHeat(main, reading);
  renderNormal(main, reading);
  renderDevices(main, reading);
  renderMessages(shown, reading.minute);
  syncUrl();
}

function setIndex(index) {
  const last = Math.min(...days().map((day) => day.readings.length - 1));
  view.index = Math.max(0, Math.min(last, index));
  render();
}

/* Playback */

function stop() {
  clearInterval(view.timer);
  view.timer = null;
  ui.play.setAttribute("aria-pressed", "false");
  ui.playLabel.textContent = "Play the day";
  ui.playIcon.setAttribute("d", "M4 2.5v11l9-5.5z");
}

function play() {
  const last = Number(ui.time.max);
  if (view.index >= last) setIndex(0);
  clearInterval(view.timer);
  view.timer = setInterval(() => {
    if (view.index >= Number(ui.time.max)) {
      stop();
      return;
    }
    setIndex(view.index + 1);
  }, PACE_MS / Number(ui.speed.value));
  ui.play.setAttribute("aria-pressed", "true");
  ui.playLabel.textContent = "Pause";
  ui.playIcon.setAttribute("d", "M4 2.5h3v11H4zM9 2.5h3v11H9z");
}

function toggle() {
  if (view.timer) stop();
  else play();
}

/* Address bar */

function syncUrl() {
  const params = new URLSearchParams();
  params.set("scenario", view.primary);
  if (view.second) params.set("compare", view.second);
  const main = view.days[view.primary];
  if (main) params.set("t", clock(current(main).minute));
  history.replaceState(null, "", "?" + params.toString());
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  const keys = view.scenarios.map((s) => s.key);
  const wanted = params.get("scenario");
  view.primary = keys.includes(wanted) ? wanted : keys[0];
  const other = params.get("compare");
  view.second = keys.includes(other) && other !== view.primary ? other : "";
  const t = params.get("t");
  if (t && /^\d{1,2}:\d{2}$/.test(t)) {
    const [h, m] = t.split(":").map(Number);
    view.index = Math.floor((h * 60 + m) / 5);
  }
}

/* Loading */

function fillPickers() {
  ui.scenario.replaceChildren(...view.scenarios.map((s) =>
    el("option", { value: s.key, text: s.title })));
  ui.compare.replaceChildren(
    el("option", { value: "", text: "Nothing" }),
    ...view.scenarios.map((s) => el("option", { value: s.key, text: s.title })));
  ui.scenario.value = view.primary;
  ui.compare.value = view.second;
}

async function load() {
  stop();
  ui.app.setAttribute("aria-busy", "true");
  try {
    await loadDay(view.primary);
    if (view.second) await loadDay(view.second);
  } catch (error) {
    showEmpty("Could not load that replay. " + error.message);
    return;
  }

  const shown = days();
  const last = Math.min(...shown.map((day) => day.readings.length - 1));
  ui.time.max = String(last);
  buildStrips(shown);

  const main = shown[0];
  ui.home.textContent = main.persona + "'s home";
  ui.footnote.textContent = main.live
    ? "Live from Ring, refreshed every minute. Times are the household's own clock."
    : "Replaying simulated data for " + main.persona + ". Times are the household's own clock.";

  clearInterval(view.refresh);
  if (main.live) {
    view.following = true;
    view.index = last;
    view.refresh = setInterval(refreshLive, 60000);
  }

  ui.app.setAttribute("aria-busy", "false");
  setIndex(view.index);
}

async function refreshLive() {
  // Today keeps happening, so ask again and stay at the present unless the
  // viewer has scrubbed back to look at something.
  delete view.days[view.primary];
  const wasAtEnd = view.following;
  await load();
  if (wasAtEnd) setIndex(Number(ui.time.max));
}

async function start() {
  let listing;
  try {
    listing = await getJSON("/api/scenarios");
  } catch (error) {
    showEmpty("Could not reach the Stillwatch service. Is it running?");
    return;
  }

  view.scenarios = listing.scenarios;
  if (!view.scenarios.length) {
    showEmpty("No replay data yet. From the project folder run: python demo_data.py");
    return;
  }

  readUrl();
  fillPickers();
  await load();
}

/* Wiring */

ui.scenario.addEventListener("change", () => {
  view.primary = ui.scenario.value;
  if (view.second === view.primary) {
    view.second = "";
    ui.compare.value = "";
  }
  load();
});

ui.compare.addEventListener("change", () => {
  view.second = ui.compare.value === view.primary ? "" : ui.compare.value;
  ui.compare.value = view.second;
  load();
});

ui.time.addEventListener("input", () => {
  stop();
  setIndex(Number(ui.time.value));
});

ui.play.addEventListener("click", toggle);

ui.speed.addEventListener("change", () => {
  if (view.timer) play();
});

ui.strips.addEventListener("click", (event) => {
  const box = ui.strips.getBoundingClientRect();
  const share = (event.clientX - box.left) / box.width;
  stop();
  setIndex(Math.round(share * Number(ui.time.max)));
});

document.addEventListener("keydown", (event) => {
  const target = event.target;
  if (target instanceof HTMLSelectElement || target === ui.time) return;
  if (event.key === " ") {
    event.preventDefault();
    toggle();
  } else if (event.key === "ArrowRight") {
    stop();
    setIndex(view.index + (event.shiftKey ? 12 : 1));
  } else if (event.key === "ArrowLeft") {
    stop();
    setIndex(view.index - (event.shiftKey ? 12 : 1));
  }
});

start();
