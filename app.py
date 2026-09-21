"""
Automatic Public Transport Information Agent
============================================
A mini project built with Gradio.

The agent answers the questions a commuter usually has:
  * How do I get from A to B?           (journey planner with transfers)
  * When is the next bus / metro?       (live-style departure board)
  * How much will it cost?              (distance-based fares + concessions)
  * What are the routes, first/last     (route information)
    services and timings?
  * Is anything delayed today?          (service alerts)

Everything runs offline on a small sample network ("Metro City").
Replace the ROUTES list (or load your own GTFS/CSV data) to use a real city.

Run:  python app.py
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import datetime

import gradio as gr

# ----------------------------------------------------------------------------
# 1. TRANSPORT DATA  (sample network - edit freely)
# ----------------------------------------------------------------------------
CURRENCY = "₹"
TRANSFER_BUFFER = 3  # minutes needed to change between routes
DISCOUNTS = {"Adult": 1.0, "Student (30% off)": 0.7, "Senior (50% off)": 0.5}


@dataclass(frozen=True)
class Route:
    id: str
    name: str
    mode: str  # "Metro" or "Bus"
    color: str
    stops: tuple  # ordered stop names, terminus to terminus
    first: str  # first departure from the terminus (HH:MM)
    last: str  # last departure from the terminus (HH:MM)
    headway: int  # minutes between vehicles
    mins_per_stop: int  # average minutes between two stops
    base_fare: int
    per_stop_fare: int

    @property
    def icon(self) -> str:
        return "🚇" if self.mode == "Metro" else "🚌"

    @property
    def label(self) -> str:
        return f"{self.id} {self.name}"


ROUTES = [
    Route("M1", "Red Line", "Metro", "#d64545",
          ("Airport", "Tech Park", "University", "Central Station", "Market Square", "Old Town"),
          "05:30", "22:30", 8, 3, 10, 5),
    Route("M2", "Blue Line", "Metro", "#2f6fdb",
          ("Lakeside", "Stadium", "Central Station", "City Hospital", "Riverside"),
          "05:45", "22:45", 10, 3, 10, 5),
    Route("B10", "City Circular", "Bus", "#1f9d6b",
          ("Bus Terminal", "Market Square", "City Hospital", "Green Park", "Lakeside"),
          "06:00", "21:30", 15, 4, 5, 2),
    Route("B21", "Campus Connector", "Bus", "#d98a1c",
          ("University", "Green Park", "Stadium", "Bus Terminal"),
          "06:15", "21:15", 20, 5, 5, 2),
    Route("B5", "Heritage Loop", "Bus", "#8a4fd0",
          ("Central Station", "Old Town", "Riverside", "Green Park"),
          "06:00", "21:00", 12, 4, 5, 2),
]
ROUTE_BY_ID = {r.id: r for r in ROUTES}
STOPS = sorted({s for r in ROUTES for s in r.stops})
INTERCHANGES = {s for s in STOPS if sum(s in r.stops for r in ROUTES) > 1}

# Live service alerts (route id -> info). Editable from the "Service Alerts" tab.
ALERTS: dict[str, dict] = {
    "B10": {"delay": 7, "msg": "Road works near Market Square, expect slower travel."}
}


def delay_of(route: Route) -> int:
    return ALERTS.get(route.id, {}).get("delay", 0)


# ----------------------------------------------------------------------------
# 2. TIME HELPERS
# ----------------------------------------------------------------------------
def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def fmt(minutes: int) -> str:
    minutes = int(minutes) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def now_min() -> int:
    n = datetime.now()
    return n.hour * 60 + n.minute


def parse_time(text: str | None) -> int | None:
    """'now', '18:30', '9:05', '9 pm', '9:30pm' -> minutes since midnight."""
    if text is None or not text.strip() or text.strip().lower() in ("now", "today"):
        return now_min()
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*", text.lower())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


# ----------------------------------------------------------------------------
# 3. SCHEDULE ENGINE
# ----------------------------------------------------------------------------
def _offset(route: Route, idx: int, direction: int) -> int:
    """Minutes after leaving the terminus that a vehicle reaches stop `idx`."""
    n = len(route.stops)
    steps = idx if direction == 0 else n - 1 - idx
    return steps * route.mins_per_stop


def upcoming(route: Route, idx: int, direction: int, after: int, count: int = 1) -> list[int]:
    """Next `count` departures (minutes) from stop `idx` at or after `after`.
    direction 0 = towards last stop, 1 = towards first stop."""
    n = len(route.stops)
    if (direction == 0 and idx == n - 1) or (direction == 1 and idx == 0):
        return []  # vehicle terminates here, nobody boards
    off = _offset(route, idx, direction) + delay_of(route)
    out, t, last = [], to_min(route.first), to_min(route.last)
    while t <= last and len(out) < count:
        dep = t + off
        if dep >= after:
            out.append(dep)
        t += route.headway
    return out


def service_span(route: Route, idx: int, direction: int):
    n = len(route.stops)
    if (direction == 0 and idx == n - 1) or (direction == 1 and idx == 0):
        return None
    off = _offset(route, idx, direction)
    return to_min(route.first) + off, to_min(route.last) + off


# ----------------------------------------------------------------------------
# 4. JOURNEY PLANNER  (direct + up to 2 transfers)
# ----------------------------------------------------------------------------
@dataclass
class Leg:
    route: Route
    board: str
    alight: str
    dep: int
    arr: int
    n_stops: int
    towards: str

    @property
    def fare(self) -> int:
        return self.route.base_fare + self.route.per_stop_fare * self.n_stops


@dataclass
class Journey:
    legs: list

    @property
    def dep(self) -> int:
        return self.legs[0].dep

    @property
    def arr(self) -> int:
        return self.legs[-1].arr

    @property
    def transfers(self) -> int:
        return len(self.legs) - 1

    @property
    def fare(self) -> int:
        return sum(leg.fare for leg in self.legs)


def find_journeys(origin: str, dest: str, start: int, max_transfers: int = 2) -> list[Journey]:
    found: list[Journey] = []

    def dfs(cur, ready_at, legs, used, visited):
        for r in ROUTES:
            if r.id in used or cur not in r.stops:
                continue
            i = r.stops.index(cur)
            ready = ready_at + (TRANSFER_BUFFER if legs else 0)
            for j, s in enumerate(r.stops):
                if j == i or s in visited:
                    continue
                is_dest = s == dest
                if not is_dest and (s not in INTERCHANGES or len(legs) >= max_transfers):
                    continue
                direction = 0 if j > i else 1
                deps = upcoming(r, i, direction, ready)
                if not deps:
                    continue
                n = abs(j - i)
                leg = Leg(r, cur, s, deps[0], deps[0] + n * r.mins_per_stop, n,
                          r.stops[-1] if direction == 0 else r.stops[0])
                if is_dest:
                    found.append(Journey(legs + [leg]))
                else:
                    dfs(s, leg.arr, legs + [leg], used | {r.id}, visited | {s})

    dfs(origin, start, [], frozenset(), frozenset({origin}))

    unique, seen = [], set()
    for j in found:
        key = tuple((l.route.id, l.board, l.alight) for l in j.legs)
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


def pick_options(journeys: list[Journey], k: int = 3):
    """Choose fastest, fewest-transfers and cheapest, then fill up by arrival time."""
    if not journeys:
        return []
    by_arrival = sorted(journeys, key=lambda j: (j.arr, j.transfers, j.fare))
    picks = [
        (by_arrival[0], "⚡ Fastest"),
        (min(journeys, key=lambda j: (j.transfers, j.arr, j.fare)), "🔁 Fewest changes"),
        (min(journeys, key=lambda j: (j.fare, j.arr, j.transfers)), "💰 Cheapest"),
    ]
    chosen: list[tuple[Journey, list[str]]] = []
    for j, tag in picks:
        for c, tags in chosen:
            if c is j:
                tags.append(tag)
                break
        else:
            chosen.append((j, [tag]))
    for j in by_arrival:
        if len(chosen) >= k:
            break
        if all(c is not j for c, _ in chosen):
            chosen.append((j, []))
    chosen.sort(key=lambda x: (x[0].arr, x[0].transfers))
    return chosen[:k]


def render_journey(n: int, j: Journey, tags: list[str], factor: float, start: int) -> str:
    fare = round(j.fare * factor)
    changes = "direct" if j.transfers == 0 else f"{j.transfers} change{'s' if j.transfers > 1 else ''}"
    title = f"#### Option {n}" + (f" · {' · '.join(tags)}" if tags else "")
    wait = max(j.dep - start, 0)
    leaves = "Leaves now." if wait == 0 else f"Leave in {wait} min."
    lines = [
        title,
        f"**{fmt(j.dep)} → {fmt(j.arr)}**, {j.arr - j.dep} min travelling, {changes}, "
        f"**{CURRENCY}{fare}**. {leaves}",
        "",
    ]
    for i, leg in enumerate(j.legs, 1):
        d = delay_of(leg.route)
        warn = f" ⚠️ running +{d} min late" if d else ""
        lines.append(
            f"{i}. {leg.route.icon} **{leg.route.id} {leg.route.name}** towards {leg.towards}: "
            f"board **{leg.board}** at {fmt(leg.dep)}, get off at **{leg.alight}** at {fmt(leg.arr)} "
            f"({leg.n_stops} stop{'s' if leg.n_stops > 1 else ''}){warn}"
        )
        if i < len(j.legs):
            wait = j.legs[i].dep - leg.arr
            lines.append(f"   - 🔄 Change at **{leg.alight}**, {wait} min until the next vehicle leaves")
    return "\n".join(lines)


def plan_trip(origin: str, dest: str, when: str, ptype: str = "Adult", start: int | None = None) -> str:
    if not origin or not dest:
        return "Pick both a starting stop and a destination."
    if origin == dest:
        return "Your start and destination are the same stop. Pick two different stops."
    if start is None:
        start = parse_time(when)
    if start is None:
        return "I couldn't read that time. Try `18:30`, `6:30 pm` or `now`."
    options = pick_options(find_journeys(origin, dest, start))
    header = f"### {origin} → {dest}\nDeparting after **{fmt(start)}**"
    if not options:
        return (f"{header}\n\nNo service found after that time. "
                f"The last departures leave in the evening, try an earlier time.")
    factor = DISCOUNTS.get(ptype, 1.0)
    body = "\n\n".join(render_journey(i, j, t, factor, start) for i, (j, t) in enumerate(options, 1))
    fare_note = "" if factor == 1 else f"\n\n_Fares shown with the **{ptype}** concession._"
    return f"{header}\n\n{body}{fare_note}"


# ----------------------------------------------------------------------------
# 5. DEPARTURE BOARD, ROUTES, ALERTS
# ----------------------------------------------------------------------------
def departures(stop: str, when: str, limit: int = 10):
    """Returns (markdown message, table rows)."""
    after = parse_time(when)
    if after is None:
        return "I couldn't read that time. Try `18:30`, `6:30 pm` or `now`.", []
    rows = []
    for r in ROUTES:
        if stop not in r.stops:
            continue
        i = r.stops.index(stop)
        for d in (0, 1):
            towards = r.stops[-1] if d == 0 else r.stops[0]
            for dep in upcoming(r, i, d, after, 3):
                dl = delay_of(r)
                status = f"🟠 Delayed +{dl} min" if dl else "🟢 On time"
                rows.append((dep, [fmt(dep), dep - after, f"{r.icon} {r.id} {r.name}", towards, status]))
    rows.sort(key=lambda x: (x[0], x[1][2]))
    rows = [r for _, r in rows][:limit]
    if not rows:
        return (f"### {stop}\nService has ended for today. Tomorrow's first vehicles run from about 05:30.", [])
    served = ", ".join(r.id for r in ROUTES if stop in r.stops)
    tag = " · interchange stop" if stop in INTERCHANGES else ""
    return f"### {stop}\nServed by {served}{tag}. Showing departures after **{fmt(after)}**.", rows


def route_info_md(r: Route) -> str:
    ride = (len(r.stops) - 1) * r.mins_per_stop
    d = delay_of(r)
    status = f"🟠 running **+{d} min** late. {ALERTS[r.id]['msg']}" if d else "🟢 running on time"
    return (
        f"### {r.icon} {r.id} {r.name}\n"
        f"- **Type:** {r.mode}\n"
        f"- **Stops ({len(r.stops)}):** {' → '.join(r.stops)}\n"
        f"- **Service hours:** {r.first} to {r.last} (departures from the terminus)\n"
        f"- **Frequency:** every {r.headway} min\n"
        f"- **End-to-end time:** about {ride} min\n"
        f"- **Fare:** {CURRENCY}{r.base_fare} + {CURRENCY}{r.per_stop_fare} per stop\n"
        f"- **Status:** {status}"
    )


def route_diagram_html(r: Route) -> str:
    items = "".join(
        f'<div style="flex:1;min-width:92px;text-align:center">'
        f'<div style="height:5px;background:{r.color};position:relative;margin-top:12px">'
        f'<span style="position:absolute;left:50%;top:-6px;width:13px;height:13px;border-radius:50%;'
        f'background:var(--background-fill-primary);border:3px solid {r.color};'
        f'transform:translateX(-50%)"></span></div>'
        f'<div style="font-size:12.5px;margin-top:14px;padding:0 2px">{s}'
        f'{"<br><small>⇄ interchange</small>" if s in INTERCHANGES else ""}</div></div>'
        for s in r.stops
    )
    return f'<div style="display:flex;overflow-x:auto;padding:8px 0 4px">{items}</div>'


def route_details(route_id: str):
    r = ROUTE_BY_ID[route_id]
    return route_info_md(r), route_diagram_html(r)


def routes_overview_rows():
    return [
        [r.id, r.name, r.mode, f"{r.stops[0]} ↔ {r.stops[-1]}", r.first, r.last,
         f"{r.headway} min", f"{CURRENCY}{r.base_fare} + {CURRENCY}{r.per_stop_fare}/stop"]
        for r in ROUTES
    ]


def alerts_md() -> str:
    if not ALERTS:
        return "### ✅ All services running normally\nNo active alerts."
    lines = ["### Active alerts"]
    for rid, a in ALERTS.items():
        r = ROUTE_BY_ID[rid]
        delay = f"delayed **+{a['delay']} min**. " if a["delay"] else ""
        lines.append(f"- {r.icon} **{r.id} {r.name}**: {delay}{a['msg']}")
    return "\n".join(lines)


def set_alert(route_id: str, delay: int, msg: str) -> str:
    delay = int(delay or 0)
    msg = (msg or "").strip()
    if delay == 0 and not msg:
        ALERTS.pop(route_id, None)
    else:
        ALERTS[route_id] = {"delay": delay, "msg": msg or "Operational delay."}
    return alerts_md()


def clear_alerts() -> str:
    ALERTS.clear()
    return alerts_md()


# ----------------------------------------------------------------------------
# 6. CONVERSATIONAL AGENT  (rule-based NLU: intent + entity extraction)
# ----------------------------------------------------------------------------
ALIASES: dict[str, str] = {s.lower(): s for s in STOPS}
ALIASES.update({
    "central": "Central Station", "station": "Central Station", "railway station": "Central Station",
    "hospital": "City Hospital", "uni": "University", "college": "University", "campus": "University",
    "tech": "Tech Park", "it park": "Tech Park", "market": "Market Square", "lake": "Lakeside",
    "river": "Riverside", "green": "Green Park", "terminal": "Bus Terminal", "bus stand": "Bus Terminal",
    "bus station": "Bus Terminal", "old city": "Old Town", "heritage": "Old Town",
})
_FUZZY_WORDS = [a for a in ALIASES if " " not in a and len(a) >= 5]
_FUZZY_SKIP = {"take", "rider", "ride", "time", "next", "from", "here", "that", "when", "with",
               "this", "stop", "route", "line", "buses", "metro", "cost", "price", "late", "tell"}


def find_stops(text: str) -> list[str]:
    """Stops mentioned in the text, in order of appearance (handles small typos)."""
    t = " " + re.sub(r"[^a-z0-9: ]", " ", text.lower()) + " "
    taken = [False] * len(t)
    hits: list[tuple[int, str]] = []
    for alias in sorted(ALIASES, key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(alias)}\b", t):
            if any(taken[m.start():m.end()]):
                continue
            taken[m.start():m.end()] = [True] * (m.end() - m.start())
            hits.append((m.start(), ALIASES[alias]))
    for m in re.finditer(r"[a-z]{5,}", t):
        if any(taken[m.start():m.end()]) or m.group() in _FUZZY_SKIP:
            continue
        close = difflib.get_close_matches(m.group(), _FUZZY_WORDS, n=1, cutoff=0.8)
        if close:
            hits.append((m.start(), ALIASES[close[0]]))
    ordered: list[str] = []
    for _, s in sorted(hits):
        if s not in ordered:
            ordered.append(s)
    return ordered


def find_routes(text: str) -> list[Route]:
    t = text.lower()
    return [r for r in ROUTES if re.search(rf"\b{r.id.lower()}\b", t) or r.name.lower() in t]


def extract_time(q: str) -> int:
    m = re.search(r"\b(?:at|after|around|by|leave|leaving|depart(?:ing)?)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", q)
    if not m:
        m = re.search(r"\b(\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))\b", q)
    if m:
        parsed = parse_time(m.group(1).strip())
        if parsed is not None:
            return parsed
    return now_min()


def extract_origin_dest(q: str, stops: list[str]):
    for pattern, order in ((r"\bfrom\b\s+(.*?)\s+\bto\b\s+(.*)", "od"),
                           (r"\bto\b\s+(.*?)\s+\bfrom\b\s+(.*)", "do"),
                           (r"^(.*?)\s+\bto\b\s+(.*)$", "od")):
        m = re.search(pattern, q)
        if m:
            a, b = find_stops(m.group(1)), find_stops(m.group(2))
            if a and b:
                return (a[0], b[0]) if order == "od" else (b[0], a[0])
    return stops[0], stops[1]


def md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


HELP = """👋 I'm your **public transport information agent**. Ask me things like:

