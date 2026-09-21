# Automatic Public Transport Information Agent

## Run in VS Code (step by step)

**Requirements:** Python 3.10 or newer (check with `python --version`) and VS Code with the
*Python* extension.

1. Open VS Code, choose **File > Open Folder** and select this `transport_agent` folder.
2. Open a terminal with **Terminal > New Terminal** (Ctrl + `).
3. Create a virtual environment:
   - Windows: `python -m venv .venv`
   - macOS / Linux: `python3 -m venv .venv`
4. Activate it:
   - Windows PowerShell: `.venv\Scripts\Activate.ps1`
     (if blocked, run `Set-ExecutionPolicy -Scope Process RemoteSigned` first)
   - Windows CMD: `.venv\Scripts\activate.bat`
   - macOS / Linux: `source .venv/bin/activate`
5. Install the dependency: `pip install -r requirements.txt`
6. Select the interpreter: press **Ctrl+Shift+P**, choose **Python: Select Interpreter**,
   then pick the one inside `.venv`.
7. Start the app, either way:
   - Press **F5** (uses the ready-made "Run Transport Agent (Gradio)" config), or
   - Run `python app.py` in the terminal.
8. Open **http://127.0.0.1:7860** in your browser (Ctrl + click the link in the terminal).

Stop the app with **Ctrl+C** in the terminal, or the red square in the debug toolbar.

**Troubleshooting**

| Problem | Fix |
|---|---|
| `python` not found | Reinstall Python and tick "Add Python to PATH", or use `py` on Windows / `python3` on macOS/Linux |
| `No module named gradio` | The venv isn't active or the interpreter isn't selected. Repeat steps 4 to 6 |
| Port 7860 already in use | Close the other app, or change the last line of `app.py` to `.launch(server_port=7861, ...)` |
| Emoji show as boxes in the terminal | Harmless. The browser page displays them fine |

---

A mini project with a Gradio GUI. It answers commuter questions in plain English and
also offers point-and-click tools. No API keys, no internet, no extra libraries.

## Run

```bash
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:7860

## What it does

| Tab | Purpose |
|---|---|
| 💬 Ask the agent | Chat in natural language: journeys, next departures, fares, route info, first/last service, delays |
| 🧭 Trip planner | Pick From / To / time / passenger type. Shows up to 3 options (fastest, fewest changes, cheapest) with transfers |
| 🚏 Departures | Live-style departure board for any stop, with on-time / delayed status |
| 🗺️ Routes & fares | Route details, a stop-by-stop line diagram, and a table of all routes |
| ⚠️ Service alerts | Simulate a delay on a route. The planner, board and chat agent all react to it |

## Example questions for the chat agent

- How do I get from Airport to Riverside?
- Next buses at Central Station
- Fare from Tech Park to Stadium for a student
- Tell me about route B10
- First and last metro at University
- Any delays today?
- Trip from Lakeside to Old Town at 6:30 pm
- when is the next bus at univarsity (typos are tolerated)

## How it works

```
User message
   |
   v
Entity extraction: stops (aliases + fuzzy match), routes, time, passenger type
   |
   v
Intent detection: alerts | first/last | fare | route info | trip | departures
   |
   v
Schedule engine: timetable generated from first/last departure + headway
   |             (per-stop offsets, both directions, live delays)
   v
Journey planner: depth-first search over routes, interchange stops only,
                 up to 2 transfers, 3 min transfer buffer, ranked
                 by arrival time / changes / fare
```

Everything lives in `app.py`, in numbered sections:
1. Transport data, 2. Time helpers, 3. Schedule engine, 4. Journey planner,
5. Departures / routes / alerts, 6. Conversational agent, 7. Gradio UI.

## Use your own city

Edit the `ROUTES` list at the top of `app.py`. Each route needs an ordered list of stops,
first/last departure, headway (minutes between vehicles), minutes per stop and fares.
To load real data, parse a GTFS `stops.txt`, `routes.txt` and `stop_times.txt`
into the same `Route` objects.

## Ideas to extend

- Replace the rule-based `agent_reply` with an LLM that calls `plan_trip`, `departures` and `alerts_md` as tools
- Real-time feeds (GTFS-Realtime) instead of simulated delays
- Map view with `folium` or `plotly`
- Walking distance between nearby stops, and accessibility filters
