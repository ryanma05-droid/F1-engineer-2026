"""Weather calls from the game's forecast (12 Nov: the most common thing a real engineer brought
up unprompted, 41 of 976 lines: "expecting the increased rain around lap 16").

The Session packet carries a forecast for the next half hour or so (minutes ahead, weather, rain
chance). Calls, each once per change:
- rain coming while it's dry: "Rain expected in about ten minutes, around lap fourteen. Sixty
  percent chance." and again at five minutes or less;
- it starts raining: "Rain's starting, careful on the white lines.";
- rain easing while it's wet: "Rain easing in about five minutes, around lap twelve.";
- rain dropping out of the forecast after it was called: "Forecast's cleared, no rain expected
  now." Only once the chance has fallen well below the threshold (CLEAR_MARGIN), so a chance
  hovering around it doesn't flip the calls back and forth.
"""

from __future__ import annotations

from engineer.bus import events as ev
from engineer.config import Weather
from engineer.ingest.formats import f1_2025 as f
from engineer.radio import phrases
from engineer.radio.director import Priority, RadioCall, RadioDirector
from engineer.state.race_state import RaceState
from engineer.tools.rivals import minutes_to_lap, next_weather_change, raining

SOON_MIN = 5
CLEAR_MARGIN = 15           # % below the threshold before "forecast's cleared"


class WeatherAnalyzer:
    def __init__(self, bus: ev.EventBus, state: RaceState, director: RadioDirector,
                 settings: Weather | None = None):
        self.state, self.director = state, director
        self.cfg = settings or Weather()
        self._reset()
        bus.subscribe(ev.SessionStarted, lambda e: self._reset())

    def _reset(self) -> None:
        self.announced = None        # the change last called (WeatherChange)
        self.soon_said = False
        self.was_raining: bool | None = None

    def _say(self, text: str) -> None:
        self.director.submit(RadioCall(topic="weather", text=text, priority=Priority.NORMAL,
                                       max_wait_s=30.0))

    def after_packet(self, packet_id: int | None) -> None:
        """Hooked into Pipeline.after_packet: look at each Session packet's forecast."""
        if packet_id != f.PacketId.SESSION or not self.cfg.enabled:
            return
        s = self.state
        if s.session.session_type is None or not s.session.forecast:
            return
        wet = raining(s)
        if self.was_raining is not None and wet and not self.was_raining:
            self._say(phrases.rain_started_phrase(s.session.weather or 0))
            self.announced, self.soon_said = None, False
        self.was_raining = wet
        change = next_weather_change(s, self.cfg.rain_pct, self.cfg.horizon_min)
        if change is not None:
            lap = minutes_to_lap(s, change.minutes) if s.session.is_race else None
            if self.announced is None or self.announced.kind != change.kind:
                self.announced, self.soon_said = change, change.minutes <= SOON_MIN
                self._say(phrases.rain_coming_phrase(change.minutes, change.rain_pct, lap,
                                                     change.weather)
                          if change.kind == "rain" else
                          phrases.rain_easing_phrase(change.minutes, lap))
            elif change.kind == "rain" and not self.soon_said and change.minutes <= SOON_MIN:
                self.soon_said = True
                self._say(phrases.rain_coming_phrase(change.minutes, change.rain_pct, lap,
                                                     change.weather, soon=True))
            return
        if self.announced is not None and self.announced.kind == "rain" and not wet:
            # Rain was called and has gone from the forecast: say so once it's clearly gone.
            near = [pct for st, m, _w, pct in s.session.forecast
                    if st == s.session.session_type and 0 < m <= self.cfg.horizon_min]
            if not near or max(near) < self.cfg.rain_pct - CLEAR_MARGIN:
                self.announced = None
                self._say(phrases.forecast_cleared_phrase())
        elif self.announced is not None and self.announced.kind == "dry" and not wet:
            self.announced = None                          # it dried, as forecast