- *How do I get from Airport to Riverside?*
- *Next buses at Central Station*
- *Fare from Tech Park to Stadium for a student*
- *Tell me about route B10*
- *When is the first and last metro at University?*
- *Any delays today?*
- *Plan a trip from Lakeside to Old Town at 6:30 pm*

Stops I know: """ + ", ".join(STOPS) + "."


def agent_reply(text: str) -> str:
    q = text.lower().strip()
    if not q:
        return HELP
    stops, routes = find_stops(q), find_routes(q)
    start = extract_time(q)
    ptype = "Student (30% off)" if "student" in q else "Senior (50% off)" if "senior" in q else "Adult"

    # greetings / help
    if re.search(r"\b(hi|hello|hey|help|what can you do|thanks|thank you)\b", q) and not stops and not routes:
        return HELP

    # alerts / status
    if re.search(r"delay|alert|disrupt|status|running late|on time|cancel|problem", q) and len(stops) < 2:
        if routes:
            return "\n\n".join(route_info_md(r) for r in routes)
        return alerts_md()

    # first / last service
    if re.search(r"\b(first|last|earliest|latest)\b", q) and re.search(r"\b(bus|buses|metro|train|service|departure)", q):
        targets = routes or [r for r in ROUTES if not stops or stops[0] in r.stops]
        rows = []
        for r in targets:
            idxs = [r.stops.index(stops[0])] if stops and stops[0] in r.stops else [0, len(r.stops) - 1]
            for i in idxs:
                for d in (0, 1):
                    span = service_span(r, i, d)
                    if span and (stops or i == (0 if d == 0 else len(r.stops) - 1)):
                        towards = r.stops[-1] if d == 0 else r.stops[0]
                        rows.append([f"{r.icon} {r.id}", r.stops[i], towards, fmt(span[0]), fmt(span[1])])
        where = f" at **{stops[0]}**" if stops else ""
        return f"### First and last departures{where}\n" + md_table(
            ["Route", "From", "Towards", "First", "Last"], rows)

    # fares
    if re.search(r"\b(fare|fares|cost|price|ticket|how much|charge)\b", q):
        if len(stops) >= 2:
            o, d = extract_origin_dest(q, stops)
            opts = pick_options(find_journeys(o, d, start))
            if not opts:
                return f"I couldn't find a service from **{o}** to **{d}** right now."
            factor = DISCOUNTS[ptype]
            rows = [[i, " → ".join(f"{l.route.id}" for l in j.legs), f"{j.arr - j.dep} min",
                     f"{CURRENCY}{round(j.fare * factor)}"] for i, (j, _) in enumerate(opts, 1)]
            return (f"### Fare: {o} → {d} ({ptype})\n" +
                    md_table(["Option", "Routes", "Time", "Fare"], rows))
        if routes:
            return "\n".join(f"{r.icon} **{r.id} {r.name}**: {CURRENCY}{r.base_fare} + "
                             f"{CURRENCY}{r.per_stop_fare} per stop" for r in routes)
        return ("Fares are **base fare + a charge per stop**:\n\n" +
                md_table(["Route", "Base", "Per stop"],
                         [[f"{r.icon} {r.id} {r.name}", f"{CURRENCY}{r.base_fare}",
                           f"{CURRENCY}{r.per_stop_fare}"] for r in ROUTES]) +
                "\n\nStudents get 30% off and seniors 50% off. Give me two stops for an exact price.")

    # route information
    if routes and len(stops) < 2:
        return "\n\n".join(route_info_md(r) for r in routes)

    # journey planning
    if len(stops) >= 2:
        o, d = extract_origin_dest(q, stops)
        return plan_trip(o, d, "", ptype, start=start)

    # departures at one stop
    if len(stops) == 1:
        msg, rows = departures(stops[0], fmt(start), limit=8)
        if not rows:
            return msg
        table = md_table(["Time", "In", "Route", "Towards", "Status"],
                         [[r[0], f"{r[1]} min", r[2], r[3], r[4]] for r in rows])
        return f"{msg}\n\n{table}"

    return ("I didn't catch a stop or route in that. Try naming a stop, for example "
            "*next bus at University*, or ask for **help** to see what I can do.\n\n"
            "Known stops: " + ", ".join(STOPS) + ".")


def chat_fn(message, history):
    return agent_reply(message)


# ----------------------------------------------------------------------------
# 7. GRADIO USER INTERFACE
# ----------------------------------------------------------------------------
def build_ui() -> gr.Blocks:
    route_choices = [(f"{r.id} · {r.name}", r.id) for r in ROUTES]

    with gr.Blocks(title="Public Transport Information Agent") as demo:
        gr.Markdown(
            "# 🚍 Public Transport Information Agent\n"
            "Ask in plain English, plan a trip, check departures, compare fares and see live alerts "
            "for **Metro City**."
        )

        with gr.Tabs():
            # ---- Chat -------------------------------------------------------
            with gr.Tab("💬 Ask the agent"):
                gr.ChatInterface(
                    fn=chat_fn,
                    examples=[
                        "How do I get from Airport to Riverside?",
                        "Next buses at Central Station",
                        "Fare from Tech Park to Stadium for a student",
                        "Tell me about route B10",
                        "First and last metro at University",
                        "Any delays today?",
                        "Trip from Lakeside to Old Town at 6:30 pm",
                    ],
                    cache_examples=False,
                )

            # ---- Trip planner ----------------------------------------------
            with gr.Tab("🧭 Trip planner"):
                with gr.Row():
                    origin = gr.Dropdown(STOPS, value="Airport", label="From")
                    swap = gr.Button("⇄ Swap", scale=0, min_width=90)
                    dest = gr.Dropdown(STOPS, value="Riverside", label="To")
                with gr.Row():
                    when = gr.Textbox(value="now", label="Depart at", info="now, 18:30 or 6:30 pm")
                    ptype = gr.Radio(list(DISCOUNTS), value="Adult", label="Passenger")
                plan_btn = gr.Button("Find trips", variant="primary")
                plan_out = gr.Markdown()

                plan_inputs = [origin, dest, when, ptype]
                plan_btn.click(plan_trip, plan_inputs, plan_out)
                swap.click(lambda o, d: (d, o), [origin, dest], [origin, dest]).then(
                    plan_trip, plan_inputs, plan_out)

            # ---- Departure board -------------------------------------------
            with gr.Tab("🚏 Departures"):
                with gr.Row():
                    stop = gr.Dropdown(STOPS, value="Central Station", label="Stop")
                    dep_when = gr.Textbox(value="now", label="From time", info="now, 18:30 or 6:30 pm")
                    dep_btn = gr.Button("Refresh", variant="primary", scale=0, min_width=110)
                dep_msg = gr.Markdown()
                dep_table = gr.Dataframe(
                    headers=["Time", "In (min)", "Route", "Towards", "Status"],
                    interactive=False, wrap=True)
                for trigger in (dep_btn.click, stop.change):
                    trigger(departures, [stop, dep_when], [dep_msg, dep_table])
                demo.load(departures, [stop, dep_when], [dep_msg, dep_table])

            # ---- Routes & fares --------------------------------------------
            with gr.Tab("🗺️ Routes & fares"):
                route_sel = gr.Dropdown(route_choices, value="M1", label="Choose a route")
                route_md = gr.Markdown()
                route_map = gr.HTML()
                gr.Markdown("#### All routes")
                gr.Dataframe(
                    value=routes_overview_rows(),
                    headers=["ID", "Name", "Type", "Terminals", "First", "Last", "Every", "Fare"],
                    interactive=False, wrap=True)
                route_sel.change(route_details, route_sel, [route_md, route_map])
                demo.load(route_details, route_sel, [route_md, route_map])

            # ---- Alerts ----------------------------------------------------
            with gr.Tab("⚠️ Service alerts"):
                alert_view = gr.Markdown(alerts_md())
                gr.Markdown("**Simulate a disruption.** Delays feed straight into the planner, "
                            "the departure board and the chat agent.")
                with gr.Row():
                    a_route = gr.Dropdown(route_choices, value="M1", label="Route")
                    a_delay = gr.Slider(0, 30, value=5, step=1, label="Delay (minutes)")
                a_msg = gr.Textbox(label="Message", placeholder="Signal failure at Central Station")
                with gr.Row():
                    a_apply = gr.Button("Apply alert", variant="primary")
                    a_clear = gr.Button("Clear all alerts")
                a_apply.click(set_alert, [a_route, a_delay, a_msg], alert_view)
                a_clear.click(clear_alerts, None, alert_view)
                demo.load(alerts_md, None, alert_view)

    return demo


if __name__ == "__main__":
    build_ui().launch(theme=gr.themes.Soft(primary_hue="teal", neutral_hue="slate"))
