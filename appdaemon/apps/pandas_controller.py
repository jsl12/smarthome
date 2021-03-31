from datetime import datetime
from pathlib import Path

import pandas as pd
from appdaemon.plugins.hass import hassapi as hass

from colors import get_colors


class PandasCtl(hass.Hass):
    def initialize(self):
        self.initial = self.args['initial']
        self.final = self.args['final']
        self.freq = self.args.get('freq', '1min')

        self.colors = get_colors(Path(r'/conf/apps/colors.yaml'))

        if (isinstance(self.initial, str) and self.initial in self.colors.index) and \
                (isinstance(self.final, str) and self.final in self.colors.index):
            self.initial = self.colors.loc[self.initial].values
            self.final = self.colors.loc[self.final].values

        self.profile = pd.DataFrame(
            data=[self.initial, self.final],
            index=[self.start_datetime, self.end_datetime]
        ).asfreq(self.freq).interpolate('linear').applymap(round).applymap(int).drop_duplicates(keep='first')

        self.start_timer = self.run_daily(callback=self.start, start=self.args['start'])
        self.end_timer = self.run_daily(callback=self.stop, start=self.args['end'])

        if self.active:
            self.i = s if (s := (self.profile.index <= self.current_datetime).sum()) > 0 else 0
            self.log(f'Already supposed to be active, starting. Last action: {self.profile.index[self.i-1].time().isoformat()[:8]}')
            self.start()

    @property
    def start_datetime(self) -> datetime:
        return datetime.combine(self.date(), self.parse_time(self.args['start'])).astimezone()

    @property
    def current_datetime(self) -> datetime:
        return self.datetime().astimezone()

    @property
    def end_datetime(self) -> datetime:
        return datetime.combine(self.date(), self.parse_time(self.args['end'])).astimezone()

    @property
    def active(self):
        return self.start_datetime <= self.current_datetime <= self.end_datetime

    @property
    def this_step(self):
        return self.profile.iloc[self.i]

    @property
    def prev_step(self):
        return self.profile.iloc[self.i - 1]

    def start(self, kwargs=None):
        self.operate()

    def operate(self, kwargs=None):
        self.log(self.this_step.to_dict())
        next_operation_time = self.profile.index[self.i].time().isoformat()[:8]
        self.operate_timer = self.run_at(callback=self.operate, start=next_operation_time)
        self.log(f'Next operation at {next_operation_time}')
        self.i += 1

    def stop(self, kwargs=None):
        return

    def terminate(self):
        self.cancel_timer(self.start_timer)
        self.cancel_timer(self.end_timer)
